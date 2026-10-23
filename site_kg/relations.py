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
  * content-derived dimensions are per-node gated and fan-out capped, so they
    may run even when the corpus has zero cross-references (ref/imports == 0):
    a pile of files with comparable content gains real signal instead of an
    empty graph, while the NOT-READY verdict still says "no link structure".
    A stub corpus with no shared evidence of any kind (no prose, headings,
    cohorts, versions, curated terms, or content hash) still ends up with
    zero edges, never a decorative hairball; the one exception is identical
    content, where the shared sha256 is itself the evidence;
  * corpus-level dimensions (duplicate_of from frontmatter sha256,
    coupled_with/co_cited_with from the evidence graph's own neighbourhoods)
    skip per-node gating because their evidence is graph- or hash-level, not
    per-node content;
  * no LLM or embedding model is required. Every keyless dimension works
    offline; the LLM/embedding dimensions fail open to "skipped".

Literature behind the keyless dimensions (see docs/RELATIONS.md for the full
map): bibliographic coupling / co-citation (Kessler 1963, Small 1973) with a
resource-allocation weight (Zhou-Lü-Zhang 2009) for the neighbourhood
dimensions; keyword-salience features (YAKE, Campos et al. 2020; entity
salience, Dunietz & Gillick 2014) for term and symbol weighting; SimHash
banded blocking (Charikar 2002, Manku et al. 2007) as the model for the
sha256 duplicate tier; relative df windows to keep the tf-idf pass
scale-robust (the search index's DF_RATIO_CAP semantics).
"""
from __future__ import annotations

import math
import os
import re
from collections import Counter, defaultdict
from itertools import pairwise
from pathlib import PurePosixPath

from .graph import FENCE, STOP, TOKEN, _prose

EVIDENCE_KINDS = ("ref", "imports", "contains")
KEYLESS_KINDS = ("mentions_symbol", "similar_to", "sibling_of", "supersedes",
                 "shares_heading", "shares_term", "duplicate_of",
                 "coupled_with", "co_cited_with")
MODEL_KINDS = ("shares_entity", "semantic_similar")
# Corpus-level dimensions: their evidence is a whole-corpus hash or the
# evidence graph's neighbourhood structure, not per-node content, so the
# per-node selector does not gate them.
ALWAYS_ON_KINDS = ("duplicate_of", "coupled_with", "co_cited_with")

DIMENSIONS = {
    "ref": "markdown link / wikilink, resolved against the corpus",
    "imports": "code import / include / require, resolved against the corpus",
    "contains": "README or index hub listing its directory members",
    "mentions_symbol": "code file mentions an identifier defined in another file",
    "similar_to": "tf-idf cosine over prose: same topic, no explicit link",
    "sibling_of": "same directory cohort (package or URL folder)",
    "supersedes": "version lineage: 2.0 supersedes 1.4 of the same page family",
    "shares_heading": "pages share a document skeleton: >=35% Jaccard overlap of heading sets",
    "shares_term": "pages share >= 2 salient curated terms (inline code, headings, symbols)",
    "duplicate_of": "identical content: same frontmatter sha256",
    "coupled_with": "bibliographic coupling: both pages cite the same targets",
    "co_cited_with": "co-citation: the same pages cite both of these",
    "shares_entity": "pages share >= 2 LLM-extracted named entities",
    "semantic_similar": "embedding cosine over title + body head",
}

# ---- tunables -------------------------------------------------------------
MIN_PROSE = 220           # shorter bodies have no lexical signature to compare
MIN_PROSE_LLM = 400       # entity extraction on a stub is wasted tokens
SIG_TERMS = 15            # signature terms per doc for sparse cosine
DF_MIN = 2                # a term in 1 doc matches nothing
DF_RATIO_MAX = 0.35       # df ceiling as a corpus fraction (matches graph.py
                          # search-index semantics: beyond this it is boilerplate).
                          # Pure ratio, no absolute cap: POSTING_K truncation
                          # bounds the pair pass, so a topical term at 20% of a
                          # 20k corpus stays in play instead of being silently
                          # dropped by a stale absolute ceiling.
POSTING_K = 64            # per-term posting truncation, weight-ordered: bounds
                          # the pair pass at K*(K-1)/2 per term once DF_MAX
                          # stops doing that job implicitly
SIM_THRESHOLD = 0.20
SIM_CAP = 5
SIBLING_MIN, SIBLING_MAX = 1, 12
SYMBOL_CAP = 5
SYMBOL_MIN_LEN = 4
SYMBOL_TITLE_BONUS = 2    # symbol named in the title is load-bearing, not incidental
ENTITY_CAP = 5
EMBED_CAP = 5
EMBED_THRESHOLD = 0.70
HEAD_MIN = 3              # a page needs >=3 headings for a comparable skeleton
HEAD_OVERLAP = 0.35       # heading-set Jaccard threshold
HEAD_CAP = 5              # per-nominator heading neighbours (same shape as SIM_CAP)
HEAD_DF_FLOOR = 8         # relative boilerplate window for headings (see _df_ceiling)
HEAD_POST_K = 64          # per-heading posting truncation: bounds the pair pass
                          # under the pure-ratio df window (deterministic: id order)
TERM_MIN = 2              # shared curated terms required for a shares_term edge
TERM_SIG = 10             # signature terms per doc for the term dimension
TERM_CAP = 5              # per-nominator term neighbours
TERM_POST_K = 40          # per-term posting truncation for the term dimension
TERM_MIN_W = 1.0          # minimum summed idf over the shared terms (only bites
                          # when idf itself collapses, i.e. df ~ n)
HUB_OUT_MAX = 24          # a page linking to more than this is a ToC: its
                          # out-neighbourhood carries no coupling signal
GROUP_MAX = 40            # deterministic truncation of one shared neighbour's
                          # co-linker group (<=780 pairs per neighbour)
COUPLE_CAP, COUPLE_MIN = 5, 0.25  # RA-weighted overlap threshold + fan-out
MAX_HEAVY_DOCS = 8000     # above this, only cheap dimensions run (kernel trees)
MAX_SYMBOL_DOCS = 4000    # code files scanned for cross-file symbol references
MAX_ENTITY_PAGES = 120    # LLM entity-extraction budget per ingest
MAX_EMBED_PAGES = 300     # embedding budget per ingest
LLM_BATCH = 8

CODE_HEADER = re.compile(
    r"^path:\s*(\S*)\s*\nlanguage:\s*(\S*)\s*\nsymbols:\s*(.*?)\s*\n", re.M)
# 'v1' after a separator counts as a version; 'conv1' must not. The dotted
# tail makes 'v2.0'/'version2.0'/'rev1.2' parse (and strip from base keys),
# while the trailing-dot exclusion keeps 'file-v2.txt' unversioned.
VER_TAGGED = re.compile(
    r"(?<![0-9A-Za-z])(?:v|ver|version|rev)(\d+(?:\.\d+){0,3})(?![0-9A-Za-z_.])", re.I)
VER_STRICT = re.compile(r"(?<![0-9A-Za-z_.])(\d+(?:\.\d+){1,3})(?![0-9A-Za-z_.])")
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
INLINE_CODE = re.compile(r"`([^`\n]{2,60})`")
HEADING_RE = re.compile(r"^(#{1,4})\s+(.+?)\s*#*\s*$", re.M)


def _df_ceiling(n: int, floor: int) -> int:
    """Relative df ceiling: a term held by a fixed *fraction* of the corpus is
    boilerplate, whatever the corpus size. The old absolute caps (df>25 => gone)
    amputated genuinely topical terms exactly where the corpus was rich, and the
    floor keeps tiny corpora from treating every recurring word as signal."""
    return max(floor, int(n * DF_RATIO_MAX))
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
    """Directory cohort: source path for code, URL parent folder for pages.
    `file://` source URLs (local corpora written by the local scanner) count
    too: without this the whole local-prose class silently got no cohorts."""
    m = _code_header(doc.get("body") or "")
    if m and m.group(1):
        return str(PurePosixPath(m.group(1)).parent) or "."
    u = doc.get("url") or ""
    if u.startswith("http") or u.startswith("file://"):
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


def _headings_of(body: str) -> list[str]:
    """Normalized markdown heading texts (h1-h4), fences excluded. The corpus
    writer keeps headings as markdown, so this works for crawled pages and
    local docs alike."""
    body = FENCE.sub(" ", body)
    out: list[str] = []
    for m in HEADING_RE.finditer(body):
        t = re.sub(r"[^a-z0-9]+", " ", m.group(2).lower()).strip()
        if len(t) >= 3 and t not in out:
            out.append(t)
    return out


def _terms_of(body: str, headings: list[str], symbols: list[str]) -> list[tuple[str, int]]:
    """Curated term inventory with local salience. Inline code spans are the
    writer's own terminology marking (weight 3), headings delimit document
    structure (2), declared symbols unify the code+prose vocabulary (2).
    Fenced code is excluded first so block content cannot masquerade as a
    curated span. Deterministic: sorted by (-weight, term)."""
    fenced = FENCE.sub(" ", body)
    counts: Counter = Counter()
    for m in INLINE_CODE.findall(fenced):
        t = re.sub(r"[^a-z0-9]+", " ", m.lower()).strip()
        if 2 <= len(t) <= 60:
            counts[t] += 3
    for h in headings:
        counts[h] += 2
    for s in symbols:
        t = re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()
        if len(t) >= 2:
            counts[t] += 2
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def profile_of(doc: dict) -> dict:
    """Cheap per-node signals: the input to dimension selection."""
    body = doc.get("body") or ""
    prose = _prose(body)
    m = _code_header(body)
    syms: list[str] = []
    if m:
        syms = [s.strip() for s in m.group(3).split(",")
                if s.strip() and s != "(none detected)"]
    headings = _headings_of(body)
    return {
        "id": doc["id"],
        "title": doc.get("title", ""),
        "url": doc.get("url", ""),
        "sha": doc.get("sha", ""),
        "body": body,
        "prose": prose,
        "prose_len": len(prose.strip()),
        "is_code": m is not None,
        "lang": m.group(2) if m else "",
        "symbols": syms,
        "headings": headings,
        "terms": _terms_of(body, headings, syms),
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
    if "shares_heading" in enabled and len(p["headings"]) >= HEAD_MIN:
        out.append("shares_heading")
    if "shares_term" in enabled and len(p["terms"]) >= TERM_MIN:
        out.append("shares_term")
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
def _symbol_edges(profiles: list[dict], notes: list[str]) -> list[tuple[str, str, str]]:
    """Cross-file symbol references: identifiers defined in one file, used in
    another. Ranked by mention count plus a title bonus (a symbol the page is
    *about* beats one pasted into a helper), with a (-score, symbol) tie-break:
    Counter.most_common would fall back to scan order, so a permuted doc list
    could permute which symbols make the cap."""
    code = [p for p in profiles if p["is_code"]]
    if len(code) > MAX_SYMBOL_DOCS:
        # the selector gates on total corpus size, the pass itself on *code*
        # docs: between the two budgets the dimension ran and stayed silent
        notes.append(f"mentions_symbol skipped: {len(code)} code docs over the "
                     f"{MAX_SYMBOL_DOCS}-doc budget for the symbol pass")
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
        title = p["title"].lower()
        hits: Counter = Counter()
        for tok in IDENT.findall(p["body"]):
            owner = index.get(tok)
            if owner and owner != p["id"] and tok not in own:
                hits[tok] += 1
        scored = sorted(((n + (SYMBOL_TITLE_BONUS if tok.lower() in title else 0), tok)
                         for tok, n in hits.items()), key=lambda kv: (-kv[0], kv[1]))
        for _score, sym in scored[:SYMBOL_CAP]:
            out.append((p["id"], index[sym], "mentions_symbol"))
    return out


def _similar_edges(profiles: list[dict]) -> list[tuple[str, str, str]]:
    """Sparse tf-idf cosine between prose signatures (no O(n^2) dense pass).

    Scale-robust by construction: the df ceiling is a corpus *fraction*
    (a term in 30 of 100 docs can be topical; in 30 of 28 it is boilerplate),
    title tokens join the signature (the most discriminative strings on a
    page), and each posting list is truncated to the top POSTING_K weights so
    the pair pass stays bounded once the df ceiling stops capping it.
    """
    cands = [p for p in profiles if p["prose_len"] >= MIN_PROSE]
    n = len(cands)
    if n < 2:
        return []
    df_hi = _df_ceiling(n, DF_MIN + 1)
    body_tf: list[Counter] = []
    title_tf: list[Counter] = []
    df: Counter = Counter()
    for p in cands:
        cb: Counter = Counter()
        for tok in TOKEN.findall(p["prose"][:4000].lower()):
            if tok in STOP or tok.isdigit():
                continue
            cb[tok] += 1
        ct: Counter = Counter()
        for tok in TOKEN.findall(p["title"].lower()):
            if tok not in STOP and not tok.isdigit():
                ct[tok] += 2  # title terms speak for the whole page
        body_tf.append(cb)
        title_tf.append(ct)
        df.update((cb + ct).keys())
    idf = {t: math.log(1 + n / (1 + d)) for t, d in df.items()}
    sig: dict[str, list[tuple[float, str]]] = {}
    norm: dict[str, float] = {}
    body_sig: dict[str, set[str]] = {}
    for p, cb, ct in zip(cands, body_tf, title_tf):
        full = cb + ct
        ranked = sorted(((full[t] * idf[t], t) for t in full
                         if DF_MIN <= df[t] <= df_hi), reverse=True)[:SIG_TERMS]
        sig[p["id"]] = ranked
        norm[p["id"]] = math.sqrt(sum(w * w for w, _ in ranked)) or 1.0
        # title tokens may only reinforce a signature, never carry one: a pair
        # whose bodies share zero in-window terms is a title convention, not a
        # topic match (generated API sites share title boilerplate by design)
        body_sig[p["id"]] = {t for _w, t in ranked if cb[t] > 0}
    postings: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for p in cands:
        for w, t in sig[p["id"]]:
            postings[t].append((p["id"], w))
    acc: dict[tuple[str, str], float] = defaultdict(float)
    for plist in postings.values():
        if len(plist) > POSTING_K:
            plist.sort(key=lambda x: (-x[1], x[0]))
            plist = plist[:POSTING_K]
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
        if not body_sig[a] & body_sig[b]:
            continue
        neighbours[a].append((cos, b))
        neighbours[b].append((cos, a))
    out: list[tuple[str, str, str]] = []
    for a, lst in neighbours.items():
        for _cos, b in sorted(lst, key=lambda kv: (-kv[0], kv[1]))[:SIM_CAP]:
            if a < b:
                out.append((a, b, "similar_to"))
    return out


def _heading_shared_pairs(cands: list[dict], head_hi: int) -> Counter:
    """Inverted index on headings -> per-pair shared-heading counts. Posting
    lists are truncated to the top HEAD_POST_K ids, so a df just under the
    ceiling stays sub-quadratic in the pair pass."""
    by_heading: dict[str, list[str]] = defaultdict(list)
    for p in cands:
        for h in set(p["headings"]):
            by_heading[h].append(p["id"])
    shared: Counter = Counter()
    for owners in by_heading.values():
        if not 2 <= len(owners) <= head_hi:
            continue
        if len(owners) > HEAD_POST_K:
            owners = sorted(owners)[:HEAD_POST_K]
        for i in range(len(owners)):
            for j in range(i + 1, len(owners)):
                a, b = owners[i], owners[j]
                shared[(a, b) if a < b else (b, a)] += 1
    return shared


def _heading_edges(profiles: list[dict]) -> list[tuple[str, str, str]]:
    """Document-skeleton overlap: two pages whose normalized heading sets pass a
    Jaccard threshold describe the same kind of document. Inverted index on
    headings keeps the pass off O(n^2). Boilerplate headings (shared by more
    than the relative df ceiling) are excluded from the shared-count numerator
    but stay in the Jaccard union -- a conservative bias against
    boilerplate-laden sets. Like SIM_CAP, HEAD_CAP limits each node's
    *nominations*, so a node's incident degree can exceed it via nominations
    from smaller-id peers."""
    cands = [p for p in profiles if len(p["headings"]) >= HEAD_MIN]
    if len(cands) < 2:
        return []
    head_hi = _df_ceiling(len(cands), HEAD_DF_FLOOR)
    shared = _heading_shared_pairs(cands, head_hi)
    heads = {p["id"]: set(p["headings"]) for p in cands}
    neighbours: dict[str, list[tuple[float, str]]] = defaultdict(list)
    for (a, b), n in shared.items():
        union = len(heads[a] | heads[b])
        jac = n / union if union else 0.0
        if jac >= HEAD_OVERLAP:
            neighbours[a].append((jac, b))
            neighbours[b].append((jac, a))
    out: list[tuple[str, str, str]] = []
    for a, lst in neighbours.items():
        for _jac, b in sorted(lst, key=lambda kv: (-kv[0], kv[1]))[:HEAD_CAP]:
            if a < b:
                out.append((a, b, "shares_heading"))
    return out


def _duplicate_edges(profiles: list[dict]) -> list[tuple[str, str, str]]:
    """Exact duplicates: same frontmatter sha256, chained in sorted-id order
    (never a clique -- a 400-copy page would fan out 79 800 edges, and the
    chain keeps incident degree at 2). Precision ~1.0 by hash equality;
    adjacent copies in the chain claim their pair before similar_to can spend
    a nomination on it."""
    by_sha: dict[str, list[str]] = defaultdict(list)
    for p in profiles:
        if p["sha"]:
            by_sha[p["sha"]].append(p["id"])
    out: list[tuple[str, str, str]] = []
    for members in by_sha.values():
        if len(members) < 2:
            continue
        ordered = sorted(members)
        for a, b in pairwise(ordered):
            out.append((a, b, "duplicate_of"))
    return out


def _overlap_edges(link_pairs: set[tuple[str, str]],
                   direction: str) -> list[tuple[str, str, str]]:
    """Neighbourhood overlap over the evidence graph, Kessler/Small style with
    a resource-allocation weight (1/deg(z): a shared neighbour that everyone
    cites contributes almost nothing). direction="out" is bibliographic
    coupling (both pages cite the same targets), "in" is co-citation (the same
    pages cite both) -- the only dimension that can relate two stubs that
    never link anywhere. Hub guards: pages with HUB_OUT_MAX+ out-links are
    tables of contents and contribute no candidates; a shared neighbour's
    co-linker group is truncated deterministically (lowest ids first) so one
    hub cannot blow up the pair pass."""
    out_, in_ = defaultdict(set), defaultdict(set)
    for a, b in sorted(link_pairs):
        if a != b:
            out_[a].add(b)
            in_[b].add(a)
    deg = {n: len(out_[n]) + len(in_[n]) for n in set(out_) | set(in_)}
    nbrs = out_ if direction == "out" else in_
    inv: dict[str, list[str]] = defaultdict(list)
    for n in sorted(out_ | in_):
        if len(out_[n]) <= HUB_OUT_MAX:
            for z in sorted(nbrs[n]):
                inv[z].append(n)
    acc: dict[tuple[str, str], float] = defaultdict(float)
    for z in sorted(inv):
        pages = inv[z][:GROUP_MAX]
        if len(pages) < 2:
            continue
        w = 1.0 / max(2, deg.get(z, 2))
        for i in range(len(pages)):
            for j in range(i + 1, len(pages)):
                a, b = pages[i], pages[j]
                acc[(a, b) if a < b else (b, a)] += w
    kind = "coupled_with" if direction == "out" else "co_cited_with"
    neighbours: dict[str, list[tuple[float, str]]] = defaultdict(list)
    for (a, b), s in acc.items():
        if s < COUPLE_MIN:
            continue
        neighbours[a].append((s, b))
        neighbours[b].append((s, a))
    out: list[tuple[str, str, str]] = []
    for a, lst in neighbours.items():
        for _s, b in sorted(lst, key=lambda kv: (-kv[0], kv[1]))[:COUPLE_CAP]:
            if a < b:
                out.append((a, b, kind))
    return out


def _term_edges(profiles: list[dict]) -> list[tuple[str, str, str]]:
    """Shared curated terms: inline code spans, headings and declared symbols
    are the writer's own terminology marking, and two pages anchored on the
    same backticked API names relate even when their prose styles differ --
    exactly the signal _prose() discards for the cosine pass. Inverted index
    with relative df filtering (a term on a third of the corpus is chrome) and
    per-term posting truncation; an edge needs >= TERM_MIN distinct shared
    terms with summed idf >= TERM_MIN_W, so a single coincidence never fires."""
    cands = [p for p in profiles if p["terms"]]
    n = len(cands)
    if n < 2:
        return []
    df: Counter = Counter()
    for p in cands:
        df.update(t for t, _w in p["terms"])
    df_hi = _df_ceiling(n, TERM_MIN + 1)
    idf = {t: math.log(1 + n / (1 + d)) for t, d in df.items()}
    sig: dict[str, list[tuple[str, float]]] = {}
    postings: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for p in cands:
        ranked = [(t, w * idf[t]) for t, w in p["terms"]
                  if TERM_MIN <= df[t] <= df_hi]
        ranked.sort(key=lambda kv: (-kv[1], kv[0]))
        ranked = ranked[:TERM_SIG]
        sig[p["id"]] = ranked
        for t, w in ranked:
            postings[t].append((p["id"], w))
    shared: dict[tuple[str, str], list[float]] = defaultdict(list)
    for t, plist in postings.items():
        if len(plist) > TERM_POST_K:
            plist.sort(key=lambda x: (-x[1], x[0]))
            plist = plist[:TERM_POST_K]
        for i in range(len(plist)):
            a, _wa = plist[i]
            for j in range(i + 1, len(plist)):
                b, _wb = plist[j]
                shared[(a, b) if a < b else (b, a)].append(idf[t])
    neighbours: dict[str, list[tuple[float, str]]] = defaultdict(list)
    for (a, b), ws in shared.items():
        if len(ws) < TERM_MIN or sum(ws) < TERM_MIN_W:
            continue
        s = sum(ws)
        neighbours[a].append((s, b))
        neighbours[b].append((s, a))
    out: list[tuple[str, str, str]] = []
    for a, lst in neighbours.items():
        for _s, b in sorted(lst, key=lambda kv: (-kv[0], kv[1]))[:TERM_CAP]:
            if a < b:
                out.append((a, b, "shares_term"))
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
        ordered = sorted(members)  # once per group: the inner loop re-sorted every row
        for i, a in enumerate(ordered):
            for b in ordered[i + 1:]:
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
        notes.append(f"shares_entity skipped: {type(e).__name__}: {e}")
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
        notes.append(f"semantic_similar skipped: {type(e).__name__}: {e}")
        return []
    # one dot per unordered pair with precomputed norms: semantic.cosine
    # re-derives both norms on every call, so a 5000-page budget recomputed
    # each vector's norm ~10k times over the pair pass
    norms = [math.sqrt(sum(x * x for x in v)) for v in vecs]
    near: dict[int, list[tuple[float, str]]] = defaultdict(list)
    for i in range(len(cands)):
        for j in range(i + 1, len(cands)):
            dot = sum(x * y for x, y in zip(vecs[i], vecs[j]))
            cos = dot / (norms[i] * norms[j] + 1e-12)  # semantic.cosine's epsilon
            if cos >= EMBED_THRESHOLD:  # threshold before the cap: identical
                near[i].append((cos, cands[j]["id"]))  # selection, fewer sort keys
                near[j].append((cos, cands[i]["id"]))
    out = []
    for i, p in enumerate(cands):
        for _cos, other in sorted(near[i], key=lambda kv: (-kv[0], kv[1]))[:EMBED_CAP]:
            if p["id"] < other:
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
        notes.append(f"llm router skipped: {type(e).__name__}: {e}")
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
                    have: set[tuple[str, str]] | None = None,
                    link_pairs: set[tuple[str, str]] | None = None
                    ) -> tuple[list[dict], dict]:
    """Derived edges for `docs`, plus a per-dimension report.

    `have` is the set of evidence edge pairs already resolved upstream: a pair
    that the corpus already links is never re-labelled with a derived kind.
    `link_pairs` is the ref/imports subset of that graph, passed separately so
    the neighbourhood dimensions (coupled_with/co_cited_with) can read the
    citation structure -- `contains` hub edges are deliberately excluded,
    a README listing its directory is not a citation.
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
        gate = select_dimensions(p, enabled=enabled, heavy_ok=heavy_ok)
        if p["id"] in picks:
            # the router's narrowing stands, even to zero -- but the router may
            # pick a dimension the content cannot support, so re-gate against it
            chosen[p["id"]] = [d for d in picks[p["id"]] if d in gate]
        else:
            chosen[p["id"]] = gate

    seen = have or set()
    raw: list[tuple[str, str, str]] = []
    # duplicates first: exact copies claim their pair before the fuzzy
    # dimensions spend nominations on them
    if "duplicate_of" in enabled:
        raw += _duplicate_edges(profiles)
    if "coupled_with" in enabled and link_pairs:
        raw += _overlap_edges(link_pairs, "out")
    if "co_cited_with" in enabled and link_pairs:
        raw += _overlap_edges(link_pairs, "in")
    if any("mentions_symbol" in v for v in chosen.values()):
        raw += _symbol_edges(profiles, notes)
    if any("similar_to" in v for v in chosen.values()):
        raw += _similar_edges(profiles)
    if any("sibling_of" in v for v in chosen.values()):
        raw += _sibling_edges(profiles)
    if any("supersedes" in v for v in chosen.values()):
        raw += _supersedes_edges(profiles)
    if any("shares_heading" in v for v in chosen.values()):
        raw += _heading_edges(profiles)
    if any("shares_term" in v for v in chosen.values()):
        raw += _term_edges(profiles)
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
        if k in ("similar_to", "sibling_of", "shares_entity", "semantic_similar",
                 "shares_heading", "shares_term", "duplicate_of",
                 "coupled_with", "co_cited_with"):
            pair = (a, b) if a < b else (b, a)
        if pair in seen:
            continue
        if k not in ALWAYS_ON_KINDS and k not in chosen.get(a, []) \
                and k not in chosen.get(b, []):
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
