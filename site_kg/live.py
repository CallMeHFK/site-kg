"""Loopback live delivery: ingest a target, stream events over SSE, serve snapshots.

The server binds 127.0.0.1 only, validates Host against loopback names, and
exposes a fixed route allowlist. It never serves corpus files: ``/`` is the
assembled viewer shell, every other route is JSON or SSE. No CORS headers are
sent, so a browser page from another origin cannot read the stream.

Event order is authoritative: ``EventLog`` assigns seq numbers, ``LiveState``
folds them into the queryable graph, and ``LiveLayout`` places nodes as they
arrive. ``/snapshot`` is derived from both under one lock so a reconnect never
sees a node without its edge or a stale position.
"""
from __future__ import annotations

import asyncio
import hmac
import ipaddress
import json
import queue
import secrets
import socket
import tempfile
import threading
import time
import urllib.parse
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import graph as graphmod
from . import ingest as ingestmod
from . import local as localmod
from . import manifest, store
from .events import EventLog, LiveState
from .layout import LiveLayout
from .viewer import SCENES, render_viewer, trim_graph

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
MAX_BODY = 256 * 1024
HEARTBEAT_S = 15.0
LAYOUT_BATCH = 512
# Hard ceilings: a single unresponsive host must not wedge the run forever.
DISCOVERY_TIMEOUT_S = 180.0
FETCH_TIMEOUT_S = 900.0
_STATE_ALIASES = {"c": "cluster", "g": "doc", "r": "rank"}


def _is_http(target: str) -> bool:
    scheme = urllib.parse.urlsplit(target).scheme.lower()
    return scheme in ("http", "https")


_INTERNAL_TLDS = (".local", ".internal")


def _addr_is_blocked(addr: str) -> bool:
    """True when an IP literal/resolved address must be refused under
    --block-private: loopback, private ranges, link-local, unspecified."""
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return True  # not an address we can vouch for
    return (ip.is_loopback or ip.is_private or ip.is_link_local
            or ip.is_unspecified or ip.is_reserved or ip.is_multicast)


def _private_target_error(target: str) -> str | None:
    """Rejection reason for an http(s) target that points at the private
    network, or None when it may pass. Only consulted when --block-private is
    on: by default local dev servers and intranets are first-class targets.
    Domains are resolved and every record must be public; resolution failure
    counts as blocked so DNS cannot smuggle a private address through."""
    host = urllib.parse.urlsplit(target).hostname
    if not host:
        return ("target has no host; blocked by --block-private")
    host = host.rstrip(".").lower()
    if host.endswith(_INTERNAL_TLDS):
        return (f"target host {host} is an internal name (.local/.internal); "
                "blocked by --block-private")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass  # a name: resolve it below
    else:
        if _addr_is_blocked(host):
            return (f"target host {host} is a private/loopback address; "
                    "blocked by --block-private")
        return None
    try:
        records = socket.getaddrinfo(host, None)
    except OSError:
        return (f"cannot resolve host {host}; blocked by --block-private")
    for record in records:
        addr = record[4][0]
        # strip IPv6 scope/zone ids ("fe80::1%eth0") before judging
        if _addr_is_blocked(addr.split("%", 1)[0]):
            return (f"target host {host} resolves to a private/loopback "
                    "address; blocked by --block-private")
    return None


def _state_event(ev: dict) -> dict:
    """Wire nodes use graph field names (c/g/r); LiveState was written with
    the long names, so translate at the boundary instead of duplicating the
    canonical schema."""
    out = ev
    for short, long in _STATE_ALIASES.items():
        if short in ev and long not in ev:
            if out is ev:
                out = dict(ev)
            out[long] = ev[short]
    return out


class LiveHub:
    def __init__(self, *, site_id: str = "", scene: str = "planets",
                 max_nodes: int | None = None, block_private: bool = False):
        if scene not in SCENES:
            raise ValueError(f"unknown scene {scene!r}; choose from {', '.join(SCENES)}")
        self.site_id = site_id
        self.max_nodes = max_nodes
        self.block_private = bool(block_private)
        self.log = EventLog()
        self.state = LiveState()
        self.layout = LiveLayout()
        self.run = {"run_id": "", "target": "", "scene": scene, "state": "idle",
                    "site_id": site_id, "started_at": None, "finished_at": None,
                    "error": None}
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._closed = False

    # -- state -----------------------------------------------------------

    def emit(self, ev: dict) -> dict | None:
        if self._closed:
            return None
        with self._lock:
            if self._closed:
                return None
            try:
                out = self.log.append(ev)
            except RuntimeError:
                return None
            if out is not None:
                self.state.apply(_state_event(out))
                self.layout.apply([out])
            return out

    def health(self) -> dict:
        with self._lock:
            return {"ok": True, "run_id": self.run.get("run_id"),
                    "last_seq": self.log.last_seq, "state": self.run.get("state"),
                    "scene": self.run.get("scene"), "error": self.run.get("error"),
                    "nodes": len(self.state.nodes), "edges": len(self.state.edges)}

    def snapshot(self) -> dict:
        with self._lock:
            rows = []
            for nid, index in sorted(self.layout.index.items(), key=lambda kv: kv[1]):
                pos = self.layout.positions.get(nid)
                if pos is not None:
                    rows.append({"i": index, "x": pos[0], "y": pos[1], "z": pos[2]})
            return self.state.snapshot({"nodes": rows}, self.run)

    def _row(self, node_id: str) -> dict | None:
        with self._lock:
            index = self.layout.index.get(node_id)
            pos = self.layout.positions.get(node_id)
        if index is None or pos is None:
            return None
        return {"i": index, "x": pos[0], "y": pos[1], "z": pos[2]}

    # -- runs ------------------------------------------------------------

    def _begin(self, run_id: str, target: str, scene: str, site_id: str = "") -> None:
        old = self.log
        self.log = EventLog()
        self.state = LiveState()
        self.layout = LiveLayout()
        self.run.update({"run_id": run_id, "target": target, "scene": scene,
                         "state": "running", "started_at": time.time(),
                         "finished_at": None, "error": None})
        if site_id:
            self.site_id = site_id
        old.close()

    def start(self, target: str, scene: str | None = None, local: bool = False) -> str:
        scene = scene or self.run["scene"]
        if scene not in SCENES:
            raise ValueError(f"unknown scene {scene!r}; choose from {', '.join(SCENES)}")
        target = str(target or "").strip()
        if not target:
            raise ValueError("target is required")
        if local:
            root = Path(target).expanduser()
            if not root.is_dir():
                raise ValueError(f"not a directory: {target}")
            target = str(root.resolve())
        elif not _is_http(target):
            raise ValueError("target must be an http(s) URL")
        elif self.block_private:
            reason = _private_target_error(target)
            if reason:
                raise ValueError(reason)
        with self._lock:
            if self.run["state"] == "running":
                raise RuntimeError("a live run is already in progress")
            run_id = "live-" + secrets.token_hex(8)
            site_id = store.site_id_for(
                target if not local else f"file://{target}")
            self._begin(run_id, target, scene, site_id)
            thread = threading.Thread(target=self._run, args=(target, scene, local),
                                      name=f"site-kg-live-{run_id}", daemon=True)
            self._thread = thread
            thread.start()
        return run_id

    def replay(self, site_id: str | None = None, scene: str | None = None) -> str:
        site_id = site_id or self.site_id
        if not site_id:
            raise ValueError("no stored site to replay")
        scene = scene or self.run["scene"]
        if scene not in SCENES:
            raise ValueError(f"unknown scene {scene!r}; choose from {', '.join(SCENES)}")
        try:
            graph = store.load(site_id, "graph")
            meta = store.load(site_id, "meta")
        except KeyError as exc:
            raise ValueError(f"no stored site {site_id!r}") from exc
        with self._lock:
            if self.run["state"] == "running":
                raise RuntimeError("a live run is already in progress")
            run_id = "replay-" + secrets.token_hex(8)
            self._begin(run_id, str(meta.get("url") or site_id), scene, site_id)
            thread = threading.Thread(target=self._replay_run, args=(graph,),
                                      name=f"site-kg-replay-{run_id}", daemon=True)
            self._thread = thread
            thread.start()
        return run_id

    def _run(self, target: str, scene: str, local: bool) -> None:
        try:
            self.emit({"t": "run_start", "run_id": self.run.get("run_id"),
                       "target": self.run.get("target"), "scene": self.run.get("scene")})
            built = self._ingest_local(target) if local else self._ingest_remote(target)
            if self._closed:
                return
            # nodes streamed during ingest, so max_nodes only trims replay
            if self._publish(built["graph"]):
                self._finish("complete")
        except Exception as exc:  # surfaced to clients as an error event
            self._fail(exc)

    def _replay_run(self, graph: dict) -> None:
        try:
            self.emit({"t": "run_start", "run_id": self.run.get("run_id"),
                       "target": self.run.get("target"), "scene": self.run.get("scene")})
            if self.max_nodes:
                graph = trim_graph(graph, self.max_nodes)
            if self._stream_graph(graph):
                self._finish("complete")
        except Exception as exc:
            self._fail(exc)

    def _finish(self, state: str) -> None:
        with self._lock:
            self.run["state"] = state
            self.run["finished_at"] = time.time()

    def _fail(self, exc: Exception) -> None:
        message = str(exc) or exc.__class__.__name__
        with self._lock:
            self.run["state"] = "failed"
            self.run["error"] = message
            self.run["finished_at"] = time.time()
        self.emit({"t": "error", "error": message})

    # -- ingest ----------------------------------------------------------

    def _ingest_remote(self, target: str) -> dict:
        fetched = 0
        nbytes = 0
        announced: set[str] = set()
        seen_pids: set[str] = set()
        seen_edges: set[tuple[str, str]] = set()
        node_event = self._node_emitter(announced)

        def chapter_of(pid: str) -> str:
            return pid.split("_", 1)[0] if "_" in pid else "_root"

        def edge_kind(src: str) -> str:
            hub = src == "index" or src.endswith("_index")
            return ("hub" if src == "index" else "contains") if hub else "ref"

        def observe(url: str, html: str) -> None:
            """Turn a page that already landed into graph elements now, so the
            scene grows while the crawl is still running instead of exploding
            into existence at settle time."""
            try:
                pid, title, links = ingestmod.page_meta(html, url, target)
            except Exception:
                # exotic markup must not abort a crawl that would otherwise
                # succeed; still show the node, just without resolved links
                fallback = url.rstrip("/").rsplit("/", 1)[-1] or url
                try:
                    pid = manifest.page_id(target, url)
                except Exception:
                    return
                title, links = fallback, []
            if pid in seen_pids:
                return
            seen_pids.add(pid)
            node_event({"t": "node_added", "id": pid, "title": title,
                        "url": url, "c": chapter_of(pid)})
            kind = edge_kind(pid)
            for dst in links:
                if dst == pid or dst not in announced:
                    continue
                key = (pid, dst)
                if key in seen_edges:
                    continue
                seen_edges.add(key)
                self.emit({"t": "edge_added", "s": pid, "d": dst, "kind": kind})

        def on_page(url: str, page: str | None, reason: str | None) -> None:
            nonlocal fetched, nbytes
            if page is None:
                self.emit({"t": "node_skipped", "url": url,
                           "reason": reason or "skip"})
                return
            observe(url, page)
            fetched += 1
            nbytes += len(page.encode("utf-8", "ignore"))
            self.emit({"t": "progress", "pages": fetched, "bytes": nbytes})

        def on_discover(found: int, depth: int, failed: int) -> None:
            self.emit({"t": "discover", "found": int(found),
                       "failed": int(failed), "depth": int(depth)})

        async def crawl():
            try:
                urls, _source, _inv = await asyncio.wait_for(
                    manifest.page_inventory(target, max_pages=200, max_depth=4,
                                            on_discover=on_discover,
                                            on_page_found=observe),
                    timeout=DISCOVERY_TIMEOUT_S)
            except TimeoutError:
                raise RuntimeError(
                    f"discovery timed out after {DISCOVERY_TIMEOUT_S:g}s") from None
            if not urls:
                raise RuntimeError("no pages discovered")
            try:
                return await asyncio.wait_for(
                    ingestmod.fetch_pages(urls, target, concurrency=4,
                                          on_page=on_page),
                    timeout=FETCH_TIMEOUT_S)
            except TimeoutError:
                raise RuntimeError(
                    f"fetch timed out after {FETCH_TIMEOUT_S:g}s") from None

        pages, stats = asyncio.run(crawl())
        if not pages:
            if stats.get("robots_blocked"):
                raise RuntimeError("blocked by robots.txt")
            raise RuntimeError("all fetches failed")
        site_id = store.site_id_for(target)
        corpus = store.site_dir(site_id) / "corpus"
        store.reset_corpus(corpus)
        # every page already streamed in on_page/on_page_found, so the corpus
        # write must not replay the same node_added burst
        ingestmod.write_corpus(pages, target, corpus)
        built = graphmod.build_graph(corpus, events=self.emit, announced=announced)
        store.save_site(site_id, target, built)
        return built

    def _ingest_local(self, target: str) -> dict:
        root = Path(target)
        source = f"file://{root}"
        site_id = store.site_id_for(source)
        corpus = store.site_dir(site_id) / "corpus"
        store.reset_corpus(corpus)
        announced: set[str] = set()
        stats = localmod.build_local_corpus(root, corpus, max_files=2000,
                                            events=self._node_emitter(announced))
        if not stats.get("files"):
            raise RuntimeError("no usable doc/code files found")
        self.emit({"t": "progress", "pages": int(stats.get("files", 0))})
        built = graphmod.build_graph(corpus, events=self.emit, announced=announced)
        store.save_site(site_id, source, built)
        return built

    # -- streaming -------------------------------------------------------

    def _node_emitter(self, announced: set[str]):
        """Emit node events during corpus materialization, batching the
        server-authoritative positions so the scene fills in while ingesting.

        A full batch is 512 rows, but a small site never reaches it: without a
        time-based flush every streamed node would sit at the origin until the
        final settle. Flush on a short interval instead."""
        rows: list[dict] = []
        last = [0.0]  # 0 means "flush the first row immediately"

        def flush() -> None:
            if rows:
                self.emit({"t": "layout_patch", "rows": list(rows)})
                rows.clear()
            last[0] = time.monotonic()

        def emit_node(ev: dict) -> None:
            if self.emit(ev) is None:
                return
            if ev.get("t") != "node_added":
                return
            announced.add(str(ev.get("id")))
            row = self._row(str(ev.get("id")))
            if row is not None:
                rows.append(row)
                if (len(rows) >= LAYOUT_BATCH
                        or time.monotonic() - last[0] >= 0.15):
                    flush()

        return emit_node

    def _publish(self, graph: dict) -> bool:
        self.layout.settle(graph)
        with self._lock:
            final = []
            for nid, index in sorted(self.layout.index.items(), key=lambda kv: kv[1]):
                pos = self.layout.positions.get(nid)
                if pos is not None:
                    final.append({"i": index, "x": pos[0], "y": pos[1], "z": pos[2]})
        if final:
            self.emit({"t": "layout_patch", "rows": final, "final": 1})
        self.emit({"t": "settled", "nodes": len(graph["nodes"]),
                   "edges": len(graph["edges"])})
        return not self._closed

    def _stream_graph(self, graph: dict, *, pages: int | None = None,
                      nbytes: int = 0) -> bool:
        nodes, edges = graph["nodes"], graph["edges"]
        pages = pages if pages is not None else len(nodes)
        rows: list[dict] = []
        for node in nodes:
            if self._closed:
                return False
            self.emit({"t": "node_added", "id": node["i"],
                       "title": node.get("t", ""), "url": node.get("u", ""),
                       "c": node.get("c", ""), "g": node.get("g", 0),
                       "r": node.get("r", 0.0)})
            row = self._row(str(node["i"]))
            if row is not None:
                rows.append(row)
            if len(rows) >= LAYOUT_BATCH:
                self.emit({"t": "layout_patch", "rows": rows})
                rows = []
        for edge in edges:
            if self._closed:
                return False
            self.emit({"t": "edge_added", "s": edge["s"], "d": edge["t"],
                       "kind": edge.get("k", "ref")})
        if rows:
            self.emit({"t": "layout_patch", "rows": rows})
        self.emit({"t": "progress", "pages": pages, "nodes": len(nodes),
                   "edges": len(edges), "bytes": nbytes})
        self.emit({"t": "ingest_done", "nodes": len(nodes), "edges": len(edges),
                   "pages": pages})
        return self._publish(graph)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self.log.close()


@dataclass
class LiveHandle:
    url: str
    port: int
    hub: LiveHub
    _server: LiveServer

    @property
    def run_id(self) -> str:
        return str(self.hub.run.get("run_id") or "")

    @property
    def last_seq(self) -> int:
        return self.hub.log.last_seq

    @property
    def state(self) -> str:
        return str(self.hub.run.get("state") or "idle")

    def close(self) -> None:
        self._server.stop()


class LiveServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    allow_reuse_address = True

    def __init__(self, hub: LiveHub, *, port: int = 0, token: str | None = None,
                 scene: str | None = None, connect: bool = False,
                 title: str = ""):
        self.hub = hub
        self.token = token
        self.scene = scene or str(hub.run.get("scene") or "planets")
        self.title = title or str(hub.run.get("target") or hub.site_id or "site-kg live")
        self.connect = connect
        self._page: str | None = None
        self._thread: threading.Thread | None = None
        self._stopped = False
        super().__init__(("127.0.0.1", port), _Handler)
        self.url = f"http://127.0.0.1:{self.server_port}"

    @property
    def port(self) -> int:
        return self.server_port

    def page(self) -> str:
        if self._page is None:
            with tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp) / "live.html"
                render_viewer({"nodes": [], "edges": []},
                              {"url": self.title,
                               "live_url": self.url if self.connect else ""},
                              out, scene=self.scene, mode="live")
                self._page = out.read_text(encoding="utf-8")
        return self._page

    def start(self) -> LiveServer:
        if self._thread is None:
            self._thread = threading.Thread(target=self.serve_forever,
                                            name="site-kg-live-http", daemon=True)
            self._thread.start()
        return self

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        if self._thread is not None:
            self.shutdown()
        self.server_close()
        self.hub.close()


class _Handler(BaseHTTPRequestHandler):
    server: LiveServer

    def log_message(self, format: str, *args) -> None:
        pass

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    # -- routing ---------------------------------------------------------

    def _dispatch(self, method: str) -> None:
        try:
            parsed = urllib.parse.urlsplit(self.path)
        except ValueError:
            return self._json(400, {"ok": False, "error": "bad request path"})
        path = urllib.parse.unquote(parsed.path)
        if not self._host_ok():
            return self._json(403, {"ok": False, "error": "non-loopback Host"})
        if _unsafe_path(path):
            return self._json(404, {"ok": False, "error": "not found"})
        query = urllib.parse.parse_qs(parsed.query)
        if path not in ("/", "/index.html") and not self._authorized(query):
            return self._json(401, {"ok": False, "error": "token required"})
        if method == "GET":
            if path in ("/", "/index.html"):
                return self._page()
            if path == "/health":
                return self._json(200, self.server.hub.health())
            if path == "/snapshot":
                return self._json(200, self.server.hub.snapshot())
            if path == "/events":
                return self._events(query)
        else:
            if path == "/api/start":
                return self._api_start()
            if path == "/api/replay":
                return self._api_replay()
        return self._json(404, {"ok": False, "error": "not found"})

    def _host_ok(self) -> bool:
        host = self.headers.get("Host")
        if not host:
            return True  # HTTP/1.0 clients; loopback bind still applies
        try:
            name = urllib.parse.urlsplit("//" + host).hostname
        except ValueError:
            return False
        return name in LOOPBACK_HOSTS

    def _authorized(self, query: dict) -> bool:
        if not self.server.token:
            return True
        supplied = (query.get("token") or [""])[0] or self.headers.get(
            "X-Site-KG-Token", "")
        return hmac.compare_digest(str(supplied), str(self.server.token))

    # -- responses -------------------------------------------------------

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _page(self) -> None:
        body = self.server.page().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _events(self, query: dict) -> None:
        raw = (query.get("since") or ["0"])[0]
        try:
            since = max(0, int(raw))
        except (TypeError, ValueError):
            return self._json(400, {"ok": False, "error": "since must be an integer"})
        log = self.server.hub.log
        try:
            sub, backlog = log.subscribe(since)
        except RuntimeError:
            return self._json(503, {"ok": False, "error": "event log is closed"})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        try:
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            for ev in backlog:
                self._sse(ev)
            idle = time.monotonic()
            while True:
                try:
                    ev = sub.get(timeout=1.0)
                except queue.Empty:
                    if time.monotonic() - idle >= HEARTBEAT_S:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        idle = time.monotonic()
                    continue
                if ev is None:
                    break
                self._sse(ev)
                idle = time.monotonic()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            log.unsubscribe(sub)

    def _sse(self, ev: dict) -> None:
        data = json.dumps(ev, ensure_ascii=False, separators=(",", ":"))
        self.wfile.write(f"id: {int(ev.get('seq', 0))}\ndata: {data}\n\n".encode())
        self.wfile.flush()

    # -- APIs ------------------------------------------------------------

    def _read_json(self) -> dict | None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._json(400, {"ok": False, "error": "bad Content-Length"})
            return None
        if length < 0 or length > MAX_BODY:
            self._json(413, {"ok": False, "error": "request body too large"})
            return None
        if length == 0:
            return {}
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            self._json(415, {"ok": False, "error": "Content-Type must be application/json"})
            return None
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._json(400, {"ok": False, "error": "invalid JSON"})
            return None
        if not isinstance(payload, dict):
            self._json(400, {"ok": False, "error": "JSON body must be an object"})
            return None
        return payload

    def _api_start(self) -> None:
        payload = self._read_json()
        if payload is None:
            return
        target = payload.get("target")
        scene = payload.get("scene") or self.server.hub.run.get("scene")
        if not isinstance(target, str) or not target.strip():
            return self._json(400, {"ok": False, "error": "target is required"})
        try:
            run_id = self.server.hub.start(target.strip(), str(scene))
        except ValueError as exc:
            return self._json(400, {"ok": False, "error": str(exc)})
        except RuntimeError as exc:
            return self._json(409, {"ok": False, "error": str(exc)})
        self._json(200, {"ok": True, "run_id": run_id, "scene": scene,
                         "last_seq": self.server.hub.log.last_seq})

    def _api_replay(self) -> None:
        payload = self._read_json()
        if payload is None:
            return
        scene = payload.get("scene") or self.server.hub.run.get("scene")
        try:
            run_id = self.server.hub.replay(self.server.hub.site_id, str(scene))
        except ValueError as exc:
            return self._json(400, {"ok": False, "error": str(exc)})
        except RuntimeError as exc:
            return self._json(409, {"ok": False, "error": str(exc)})
        self._json(200, {"ok": True, "run_id": run_id, "scene": scene,
                         "last_seq": self.server.hub.log.last_seq})


def _unsafe_path(path: str) -> bool:
    if "\x00" in path or "\\" in path:
        return True
    return ".." in path.split("/")


def serve_hub(hub: LiveHub, *, port: int = 0, token: str | None = None,
              scene: str | None = None, connect: bool = False,
              title: str = "") -> LiveServer:
    return LiveServer(hub, port=port, token=token, scene=scene, connect=connect,
                      title=title)


def serve_live(site_id: str, *, source: str | None = None, scene: str = "planets",
               max_nodes: int | None = None, port: int = 0, replay: bool = False,
               token: str | None = None, title: str | None = None,
               block_private: bool = False) -> LiveHandle:
    if scene not in SCENES:
        raise ValueError(f"unknown scene {scene!r}; choose from {', '.join(SCENES)}")
    hub = LiveHub(site_id=site_id, scene=scene, max_nodes=max_nodes,
                  block_private=block_private)
    server = serve_hub(hub, port=port, token=token, scene=scene,
                       connect=bool(source or replay), title=title or "")
    server.start()
    handle = LiveHandle(url=server.url, port=server.port, hub=hub, _server=server)
    try:
        if replay:
            hub.replay(site_id, scene)
        elif source:
            hub.start(source, scene, local=not _is_http(str(source)))
    except Exception:
        handle.close()
        raise
    return handle
