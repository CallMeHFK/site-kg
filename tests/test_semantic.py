"""Offline tests for semantic.answer: every LLM call is monkeypatched, so no
endpoint, key, or network is involved."""
import site_kg.semantic as sem
from site_kg import graph

KW_IDS = [f"kw{i}" for i in range(8)]


def _patch_llm(monkeypatch, calls: dict):
    monkeypatch.setattr(sem, "discover_models",
                        lambda: {"all": [], "chat": "fake-chat",
                                 "embed": "fake-embed", "rerank": "fake-rerank"})

    def fake_embed(texts, model):
        calls.setdefault("embed", []).append(list(texts))
        return [[1.0, 0.0]] * len(texts)  # equal vectors: ranking keeps input order
    monkeypatch.setattr(sem, "embed", fake_embed)

    def fake_rerank(query, docs, model, top_n):
        calls["rerank_docs"] = list(docs)
        return list(range(top_n))
    monkeypatch.setattr(sem, "rerank", fake_rerank)
    monkeypatch.setattr(sem, "chat", lambda messages, model: "fake answer")
    monkeypatch.setattr(graph, "search_index",
                        lambda search, query, limit=8: [{"id": i} for i in KW_IDS[:limit]])


def _docs(ids: list[str]) -> dict:
    return {i: {"title": f"TITLE-{i}", "body": f"body-{i} " * 300,
                "url": f"http://x/{i}"} for i in ids}


def test_answer_embeds_title_and_body_head(monkeypatch):
    """Regression: candidate embeddings must use title + body[:600], never the
    150-char display digest (digest-level similarity ranked paraphrase targets
    at 14/18 on the Vue guide corpus)."""
    calls: dict = {}
    _patch_llm(monkeypatch, calls)
    docs = _docs([*KW_IDS, "extra0", "extra1"])
    r = sem.answer({"search": {}}, docs, "q", [], top_k=2)
    assert r["ok"]
    corpus_embed = calls["embed"][1]  # [query] first, per-doc texts second
    assert len(corpus_embed) == len(docs)
    for pid, text in zip(docs, corpus_embed, strict=True):
        assert text == f"TITLE-{pid}\n{docs[pid]['body'][:600]}"


def test_answer_union_keeps_emb_and_graph_channels(monkeypatch):
    """Regression x2: (a) candidates are a union, not a serial fallback -- an
    embedding-only page must be a candidate even when keyword search returns 8
    hits; (b) the graph channel survives two full channels -- the old [:16] cap
    on kw+emb+graph truncated graph_ctx to zero."""
    calls: dict = {}
    _patch_llm(monkeypatch, calls)
    emb_ids = [f"emb{i}" for i in range(8)]  # embedding-only channel
    graph_ids = ["g0", "g1"]
    # insertion order = embedding rank order (equal vectors, stable sort)
    docs = _docs(emb_ids + KW_IDS + graph_ids)
    r = sem.answer({"search": {}}, docs, "q", graph_ids, top_k=2)
    assert r["ok"]
    reranked = calls["rerank_docs"]
    assert any("body-emb0" in d for d in reranked)  # union, not kw-only fallback
    assert any("body-g0" in d for d in reranked)  # graph channel not truncated away
    assert any("body-g1" in d for d in reranked)
    # bge-reranker-large 400s past its 512-token context; docs must be capped
    assert all(len(d) <= 1200 for d in reranked)
