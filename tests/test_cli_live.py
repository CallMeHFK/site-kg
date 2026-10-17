"""CLI and MCP surfaces for live sessions and scene-aware archive renders."""
from __future__ import annotations

from pathlib import Path

import pytest

from site_kg import cli, mcp_server, store

GRAPH = {
    "nodes": [
        {"i": "a", "t": "A", "u": "https://example.com/a", "c": "c", "g": 1, "r": 1.0},
        {"i": "b", "t": "B", "u": "https://example.com/b", "c": "c", "g": 0, "r": 0.5},
    ],
    "edges": [{"s": "a", "t": "b", "k": "ref"}],
}
REPORT = {"docs": 2, "edges": 1, "edgeTypes": {"ref": 1}, "verdict": "READY"}
SITE_ID = "demo"


@pytest.fixture
def isolated_data(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA", tmp_path / "sites")
    for cache in (store._inventory_cached, store._shard_cached, store._meta_cached):
        cache.cache_clear()
    return tmp_path / "sites"


@pytest.fixture
def site(isolated_data):
    built = {
        "graph": GRAPH,
        "search": {"a": ["alpha"], "b": ["beta"]},
        "docs": {
            "a": {"title": "A", "url": "https://example.com/a", "body": "alpha"},
            "b": {"title": "B", "url": "https://example.com/b", "body": "beta"},
        },
        "report": REPORT,
    }
    store.save_site(SITE_ID, "https://example.com/", built)
    return SITE_ID


class FakeHandle:
    url = "http://127.0.0.1:4321"
    run_id = "live-test"
    last_seq = 3
    state = "running"

    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def _fake_serve(monkeypatch):
    calls: dict = {}
    handle = FakeHandle()

    def serve(site_id, **kwargs):
        calls["site_id"] = site_id
        calls.update(kwargs)
        return handle

    monkeypatch.setattr("site_kg.live.serve_live", serve)
    monkeypatch.setattr(cli, "_wait_live", lambda h: calls.setdefault("waited", h))
    return calls, handle


# -- CLI: live resolution ---------------------------------------------------


def test_live_url_without_scene_opens_launcher(monkeypatch):
    calls, _ = _fake_serve(monkeypatch)
    url = "https://example.com/docs"
    cli.main(["live", url])
    assert calls["site_id"] == store.site_id_for(url)
    assert calls.get("source") is None
    assert calls.get("replay") is None
    assert calls["title"] == url  # launcher input is prefilled
    assert calls["scene"] == "planets"
    assert calls["waited"] is not None


def test_live_url_with_scene_autostarts(monkeypatch):
    calls, _ = _fake_serve(monkeypatch)
    url = "https://example.com/docs"
    cli.main(["live", url, "--scene", "neural"])
    assert calls["source"] == url
    assert calls["scene"] == "neural"
    assert calls.get("title") is None


def test_live_local_directory(monkeypatch, tmp_path):
    calls, _ = _fake_serve(monkeypatch)
    root = tmp_path / "docs"
    root.mkdir()
    cli.main(["live", str(root), "--local", "--scene", "people"])
    resolved = str(root.resolve())
    assert calls["site_id"] == store.site_id_for(f"file://{resolved}")
    assert calls["source"] == resolved
    assert calls["scene"] == "people"


def test_live_site_id_replays(monkeypatch, site):
    calls, _ = _fake_serve(monkeypatch)
    cli.main(["live", site, "--replay", "--scene", "neural"])
    assert calls["site_id"] == site
    assert calls["replay"] is True
    assert calls["scene"] == "neural"


def test_live_site_id_without_replay_flag_also_replays(monkeypatch, site):
    calls, _ = _fake_serve(monkeypatch)
    cli.main(["live", site, "--scene", "people"])
    assert calls["site_id"] == site
    assert calls["replay"] is True


@pytest.mark.parametrize("argv, message", [
    (["live", "https://example.com", "--local"], "--local"),
    (["live", "https://example.com", "--replay"], "--replay"),
    (["live", "https://example.com", "--local", "--replay"], "mutually exclusive"),
    (["live", "ftp://example.com"], "http"),
    (["live", "no-such-site"], "unknown site"),
])
def test_live_ambiguous_or_unknown_targets_fail(monkeypatch, argv, message):
    _fake_serve(monkeypatch)
    with pytest.raises(SystemExit) as err:
        cli.main(argv)
    assert message in str(err.value)


def test_live_directory_without_local_flag_fails(monkeypatch, tmp_path):
    _fake_serve(monkeypatch)
    root = tmp_path / "docs"
    root.mkdir()
    with pytest.raises(SystemExit) as err:
        cli.main(["live", str(root)])
    assert "--local" in str(err.value)


def test_live_unknown_scene_fails(monkeypatch):
    _fake_serve(monkeypatch)
    with pytest.raises(SystemExit):
        cli.main(["live", "https://example.com", "--scene", "galaxy"])


# -- CLI: render ------------------------------------------------------------


def test_render_honors_scene_and_external_data(site, capsys):
    cli.main(["render", site, "--scene", "neural", "--external-data"])
    out = Path(capsys.readouterr().out.strip())
    assert out == store.site_dir(site) / "viewer.html"
    text = out.read_text(encoding="utf-8")
    assert '"scene":"neural"' in text
    assert '"external":true' in text
    assert '"mode":"archive"' in text
    for name in ("sk-graph.bin", "sk-layout.bin", "sk-timeline.bin"):
        assert (out.parent / name).exists()


def test_render_rejects_unknown_scene(site):
    with pytest.raises(SystemExit):
        cli.main(["render", site, "--scene", "galaxy"])


def test_render_embedded_payload_by_default(site, capsys):
    cli.main(["render", site])
    out = Path(capsys.readouterr().out.strip())
    text = out.read_text(encoding="utf-8")
    assert '"external":false' in text
    assert 'id="sk-payload"' in text
    assert not (out.parent / "sk-graph.bin").exists()


# -- MCP: render_site -------------------------------------------------------


def test_mcp_render_site_archive_keeps_path(site):
    out = mcp_server.render_site(site, scene="people", max_nodes=1)
    assert out["ok"] is True
    assert Path(out["path"]) == store.site_dir(site) / "viewer.html"
    assert out["pages"] == 2 and out["edges"] == 1  # counts describe the site
    assert '"scene":"people"' in Path(out["path"]).read_text(encoding="utf-8")


def test_mcp_render_site_live_starts_replay_handle(site, monkeypatch):
    calls: dict = {}
    handle = FakeHandle()

    def serve(site_id, **kwargs):
        calls["site_id"] = site_id
        calls.update(kwargs)
        return handle

    monkeypatch.setattr("site_kg.live.serve_live", serve)
    monkeypatch.setattr(mcp_server, "_LIVE_HANDLES", {})
    out = mcp_server.render_site(site, scene="people", max_nodes=5, mode="live")
    assert out == {"ok": True, "live_url": handle.url, "run_id": handle.run_id,
                   "last_seq": handle.last_seq}
    assert calls["site_id"] == site
    assert calls["scene"] == "people"
    assert calls["max_nodes"] == 5
    assert calls["replay"] is True
    assert mcp_server._LIVE_HANDLES[site] is handle


def test_mcp_render_site_live_replaces_stale_handle(site, monkeypatch):
    handles = [FakeHandle(), FakeHandle()]

    def serve(site_id, **kwargs):
        return handles.pop(0)

    monkeypatch.setattr("site_kg.live.serve_live", serve)
    monkeypatch.setattr(mcp_server, "_LIVE_HANDLES", {})
    first = FakeHandle()
    second = FakeHandle()
    handles[:] = [first]

    mcp_server.render_site(site, mode="live")
    handles.append(second)
    mcp_server.render_site(site, mode="live")
    assert first.closed is True
    assert mcp_server._LIVE_HANDLES[site] is second


def test_mcp_render_site_rejects_bad_scene_or_mode(site, monkeypatch):
    monkeypatch.setattr(mcp_server, "_LIVE_HANDLES", {})
    bad_scene = mcp_server.render_site(site, scene="galaxy")
    assert bad_scene["ok"] is False and "scene" in bad_scene["error"]
    bad_mode = mcp_server.render_site(site, mode="hologram")
    assert bad_mode["ok"] is False and "mode" in bad_mode["error"]
