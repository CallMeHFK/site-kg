"""Multi-dimensional relation construction with per-node dimension selection.

Upstream site-kg only knows three edge kinds -- `ref` (markdown/wikilink),
`imports` (code include/import) and `contains` (README/index hub) -- so most of
a corpus sits unconnected unless the source happens to be densely
cross-linked. This module adds derived dimensions (symbol references, lexical
similarity, directory cohorts, version lineage, optional LLM entity and
embedding similarity) and, for every node, decides which of them apply to
*that* node instead of running every builder everywhere: a 40-byte stub gets
no lexical neighbours, a release page gets version lineage, a source file gets
symbol references.

Why per-node selection and not a fixed extract-everything pass: see
docs/RELATIONS.md for the literature. In short -- schema-free relation typing
(AutoSchemaKG arXiv:2505.23628), entity clustering to keep an induced schema
from exploding (KGGen arXiv:2502.09956), and cheap per-item routing instead of
one-size-fits-all extraction (Adaptive-RAG arXiv:2403.14403) are all separately
validated; unbounded LLM-invented relation types are not affordable (ATLAS cost
78.4k GPU-hours and reached 31M concepts on Common Crawl), so the registry here
is bounded and the LLM is only allowed to *choose*, never to mint edge kinds.

Ground rules inherited from the rest of site-kg:
  * every edge is derived from the corpus, never invented;
  * derived edges only enrich a corpus that already has structural evidence
    (ref/imports > 0). A corpus with zero cross-references stays NOT-READY with
    zero edges rather than gaining a decorative hairball;
  * no LLM or embedding model is required. Every keyless dimension works
    offline; the LLM/embedding dimensions fail open to "skipped".
"""
from __future__ import annotations

import math
import os
import re
from collections import Counter, defaultdict
from pathlib import PurePosixPath

from .graph import STOP, TOKEN, _prose

EVIDENCE_KINDS = ("ref", "imports", "contains")
KEYLESS_KINDS = ("mentions_symbol", "similar_to", "sibling_of", "supersedes")
MODEL_KINDS = ("shares_entity", "semantic_similar")

DIMENSIONS = {
    "ref": "markdown link / wikilink, resolved against the corpus",
    "imports": "code import / include / require, resolved against the corpus",
    "contains": "README or index hub listing its directory members",
    "mentions_symbol": "code file mentions an identifier defined in another file",
    "similar_to": "tf-idf cosine over prose: same topic, no explicit link",
    "sibling_of": "same directory cohort (package or URL folder)",
    "supersedes": "version lineage: 2.0 supersedes 1.4 of the same page family",
    "shares_entity": "pages share >= 2 LLM-extracted named entities",
    "semantic_similar": "embedding cosine over title + body head",
}

# ---- tunables -------------------------------------------------------------
MIN_PROSE = 220           # shorter bodies have no lexical signature to compare
MIN_PROSE_LLM = 400       # entity extraction on a stub is wasted tokens
SIG_TERMS = 15            # signature terms per doc for sparse cosine
DF_MIN, DF_MAX = 2, 25    # a term in 1 doc matches nothing; in >25 it is a stopword
SIM_THRESHOLD = 0.20
SIM_CAP = 5
SIBLING_MIN, SIBLING_MAX = 1, 12
SYMBOL_CAP = 5
SYMBOL_MIN_LEN = 4
ENTITY_CAP = 5
EMBED_CAP = 5
EMBED_THRESHOLD = 0.70
MAX_HEAVY_DOCS = 8000     # above this, only cheap dimensions run (kernel trees)
MAX_SYMBOL_DOCS = 4000    # code files scanned for cross-file symbol references
MAX_ENTITY_PAGES = 120    # LLM entity-extraction budget per ingest
MAX_EMBED_PAGES = 300     # embedding budget per ingest
LLM_BATCH = 8

CODE_HEADER = re.compile(
    r"^path:\s*(\S*)\s*\nlanguage:\s*(\S*)\s*\nsymbols:\s*(.*?)\s*\n", re.M)
# 'v1' after a separator counts as a version; 'conv1' must not
VER_TAGGED = re.compile(r"(?<![0-9A-Za-z])(?:v|ver|version|rev)(\d+)(?![0-9A-Za-z_.])", re.I)
VER_STRICT = re.compile(r"(?<![0-9A-Za-z_.])(\d+(?:\.\d+){1,3})(?![0-9A-Za-z_.])")
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
SYMBOL_STOP = {
    "self", "this", "main", "init", "name", "args", "kwargs", "value", "values",
    "item", "items", "data", "result", "results", "text", "path", "type", "types",
    "ret", "true", "false", "none", "null", "error", "errors", "test", "tests",
    "config", "default", "return", "class", "def", "func", "function", "import",
    "from", "elif", "else", "then", "print", "format", "index", "count", "size",
    "length", "list", "dict", "tuple", "json", "args_", "kwargs_", "setup",
    "build", "make", "create", "update", "delete", "write", "read", "open",
}
OFF_MODES = {"off", "none", "evidence", "0", "false"}


def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(lo, min(hi, int(raw)))
    except ValueError:
        return default


def resolve_mode(mode: str | None) -> tuple[str, set[str], bool]:
    """(mode, enabled derived kinds, use_llm_router). Anything outside
    auto/llm/all/off parses as a comma list of dimension names; names that
    match nothing leave every derived dimension disabled."""
    m = (mode or os.environ.get("SITE_KG_RELATIONS", "auto") or "auto").strip().lower()
    if m in OFF_MODES:
        return "off", set(), False
    if m == "all":
        return "all", set(KEYLESS_KINDS) | set(MODEL_KINDS), False
    if m == "llm":
        return "llm", set(KEYLESS_KINDS) | set(MODEL_KINDS), True
    if m == "auto":
        return "auto", set(KEYLESS_KINDS), False
    picked = {p.strip() for p in m.split(",") if p.strip()}
    known = set(KEYLESS_KINDS) | set(MODEL_KINDS)
    picked &= known
    return m, picked, False


def _code_header(body: str):
    """Match the code-doc header. `search`, not `match`: the corpus writer leaves
    a blank line between the frontmatter and the body."""
    return CODE_HEADER.search(body[:300])


def _dir_key(doc: dict) -> str | None:
    """Directory cohort: source path for code, URL parent folder for pages."""
    m = _code_header(doc.get("body") or "")
    if m and m.group(1):
        return str(PurePosixPath(m.group(1)).parent) or "."
    u = doc.get("url") or ""
    if u.startswith("http"):
        return u.rsplit("/", 1)[0] or None
    return None


def _version(doc: dict) -> tuple[int, ...] | None:
    """Version of a page family, read from id or URL only -- never from prose,
    where numbers like 'Python 3.11' or 'chapter 2' are not lineage."""
    for src in (doc.get("id", ""), doc.get("url", "")):
        m = VER_TAGGED.search(src) or VER_STRICT.search(src)
        if m:
            raw = m.group(1)
            try:
                return tuple(int(x) for x in raw.split("."))
            except ValueError:
                return None
    return None


def _base_key(doc: dict) -> str:
    src = doc.get("id", "") or doc.get("url", "")
    stripped = VER_TAGGED.sub("", src)
    stripped = VER_STRICT.sub("", stripped)
    return re.sub(r"[^a-z0-9]+", "", stripped.lower())


def profile_of(doc: dict) -> dict:
    """Cheap per-node signals: the input to dimension selection."""
    body = doc.get("body") or ""
    prose = _prose(body)
    m = _code_header(body)
    syms: list[str] = []
    if m:
        syms = [s.strip() for s in m.group(3).split(",")
                if s.strip() and s != "(none detected)"]
    return {
        "id": doc["id"],
        "title": doc.get("title", ""),
        "url": doc.get("url", ""),
        "body": body,
        "prose": prose,
        "prose_len": len(prose.strip()),
        "is_code": m is not None,
        "lang": m.group(2) if m else "",
        "symbols": syms,
        "dir": _dir_key(doc),
        "version": _version(doc),
        "base": _base_key(doc),
        "digest": re.sub(r"\s+", " ", prose).strip()[:200],
    }


def select_dimensions(p: dict, *, enabled: set[str], heavy_ok: bool) -> list[str]:
    """Dimensions that apply to THIS node, from its own content.

    This is the whole point of the feature: the same corpus yields different
    dimension sets per node. A stub is not compared lexically, a prose page gets
    no symbol edges, a release note gets version lineage.
    """
    out: list[str] = []
    if heavy_ok and "mentions_symbol" in enabled and p["is_code"]:
        out.append("mentions_symbol")
    if heavy_ok and "similar_to" in enabled and p["prose_len"] >= MIN_PROSE:
        out.append("similar_to")
    if "sibling_of" in enabled and SIBLING_MIN <= _siblings_of(p) <= SIBLING_MAX:
        out.append("sibling_of")
    if "supersedes" in enabled and p["version"] and len(p["base"]) >= 4:
        out.append("supersedes")
    if "shares_entity" in enabled and p["prose_len"] >= MIN_PROSE_LLM:
        out.append("shares_entity")
    if "semantic_similar" in enabled and p["prose_len"] >= MIN_PROSE:
        out.append("semantic_similar")
    return out


def _siblings_of(p: dict) -> int:
    return int(p.get("_n_siblings", 0))


def _index_groups(profiles: list[dict]) -> None:
    """Fill per-profile context the selector needs: directory cohort size."""
    dirs: Counter = Counter()
    for p in profiles:
        if p["dir"]:
            dirs[p["dir"]] += 1
    for p in profiles:
        p["_n_siblings"] = dirs[p["dir"]] - 1 if p["dir"] else 0


# ---- dimension builders ---------------------------------------------------
def _symbol_edges(profiles: list[dict]) -> list[tuple[str, str, str]]:
    """Cross-file symbol references: identifiers defined in one file, used in another."""
    code = [p for p in profiles if p["is_code"]]
    if len(code) > MAX_SYMBOL_DOCS:
        return []
    owners: dict[str, set[str]] = defaultdict(set)
    for p in code:
        for s in p["symbols"]:
            if len(s) >= SYMBOL_MIN_LEN and s.lower() not in SYMBOL_STOP:
                owners[s].add(p["id"])
    # a symbol defined in several files carries no usable direction
    index = {s: next(iter(o)) for s, o in owners.items() if len(o) == 1}
    out: list[tuple[str, str, str]] = []
    for p in code:
        own = set(p["symbols"])
        hits: Counter = Counter()
        for tok in IDENT.findall(p["body"]):
            owner = index.get(tok)
            if owner and owner != p["id"] and tok not in own:
                hits[tok] += 1
        for sym, _n in hits.most_common(SYMBOL_CAP):
            out.append((p["id"], index[sym], "mentions_symbol"))
    return out


def _similar_edges(profiles: list[dict]) -> list[tuple[str, str, str]]:
    """Sparse tf-idf cosine between prose signatures (no O(n^2) dense pass)."""
    cands = [p for p in profiles if p["prose_len"] >= MIN_PROSE]
    n = len(cands)
    if n < 2:
        return []
    tf: list[Counter] = []
    df: Counter = Counter()
    for p in cands:
        c: Counter = Counter()
        for tok in TOKEN.findall(p["prose"][:4000].lower()):
            if tok in STOP or tok.isdigit():
                continue
            c[tok] += 1
        tf.append(c)
        df.update(c.keys())
    idf = {t: math.log(1 + n / (1 + d)) for t, d in df.items()}
    sig: dict[str, list[tuple[float, str]]] = {}
    norm: dict[str, float] = {}
    for p, c in zip(cands, tf):
        ranked = sorted(((v * idf[t], t) for t, v in c.items()
                         if DF_MIN <= df[t] <= DF_MAX), reverse=True)[:SIG_TERMS]
        sig[p["id"]] = ranked
        norm[p["id"]] = math.sqrt(sum(w * w for w, _ in ranked)) or 1.0
    postings: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for p in cands:
        for w, t in sig[p["id"]]:
            postings[t].append((p["id"], w))
    acc: dict[tuple[str, str], float] = defaultdict(float)
    for plist in postings.values():
        for i in range(len(plist)):
            a, wa = plist[i]
            for j in range(i + 1, len(plist)):
                b, wb = plist[j]
                acc[(a, b) if a < b else (b, a)] += wa * wb
    neighbours: dict[str, list[tuple[float, str]]] = defaultdict(list)
    for (a, b), dot in acc.items():
        cos = dot / (norm[a] * norm[b])
        if cos < SIM_THRESHOLD:
            continue
        neighbours[a].append((cos, b))
        neighbours[b].append((cos, a))
    out: list[tuple[str, str, str]] = []
    for a, lst in neighbours.items():
        for _cos, b in sorted(lst, key=lambda kv: (-kv[0], kv[1]))[:SIM_CAP]:
            if a < b:
                out.append((a, b, "similar_to"))
    return out


def _sibling_edges(profiles: list[dict]) -> list[tuple[str, str, str]]:
    groups: dict[str, list[str]] = defaultdict(list)
    for p in profiles:
        if p["dir"]:
            groups[p["dir"]].append(p["id"])
    out: list[tuple[str, str, str]] = []
    for members in groups.values():
        if not SIBLING_MIN <= len(members) - 1 <= SIBLING_MAX:
            continue
        for i, a in enumerate(sorted(members)):
            for b in sorted(members)[i + 1:]:
                out.append((a, b, "sibling_of"))
    return out


def _supersedes_edges(profiles: list[dict]) -> list[tuple[str, str, str]]:
    fams: dict[str, list[dict]] = defaultdict(list)
    for p in profiles:
        if p["version"] and len(p["base"]) >= 4:
            fams[p["base"]].append(p)
    out: list[tuple[str, str, str]] = []
    for members in fams.values():
        if len(members) < 2:
            continue
        ordered = sorted(members, key=lambda p: p["version"])
        for newer, older in zip(ordered[1:], ordered[:-1]):
            out.append((newer["id"], older["id"], "supersedes"))
    return out


def _entity_edges(profiles: list[dict], notes: list[str]) -> list[tuple[str, str, str]]:
    """LLM-extracted entities shared by two pages. Fails open: no key, no edges."""
    cands = [p for p in profiles if p["prose_len"] >= MIN_PROSE_LLM]
    if len(cands) < 2:
        return []
    budget = _env_int("SITE_KG_RELATION_ENTITY_PAGES", MAX_ENTITY_PAGES, 2, 2000)
    if len(cands) > budget:  # even sample, deterministic
        step = len(cands) / budget
        cands = [cands[int(i * step)] for i in range(budget)]
    try:
        from . import semantic
        models = semantic.discover_models()
        chat_model = models["chat"]
        if not chat_model:
            notes.append("shares_entity skipped: no chat model on the gateway")
            return []
    except Exception as e:  # config missing, gateway down -> not fatal
        notes.append(f"shares_entity skipped: {type(e).__name__}")
        return []

    entities: dict[str, set[str]] = defaultdict(set)
    for i in range(0, len(cands), LLM_BATCH):
        batch = cands[i:i + LLM_BATCH]
        prompt = "\n".join(f"[{p['id']}] {p['title']}: {p['digest']}" for p in batch)
        try:
            raw = semantic.chat([
                {"role": "system", "content":
                 "Extract up to 6 named entities (API names, components, concepts) per page. "
                 "Reply with JSON only: an array of {\"id\": <page id>, \"entities\": [...]}. "
                 "Use only names that appear in the text."},
                {"role": "user", "content": prompt},
            ], chat_model, max_tokens=1024)
        except Exception as e:
            notes.append(f"shares_entity batch failed: {type(e).__name__}: {e}")
            continue
        for pid, ents in _parse_entity_reply(raw, {p["id"] for p in batch}):
            for e in ents:
                entities[_norm_entity(e)].add(pid)

    ceiling = max(2, int(len(cands) * 0.3))
    shared: dict[tuple[str, str], int] = defaultdict(int)
    for owners in entities.values():
        if not 2 <= len(owners) <= ceiling:
            continue
        ids = sorted(owners)
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                shared[(a, b)] += 1
    by_node: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for (a, b), n in shared.items():
        if n < 2:
            continue
        by_node[a].append((n, b))
        by_node[b].append((n, a))
    out = []
    for a, lst in by_node.items():
        for _n, b in sorted(lst, key=lambda kv: (-kv[0], kv[1]))[:ENTITY_CAP]:
            if a < b:
                out.append((a, b, "shares_entity"))
    return out


def _parse_entity_reply(raw: str, valid: set[str]) -> list[tuple[str, list[str]]]:
    import json

    start, end = raw.find("["), raw.rfind("]")
    if start < 0 or end <= start:
        return []
    try:
        data = json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        return []
    out = []
    for row in data:
        if not isinstance(row, dict):
            continue
        pid, ents = row.get("id"), row.get("entities")
        if pid in valid and isinstance(ents, list):
            out.append((pid, [str(e) for e in ents if str(e).strip()][:6]))
    return out


def _norm_entity(e: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(e).lower()).strip()


def _embed_edges(profiles: list[dict], notes: list[str]) -> list[tuple[str, str, str]]:
    """Precomputed embedding neighbours, materialised as graph edges."""
    cands = [p for p in profiles if p["prose_len"] >= MIN_PROSE]
    budget = _env_int("SITE_KG_RELATION_EMBED_PAGES", MAX_EMBED_PAGES, 2, 5000)
    if len(cands) > budget:
        step = len(cands) / budget
        cands = [cands[int(i * step)] for i in range(budget)]
    if len(cands) < 2:
        return []
    try:
        from . import semantic
        model = semantic.discover_models()["embed"]
        if not model:
            notes.append("semantic_similar skipped: no embedding model on the gateway")
            return []
        texts = [f"{p['title']}\n{p['body'][:600]}" for p in cands]
        vecs = semantic.embed(texts, model)
    except Exception as e:
        notes.append(f"semantic_similar skipped: {type(e).__name__}")
        return []
    out = []
    for i, p in enumerate(cands):
        scored = sorted(((semantic.cosine(vecs[i], vecs[j]), cands[j]["id"])
                         for j in range(len(cands)) if j != i),
                        key=lambda kv: (-kv[0], kv[1]))[:EMBED_CAP]
        for cos, other in scored:
            if cos >= EMBED_THRESHOLD and p["id"] < other:
                out.append((p["id"], other, "semantic_similar"))
    return out


# ---- LLM router -----------------------------------------------------------
def route_with_llm(profiles: list[dict], enabled: set[str],
                   notes: list[str]) -> dict[str, list[str]]:
    """Ask the LLM which registry dimensions apply to each node.

    The LLM chooses from a bounded registry -- it never invents an edge kind --
    and only sees title + digest + the cheap profile flags, not whole bodies.
    Any failure returns {} and the caller keeps the keyless selection.
    """
    budget = _env_int("SITE_KG_RELATION_ROUTE_PAGES", MAX_ENTITY_PAGES, 2, 2000)
    targets = [p for p in profiles if p["prose_len"] >= MIN_PROSE]
    if len(targets) > budget:
        step = len(targets) / budget
        targets = [targets[int(i * step)] for i in range(budget)]
    try:
        from . import semantic
        chat_model = semantic.discover_models()["chat"]
        if not chat_model:
            notes.append("llm router skipped: no chat model on the gateway")
            return {}
    except Exception as e:
        notes.append(f"llm router skipped: {type(e).__name__}")
        return {}
    menu = "\n".join(f"- {k}: {DIMENSIONS[k]}" for k in sorted(enabled))
    picks: dict[str, list[str]] = {}
    for i in range(0, len(targets), LLM_BATCH):
        batch = targets[i:i + LLM_BATCH]
        lines = []
        for p in batch:
            flags = ("code" if p["is_code"] else "prose",
                     f"chars={p['prose_len']}",
                     f"version={p['version'][0] if p['version'] else '-'}")
            lines.append(f"[{p['id']}] {p['title']} ({', '.join(flags)}): {p['digest']}")
        try:
            raw = semantic.chat([
                {"role": "system", "content":
                 "For each page pick the relation dimensions that genuinely apply to it. "
                 "Reply with JSON only: an array of {\"id\": <page id>, \"dims\": [...]}. "
                 "Available dimensions:\n" + menu},
                {"role": "user", "content": "\n".join(lines)},
            ], chat_model, max_tokens=1024)
        except Exception as e:
            notes.append(f"llm router batch failed: {type(e).__name__}: {e}")
            continue
        start, end = raw.find("["), raw.rfind("]")
        if start < 0 or end <= start:
            continue
        import json
        try:
            rows = json.loads(raw[start:end + 1])
        except json.JSONDecodeError:
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            pid, dims = row.get("id"), row.get("dims")
            if isinstance(pid, str) and isinstance(dims, list):
                picks[pid] = [d for d in dims if d in enabled]
    return picks


# ---- entry point ----------------------------------------------------------
def build_relations(docs: list[dict], mode: str | None = None,
                    have: set[tuple[str, str]] | None = None) -> tuple[list[dict], dict]:
    """Derived edges for `docs`, plus a per-dimension report.

    `have` is the set of evidence edge pairs already resolved upstream: a pair
    that the corpus already links is never re-labelled with a derived kind.
    """
    resolved, enabled, use_llm = resolve_mode(mode)
    notes: list[str] = []
    if resolved == "off" or not enabled:
        return [], {"relationMode": resolved, "dimensions": {},
                    "relationNotes": ["derived dimensions disabled"]}
    profiles = [profile_of(d) for d in docs]
    _index_groups(profiles)
    heavy_ok = len(profiles) <= MAX_HEAVY_DOCS
    if not heavy_ok:
        notes.append(
            f"mentions_symbol/similar_to skipped: {len(profiles)} docs over the "
            f"{MAX_HEAVY_DOCS}-doc budget for quadratic passes")

    picks: dict[str, list[str]] = {}
    if use_llm:
        picks = route_with_llm(profiles, enabled, notes)
    chosen: dict[str, list[str]] = {}
    for p in profiles:
        if p["id"] in picks:
            dims = picks[p["id"]]  # the router's narrowing stands, even to zero
        else:
            dims = select_dimensions(p, enabled=enabled, heavy_ok=heavy_ok)
        # the router may pick a dimension the content cannot support; re-gate it
        chosen[p["id"]] = [d for d in dims
                           if d in select_dimensions(p, enabled=enabled, heavy_ok=heavy_ok)]

    seen = have or set()
    raw: list[tuple[str, str, str]] = []
    if any("mentions_symbol" in v for v in chosen.values()):
        raw += _symbol_edges(profiles)
    if any("similar_to" in v for v in chosen.values()):
        raw += _similar_edges(profiles)
    if any("sibling_of" in v for v in chosen.values()):
        raw += _sibling_edges(profiles)
    if any("supersedes" in v for v in chosen.values()):
        raw += _supersedes_edges(profiles)
    if any("shares_entity" in v for v in chosen.values()):
        raw += _entity_edges(profiles, notes)
    if any("semantic_similar" in v for v in chosen.values()):
        raw += _embed_edges(profiles, notes)

    by_node: dict[str, set[str]] = defaultdict(set)
    edges: list[dict] = []
    for a, b, k in raw:
        if a == b or (a, b) in seen or (b, a) in seen:
            continue
        pair = (a, b)
        if k in ("similar_to", "sibling_of", "shares_entity", "semantic_similar"):
            pair = (a, b) if a < b else (b, a)
        if pair in seen:
            continue
        if k not in chosen.get(a, []) and k not in chosen.get(b, []):
            continue  # the node's own selection did not admit this dimension
        edges.append({"s": pair[0], "t": pair[1], "k": k})
        by_node[k].add(pair[0])
        by_node[k].add(pair[1])
        seen.add(pair)

    counts: Counter = Counter(e["k"] for e in edges)
    dimensions = {k: {"kind": "derived", "edges": n, "nodes": len(by_node[k])}
                  for k, n in sorted(counts.items())}
    return edges, {"relationMode": resolved, "dimensions": dimensions,
                   "relationNotes": notes}
