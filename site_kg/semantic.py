"""LLM-backed Q&A over a site's graph+index. OpenAI-compatible endpoints only
(vLLM / new-api gateways included). Key and base URL come from env, never baked in:

  SITE_KG_LLM_BASE   e.g. http://your-llm-gateway:8000/v1
  SITE_KG_LLM_KEY    bearer token
  SITE_KG_CHAT_MODEL / SITE_KG_EMBED_MODEL / SITE_KG_RERANK_MODEL (auto-discovered if unset)
"""
from __future__ import annotations

import os
from functools import lru_cache

import httpx


@lru_cache
def _cfg() -> dict:
    base = os.environ.get("SITE_KG_LLM_BASE", "").rstrip("/")
    key = os.environ.get("SITE_KG_LLM_KEY", "")
    if not base or not key:
        raise RuntimeError("semantic layer not configured: set SITE_KG_LLM_BASE and SITE_KG_LLM_KEY")
    return {"base": base, "key": key,
            "chat": os.environ.get("SITE_KG_CHAT_MODEL", ""),
            "embed": os.environ.get("SITE_KG_EMBED_MODEL", ""),
            "rerank": os.environ.get("SITE_KG_RERANK_MODEL", "")}


def _client() -> httpx.Client:
    c = _cfg()
    # trust_env=False: LLM endpoint is site-local config; ambient proxy vars must not hijack it
    return httpx.Client(base_url=c["base"], headers={"Authorization": f"Bearer {c['key']}"},
                        timeout=60, trust_env=False)


def discover_models() -> dict:
    """Classify /v1/models entries into chat/embed/rerank by name heuristics."""
    r = _client().get("/models")
    r.raise_for_status()
    ids = [m["id"] for m in r.json().get("data", [])]
    pick = lambda *kw: next((i for i in ids if any(k in i.lower() for k in kw)), "")
    return {"all": ids,
            "chat": _cfg()["chat"] or pick("qwen", "llama", "gpt", "chat", "instruct", "deepseek", "glm"),
            "embed": _cfg()["embed"] or pick("embed", "bge-m3", "gte"),
            "rerank": _cfg()["rerank"] or pick("rerank", "reranker")}


def embed(texts: list[str], model: str) -> list[list[float]]:
    r = _client().post("/embeddings", json={"model": model, "input": texts})
    r.raise_for_status()
    return [d["embedding"] for d in sorted(r.json()["data"], key=lambda d: d["index"])]


def rerank(query: str, docs: list[str], model: str, top_n: int) -> list[int]:
    r = _client().post("/rerank", json={"model": model, "query": query,
                                        "documents": docs, "top_n": top_n})
    r.raise_for_status()
    return [d["index"] for d in r.json()["results"]]


def chat(messages: list[dict], model: str, max_tokens: int = 4096) -> str:
    # Qwen thinking variants burn the whole budget on hidden reasoning; disable it
    # and keep headroom, otherwise content comes back None with finish=length.
    r = _client().post("/chat/completions", json={
        "model": model, "messages": messages, "max_tokens": max_tokens,
        "temperature": 0.1, "chat_template_kwargs": {"enable_thinking": False}})
    r.raise_for_status()
    m = r.json()["choices"][0]["message"]
    return m.get("content") or m.get("reasoning_content") or ""


def cosine(a: list[float], b: list[float]) -> float:
    import math
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb + 1e-12)


def _blocks(text: str, size: int = 600) -> list[str]:
    return [text[i:i + size] for i in range(0, len(text), size)] or [""]


def best_excerpt(query_vec: list[float] | None, body: str, model: str, budget: int = 1800) -> str:
    """Head truncation drops the answer when it sits mid-page (real failure on a
    long FAQ page). With an embed model available, keep the blocks closest to the query."""
    if not model or query_vec is None:
        return body[:budget]
    bl = _blocks(body)
    vecs = embed(bl, model)
    order = sorted(range(len(bl)), key=lambda i: -cosine(query_vec, vecs[i]))
    picked, out = [], ""
    for i in order[:6]:
        if len(out) >= budget:
            break
        picked.append(i)
        out += bl[i]
    return "\n…\n".join(bl[i] for i in sorted(picked))[:budget * 2]


def answer(site: dict, docs: dict, query: str, graph_ctx: list[str], top_k: int = 4) -> dict:
    """Retrieve (keyword ∪ graph neighborhood), rerank, then answer with citations."""
    from .graph import search_index

    models = discover_models()
    kw = [h["id"] for h in search_index(site["search"], query, limit=8)]
    cand = list(dict.fromkeys(kw + graph_ctx))[:12]
    if not cand and models["embed"]:
        # keyword recall failed (e.g. cross-lingual query on an ASCII index):
        # fall back to embedding similarity over every page digest
        ids = list(docs.keys())
        digests = [site["search"]["digest"].get(i) or docs[i]["title"] for i in ids]
        qv = embed([query], models["embed"])[0]
        dvs = embed(digests, models["embed"])
        cand = [i for i, _ in sorted(zip(ids, (cosine(qv, dv) for dv in dvs)),
                                     key=lambda kv: -kv[1])[:8]]
    if not cand:
        return {"ok": False, "error": "no candidate pages retrieved"}
    bodies = {pid: docs[pid]["body"][:3000] for pid in cand if pid in docs}
    cand = [c for c in cand if c in bodies]

    if models["embed"] and models["rerank"]:
        qv = embed([query], models["embed"])[0]
        dvs = embed([bodies[c] for c in cand], models["embed"])
        scored = sorted(zip(cand, (cosine(qv, dv) for dv in dvs)), key=lambda kv: -kv[1])
        order = rerank(query, [bodies[c] for c, _ in scored], models["rerank"], min(top_k, len(scored)))
        chosen = [scored[i][0] for i in order]
    else:  # endpoint without embed/rerank: keyword ranking only
        chosen, qv = cand[:top_k], None

    excerpts = {p: best_excerpt(qv, docs[p]["body"], models["embed"]) for p in chosen}
    ctx = "\n\n".join(f"[{i}] {docs[p]['title']} ({docs[p]['url']})\n{excerpts[p]}"
                      for i, p in enumerate(chosen, 1))
    text = chat([
        {"role": "system", "content":
         "Answer strictly from the provided context pages. Cite as [n] after each claim. "
         "If the context is insufficient, say so. Answer in the user's language."},
        {"role": "user", "content": f"Question: {query}\n\nContext:\n{ctx}"},
    ], models["chat"])
    return {"ok": True, "answer": text, "models": models,
            "citations": [{"n": i, "id": p, "title": docs[p]["title"], "url": docs[p]["url"]}
                          for i, p in enumerate(chosen, 1)]}
