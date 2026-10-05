"""Corpus -> graph.json + search.json. Port of an earlier internal prototype tools/build.mjs edge logic.
Edges come from the corpus only; zero resolvable edges => NOT-READY, never invent links."""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path

FRONT = re.compile(r"^---\n([\s\S]*?)\n---\n?")
LINK_RE = re.compile(r"\[[^\]]*\]\([^)\s#]+\.html?(#[^)\s]*)?\)", re.I)  # placeholder, unused
MD_LINK_ID = re.compile(r"\[[^\]]*\]\(([^)\s#]+)(#[^)\s]*)?\)")
FENCE = re.compile(r"```[\s\S]*?```", re.M)
INLINE = re.compile(r"`[^`]*`")
URL_RE = re.compile(r"https?://\S+")
TOKEN = re.compile(r"[a-z0-9][a-z0-9._/-]{1,40}", re.I)

POSTING_CAP = 40
DF_RATIO_CAP = 0.35
STOP = set("""a an and are as at be been but by can could did does for from had has have how i if in
is it its no not of on or our so that the their them then there these they this to was we were when
which will with you your use used using via etc also may must shall""".split())


def _parse(path: Path) -> dict:
    raw = path.read_text(encoding="utf-8")
    meta, body = {}, raw
    m = FRONT.match(raw)
    if m:
        for line in m.group(1).splitlines():
            kv = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
            if kv:
                meta[kv.group(1)] = kv.group(2).strip().strip("'\"")
        body = raw[m.end():]
    return {"meta": meta, "body": body}


def _prose(body: str) -> str:
    body = FENCE.sub(" ", body)
    body = INLINE.sub(" ", body)
    return URL_RE.sub(" ", body)


def build_graph(corpus_dir: Path) -> dict:
    docs = []
    for md in sorted(corpus_dir.rglob("*.md")):
        p = _parse(md)
        pid = md.stem
        docs.append({
            "id": pid,
            "chapter": md.parent.name,
            "title": p["meta"].get("title", pid.replace("_", " ")),
            "url": p["meta"].get("source_url", ""),
            "sha": p["meta"].get("sha256", ""),
            "body": p["body"],
        })
    known = {d["id"] for d in docs}

    # Prefer the crawler's URL-resolved sidecar; markdown hrefs are page-relative.
    sidecar = corpus_dir / "_links.json"
    link_map: dict[str, list[str]] | None = None
    if sidecar.exists():
        link_map = {k: [t for t in v if t in known]
                    for k, v in json.loads(sidecar.read_text()).items()}

    def targets_of(doc: dict) -> set[str]:
        if link_map is not None:
            return set(link_map.get(doc["id"], [])) - {doc["id"]}
        hits = set()
        for m in MD_LINK_ID.finditer(doc["body"]):
            cand = (m.group(1).replace("/", "_")
                    .removesuffix(".html").removesuffix(".htm").removesuffix(".md"))
            if cand in known:
                hits.add(cand)
        return hits - {doc["id"]}

    edges: dict[tuple[str, str], str] = {}
    for d in docs:
        targets = targets_of(d)
        is_hub = d["id"] == "index" or d["id"].endswith("_index")
        for t in sorted(targets):
            etype = ("hub" if d["id"] == "index" else "contains") if is_hub else "ref"
            edges.setdefault((d["id"], t), etype)

    # inverted index over prose
    df: Counter = Counter()
    postings: dict[str, dict[str, int]] = {}
    digest: dict[str, str] = {}
    for d in docs:
        prose = _prose(d["body"])
        digest[d["id"]] = re.sub(r"\s+", " ", prose).strip()[:150]
        tf: Counter = Counter()
        for tok in TOKEN.findall(prose[:2600].lower()):
            if tok not in STOP and not tok.isdigit():
                tf[tok] += 1
        for term, n in tf.items():
            df[term] += 1
            postings.setdefault(term, {})[d["id"]] = n
    n_docs = max(len(docs), 1)
    terms, post = [], {}
    for term, plist in sorted(postings.items()):
        if df[term] / n_docs > DF_RATIO_CAP:
            continue
        top = sorted(plist.items(), key=lambda kv: -kv[1])[:POSTING_CAP]
        post[term] = dict(top)
        terms.append(term)

    in_deg: Counter = Counter(t for _, t in edges)
    nodes = [{"i": d["id"], "c": d["chapter"], "t": d["title"], "u": d["url"], "g": in_deg.get(d["id"], 0)} for d in docs]
    edge_list = [{"s": a, "t": b, "k": k} for (a, b), k in sorted(edges.items())]
    edge_counts = Counter(k for k in edges.values())

    ref_edges = edge_counts.get("ref", 0)
    verdict = "READY" if ref_edges > 0 else "NOT-READY"
    report = {
        "docs": len(docs),
        "edges": len(edge_list),
        "edgeTypes": dict(edge_counts),
        "terms": len(terms),
        "verdict": verdict,
        "verdictNote": "link structure present" if ref_edges else
            "zero resolvable cross-references: this site is a directory tree, not a graph. "
            "Serve search+outline, or enable the semantic layer (LLM extraction).",
    }
    return {
        "graph": {"nodes": nodes, "edges": edge_list},
        "search": {"terms": terms, "post": post, "digest": digest},
        "report": report,
        "docs": {d["id"]: {"title": d["title"], "url": d["url"], "body": d["body"]} for d in docs},
    }


def search_index(search: dict, query: str, limit: int = 10) -> list[dict]:
    post, digest = search["post"], search["digest"]
    n_docs = max(len(digest), 1)
    qterms = [t for t in TOKEN.findall(query.lower()) if t in post]
    scores: Counter = Counter()
    for t in qterms:
        idf = math.log(1 + n_docs / (1 + len(post[t])))
        for doc, tf in post[t].items():
            scores[doc] += idf * (1 + math.log(tf))
    return [{"id": d, "score": round(s, 3), "snippet": digest.get(d, "")}
            for d, s in scores.most_common(limit)]
