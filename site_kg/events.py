"""Append-only event log and live graph state for the live delivery mode."""

from __future__ import annotations

import contextlib
import json
import queue
import threading
from pathlib import Path


class EventLog:
    def __init__(self, path: Path | None = None):
        self.path = path
        self._lock = threading.Lock()
        self._seq = 0
        self._seen_nodes: set[str] = set()
        self._seen_edges: set[tuple[str, str, str]] = set()
        self._subscribers: list[queue.Queue] = []
        self._closed = False
        self._events: list[dict] = []
        self._fh = None
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = path.open("a", encoding="utf-8", buffering=1)

    @property
    def last_seq(self) -> int:
        with self._lock:
            return self._seq

    def append(self, ev: dict) -> dict | None:
        with self._lock:
            if self._closed:
                raise RuntimeError("event log is closed")
            kind = ev.get("t")
            if kind == "node_added":
                node_id = ev.get("id")
                if node_id in self._seen_nodes:
                    ev = dict(ev)
                    ev["t"] = "node_updated"
                else:
                    self._seen_nodes.add(node_id)
            elif kind == "edge_added":
                key = (ev.get("s"), ev.get("d"), ev.get("kind") or "ref")
                if key in self._seen_edges:
                    return None
                self._seen_edges.add(key)
            self._seq += 1
            out = dict(ev)
            out["seq"] = self._seq
            line = json.dumps(out, separators=(",", ":"), ensure_ascii=False)
            if self._fh is not None:
                self._fh.write(line + "\n")
                self._fh.flush()
            else:
                self._events.append(out)
            for q in self._subscribers:
                q.put(out)
            return out

    def subscribe(self, since: int) -> tuple[queue.Queue, list[dict]]:
        with self._lock:
            if self._closed:
                raise RuntimeError("event log is closed")
            q: queue.Queue = queue.Queue()
            self._subscribers.append(q)
            hi = self._seq
            backlog: list[dict] = []
            if since < hi:
                if self._fh is not None:
                    for raw in self.path.read_text(encoding="utf-8").splitlines():
                        if not raw:
                            continue
                        ev = json.loads(raw)
                        if since < ev["seq"] <= hi:
                            backlog.append(ev)
                else:
                    backlog = [e for e in self._events if since < e["seq"] <= hi]
            return q, backlog

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock, contextlib.suppress(ValueError):
            self._subscribers.remove(q)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._fh is not None:
                self._fh.close()
                self._fh = None
            for q in self._subscribers:
                q.put(None)
            self._subscribers.clear()


class LiveState:
    def __init__(self):
        self.nodes: dict[str, dict] = {}
        self.edges: dict[tuple[str, str, str], dict] = {}
        self.pending: dict[str, int] = {}
        self.dangling = 0
        self.progress: dict = {"pages": 0, "nodes": 0, "edges": 0, "bytes": 0}
        self.done: dict | None = None
        self.settled: dict | None = None
        self.last_seq = 0

    def apply(self, ev: dict) -> dict | None:
        kind = ev.get("t")
        self.last_seq = ev.get("seq", self.last_seq)
        if kind in ("node_added", "node_updated"):
            node_id = ev.get("id")
            node = {"i": node_id}
            for src, dst in (("title", "t"), ("cluster", "c"), ("url", "u"), ("doc", "g"),
                             ("rank", "r")):
                if src in ev:
                    node[dst] = ev[src]
            if kind == "node_added":
                self.nodes[node_id] = node
                if node_id in self.pending:
                    self.pending[node_id] = 0
                return None
            existing = self.nodes.get(node_id)
            if existing is None:
                self.nodes[node_id] = node
                if node_id in self.pending:
                    self.pending[node_id] = 0
            else:
                existing.update(node)
            return ev
        if kind == "edge_added":
            key = (ev.get("s"), ev.get("d"), ev.get("kind") or "ref")
            edge = {"s": ev.get("s"), "t": ev.get("d"), "k": ev.get("kind") or "ref"}
            if ev.get("late"):
                edge["late"] = 1
            if ev.get("why"):
                edge["why"] = ev["why"]
            self.edges[key] = edge
            target = edge["t"]
            if target not in self.nodes:
                self.pending[target] = self.pending.get(target, 0) + 1
            return ev
        if kind == "progress":
            for field in ("pages", "nodes", "edges", "bytes"):
                if field in ev:
                    self.progress[field] = ev[field]
            return ev
        if kind == "ingest_done":
            self.done = ev
            self.dangling = sum(1 for n in self.pending.values() if n > 0)
            return ev
        if kind == "settled":
            self.settled = ev
            return ev
        return ev

    def snapshot(self, layout: dict, run: dict) -> dict:
        return {
            "run_id": run.get("run_id"),
            "last_seq": self.last_seq,
            "nodes": list(self.nodes.values()),
            "edges": list(self.edges.values()),
            "layout": layout,
            "progress": dict(self.progress),
            "dangling": self.dangling,
        }
