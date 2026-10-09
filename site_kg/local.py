"""Local directory -> corpus in the site-kg format.

Docs (md/mdx/txt/wiki/rst/adoc) keep their body; code files become docs whose
body is a prose header (path, language, defined symbols) plus the raw source,
so the inverted index tokenizes identifiers. Edges come from markdown links,
wikilinks ([[Page]], Obsidian/MediaWiki style), README/index hub membership
and import/include statements -- emitted as a typed _edges.json sidecar
({"s","t","k"} triples). Unresolvable references are dropped, never invented.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

try:
    import pathspec
except ImportError:  # gitignore support degrades to the builtin noise list
    pathspec = None

DOC_EXTS = {".md", ".markdown", ".mdx", ".txt", ".wiki", ".rst", ".adoc"}
CODE_EXTS = {
    ".py": "python", ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript",
    ".cjs": "javascript", ".ts": "typescript", ".tsx": "typescript", ".go": "go",
    ".rs": "rust", ".c": "c", ".h": "c", ".cpp": "c++", ".cc": "c++", ".cxx": "c++",
    ".hpp": "c++", ".hh": "c++", ".java": "java", ".kt": "kotlin", ".rb": "ruby",
    ".php": "php", ".sh": "bash", ".lua": "lua", ".swift": "swift", ".cs": "csharp",
    ".scala": "scala", ".m": "objc", ".zig": "zig",
}
NOISE_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv", "env",
    "dist", "build", "target", "out", ".next", ".nuxt", ".tox", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", ".idea", ".vscode", "coverage", ".cache",
    "vendor", "third_party", "site-packages",
}
# lockfiles and minified bundles drown the index without adding structure
NOISE_FILES = re.compile(
    r"(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|poetry\.lock|Cargo\.lock"
    r"|go\.sum|.*\.min\.(js|css))$")
MAX_BYTES = 512 * 1024
MAX_SYMBOLS = 40
MAX_PKG_EDGE_TARGETS = 20

MD_LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s#]+)(#[^)\s]*)?\)")
WIKI_LINK = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")
FENCE = re.compile(r"```[\s\S]*?```", re.M)
INLINE = re.compile(r"`[^`]*`")
PY_IMPORT = re.compile(
    r"^\s*(?:from\s+([\w.]+)\s+import\s+(\([^)]*\)|[\w.,* ]+)|import\s+([\w., ]+))", re.M)
JS_IMPORT = re.compile(
    r"(?:import[^'\"()]*?from\s*|export[^'\"()]*?from\s*|require\(\s*|import\s*\(\s*"
    r"|import\s*)['\"]([^'\"]+)['\"]")
C_INCLUDE = re.compile(r'^\s*#\s*include\s*"([^"]+)"', re.M)
GO_IMPORT_BLOCK = re.compile(r"import\s*\(([^)]*)\)", re.S)
GO_IMPORT_ONE = re.compile(r'^\s*import\s+"([^"]+)"', re.M)
GO_QUOTED = re.compile(r'"([^"]+)"')
RUST_MOD = re.compile(r"^\s*(?:pub\s+)?mod\s+(\w+)\s*;", re.M)
RUST_USE_CRATE = re.compile(r"\buse\s+crate::([\w:]+)")
JVM_IMPORT = re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+)\s*;", re.M)
RUBY_REQUIRE = re.compile(r"^\s*require_relative\s+['\"]([^'\"]+)['\"]", re.M)

SYMBOL_RES = {
    "python": re.compile(r"^(?:class\s+\w+|(?:async\s+)?def\s+\w+)", re.M),
    "javascript": re.compile(
        r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:function\s+\w+|class\s+\w+"
        r"|(?:const|let|var)\s+\w+\s*=\s*(?:async\s*)?\()", re.M),
    "typescript": None,  # aliases javascript
    "go": re.compile(r"^(?:func\s+(?:\(\w+\s+[*\w]+\)\s+)?\w+|type\s+\w+)", re.M),
    "rust": re.compile(r"^\s*(?:pub\s+)?(?:fn|struct|enum|trait|impl|mod)\s+\w+", re.M),
    "java": re.compile(
        r"^\s*(?:public\s+|private\s+|protected\s+)*(?:class|interface|enum)\s+\w+", re.M),
}
SYMBOL_RES["typescript"] = SYMBOL_RES["javascript"]


def _symbols(lang: str, text: str) -> list[str]:
    rx = SYMBOL_RES.get(lang)
    if not rx:
        return []
    out = []
    for line in rx.finditer(text):
        words = re.findall(r"[A-Za-z_]\w*", line.group(0))
        skip = {"export", "default", "async", "function", "class", "const", "let", "var",
                "func", "type", "pub", "fn", "struct", "enum", "trait", "impl", "mod",
                "public", "private", "protected", "def"}
        name = next((w for w in words if w not in skip), None)
        if name and name not in out:
            out.append(name)
        if len(out) >= MAX_SYMBOLS:
            break
    return out


def _is_binary(raw: bytes) -> bool:
    return b"\0" in raw[:8192]


def _load_gitignore(root: Path, respect: bool):
    if not (respect and pathspec):
        return None
    lines = []
    for gi in [root / ".gitignore"]:
        if gi.exists():
            lines += gi.read_text(encoding="utf-8", errors="replace").splitlines()
    return pathspec.PathSpec.from_lines("gitignore", lines) if lines else None


def _walk(root: Path, max_files: int, respect_gitignore: bool) -> tuple[list[Path], dict]:
    """os.walk with followlinks=False: pathlib.rglob on Python <=3.12 follows
    directory symlinks with no cycle protection, and file symlinks would pull
    content from outside root into the corpus. Symlinks are skipped entirely."""
    import os

    spec = _load_gitignore(root, respect_gitignore)
    files, stats = [], {"skipped_ignored": 0, "skipped_binary": 0, "skipped_size": 0,
                        "skipped_kind": 0, "skipped_symlink": 0}
    truncated = False
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames
                             if d not in NOISE_DIRS
                             and not (Path(dirpath) / d).is_symlink())
        for fn in sorted(filenames):
            if len(files) >= max_files:
                stats["truncated_at"] = max_files
                truncated = True
                break
            p = Path(dirpath) / fn
            if p.is_symlink():
                stats["skipped_symlink"] += 1
                continue
            rel = p.relative_to(root)
            if spec and spec.match_file(rel.as_posix()):
                stats["skipped_ignored"] += 1
                continue
            if NOISE_FILES.search(fn) or (p.suffix.lower() not in DOC_EXTS
                                          and p.suffix.lower() not in CODE_EXTS):
                stats["skipped_kind"] += 1
                continue
            try:
                if p.stat().st_size > MAX_BYTES:
                    stats["skipped_size"] += 1
                    continue
                if _is_binary(p.read_bytes()):
                    stats["skipped_binary"] += 1
                    continue
            except OSError:
                stats["skipped_kind"] += 1
                continue
            files.append(p)
        if truncated:
            break
    return files, stats


def _ids_for(files: list[Path], root: Path) -> dict[str, str]:
    """relpath(posix) -> doc id. Docs drop the extension (site parity); code keeps
    it mangled (foo_bar.py) so docs/auth.md and code auth.py never collide."""
    ids: dict[str, str] = {}
    used: set[str] = set()
    for p in files:
        rel = p.relative_to(root).as_posix()
        stem = rel if p.suffix.lower() in CODE_EXTS else rel[: -len(p.suffix)]
        pid = re.sub(r"[^A-Za-z0-9_.-]+", "_", stem.replace("/", "_")) or "index"
        if pid in used:
            pid = f"{pid}-{hashlib.sha256(rel.encode()).hexdigest()[:6]}"
        used.add(pid)
        ids[rel] = pid
    return ids


def _resolve_path(target: str, from_dir: str, known: dict[str, str],
                  probes: list[str]) -> str | None:
    """Relative (or root-absolute) path -> relpath key of `known`, with extension
    and index-file probing."""
    from urllib.parse import unquote

    target = unquote(target)
    base = Path(from_dir) / target if not target.startswith("/") else Path(target.lstrip("/"))
    norm = Path(base).as_posix()
    parts = []
    for seg in norm.split("/"):
        if seg == "..":
            if parts:
                parts.pop()
        elif seg not in (".", ""):
            parts.append(seg)
    cand = "/".join(parts)
    for probe in probes:
        if cand + probe in known:
            return cand + probe
    for probe in probes:
        idx = f"{cand}/index{probe}"
        if idx in known:
            return idx
    return None


_DOC_PROBES = ["", ".md", ".markdown", ".mdx", ".txt", ".wiki", ".rst", ".adoc"]
_JS_PROBES = ["", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"]


def _py_targets(mod: str, from_dir: str, known: dict[str, str]) -> list[str]:
    out = []
    bases = [""]
    if mod.startswith("."):
        dots = len(mod) - len(mod.lstrip("."))
        anc = Path(from_dir)
        for _ in range(dots - 1):
            anc = anc.parent
        bases = [anc.as_posix() if anc.as_posix() != "." else ""]
        mod = mod.lstrip(".")
    else:
        bases = ["", from_dir]  # absolute from root, or flat-script sibling
    frag = mod.replace(".", "/") if mod else ""
    for b in bases:
        cands = ([f"{b}/__init__.py".lstrip("/")] if not frag else
                 [f"{b}/{frag}.py".lstrip("/"), f"{b}/{frag}/__init__.py".lstrip("/")])
        for cand in cands:
            if cand in known and cand not in out:
                out.append(cand)
    return out


def _go_module_prefix(root: Path) -> str:
    gomod = root / "go.mod"
    if gomod.exists():
        m = re.search(r"^module\s+(\S+)", gomod.read_text(encoding="utf-8", errors="replace"), re.M)
        if m:
            return m.group(1)
    return ""


def _code_refs(lang: str, text: str, rel: str, known: dict[str, str],
               go_prefix: str) -> list[str]:
    """Import/include targets of one code file, as relpath keys of `known`."""
    from_dir = str(Path(rel).parent)
    from_dir = "" if from_dir == "." else from_dir
    hits: list[str] = []

    def add(r: str | None) -> None:
        if r and r != rel and r not in hits:
            hits.append(r)

    if lang == "python":
        for m in PY_IMPORT.finditer(text):
            if m.group(1) is not None:
                base_mod, names = m.group(1), m.group(2)  # from X import names
                for t in _py_targets(base_mod, from_dir, known):
                    add(t)
                for name in re.split(r"[,\s()]+", names):
                    name = name.split(" as ")[0].strip()
                    if not name or name == "*" or not re.fullmatch(r"\w+", name):
                        continue
                    sep = "" if base_mod.endswith(".") else "."
                    for t in _py_targets(f"{base_mod}{sep}{name}", from_dir, known):
                        add(t)
            elif m.group(3):
                mods = [x.strip().split(" as ")[0].strip() for x in m.group(3).split(",")]
                for mod in mods:
                    if mod and re.fullmatch(r"[\w.]+", mod):
                        for t in _py_targets(mod, from_dir, known):
                            add(t)
    elif lang in ("javascript", "typescript"):
        for m in JS_IMPORT.finditer(text):
            tgt = m.group(1)
            if tgt.startswith(("./", "../")):
                add(_resolve_path(tgt, from_dir, known, _JS_PROBES))
    elif lang in ("c", "c++", "objc"):
        for m in C_INCLUDE.finditer(text):
            add(_resolve_path(m.group(1), from_dir, known, [""])
                or _resolve_path(m.group(1), "", known, [""]))
    elif lang == "go":
        imps = [m.group(1) for m in GO_IMPORT_ONE.finditer(text)]
        for blk in GO_IMPORT_BLOCK.finditer(text):
            imps += GO_QUOTED.findall(blk.group(1))
        for imp in imps:
            rel_dir = (imp[len(go_prefix):].lstrip("/")
                       if go_prefix and imp.startswith(go_prefix) else None)
            if rel_dir is None:
                # no go.mod match: suffix match against known dirs
                dirs = {str(Path(k).parent) for k in known if k.endswith(".go")}
                rel_dir = next((d for d in sorted(dirs) if imp.endswith("/" + d) or imp == d), None)
            if rel_dir is not None:
                pkg_files = sorted(k for k in known
                                   if str(Path(k).parent) == (rel_dir or "."))
                for t in pkg_files[:MAX_PKG_EDGE_TARGETS]:
                    add(t)
    elif lang == "rust":
        for m in RUST_MOD.finditer(text):
            add(_resolve_path(m.group(1), from_dir, known, [".rs"])
                or _resolve_path(f"{m.group(1)}/mod", from_dir, known, [".rs"]))
        src = "src" if any(k.startswith("src/") for k in known) else ""
        for m in RUST_USE_CRATE.finditer(text):
            frag = m.group(1).replace("::", "/")
            add(_resolve_path(frag, src, known, [".rs"])
                or _resolve_path(f"{frag}/mod", src, known, [".rs"]))
    elif lang in ("java", "kotlin"):
        ext = ".java" if lang == "java" else ".kt"
        for m in JVM_IMPORT.finditer(text):
            tail = m.group(1).replace(".", "/") + ext
            cands = sorted(k for k in known if k.endswith(tail))
            add(cands[0] if cands else None)
    elif lang == "ruby":
        for m in RUBY_REQUIRE.finditer(text):
            add(_resolve_path(m.group(1), from_dir, known, ["", ".rb"]))
    return hits


def _doc_title(body: str, fallback: str) -> str:
    m = re.search(r"^#\s+(.+)$", body, re.M)
    return m.group(1).strip() if m else fallback


def build_local_corpus(root: Path, out_dir: Path, max_files: int = 2000,
                       respect_gitignore: bool = True) -> dict:
    """Walk `root`, write corpus docs + typed _edges.json into `out_dir`.
    Returns stats; edge/reference drops are counted, never silently invented."""
    root = root.resolve()
    files, stats = _walk(root, max_files, respect_gitignore)
    known = _ids_for(files, root)
    by_stem: dict[str, list[str]] = {}
    for rel in known:
        p = Path(rel)
        if p.suffix.lower() in DOC_EXTS:
            # Obsidian-style keys: case/underscore/hyphen-insensitive
            key = re.sub(r"[-_\s]+", " ", p.stem.lower()).strip()
            by_stem.setdefault(key, []).append(rel)

    out_dir.mkdir(parents=True, exist_ok=True)
    go_prefix = _go_module_prefix(root)
    edges: dict[tuple[str, str], str] = {}
    stats.update({"docs": 0, "code": 0, "dropped_refs": 0})

    def put(rel: str, title: str, body: str) -> None:
        pid = known[rel]
        chapter = pid.split("_", 1)[0] if "_" in pid else "_root"
        (out_dir / chapter).mkdir(exist_ok=True)
        sha = hashlib.sha256(body.encode()).hexdigest()
        fm = (f"---\ntitle: {title.replace(chr(10), ' ')}\n"
              f"source_url: file://{root}/{rel}\nsha256: {sha}\n---\n\n")
        (out_dir / chapter / f"{pid}.md").write_text(fm + body, encoding="utf-8")

    for p in files:
        rel = p.relative_to(root).as_posix()
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:  # became unreadable between walk and read
            stats["skipped_kind"] += 1
            continue
        ext = p.suffix.lower()
        if ext in DOC_EXTS:
            stats["docs"] += 1
            put(rel, _doc_title(text, Path(rel).stem.replace("_", " ")), text)
            from_dir = str(Path(rel).parent)
            from_dir = "" if from_dir == "." else from_dir
            # link scan skips fenced/inline code: example links in code blocks
            # are illustrations, not references
            prose = INLINE.sub(" ", FENCE.sub(" ", text))
            for m in MD_LINK.finditer(prose):
                tgt = m.group(1)
                if re.match(r"^[a-z]+://|^mailto:", tgt, re.I):
                    continue
                r = _resolve_path(tgt, from_dir, known, _DOC_PROBES)
                if r and known[r] != known[rel]:
                    edges.setdefault((known[rel], known[r]), "ref")
                elif not r:
                    stats["dropped_refs"] += 1
            for m in WIKI_LINK.finditer(prose):
                key = re.sub(r"[-_\s]+", " ", Path(m.group(1).strip()).stem.lower()).strip()
                cands = by_stem.get(key, [])
                if cands:
                    if len(cands) > 1:
                        stats["ambiguous_refs"] = stats.get("ambiguous_refs", 0) + 1
                    r = sorted(cands)[0]
                    if known[r] != known[rel]:
                        edges.setdefault((known[rel], known[r]), "ref")
                else:
                    stats["dropped_refs"] += 1
        else:
            stats["code"] += 1
            lang = CODE_EXTS[ext]
            syms = _symbols(lang, text)
            header = (f"path: {rel}\nlanguage: {lang}\n"
                      f"symbols: {', '.join(syms) if syms else '(none detected)'}\n\n")
            put(rel, rel, header + text)
            for tgt in _code_refs(lang, text, rel, known, go_prefix):
                edges.setdefault((known[rel], known[tgt]), "imports")

    # README/index files are hubs: contains edges to their directory siblings
    for rel, pid in known.items():
        if Path(rel).stem.lower() in ("readme", "index"):
            d = Path(rel).parent
            members = [known[r] for r in known
                       if Path(r).parent == d and r != rel]
            for m_pid in sorted(members)[:200]:
                edges.setdefault((pid, m_pid), "contains")

    edge_list = [{"s": s, "t": t, "k": k} for (s, t), k in sorted(edges.items())]
    (out_dir / "_edges.json").write_text(
        json.dumps(edge_list, indent=0, sort_keys=True))
    stats["files"] = len(files)
    return stats
