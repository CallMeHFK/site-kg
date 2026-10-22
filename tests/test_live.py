"""Loopback live server: SSE backlog, snapshot, start validation, and guards."""
from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.request

import pytest

from site_kg.live import LiveHub, _private_target_error, serve_hub


def _opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _get(base: str, path: str, *, headers: dict | None = None, timeout: float = 5.0):
    req = urllib.request.Request(base + path, headers=headers or {})
    return _opener().open(req, timeout=timeout)


def _post(base: str, path: str, payload: dict, *, headers: dict | None = None):
    body = json.dumps(payload).encode()
    hdrs = {"Content-Type": "application/json"}
    hdrs.update(headers or {})
    req = urllib.request.Request(base + path, data=body, headers=hdrs, method="POST")
    return _opener().open(req, timeout=5.0)


@pytest.fixture
def hub():
    h = LiveHub()
    h.run.update({"run_id": "run-test", "target": "https://example.com",
                  "scene": "planets", "state": "running"})
    h.emit({"t": "node_added", "id": "index", "title": "Index",
            "url": "https://example.com/", "c": "root", "g": 1, "r": 1.0})
    h.emit({"t": "node_added", "id": "docs", "title": "Docs",
            "url": "https://example.com/docs", "c": "docs", "g": 1, "r": 0.5})
    h.emit({"t": "edge_added", "s": "index", "d": "docs", "kind": "ref"})
    return h


@pytest.fixture
def server(hub):
    srv = serve_hub(hub, port=0)
    srv.start()
    yield srv
    srv.stop()


def test_health_reports_run_and_seq(server, hub):
    with _get(server.url, "/health") as resp:
        assert resp.status == 200
        assert resp.headers.get_content_type() == "application/json"
        data = json.loads(resp.read())
    assert data["ok"] is True
    assert data["run_id"] == "run-test"
    assert data["last_seq"] == 3
    assert data["state"] == "running"


def test_snapshot_has_nodes_edges_and_layout(server, hub):
    with _get(server.url, "/snapshot") as resp:
        assert resp.status == 200
        snap = json.loads(resp.read())
    assert snap["run_id"] == "run-test"
    assert snap["last_seq"] == 3
    assert {n["i"] for n in snap["nodes"]} == {"index", "docs"}
    assert snap["edges"] == [{"s": "index", "t": "docs", "k": "ref"}]
    rows = snap["layout"]["nodes"]
    assert len(rows) == 2
    for row in rows:
        for axis in ("x", "y", "z"):
            assert -1.001 <= row[axis] <= 1.001


def test_events_since_replays_backlog(server):
    with _get(server.url, "/events?since=1", timeout=5.0) as resp:
        assert resp.status == 200
        assert resp.headers.get_content_type() == "text/event-stream"
        text = ""
        for _ in range(12):
            line = resp.readline()
            if not line:
                break
            text += line.decode()
            if line == b"\n" and "data:" in text:
                break
    assert "id: 2" in text
    assert '"id":"docs"' in text
    assert "data: {" in text


def test_events_missing_since_is_rejected(server):
    with pytest.raises(urllib.error.HTTPError) as err:
        _get(server.url, "/events?since=nope")
    assert err.value.code == 400


def test_start_rejects_non_http_target(server):
    with pytest.raises(urllib.error.HTTPError) as err:
        _post(server.url, "/api/start", {"target": "ftp://example.com",
                                          "scene": "planets"})
    assert err.value.code == 400
    body = json.loads(err.value.read())
    assert body["ok"] is False


def test_start_rejects_unknown_scene(server):
    with pytest.raises(urllib.error.HTTPError) as err:
        _post(server.url, "/api/start", {"target": "https://example.com",
                                          "scene": "galaxy"})
    assert err.value.code == 400


def test_start_rejects_removed_people_scene(server):
    """The people scene no longer exists; the API answers 400, not 200."""
    with pytest.raises(urllib.error.HTTPError) as err:
        _post(server.url, "/api/start", {"target": "https://example.com",
                                          "scene": "people"})
    assert err.value.code == 400
    body = json.loads(err.value.read())
    assert body["ok"] is False
    assert "people" in body["error"]


def test_traversal_path_returns_404(server):
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5.0)
    try:
        conn.request("GET", "/../site_kg")
        resp = conn.getresponse()
        assert resp.status == 404
        resp.read()
    finally:
        conn.close()


def test_non_loopback_host_is_rejected(server):
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5.0)
    try:
        conn.request("GET", "/health", headers={"Host": "evil.example"})
        resp = conn.getresponse()
        assert resp.status == 403
        resp.read()
    finally:
        conn.close()


def test_token_guard(server, hub):
    srv = serve_hub(hub, port=0, token="s3cret")
    srv.start()
    try:
        with pytest.raises(urllib.error.HTTPError) as err:
            _get(srv.url, "/health")
        assert err.value.code == 401
        with _get(srv.url, "/health?token=s3cret") as resp:
            assert resp.status == 200
        with _get(srv.url, "/health", headers={"X-Site-KG-Token": "s3cret"}) as resp:
            assert resp.status == 200
        with _get(srv.url, "/") as resp:
            assert resp.status == 200
            assert resp.headers.get_content_type() == "text/html"
    finally:
        srv.stop()


def test_page_is_served_without_cors(server):
    with _get(server.url, "/") as resp:
        assert resp.status == 200
        assert resp.headers.get_content_type() == "text/html"
        assert resp.headers.get("Access-Control-Allow-Origin") is None


# -- --block-private: opt-in private network refusal -------------------------

def test_private_target_error_classifies_hosts(monkeypatch):
    # loopback and private ranges are refused, IP literal or resolved
    for target in ("http://127.0.0.1:9000/", "http://10.1.2.3/",
                   "http://192.168.1.4/", "http://172.16.0.9/",
                   "http://169.254.3.3/", "http://[::1]:8080/",
                   "http://[fc00::1]/", "http://0.0.0.0/"):
        reason = _private_target_error(target)
        assert reason and "block-private" in reason, target
    # internal TLDs are refused without resolution
    assert "internal" in _private_target_error("http://nas.local/")
    assert "internal" in _private_target_error("http://db.internal/x")
    # resolution is consulted for names; every record must be public
    import socket
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda host, port: [(socket.AF_INET, socket.SOCK_STREAM,
                                             6, "", ("93.184.216.34", 0))])
    assert _private_target_error("https://example.com/") is None
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda host, port: [(socket.AF_INET, socket.SOCK_STREAM,
                                             6, "", ("192.168.0.10", 0))])
    assert _private_target_error("https://example.com/") is not None
    # one private record among public ones is enough to refuse
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda host, port: [(socket.AF_INET, socket.SOCK_STREAM,
                                             6, "", ("93.184.216.34", 0)),
                                            (socket.AF_INET6, socket.SOCK_STREAM,
                                             6, "", ("::1", 0, 0, 0))])
    assert _private_target_error("https://example.com/") is not None
    # resolution failure is a refusal, not a pass
    def _boom(host, port):
        raise OSError("no dns")
    monkeypatch.setattr(socket, "getaddrinfo", _boom)
    assert "cannot resolve" in _private_target_error("https://example.com/")


def test_start_accepts_loopback_by_default():
    """Default mode must keep crawling local dev servers: no --block-private,
    a loopback target passes validation (the ingest itself may still fail)."""
    srv = serve_hub(LiveHub(), port=0)
    srv.start()
    try:
        with _post(srv.url, "/api/start", {"target": "http://127.0.0.1:1/",
                                            "scene": "planets"}) as resp:
            assert resp.status == 200
    finally:
        srv.stop()


def test_start_block_private_refuses_private_targets():
    srv = serve_hub(LiveHub(block_private=True), port=0)
    srv.start()
    try:
        for target in ("http://127.0.0.1:9000/", "http://10.9.8.7/",
                       "http://192.168.1.1/", "http://nas.local/"):
            with pytest.raises(urllib.error.HTTPError) as err:
                _post(srv.url, "/api/start", {"target": target,
                                               "scene": "planets"})
            assert err.value.code == 400
            body = json.loads(err.value.read())
            assert body["ok"] is False
            assert "block-private" in body["error"]
        # and a public-looking name still passes validation
        import socket
        orig = socket.getaddrinfo

        def _fake(host, port, *rest):
            if host == "example.com":  # the target, not the POST's loopback
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "",
                         ("93.184.216.34", 0))]
            return orig(host, port, *rest)

        socket.getaddrinfo = _fake
        try:
            with _post(srv.url, "/api/start", {"target": "https://example.com",
                                                "scene": "planets"}) as resp:
                assert resp.status == 200
        finally:
            socket.getaddrinfo = orig
    finally:
        srv.stop()
