"""Offline tests for the sharded docs store (get_page / ask path). Run: pytest -q"""
import json
import os

import pytest

from site_kg import graph, local, store


def _ingest(src) -> str:
    """Mirror the ingest flow (reset -> corpus -> graph -> save) and return site_id."""
    source = f"file://{src}"
    site_id = store.site_id_for(source)
    corpus = src.parent / "corpus"
    store.reset_corpus(corpus)
    local.build_local_corpus(src, corpus)
    store.save_site(site_id, source, graph.build_graph(corpus))
    return site_id


@pytest.fixture
def isolated_data(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA", tmp_path / "sites")
    # content-keyed caches live at module scope: drop entries from prior tests
    for cache in (store._inventory_cached, store._shard_cached, store._meta_cached):
        cache.cache_clear()
    return tmp_path / "sites"


def test_docs_sharded_roundtrip(isolated_data, tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.md").write_text("# A\n\nprose\n")
    site_id = _ingest(src)
    site = store.site_dir(site_id)
    # the monolithic single-file read is gone: get_page must not need it
    assert not (site / "docs.json").exists()
    assert list((site / "docs").glob("*.json"))
    d = store.get_doc(site_id, "a")
    assert d["title"] == "A" and "prose" in d["body"]
    assert store.get_doc(site_id, "nope") is None
    assert dict(store.iter_docs(site_id))["a"] == d


def test_shard_cache_invalidated_on_resave(isolated_data, tmp_path):
    # a long-lived MCP process re-ingests in place: the shard cache must not
    # keep serving the pre-rebuild body (this was the silent stale-read failure)
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.md").write_text("# A\n\nv1\n")
    site_id = _ingest(src)
    assert "v1" in store.get_doc(site_id, "a")["body"]
    (src / "a.md").write_text("# A\n\nv2\n")
    _ingest(src)
    assert "v2" in store.get_doc(site_id, "a")["body"]


def test_bucket_count_scales_with_corpus_size(isolated_data, tmp_path):
    # a small site keeps a handful of shard files, not 256 near-empty ones
    assert store._bucket_count(10) == 1
    assert store._bucket_count(200) == 16
    assert store._bucket_count(5000) == 64
    assert store._bucket_count(75_000) == 256
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.md").write_text("# A\n")
    site_id = _ingest(src)
    site = store.site_dir(site_id)
    assert [p.name for p in (site / "docs").glob("*.json")] == ["0.json"]
    n = json.loads((site / "meta.json").read_text())["docBuckets"]
    assert n == 1 and store._bucket("a", n) == "0"


def test_many_pages_spread_over_buckets(isolated_data, tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    for n in range(200):
        (src / f"p{n}.md").write_text(f"---\ntitle: P{n}\n---\n\nbody {n}\n")
    site_id = _ingest(src)
    ids = {f"p{n}" for n in range(200)}
    n_buckets = json.loads((store.site_dir(site_id) / "meta.json").read_text())["docBuckets"]
    # sha-keyed buckets stay small whatever the corpus shape (drivers/ with
    # 31k files must not recreate one giant shard)
    assert n_buckets == 16
    assert len({store._bucket(i, n_buckets) for i in ids}) > 2
    sizes = [p.stat().st_size for p in (store.site_dir(site_id) / "docs").glob("*.json")]
    assert max(sizes) < 8 * 1024
    for n in range(200):
        # .md title is the first # heading or the stem, never the frontmatter
        assert store.get_doc(site_id, f"p{n}")["title"] == f"p{n}"


def test_legacy_docs_json_still_read(isolated_data):
    site_id = "legacy-00000000"
    d = store.site_dir(site_id)
    d.mkdir(parents=True)
    (d / "docs.json").write_text(json.dumps(
        {"a": {"title": "A", "url": "file:///x/a", "body": "old body"}}))
    assert store.get_doc(site_id, "a")["body"] == "old body"
    assert store.get_doc(site_id, "zz") is None
    assert dict(store.iter_docs(site_id))["a"]["title"] == "A"


def test_unknown_site_raises(isolated_data):
    with pytest.raises(KeyError):
        store.get_doc("no-such-site-12345678", "a")


def test_external_rewrite_seen_without_save_site(isolated_data, tmp_path):
    # another process (cli ingest-local, a crawl job) rewrote the shard on
    # disk: the content-keyed cache must pick it up with no invalidation hook
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.md").write_text("# A\n\nv1\n")
    site_id = _ingest(src)
    assert "v1" in store.get_doc(site_id, "a")["body"]
    shard = store.site_dir(site_id) / "docs" / "0.json"
    doc = json.loads(shard.read_text())
    doc["a"]["body"] = "# A\n\nv2 rewritten by another process\n"
    tmp = shard.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False))
    os.replace(tmp, shard)
    assert "v2 rewritten" in store.get_doc(site_id, "a")["body"]


def test_missing_meta_falls_back_to_inventory(isolated_data, tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    for n in range(200):
        (src / f"p{n}.md").write_text(f"# P{n}\n\nbody {n}\n")
    site_id = _ingest(src)
    (store.site_dir(site_id) / "meta.json").unlink()
    assert store._meta(site_id) == {}
    # 200 docs -> 16 buckets; the count must come from the shard files now
    assert store.get_doc(site_id, "p123")["title"] == "P123"


def test_all_buckets_written_even_when_sparse(isolated_data, tmp_path, monkeypatch):
    # force a bucket count far above the doc count: the shard inventory must
    # still equal docBuckets so readers can derive the layout from disk
    monkeypatch.setattr(store, "_bucket_count", lambda n: 16)
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.md").write_text("# A\n")
    (src / "b.md").write_text("# B\n")
    site_id = _ingest(src)
    names = sorted((p.name for p in (store.site_dir(site_id) / "docs").glob("*.json")),
                   key=lambda n: int(n.split(".")[0]))
    assert names == [f"{i}.json" for i in range(16)]
    meta = json.loads((store.site_dir(site_id) / "meta.json").read_text())
    assert meta["docBuckets"] == 16
    assert store.get_doc(site_id, "a")["title"] == "A"


def test_bucket_shrink_drops_stale_shards(isolated_data, tmp_path, monkeypatch):
    # a site re-ingested smaller than before must not keep out-of-range shards
    monkeypatch.setattr(store, "_bucket_count", lambda n: 64)
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.md").write_text("# A\n")
    site_id = _ingest(src)
    assert len(list((store.site_dir(site_id) / "docs").glob("*.json"))) == 64
    monkeypatch.setattr(store, "_bucket_count", lambda n: 1)
    _ingest(src)
    assert [p.name for p in (store.site_dir(site_id) / "docs").glob("*.json")] == ["0.json"]
    assert store.get_doc(site_id, "a")["title"] == "A"


def test_site_dir_rejects_path_escape(isolated_data):
    # site_id is caller-controlled via every MCP tool: traversal, absolute
    # paths and non-generated shapes must never reach the filesystem join
    for bad in ("../evil", r"..\evil", "C:/evil", "/abs-12345678", "abc",
                "abc-ZZZZZZZZ", "abc-12345", "a/b-cdef1234", "", "."):
        with pytest.raises(ValueError):
            store.site_dir(bad)
    ok = store.site_id_for("https://example.org/docs/")
    assert store.site_dir(ok).name == ok
    assert store.site_dir(ok).parent == store.DATA


def test_cli_render_unknown_site_fails_cleanly(isolated_data, monkeypatch):
    import sys

    from site_kg import cli
    monkeypatch.setattr(sys, "argv", ["site-kg", "render", "ghost-00000000"])
    with pytest.raises(SystemExit) as err:
        cli.main()
    assert "render failed" in str(err.value)
