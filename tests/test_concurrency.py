"""Concurrency & storage batch (issue #30): win32-safe atomic writes,
per-site ingest exclusion, bounded SSE subscriber queues, handler timeouts.

Offline: threads and tmp paths only, no network.
"""
from __future__ import annotations

import os
import queue
import threading
import time

import pytest

from site_kg import events, store
from site_kg.live import _Handler


# ---- _write_json survives a reader holding the destination open ------------
def test_write_json_survives_a_held_reader(tmp_path):
    # win32: os.replace raises PermissionError while another handle holds the
    # destination open for reading; the bounded backoff must bridge it (measured
    # ~55% of save attempts without the retry, see issue #30)
    dest = tmp_path / "meta.json"
    store._write_json(dest, {"x": 1})
    release = threading.Event()

    def hold():
        with open(dest, "rb") as fh:  # no FILE_SHARE_DELETE: blocks replace
            fh.read(1)
            release.wait(5.0)

    t = threading.Thread(target=hold)
    t.start()
    try:
        threading.Timer(0.08, release.set).start()  # reader lives ~80ms
        store._write_json(dest, {"x": 2})           # must not raise
    finally:
        release.set()
        t.join()
    import json

    assert json.loads(dest.read_text()) == {"x": 2}


# ---- site_lock: serialized threads, stale lock stealing --------------------
def test_site_lock_serializes_threads(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "DATA", tmp_path / "sites")
    active = 0
    conflicts = []
    guard = threading.Lock()

    def work():
        nonlocal active
        with store.site_lock("t-12345678"):
            with guard:
                active += 1
                if active > 1:
                    conflicts.append(active)
            time.sleep(0.1)
            with guard:
                active -= 1

    with store.site_lock("t-12345678"):  # outer hold must block both workers
        ts = [threading.Thread(target=work) for _ in range(2)]
        [t.start() for t in ts]
        time.sleep(0.05)
    [t.join() for t in ts]
    assert conflicts == []          # never two critical sections at once
    assert len(ts) == 2             # both workers ran after the outer release


def test_site_lock_steals_a_stale_lock_file(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "DATA", tmp_path / "sites")
    lock = store._lock_dir()
    lock.mkdir(parents=True, exist_ok=True)
    stale = lock / "t-87654321.lock"
    stale.write_text("99999")
    old = time.time() - 3600.0
    os.utime(stale, ns=(int(old * 1e9), int(old * 1e9)))  # crashed hour ago

    with store.site_lock("t-87654321", stale_after=120.0):
        pass
    assert not stale.exists()  # acquired (stolen) and cleaned up


def test_site_lock_covers_isolated_data(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "DATA", tmp_path / "sites")
    with store.site_lock("t-abcabcab"):
        assert (tmp_path / ".locks" / "t-abcabcab.lock").exists()
    assert store._lock_dir() == tmp_path / ".locks"


# ---- EventLog: bounded subscribers, lag markers, non-blocking close --------
def _drain_until_lag(q: queue.Queue) -> int:
    n = 0
    while not q.empty():
        n += 1
        if q.get_nowait().get("t") == "lagged":
            return n
    return -1


def test_slow_subscriber_gets_a_lag_marker_not_unbounded_growth():
    log = events.EventLog()
    q, _backlog = log.subscribe(0)
    for i in range(events.SUBSCRIBER_MAX_BACKLOG + 64):
        log.append({"t": "progress", "pages": i})
    # the queue is bounded, and the lag marker sits at its front: the consumer
    # learns it fell behind instead of the log growing without bound
    assert q.qsize() <= events.SUBSCRIBER_MAX_BACKLOG
    assert _drain_until_lag(q) >= 1
    log.unsubscribe(q)


def test_more_appends_after_a_lag_stay_bounded():
    log = events.EventLog()
    q, _backlog = log.subscribe(0)
    for i in range(events.SUBSCRIBER_MAX_BACKLOG * 3):
        log.append({"t": "progress", "pages": i})
    assert q.qsize() <= events.SUBSCRIBER_MAX_BACKLOG
    assert _drain_until_lag(q) >= 1
    log.unsubscribe(q)


def test_close_returns_promptly_with_a_full_queue():
    # close() used q.put(None): against a full bounded queue that would block
    # forever while holding the log lock
    log = events.EventLog()
    q, _ = log.subscribe(0)
    for i in range(events.SUBSCRIBER_MAX_BACKLOG):
        log.append({"t": "progress", "pages": i})
    assert q.full()
    done = threading.Event()

    def closer():
        log.close()
        done.set()

    t = threading.Thread(target=closer)
    t.start()
    assert done.wait(2.0), "close() blocked on a full subscriber queue"
    t.join()
    assert q.get_nowait()["t"] == "lagged"


def test_fast_subscriber_still_receives_every_event():
    log = events.EventLog()
    q, _ = log.subscribe(0)
    for i in range(10):
        log.append({"t": "progress", "pages": i})
    got = [q.get_nowait() for _ in range(10)]
    assert [g["pages"] for g in got] == list(range(10))
    assert all(g["t"] != "lagged" for g in got)
    log.close()


# ---- live handler declares a socket timeout --------------------------------
def test_handler_declares_a_socket_timeout():
    # a stalled SSE client must free its thread instead of pinning it forever
    assert _Handler.timeout == 60


def test_write_json_cleans_up_tmp_on_permanent_denial(tmp_path, monkeypatch):
    # if the retry budget is exhausted the pid-scoped scratch file must not
    # be orphaned next to the store
    dest = tmp_path / "graph.json"
    dest.write_text("{}")

    def always_denied(*_a, **_k):
        raise PermissionError(13, "denied")

    monkeypatch.setattr(store.os, "replace", always_denied)
    with pytest.raises(PermissionError):
        store._write_json(dest, {"x": 1})
    leftovers = [p.name for p in tmp_path.glob(".*.tmp")]
    assert leftovers == []


def test_site_lock_excludes_a_second_process_and_beats(monkeypatch, tmp_path):
    # cross-process: a child holds the lock PAST stale_after; the heartbeat
    # keeps it looking alive, so the parent must wait for the real release
    # instead of stealing (the pre-heartbeat code stole it -- issue #30)
    import subprocess
    import sys

    monkeypatch.setattr(store, "DATA", tmp_path / "sites")
    child = (
        "import sys, time\n"
        "sys.path.insert(0, '.')\n"
        "from pathlib import Path\n"
        "from site_kg import store\n"
        f"store.DATA = Path(r'{tmp_path}') / 'sites'\n"
        "with store.site_lock('t-cafebeef', stale_after=0.5):\n"
        "    print('held', flush=True)\n"
        "    time.sleep(1.4)\n"
    )
    p = subprocess.Popen([sys.executable, "-c", child],
                         stdout=subprocess.PIPE, text=True)
    try:
        assert p.stdout.readline().strip() == "held"
        t0 = time.monotonic()
        with store.site_lock("t-cafebeef", stale_after=0.5):
            waited = time.monotonic() - t0
        assert waited >= 1.0  # waited out the child's hold: never stole it
    finally:
        assert p.wait(timeout=10) == 0
        assert not (tmp_path / ".locks" / "t-cafebeef.lock").exists()
