"""Node smoke tests for the live viewer client (no browser, no DOM)."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "site_kg" / "viewer" / "core.js"
RENDER = ROOT / "site_kg" / "viewer" / "render.js"
GPU = ROOT / "site_kg" / "viewer" / "gpu.js"
DIRECTOR = ROOT / "site_kg" / "viewer" / "director.js"
LAUNCHER = ROOT / "site_kg" / "viewer" / "launcher.js"
PLANETS = ROOT / "site_kg" / "viewer" / "scenes" / "planets.js"
NEURAL = ROOT / "site_kg" / "viewer" / "scenes" / "neural.js"
UI = ROOT / "site_kg" / "viewer" / "ui.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not installed")


def _run(js: str) -> str:
    proc = subprocess.run(["node", "-e", js], capture_output=True, text=True,
                          timeout=60, cwd=str(ROOT))
    assert proc.returncode == 0, proc.stderr + "\n" + proc.stdout
    return proc.stdout


def _prelude(*modules: Path) -> str:
    lines = ['"use strict";',
             'const assert = require("node:assert/strict");',
             "global.window = {};"]
    lines += [f"require({json.dumps(str(path))});" for path in modules]
    lines.append("const SK = global.window.SK;")
    return "\n".join(lines)


def test_pending_edge_bridges_exactly_once():
    _run(_prelude(CORE) + """
const g = new SK.LiveGraph(4);
assert.equal(g.edgeAdded("child", "root", "ref"), -1);
assert.equal(g.m, 0);
assert.equal(g.pendingCount, 1);
assert.equal(g.dangling, 1);
g.nodeAdded("root", {t: "Root"});
assert.equal(g.m, 0);
assert.equal(g.pendingCount, 1);
g.nodeAdded("child", {t: "Child"});
assert.equal(g.m, 1);
assert.equal(g.pendingCount, 0);
assert.equal(g.dangling, 0);
assert.equal(g.eSrc[0], 1);
assert.equal(g.eDst[0], 0);
assert.equal(g.edgeAdded("child", "root", "ref"), -1);
assert.equal(g.m, 1);
g.nodeAdded("child", {g: 3, r: 0.5});
assert.equal(g.m, 1);
assert.equal(g.ids[0], "root");
assert.equal(g.ids[1], "child");
""")


def test_thousand_nodes_grow_without_loss():
    _run(_prelude(CORE) + """
const g = new SK.LiveGraph(16);
for (let i = 0; i < 1000; i++) {
  g.nodeAdded("n" + i, {t: "N" + i, c: i % 5, g: i % 7, r: i / 1000});
}
assert.equal(g.n, 1000);
assert.ok(g.cap >= 1000);
assert.equal(g.ids[0], "n0");
assert.equal(g.titles[999], "N999");
assert.equal(g.degree[999], 999 % 7);
assert.ok(Math.abs(g.rank[999] - 0.999) < 1e-6);
assert.equal(g._nodeCluster[999], 999 % 5);
assert.equal(g.indexById.get("n500"), 500);
for (let i = 1; i < 1000; i++) g.edgeAdded("n" + (i - 1), "n" + i, "ref");
assert.equal(g.m, 999);
assert.ok(g.capE >= 999);
assert.equal(g.eSrc[500], 500);
assert.equal(g.eDst[500], 501);
assert.equal(g.pendingCount, 0);
assert.equal(g.edgeAdded("n0", "n1", "ref"), -1);
assert.equal(g.m, 999);
""")


def test_apply_batch_preserves_order_and_dedupes():
    _run(_prelude(CORE) + """
const g = new SK.LiveGraph(8);
g.applyBatch([
  {t: "node_added", id: "b", title: "Bee"},
  {t: "node_added", id: "a", title: "Ay"},
  {t: "edge_added", s: "b", d: "a", kind: "ref"},
  {t: "edge_added", s: "b", d: "a", kind: "ref"},
  {t: "node_updated", id: "b", g: 4, r: 0.25},
  {t: "edge_added", s: "a", d: "b", kind: "hub"},
]);
assert.equal(g.n, 2);
assert.equal(g.ids[0], "b");
assert.equal(g.ids[1], "a");
assert.equal(g.m, 2);
assert.equal(g.degree[0], 4);
assert.equal(g.rank[0], 0.25);
assert.equal(g.edgeKeys.size, 2);
assert.deepEqual(g.stats(), {nodes: 2, edges: 2, pending: 0, dangling: 0,
                             discovered: 0, discoverFailed: 0});
""")


def test_discover_events_fold_monotonically():
    """Discovery counters survive duplicate/replayed frames and snapshots."""
    _run(_prelude(CORE) + """
const g = new SK.LiveGraph(16);
g.applyBatch([
  {t: "discover", found: 5, failed: 1, depth: 1},
  {t: "discover", found: 3, failed: 0, depth: 2},
  {t: "discover", found: 9, failed: 4, depth: 2},
]);
assert.equal(g.discovered, 9, "discovered never regresses");
assert.equal(g.discoverFailed, 4, "failed never regresses");
assert.equal(g.stats().discovered, 9);
assert.equal(g.stats().discoverFailed, 4);
g.loadSnapshot({nodes: [], edges: [], last_seq: 1});
assert.equal(g.discovered, 9, "a reconnect snapshot keeps the discovery count");
assert.equal(g.discoverFailed, 4);
g.reset(16);
assert.equal(g.discovered, 0, "a new run starts clean");
assert.equal(g.discoverFailed, 0);
""")
    _run(_prelude(CORE, DIRECTOR) + """
const g = new SK.LiveGraph(16);
const d = new SK.Director(g, {name: "planets"}, null, null);
d.onEvents({t: "discover", found: 7, failed: 2, depth: 1});
assert.equal(d.status().discovered, 7);
assert.equal(d.status().discoverFailed, 2);
d.state = "failed";
d.error = "discovery timed out after 180s";
const st = d.status();
assert.equal(st.state, "failed");
assert.equal(st.error, "discovery timed out after 180s");
""")



def test_node_updated_events_never_clobber_titles():
    _run(_prelude(CORE) + """
const g = new SK.LiveGraph(8);
g.applyBatch([
  {t: "node_added", id: "a", title: "Ay", url: "http://x/"},
  {t: "node_updated", id: "a", g: 4, r: 0.25},
  {t: "node_updated", id: "late", g: 1, r: 0.5},
]);
assert.equal(g.titles[g.indexById.get("a")], "Ay");
assert.equal(g.urls[g.indexById.get("a")], "http://x/");
assert.equal(g.degree[g.indexById.get("a")], 4);
assert.equal(g.rank[g.indexById.get("a")], 0.25);
assert.equal(g.titles[g.indexById.get("late")], "late");
assert.notEqual(g.titles[g.indexById.get("late")], "node_updated");
""")


def test_batcher_orders_by_seq_and_limits_flush_rate():
    _run(_prelude(CORE) + """
const g = new SK.LiveGraph(16);
let clock = 0;
const flushes = [];
const b = new SK.Batcher(g, {
  windowMs: 300, maxNodes: 100, timer: false,
  now: function () { return clock; },
  onFlush: function (rec) { flushes.push(rec); }
});
assert.equal(b.push({t: "node_added", id: "second", title: "2", seq: 2}), false);
clock = 100;
assert.equal(b.push({t: "node_added", id: "first", title: "1", seq: 1}), false);
assert.equal(b.push({t: "node_added", id: "second", title: "2", seq: 2}), false);
assert.equal(flushes.length, 0);
assert.equal(g.n, 0);
clock = 300;
assert.equal(b.push({t: "node_added", id: "third", seq: 3}), true);
assert.equal(flushes.length, 1);
assert.equal(g.n, 3);
assert.deepEqual(g.ids.slice(0, 3), ["first", "second", "third"]);
assert.deepEqual(flushes[0].dirtyNodes, [0, 3]);
assert.equal(b.lastSeq, 3);
for (let i = 0; i < 99; i++) {
  assert.equal(b.push({t: "node_added", id: "m" + i, seq: 10 + i}, 310), false);
}
assert.equal(flushes.length, 1);
assert.equal(b.push({t: "node_added", id: "m99", seq: 109}, 310), true);
assert.equal(flushes.length, 2);
assert.equal(g.n, 103);
assert.deepEqual(flushes[1].dirtyNodes, [3, 103]);
b.push({t: "edge_added", s: "first", d: "second", kind: "ref", seq: 200}, 400);
clock = 700;
const rec = b.flush(700);
assert.ok(rec);
assert.equal(g.m, 1);
assert.deepEqual(rec.dirtyEdges, [0, 1]);
assert.equal(b.lastSeq, 200);
""")


def test_batcher_paced_reveal_spreads_a_burst():
    _run(_prelude(CORE) + """
const g = new SK.LiveGraph(64);
g.applyBatch([{t: "discover", found: 40, seq: 1}]);
let clock = 0;
const b = new SK.Batcher(g, {
  windowMs: 100, maxNodes: 100, timer: false, paced: true,
  now: function () { return clock; }
});
for (let i = 0; i < 20; i++) {
  b.push({t: "node_added", id: "n" + i, title: "N" + i, seq: 10 + i}, 0);
}
assert.equal(g.n, 0);
assert.equal(b.queue.length, 20);
clock = 100;
b.flush(100);
assert.ok(g.n >= 1 && g.n < 20, "first window reveals a slice, got " + g.n);
const first = g.n;
clock = 200;
b.flush(200);
assert.ok(g.n > first, "the next window keeps growing");
assert.ok(g.n < 20);
const rec = b.flush(Date.now() + 100000, true);
assert.equal(g.n, 20);
assert.equal(b.queue.length, 0);
assert.equal(rec.nodes, 20);
""")


def test_load_snapshot_uses_live_state_shape():
    _run(_prelude(CORE) + """
const g = new SK.LiveGraph(4);
g.loadSnapshot({
  run_id: "run-a", last_seq: 12,
  nodes: [
    {i: "index", t: "Index", u: "https://example.com/", c: "root", g: 4, r: 0.9},
    {i: "docs", t: "Docs", u: "https://example.com/docs", c: "docs", g: 1, r: 0.2}
  ],
  edges: [{s: "index", t: "docs", k: "ref"}, {s: "index", t: "ghost", k: "ref"}],
  layout: {nodes: [{i: 0, x: 0.1, y: 0.2, z: 0.3, d: 1},
                   {i: 1, x: -0.4, y: 0.5, z: -0.6, d: 2}]},
  dangling: 1
});
assert.equal(g.n, 2);
assert.equal(g.ids[0], "index");
assert.equal(g.titles[0], "Index");
assert.equal(g.urls[0], "https://example.com/");
assert.equal(g.degree[0], 4);
assert.ok(Math.abs(g.rank[0] - 0.9) < 1e-6);
assert.ok(Math.abs(g.x[0] - 0.1) < 1e-6);
assert.equal(g.depth[1], 2);
assert.equal(g.m, 1);
assert.equal(g.pendingCount, 1);
assert.equal(g.runId, "run-a");
assert.equal(g.lastSeq, 12);
assert.equal(g.dangling, 1);
""")


def test_scene_selection_and_derived_edge_visibility():
    _run(_prelude(CORE, DIRECTOR, PLANETS, NEURAL) + """
const g = new SK.LiveGraph(256);
for (let i = 0; i < 160; i++) {
  g.nodeAdded("n" + i, {t: "N" + i, c: i % 4, g: i === 0 ? 150 : 1,
                        r: (160 - i) / 160});
}
for (let i = 1; i <= 150; i++) g.edgeAdded("n0", "n" + i, "ref");
assert.equal(SK.scenes.planets.select(g, -1).length, 160);
assert.equal(SK.scenes.neural.select(g, 5).length, 160);
assert.equal(SK.scenes.planets.ego, false);
assert.equal(SK.scenes.neural.ego, false);
assert.deepEqual(SK.sceneOrder, ["planets", "neural"]);

g.edgeAdded("n1", "n2", "mentions_symbol");
const derivedEdge = g.m - 1;
assert.equal(SK.isDerived("mentions_symbol"), true);
assert.equal(SK.isDerived("ref"), false);
const hidden = SK.defaultHiddenKinds();
assert.equal(hidden.mentions_symbol, true);
const legend = SK.kindLegend(["ref", "imports", "mentions_symbol"], hidden);
const byKind = {};
legend.forEach(function (row) { byKind[row.kind] = row; });
assert.ok(byKind.ref);
assert.ok(byKind.imports);
assert.equal(byKind.ref.on, true);
assert.equal(byKind.imports.on, true);
assert.ok(byKind.mentions_symbol);
assert.equal(byKind.mentions_symbol.on, false);
assert.equal(byKind.mentions_symbol.derived, true);

g.revealed.fill(1);
g.eRevealed.fill(1);
const director = new SK.Director(g, {name: "planets", SCALE: 50,
                                     samplePoints: function () { return new Float32Array(0); }},
                                 {settled: true}, null);
director._recomputeDims();
assert.equal(g.eDim[derivedEdge], 0);
assert.ok(g.eDim[0] > 0);
director.toggleKind("mentions_symbol");
director._recomputeDims();
assert.ok(g.eDim[derivedEdge] > 0);
""")


def test_connect_events_resumes_and_resets_on_new_run():
    _run(_prelude(CORE, DIRECTOR) + """
(async function () {
  const live = new SK.LiveGraph(8);
  const director = new SK.Director(live, null, null, null);
  const sources = [];
  function FakeSource(url) { this.url = url; this.closed = false; sources.push(this); }
  FakeSource.prototype.close = function () { this.closed = true; };
  const calls = [];
  const snap = {
    run_id: "run-a", last_seq: 7,
    nodes: [{i: "a", t: "A", u: "", c: "x", g: 1, r: 0.2}],
    edges: [],
    layout: {nodes: [{i: 0, x: 0, y: 0, z: 0}]},
    dangling: 0
  };
  function fakeFetch(url) {
    calls.push(url);
    const body = url.indexOf("/health") >= 0
      ? {ok: true, run_id: "run-a", state: "running", last_seq: 7}
      : snap;
    return Promise.resolve({ok: true, json: function () {
      return Promise.resolve(body);
    }});
  }
  const handle = director.connectEvents("run-a", 3, {
    base: "http://live.test", EventSource: FakeSource, fetch: fakeFetch,
    timer: false
  });
  assert.equal(sources.length, 1);
  assert.equal(sources[0].url, "http://live.test/events?since=3");
  assert.equal(live.runId, "run-a");
  sources[0].onmessage({data: JSON.stringify(
    {t: "node_added", id: "b", title: "B", seq: 5})});
  assert.equal(director.batcher.queue.length, 1);
  sources[0].onerror();
  await new Promise(function (resolve) { setTimeout(resolve, 20); });
  assert.ok(calls.some(function (u) { return u.indexOf("/snapshot") >= 0; }));
  assert.equal(sources.length, 2);
  assert.equal(sources[1].url, "http://live.test/events?since=7");
  assert.equal(live.n, 1);
  assert.equal(live.ids[0], "a");
  assert.equal(live.lastSeq, 7);
  sources[1].onmessage({data: JSON.stringify(
    {t: "run_start", run_id: "run-b", seq: 1})});
  assert.equal(director.runId, "run-b");
  assert.equal(live.n, 0);
  sources[1].onmessage({data: JSON.stringify(
    {t: "node_added", id: "z", title: "Z", seq: 2})});
  director.batcher.flush(Date.now() + 100000, true);
  assert.equal(live.n, 1);
  assert.equal(live.ids[0], "z");
  handle.close();
  assert.equal(sources[1].closed, true);
})().catch(function (err) {
  console.error(err && err.stack ? err.stack : err);
  process.exit(1);
});
""")


def test_connect_events_discards_the_previous_run():
    _run(_prelude(CORE, DIRECTOR) + """
(async function () {
  const live = new SK.LiveGraph(8);
  live.runId = "run-old";
  live.nodeAdded("old", {t: "Old", c: "x", g: 3, r: 0.2});
  assert.equal(live.n, 1);
  const director = new SK.Director(live, null, null, null);
  const sources = [];
  function FakeSource(url) { this.url = url; sources.push(this); }
  FakeSource.prototype.close = function () {};
  director.connectEvents("run-new", 12, {
    base: "", EventSource: FakeSource, timer: false
  });
  /* A fresh crawl must not inherit the finished graph from the last one, and
     it must replay from sequence zero instead of skipping the opening events. */
  assert.equal(live.n, 0);
  assert.equal(live.runId, "run-new");
  assert.equal(sources[0].url, "/events?since=0");
  director.connectEvents("run-new", 4, {
    base: "", EventSource: FakeSource, timer: false
  });
  assert.equal(sources[1].url, "/events?since=4");
  live.nodeAdded("keep", {t: "Keep", c: "x", g: 1, r: 0.1});
  director.connectEvents("run-new", 9, {
    base: "", EventSource: FakeSource, timer: false
  });
  assert.equal(live.n, 1);
  assert.equal(sources[2].url, "/events?since=9");
})().catch(function (err) {
  console.error(err && err.stack ? err.stack : err);
  process.exit(1);
});
""")


def test_connect_events_carries_token_to_snapshot_and_sse():
    _run(_prelude(CORE, DIRECTOR) + """
(async function () {
  const live = new SK.LiveGraph(8);
  const director = new SK.Director(live, null, null, null);
  const sources = [];
  function FakeSource(url) { this.url = url; this.closed = false; sources.push(this); }
  FakeSource.prototype.close = function () { this.closed = true; };
  const calls = [];
  const snap = {run_id: "run-a", last_seq: 0, nodes: [], edges: [],
                layout: {nodes: []}, dangling: 0};
  function fakeFetch(url, init) {
    calls.push({url: url, headers: (init && init.headers) || {}});
    const body = url.indexOf("/health") >= 0
      ? {ok: true, run_id: "run-a", state: "running", last_seq: 0}
      : snap;
    return Promise.resolve({ok: true, json: function () {
      return Promise.resolve(body);
    }});
  }
  const handle = director.connectEvents("run-a", 3, {
    base: "http://live.test", token: "s3 cret&x", EventSource: FakeSource,
    fetch: fakeFetch, timer: false
  });
  assert.equal(sources[0].url,
               "http://live.test/events?since=3&token=s3%20cret%26x");
  sources[0].onerror();
  await new Promise(function (resolve) { setTimeout(resolve, 20); });
  assert.equal(calls.length, 2);
  const paths = calls.map(function (call) { return call.url; });
  assert.ok(paths.some(function (u) { return u.indexOf("/snapshot") >= 0; }));
  assert.ok(paths.some(function (u) { return u.indexOf("/health") >= 0; }));
  calls.forEach(function (call) {
    assert.equal(call.headers["X-Site-KG-Token"], "s3 cret&x");
  });
  assert.equal(sources.length, 2);
  assert.equal(sources[1].url,
               "http://live.test/events?since=0&token=s3%20cret%26x");
  handle.close();
})().catch(function (err) {
  console.error(err && err.stack ? err.stack : err);
  process.exit(1);
});
""")


def test_ui_forwards_page_token_to_live_api_and_events():
    src = (ROOT / "site_kg" / "viewer" / "ui.js").read_text()
    assert 'new URLSearchParams(window.location.search).get("token")' in src
    assert "ui.token = token;" in src
    assert 'headers["X-Site-KG-Token"] = this.token;' in src
    assert ('connectEvents("", 0, {base: base || "", token: this.token})'
            in src)
    assert ('{base: "", token: self.token}' in src)


def test_launcher_normalize_target_cases():
    _run(_prelude(LAUNCHER) + r"""
const L = SK.launcher;
assert.deepEqual(L.normalizeTarget("docs.example.com"),
                 {ok: true, url: "https://docs.example.com/"});
assert.deepEqual(L.normalizeTarget("  http://127.0.0.1:18823/mkdocs-material/  "),
                 {ok: true, url: "http://127.0.0.1:18823/mkdocs-material/"});
assert.equal(L.normalizeTarget("localhost:8080").ok, true);
assert.equal(L.normalizeTarget("").ok, false);
assert.equal(L.normalizeTarget(null).ok, false);
assert.equal(L.normalizeTarget("not a url").ok, false);
assert.equal(L.normalizeTarget("ftp://example.com").ok, false);
assert.equal(L.normalizeTarget("javascript:alert(1)").ok, false);
assert.equal(L.normalizeTarget("nodots").ok, false);
assert.equal(L.normalizeTarget("docs.example.com").url,
             "https://docs.example.com/");
// explicit scheme skips the host-shape heuristic (server is the authority)
assert.equal(L.normalizeTarget("http://intranet/").url, "http://intranet/");
assert.equal(L.normalizeTarget("http://nodebb:3000/docs").url,
             "http://nodebb:3000/docs");
assert.equal(L.normalizeTarget("http://[::1]:8080/a").url,
             "http://[::1]:8080/a");
// scheme-less loopback gets plain http, not https
assert.equal(L.normalizeTarget("localhost:8080").url, "http://localhost:8080/");
assert.equal(L.normalizeTarget("127.0.0.1:9000").url, "http://127.0.0.1:9000/");
assert.equal(L.normalizeTarget("[::1]:8080/a").url, "http://[::1]:8080/a");
// credentials are stripped, never forwarded or recorded
assert.equal(L.normalizeTarget("http://user:pw@evil.com/x").url,
             "http://evil.com/x");
assert.equal(L.normalizeTarget("http://user@host.internal/").url,
             "http://host.internal/");
""")


def test_launcher_readout_host_and_phase_labels():
    _run(_prelude(LAUNCHER) + """
const L = SK.launcher;
assert.equal(L.hostLabel("https://squidfunk.github.io/mkdocs-material/"),
             "squidfunk.github.io");
assert.equal(L.hostLabel("not a url"), "not a url");
assert.equal(L.phaseLabel("settling"), "settling");
assert.equal(L.phaseLabel("complete"), "complete");
assert.equal(L.phaseLabel("bogus"), "ingesting");
const r = L.readout({pages: 12, nodes: 9, edges: 21, state: "settling"});
assert.equal(r.label, "12 pages · 9 nodes · 21 edges");
assert.equal(r.pages, 12);
assert.equal(r.building, true);
assert.equal(r.phase, "settling");
assert.equal(L.readout({state: "complete"}).building, false);
assert.equal(L.readout(null).label, "0 pages · 0 nodes · 0 edges");
""")


def test_launcher_readout_shows_discovery_and_failure():
    _run(_prelude(LAUNCHER) + """
const L = SK.launcher;
assert.equal(L.phaseLabel("failed"), "failed", "failed is not folded into ingesting");
const found = L.readout({pages: 0, nodes: 0, edges: 0, discovered: 17,
                         state: "ingesting"});
assert.equal(found.found, true);
assert.equal(found.label, "17 pages found · 0 nodes · 0 edges");
assert.equal(found.building, true);
// once real pages arrive they replace the discovery hint
const fetched = L.readout({pages: 12, nodes: 9, edges: 21, discovered: 17,
                           state: "ingesting"});
assert.equal(fetched.found, false);
assert.equal(fetched.label, "12 pages · 9 nodes · 21 edges");
// completion still reports the real page/edge counts
const done = L.readout({pages: 17, nodes: 12, edges: 40, discovered: 20,
                        state: "complete"});
assert.equal(done.label, "17 pages · 12 nodes · 40 edges");
assert.equal(done.phase, "complete");
// archive with no doc count keeps the no-pages contract
const archive = L.readout({pages: 9, nodes: 5, edges: 3, discovered: 9},
                          {pages: false});
assert.equal(archive.label, "5 nodes · 3 edges");
// failure surfaces the server error, truncated, with a visible phase label
const failed = L.readout({state: "failed",
                          error: "discovery timed out after 180s"});
assert.equal(failed.phase, "failed");
assert.equal(failed.failed, true);
assert.equal(failed.building, false);
assert.equal(failed.label, "failed: discovery timed out after 180s");
const long = L.readout({state: "failed", error: "x".repeat(500)});
assert.ok(long.label.length <= 128, "error summary stays bounded");
assert.ok(long.label.indexOf("failed: ") === 0);
const blank = L.readout({state: "failed"});
assert.equal(blank.label, "failed: live run failed");
""")
src = (ROOT / "site_kg" / "viewer" / "launcher.js").read_text()
assert 'failed: "failed"' in src


def test_batcher_folds_progress_pages_into_live_state():
    _run(_prelude(CORE, DIRECTOR) + """
const g = new SK.LiveGraph(16);
const b = new SK.Batcher(g, {windowMs: 300, maxNodes: 100, timer: false});
b.push({t: "progress", pages: 3, bytes: 900, seq: 2});
b.push({t: "node_added", id: "a", title: "A", seq: 3});
b.flush(400, true);
assert.equal(g.pages, 3);
b.push({t: "progress", pages: 17, bytes: 4200, seq: 4});
b.flush(800, true);
assert.equal(g.pages, 17);
b.push({t: "progress", pages: 2, seq: 5});
b.flush(1200, true);
assert.equal(g.pages, 17, "pages fold to the max and never regress");
b.push({t: "ingest_done", pages: 21, seq: 6});
b.flush(1500, true);
assert.equal(g.pages, 21, "ingest_done pages fold too");
b.push({t: "progress", pages: 4, seq: 7});
b.flush(1800, true);
assert.equal(g.pages, 21);
const director = new SK.Director(g, {name: "planets"}, null, null);
director.state = "ingesting";
assert.equal(director.status().pages, 21);
assert.equal(director.status().state, "ingesting");
""")


def test_load_snapshot_keeps_server_page_count():
    """A reconnect snapshot has fewer nodes than pages crawled; restoring
    pages = n under-reported (60 pages showed as 45) after every reconnect."""
    _run(_prelude(CORE) + r"""
const g = new SK.LiveGraph(16);
g.loadSnapshot({
  nodes: [{i: "a", t: "A", u: "http://x/a"}, {i: "b", t: "B", u: "http://x/b"}],
  edges: [],
  progress: {pages: 60, nodes: 2, edges: 0, bytes: 9000},
  last_seq: 3
});
assert.equal(g.pages, 60, "server page count survives the snapshot");
// no progress block (older snapshot shape) falls back to node count
const g2 = new SK.LiveGraph(16);
g2.loadSnapshot({nodes: [{i: "c", t: "C"}], edges: []});
assert.equal(g2.pages, 1);
// a snapshot whose progress lags the stream's folded count never regresses
const g3 = new SK.LiveGraph(16);
g3.applyBatch([{t: "progress", pages: 60, seq: 1},
               {t: "node_added", id: "a", title: "A", seq: 2}]);
assert.equal(g3.pages, 60);
g3.loadSnapshot({nodes: [{i: "a", t: "A"}], edges: [],
                 progress: {pages: 45}, last_seq: 2});
assert.equal(g3.pages, 60, "snapshot pages cannot step backwards");
""")


def test_finale_parts_show_live_counts_not_baked_stats():
    _run(_prelude(UI) + r"""
const P = SK.UI.finaleParts;
// live: real counts from director status, never the baked zeros
assert.deepEqual(P({mode: "live"}, {pages: 55, nodes: 48, edges: 213}),
                 ["55 pages \u00b7 48 nodes \u00b7 213 edges"]);
assert.deepEqual(P({mode: "live", stats: "0 pages \u00b7 0 edges \u00b7 verdict ?"},
                   {pages: 3, nodes: 3, edges: 4}),
                 ["3 pages \u00b7 3 nodes \u00b7 4 edges"]);
assert.deepEqual(P({mode: "live"}, {pages: 5, nodes: 5, edges: 9, ego: 3}),
                 ["5 pages \u00b7 5 nodes \u00b7 9 edges", "ego 3"]);
// archive: the baked stats string is still the source of truth
assert.deepEqual(P({mode: "archive", stats: "12 pages \u00b7 30 edges \u00b7 verdict READY"},
                   {pages: 12, edges: 30}),
                 ["12 pages \u00b7 30 edges \u00b7 verdict READY"]);
assert.deepEqual(P({mode: "archive", stats: "s"}, {ego: 2}), ["s", "ego 2"]);
assert.deepEqual(P(null, {visible: 4}), ["4 visible"]);
""")
    src = (ROOT / "site_kg" / "viewer" / "ui.js").read_text()
    assert "UI.finaleParts(this.cfg, status)" in src


def test_set_live_target_updates_bar_and_document_title():
    """A new launch updates the host chip and the tab, while the masthead stays
    the product name instead of a raw URL; archive viewers never call this."""
    _run(_prelude(UI, LAUNCHER) + """
global.document = {title: "old baked title"};
const ui = new SK.UI();
ui.titleEl = {textContent: "old baked title"};
ui.targetEl = {textContent: "", title: ""};
ui.setLiveTarget("https://new.example/docs/start");
assert.equal(ui.targetEl.textContent, "new.example");
assert.equal(ui.titleEl.textContent, "site-kg");
assert.equal(document.title, "site-kg \u00b7 new.example");
// no mounted elements (archive-ish harness) must not throw
const bare = new SK.UI();
bare.setLiveTarget("https://other.example/");
assert.equal(document.title, "site-kg \u00b7 other.example");
""")


def test_build_view_stages_progress_and_titles():
    """The build overlay's model: stage words, a determinate ratio once pages
    arrive, and a five-title stream trimmed for the panel."""
    _run(_prelude(LAUNCHER) + """
const B = SK.launcher.buildView;
const finding = B({state: "ingesting", pages: 0, nodes: 0, edges: 0,
                   discovered: 12}, []);
assert.equal(finding.phase, "finding pages");
assert.equal(finding.indeterminate, true);
assert.equal(finding.ratio, 0);
const fetching = B({state: "ingesting", pages: 6, nodes: 0, edges: 0,
                    discovered: 12}, []);
assert.equal(fetching.phase, "fetching pages");
assert.equal(fetching.indeterminate, false);
assert.ok(Math.abs(fetching.ratio - 0.5) < 1e-9);
const growing = B({state: "ingesting", pages: 6, nodes: 6, edges: 9,
                   discovered: 12}, []);
assert.equal(growing.phase, "growing graph");
assert.ok(Math.abs(growing.ratio - 0.5) < 1e-9);
assert.equal(growing.label, "6 pages \\u00b7 6 nodes \\u00b7 9 edges");
const settling = B({state: "settling", pages: 12, nodes: 10, edges: 20,
                    discovered: 12}, []);
assert.equal(settling.phase, "settling");
assert.equal(settling.ratio, 1);
const done = B({state: "complete", pages: 12, nodes: 10, edges: 20}, []);
assert.equal(done.phase, "complete");
assert.equal(done.complete, true);
const failed = B({state: "failed", pages: 3, nodes: 2, edges: 1}, []);
assert.equal(failed.phase, "failed");
assert.equal(failed.failed, true);
const titles = B({state: "ingesting", pages: 2, discovered: 5},
                 ["a", "b", "c", "d", "e", "f"]);
assert.equal(titles.titles.length, 5);
assert.equal(titles.titles[0], "b");
assert.equal(titles.titles[4], "f");
assert.equal(SK.launcher.trimTitle("  a   b  "), "a b");
assert.equal(SK.launcher.trimTitle("9. Classes \\u00b6"), "9. Classes");
const long = SK.launcher.trimTitle("x".repeat(80));
assert.equal(long.length, 54);
assert.ok(long.indexOf("\\u2026") > 0);
""")


def test_launcher_copy_deck_constraints():
    src = (ROOT / "site_kg" / "viewer" / "launcher.js").read_text()
    _run(_prelude(LAUNCHER) + """
const copy = SK.launcher.copy;
assert.equal(copy.line1, "TURN ANY SITE");
assert.equal(copy.line2, "INTO A WORLD.");
assert.equal(copy.steps.length, 3);
assert.ok(copy.action && copy.placeholder);
assert.ok(copy.ctaNote);
assert.ok(copy.worldIdle && copy.worldOn);
Object.keys(copy.scenes).forEach(function (key) {
  assert.ok(copy.scenes[key].length > 10);
});
""")
    # the dealt sheet caps em-dashes at four across the whole viewer shell
    em = src.count("\u2014")
    assert em <= 4, f"launcher.js carries {em} em-dashes"


def test_launcher_load_art_reads_data_uri_map():
    _run(_prelude(LAUNCHER) + """
global.document = {
  getElementById: function (id) {
    if (id !== "sk-launch-art") return null;
    return {textContent: JSON.stringify({
      neural: "data:image/jpeg;base64,AAAA",
      hud: "data:image/jpeg;base64,BBBB"
    })};
  }
};
const art = SK.launcher.loadArt();
assert.equal(art.neural, "data:image/jpeg;base64,AAAA");
assert.equal(art.hud, "data:image/jpeg;base64,BBBB");
global.document = {getElementById: function () { return null; }};
assert.deepEqual(SK.launcher.loadArt(), {});
global.document = {getElementById: function () {
  return {textContent: "not json"};
}};
assert.deepEqual(SK.launcher.loadArt(), {});
""")


def test_bar_resize_floor_and_convergence():
    """--bar follows the measured HUD box (bar plus its float inset) but never
    drops below the 60px design floor, and a ResizeObserver echo loop converges
    instead of ratcheting."""
    _run(_prelude(UI) + r"""
let hudHeight = 60;
let barHeight = 40;
const hud = {
  getBoundingClientRect: function () { return {height: hudHeight}; }
};
const bar = {
  style: {minHeight: ""},
  getBoundingClientRect: function () { return {height: barHeight}; }
};
const props = {};
global.document = {
  getElementById: function (id) {
    return id === "hud" ? hud : (id === "bar" ? bar : null);
  },
  documentElement: {style: {
    setProperty: function (k, v) { props[k] = v; }
  }}
};
let roCb = null;
global.ResizeObserver = function (cb) {
  roCb = cb;
  this.observe = function () {};
  this.disconnect = function () {};
};
window.addEventListener = function () {};

const ui = new SK.UI();
ui._wireBarResize();
assert.equal(typeof roCb, "function", "ResizeObserver path was wired");
assert.equal(bar.style.minHeight, "", "min-height restored after measuring");
assert.equal(props["--bar"], "60px", "60px box sits on the 60px floor");

hudHeight = 83;
roCb();
assert.equal(props["--bar"], "83px", "taller deck is followed exactly");
assert.equal(bar.style.minHeight, "");

// echo loop: the observer firing on its own write must settle, not climb
hudHeight = 30;
for (let i = 0; i < 5; i++) roCb();
assert.equal(props["--bar"], "60px", "repeated triggers settle on the floor");
hudHeight = 83;
for (let i = 0; i < 5; i++) roCb();
assert.equal(props["--bar"], "83px", "repeated triggers settle on the height");
""")


def test_launcher_hide_show_listener_hygiene():
    """hide/show cycles never stack animationend handlers; only the poster's
    own posterLeave animation finishes the hide; reduced motion finishes sync."""
    _run(_prelude(UI) + r"""
const listeners = [];
function makeLauncher() {
  const set = new Set(["on"]);
  return {
    classList: {
      contains: function (c) { return set.has(c); },
      add: function () { for (let i = 0; i < arguments.length; i++) set.add(arguments[i]); },
      remove: function () { for (let i = 0; i < arguments.length; i++) set.delete(arguments[i]); }
    },
    addEventListener: function (type, fn) { listeners.push({type: type, fn: fn}); },
    removeEventListener: function (type, fn) {
      const i = listeners.findIndex(function (e) {
        return e.type === type && e.fn === fn;
      });
      if (i >= 0) listeners.splice(i, 1);
    },
    querySelector: function (sel) {
      return sel === "#launch-card" ? {firstChild: {}} : null;
    }
  };
}
const launcher = makeLauncher();
window.matchMedia = function () { return {matches: false}; };

const ui = new SK.UI();
ui.launcherEl = launcher;

// five hide/show cycles must leave exactly one live animationend handler
for (let i = 0; i < 5; i++) {
  ui.showLauncher();
  ui.hideLauncher();
}
const animEnds = listeners.filter(function (e) {
  return e.type === "animationend";
});
assert.equal(animEnds.length, 1, "no handler accumulation across cycles");

// a child's bubbling fadeIn must NOT finish the hide; the poster's own
// posterLeave does, removing the handler and clearing the pending timer
animEnds[0].fn({target: {parentElement: launcher}, animationName: "fadeIn"});
assert.equal(listeners.length, 1, "child animation does not finish the hide");
assert.equal(launcher.classList.contains("on"), true, "poster still up");
animEnds[0].fn({target: launcher, animationName: "posterLeave"});
assert.equal(listeners.length, 0, "posterLeave finishes and unregisters");
assert.equal(launcher.classList.contains("on"), false, "poster hidden");

// prefers-reduced-motion: finish immediately, register nothing
window.matchMedia = function () { return {matches: true}; };
const ui2 = new SK.UI();
const launcher2 = makeLauncher();
ui2.launcherEl = launcher2;
ui2.showLauncher();
ui2.hideLauncher();
assert.equal(listeners.length, 0, "reduced motion registers no listener");
assert.equal(launcher2.classList.contains("on"), false);
assert.equal(launcher2.classList.contains("leave"), false);
""")


def test_spring_step_overshoots_then_settles():
    """Drag feel: the spring must trail the pointer and, once released, cross
    the target once before settling — a snap or a limp follow would both fail
    this."""
    _run(_prelude(CORE) + """
const t = SK.DRAG_SPRING, dt = 1 / 60;
const stiff = t.stiff, damp = 2 * Math.sqrt(stiff) * t.damping;
let pos = 0, vel = 0, crossed = false, frames = 0;
for (; frames < 180; frames++) {
  const next = SK.springStep(pos, vel, 1, stiff, damp, dt);
  pos = next[0]; vel = next[1];
  if (pos > 1.001) crossed = true;
  if (Math.abs(1 - pos) < 0.003 && Math.abs(vel) < 0.01) break;
}
assert.ok(crossed, "released node overshoots its target");
assert.ok(frames < 120, "and settles in under two seconds, took " + frames);
assert.ok(Math.abs(pos - 1) < 0.01, "at rest, got " + pos);

// while held, the first frame must not jump the whole distance
let p2 = 0, v2 = 0;
const first = SK.springStep(p2, v2, 1, stiff, damp, dt);
assert.ok(first[0] > 0 && first[0] < 0.4, "first frame trails, got " + first[0]);
""")


def test_drag_phase_is_a_stable_seed_not_a_position_hash():
    """Dragging must not shake. The drift phase used to be hashed from the
    node position, so every frame of a drag re-rolled it and the cell jumped
    by up to two drift amplitudes; it is now an immutable per-cell attribute
    that the edge endpoints inherit."""
    src = (ROOT / "site_kg" / "viewer" / "render.js").read_text(encoding="utf-8")
    assert "skHash" not in src, "a position hash puts the shake back"
    assert src.count("attribute float a_seed") == 2, "node and edge shaders"
    assert src.count("float ph = a_seed;") == 2
    assert src.count('setAttribute("a_seed"') == 2
    assert "this.eSeed[e * 2] = this.nSeed[a];" in src
    assert "this.eSeed[e * 2 + 1] = this.nSeed[b];" in src


def test_brain_sequence_is_deterministic_and_on_the_unit_sphere():
    """R2 has to be a pure function of the index: the head is cached per node
    count, but a rebuild after the graph grows must not move the cells that
    were already there."""
    _run(_prelude(CORE) + """
function close(a, b, eps) { return Math.abs(a - b) <= eps; }
for (let i = 0; i < 500; i++) {
  const d = SK.brainSequence(i);
  assert.equal(d.length, 3);
  assert.ok(d.every(Number.isFinite), "finite at " + i);
  const len = Math.hypot(d[0], d[1], d[2]);
  assert.ok(close(len, 1, 1e-9), "unit at " + i + ", got " + len);
  const again = SK.brainSequence(i);
  assert.deepEqual(again, d, "deterministic at " + i);
  // copies, not aliases: an in-place nudge by the renderer must not leak
  again[0] += 1;
  assert.notEqual(SK.brainSequence(i)[0], again[0], "fresh array each call");
}
// the first few directions are distinct, not a degenerate point
assert.notDeepEqual(SK.brainSequence(0), SK.brainSequence(1));
""")


def test_brain_jitter_is_deterministic_and_stays_unit_length():
    """The jitter nudges a direction along two tangents; it must stay on the
    unit sphere, or cells float off the cortex and the soma stand-off becomes
    meaningless."""
    _run(_prelude(CORE) + """
const base = SK.brainSequence(37);
const a = SK.brainJitter(base[0], base[1], base[2], 37 * 0.618 + 1.7);
const b = SK.brainJitter(base[0], base[1], base[2], 37 * 0.618 + 1.7);
assert.deepEqual(a, b, "same seed, same direction");
const c = SK.brainJitter(base[0], base[1], base[2], 38 * 0.618 + 1.7);
assert.notDeepEqual(a, c, "a different seed moves the cell");
assert.ok(Math.abs(Math.hypot(a[0], a[1], a[2]) - 1) < 1e-9, "unit length");
assert.ok(Math.abs(a[0] * base[0] + a[1] * base[1] + a[2] * base[2]) > 0.99,
          "still on the same patch of cortex");

// a zero vector must not produce NaN: it falls back to the pole
const z = SK.brainJitter(0, 0, 0, 1);
assert.ok(z.every(Number.isFinite), "degenerate input stays finite");
assert.ok(Math.abs(Math.hypot(z[0], z[1], z[2]) - 1) < 1e-9);

// the poles are the one place a naive tangent basis degenerates
for (let i = 0; i < 64; i++) {
  const d = SK.brainJitter(0, i % 2 ? 1 : -1, 0, i + 0.5);
  assert.ok(Math.abs(Math.hypot(d[0], d[1], d[2]) - 1) < 1e-9, "pole " + i);
  assert.ok(d.every(Number.isFinite), "pole finite " + i);
}
""")


def test_brain_patch_returns_finite_radius_fold_age():
    """Every direction must resolve to a radius on the skin, a fold offset and
    an age. A NaN in any of them poisons a whole vertex or a whole cell."""
    _run(_prelude(CORE) + """
const out = [0, 0, 0];
for (let i = 0; i < 2000; i++) {
  const d = SK.brainSequence(i);
  const p = SK.brainPatch(d[0], d[1], d[2], out);
  assert.equal(p.length, 3);
  assert.ok(p.every(Number.isFinite), "finite at " + i);
  // The floor is the basal plane, not the ellipsoid: straight down the skin is
  // the floor of the skull, 0.28 out, so the shortest ray is far shorter than
  // a bare ellipsoid's. The ceiling is the occipital pole plus the cerebellar
  // mass. Neither may drift: a collapsed head and a spiked one are both bugs.
  assert.ok(p[0] > 0.24 && p[0] < 1.10, "radius in bounds at " + i + ": " + p[0]);
  assert.ok(p[1] > -0.25 && p[1] < 0.15, "fold in bounds at " + i + ": " + p[1]);
  assert.ok(p[2] >= 0 && p[2] <= 1, "age in [0,1] at " + i + ": " + p[2]);
  // the caller's buffer is reused, not replaced
  assert.equal(p, out);
}
// age really does order the build: the brainstem is younger tissue than the
// frontal pole, so the two ends must not collapse to the same value
const stem = SK.brainPatch(0, -0.97, -0.24, [0, 0, 0])[2];
const front = SK.brainPatch(0, 0, 1, [0, 0, 0])[2];
assert.ok(stem < front, "stem forms before the frontal pole: " + stem + " < " + front);
""")


def test_brain_mesh_indices_normals_and_max_radius():
    """The cortex is uploaded as Uint16 indices, so every index must stay under
    the vertex count and under 65536; the normals must be unit length or the
    gyri catch no light; the head must be the size the renderer scales to."""
    _run(_prelude(CORE) + """
const rows = 96, cols = 192;
const m = SK.brainMesh(rows, cols);
assert.equal(m.rows, rows);
assert.equal(m.cols, cols);
assert.equal(m.count, rows * cols);
assert.equal(m.positions.length, m.count * 3);
assert.equal(m.normals.length, m.count * 3);
assert.equal(m.indices.length, (rows - 1) * cols * 6);
assert.ok(m.count < 65536, "Uint16 indices can address the grid");

let maxIdx = -1, maxR = 0, badNormal = 0;
for (let i = 0; i < m.positions.length; i++) {
  if (!Number.isFinite(m.positions[i])) throw new Error("non-finite position " + i);
}
for (let i = 0; i < m.indices.length; i++) {
  const idx = m.indices[i];
  if (!Number.isInteger(idx) || idx < 0 || idx >= m.count) {
    throw new Error("index out of bounds at " + i + ": " + idx);
  }
  if (idx > maxIdx) maxIdx = idx;
}
assert.equal(maxIdx, m.count - 1, "the last vertex is referenced");
for (let i = 0; i < m.count; i++) {
  const x = m.normals[i * 3], y = m.normals[i * 3 + 1], z = m.normals[i * 3 + 2];
  if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(z)) {
    throw new Error("non-finite normal at " + i);
  }
  const len = Math.hypot(x, y, z);
  if (Math.abs(len - 1) > 1e-4) badNormal++;
  const r = Math.hypot(m.positions[i * 3], m.positions[i * 3 + 1],
                       m.positions[i * 3 + 2]);
  if (!Number.isFinite(r)) throw new Error("non-finite radius at " + i);
  if (r > maxR) maxR = r;
}
assert.equal(badNormal, 0, "every normal is unit length");
// The renderer normalizes by this number, so its exact value is not the
// contract; the envelope is. A spike from a bad bump would break the first
// bound, a shrunken head the second.
assert.ok(maxR > 0.85 && maxR < 1.15, "head max radius, got " + maxR);
assert.ok(m.dev.length === m.count && m.fold.length === m.count);
""")


def test_gpu_backend_inherits_the_renderer_contract():
    """The WebGPU backend is a subclass on purpose: layout, births, drag
    springs and finale state are built by SK.Renderer._initState/sync, so every
    method the camera, labels and director call must still resolve. Only the
    drawing path may be replaced."""
    _run(_prelude(CORE, RENDER, GPU) + r"""
assert.equal(typeof SK.GpuRenderer, "function");
assert.equal(Object.getPrototypeOf(SK.GpuRenderer.prototype),
             SK.Renderer.prototype);
assert.ok(SK.GpuRenderer.prototype instanceof SK.Renderer);

const draw = ["mount", "setScene", "resize", "render", "dispose"];
const shadowed = Object.getOwnPropertyNames(SK.Renderer.prototype)
  .filter(function (name) {
    return name !== "constructor" && draw.indexOf(name) < 0 &&
      SK.GpuRenderer.prototype[name] !== SK.Renderer.prototype[name];
  });
assert.deepEqual(shadowed, [],
  "scene methods must be the base implementation, not a copy");

/* draw calls are overridden; the shared scene machinery is not */
["mount", "setScene", "resize", "render", "dispose"].forEach(function (name) {
  assert.ok(Object.prototype.hasOwnProperty.call(SK.GpuRenderer.prototype, name),
            name + " is the WebGPU draw path");
});
["sync", "beginDrag", "moveDrag", "endDrag", "advanceDrag", "startFinale",
 "advanceFinale", "pick", "samplePoints", "ensureBrain"].forEach(function (name) {
  assert.ok(!Object.prototype.hasOwnProperty.call(SK.GpuRenderer.prototype, name),
            name + " must stay shared, or the backends can drift");
});

/* the probe only checks for the API; a missing adapter is the mount's job */
assert.equal(SK.GpuRenderer.supported(), false, "node has no navigator.gpu");
assert.equal(SK.hasWebGPU, SK.GpuRenderer.supported);
Object.defineProperty(global, "navigator", {
  value: {gpu: {}}, configurable: true
});
assert.equal(SK.GpuRenderer.supported(), true);
assert.equal(SK.hasWebGPU(), true);
""")


def test_gpu_backend_ships_wgsl_and_point_quads():
    """No WebGPURenderer in the vendored Three, and WebGPU has no
    gl_PointSize: every scene element needs its own WGSL entry point, and the
    node sprite is an instanced quad whose size is a diameter in device
    pixels."""
    src = GPU.read_text(encoding="utf-8")
    for entry in ("fn vs_sky", "fn fs_sky", "fn vs_cortex", "fn fs_cortex",
                  "fn vs_node", "fn fs_node", "fn fs_halo",
                  "fn vs_edge", "fn fs_edge", "fn vs_ring", "fn fs_ego"):
        assert entry in src, entry
    # the halo is the node quad with an additive blend and its own fragment
    # stage, so it has no vertex entry point of its own
    assert "fn vs_halo" not in src
    assert src.count("@vertex") == 6 and src.count("@fragment") == 7
    assert "multisample: {count: 4}" in src, "pipelines must match the MSAA pass"
    assert "navigator.gpu" in src
    # gl_PointSize is a diameter; the quad's half-extent is size/res, not
    # twice that. Getting this wrong renders the scene four times too large.
    assert "size / u.res.xy" in src
    assert "2.0 * size / u.res.xy" not in src


def test_ui_boots_webgpu_first_and_keeps_the_fallbacks_ordered():
    src = UI.read_text(encoding="utf-8")
    gpu = src.index("SK.GpuRenderer.supported()")
    gl = src.index("renderer = new SK.Renderer()")
    assert gpu < gl, "WebGPU is tried before WebGL"
    assert "SK.Fallback2D && SK.hasWebGL2 && !SK.hasWebGL2()" in src
    assert src.count("new SK.Fallback2D(canvas)") == 2, "no-WebGL2 and mount failure"
    # the adapter probe is async, so the Canvas2D tier hangs off ready, on a
    # canvas element that never held a webgpu context
    assert "gpu.ready.then" in src
    assert "flatFallback(gpu.gpuFailed)" in src
    assert "canvas.cloneNode(false)" in src
    assert "startView(renderer)" in src

    from site_kg.viewer import PRE_SCENE_MODULES
    mods = list(PRE_SCENE_MODULES)
    assert mods.index("gpu.js") == mods.index("render.js") + 1


def test_sky_uniform_carries_width_and_height_the_way_the_shader_reads_them():
    """The sky shader stretches by width/height. The packer writes
    b.z = width, b.w = height, and the WGSL must divide in that order: the
    reversed form renders the planets backdrop 3.16x anamorphic on 16:9."""
    _run(_prelude(CORE, RENDER, GPU) + r"""
const r = Object.create(SK.GpuRenderer.prototype);
r.camera = {position: {x: 0, y: 0, z: 2, length: function () { return 2; }}};
r.time = 1;
r.skyUni = {u_mode: {value: 0}};
r._resW = 1920;
r._resH = 1080;
const out = new Float32Array(8);
const ret = r._packSky(out);
assert.equal(ret, undefined, "packer writes into the caller's block");
assert.equal(out[6], 1920, "b.z is width");
assert.equal(out[7], 1080, "b.w is height");
""")
    src = GPU.read_text(encoding="utf-8")
    assert "u.b.z / max(u.b.w, 1.0)" in src, "aspect is width over height"
    assert "u.b.w / max(u.b.z, 1.0)" not in src


def test_fallback_reports_a_dead_canvas_instead_of_pretending_to_draw():
    """_fallbackGL decides the tier after an async probe. WebGL2 missing must
    be visible to ui.js (dead), and a canvas that already handed out a webgpu
    context must be marked dead rather than sent to _mountGL, where
    getContext('webgl2') can only return null."""
    _run(_prelude(CORE, RENDER, GPU) + r"""
const gl = Object.create(SK.GpuRenderer.prototype);
gl._mountGL = function () { this.mounted = true; };
gl.resize = function () {};
gl._fallbackGL(new Error("no WebGPU adapter"));
assert.equal(gl._gl, true);
assert.equal(gl.backend, "webgl");
assert.equal(gl.dead, undefined);

const dead = Object.create(SK.GpuRenderer.prototype);
dead._mountGL = function () { throw new Error("WebGL2 unavailable"); };
dead.resize = function () {};
dead._fallbackGL(new Error("no WebGPU adapter"));
assert.ok(!dead._gl);
assert.equal(dead.dead, true, "ui.js needs a signal to reach Canvas2D");
assert.match(dead.gpuFailed, /no WebGPU adapter \/ webgl: WebGL2 unavailable/);

const locked = Object.create(SK.GpuRenderer.prototype);
locked._context = {};
locked._mountGL = function () { throw new Error("must not be attempted"); };
locked._fallbackGL(new Error("pipeline build failed"));
assert.equal(locked.dead, true);
assert.equal(locked.gpuFailed, "pipeline build failed");
""")


def test_buffer_sweep_drops_attributes_that_left_the_scene():
    """Capacity growth installs new BufferAttributes; the old cache entries
    are unreachable and, without a sweep, their GPU buffers are never
    destroyed."""
    _run(_prelude(CORE, RENDER, GPU) + r"""
const r = Object.create(SK.GpuRenderer.prototype);
const liveAttr = {array: new Float32Array(4)};
const staleAttr = {array: new Float32Array(4)};
const indexAttr = {array: new Uint16Array(3)};
r.scene = {traverse: function (fn) {
  fn({geometry: {attributes: {position: liveAttr}, index: indexAttr}});
}};
const destroyed = [];
const map = new Map([
  [liveAttr, {buffer: {destroy: function () { destroyed.push("live"); }}}],
  [staleAttr, {buffer: {destroy: function () { destroyed.push("stale"); }}}],
  [indexAttr, {buffer: {destroy: function () { destroyed.push("index"); }}}]
]);
r._sweep(map);
assert.deepEqual(destroyed, ["stale"]);
assert.equal(map.has(liveAttr), true);
assert.equal(map.has(indexAttr), true);
assert.equal(map.has(staleAttr), false);
""")
