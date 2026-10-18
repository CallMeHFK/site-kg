"""Node smoke tests for the live viewer client (no browser, no DOM)."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "site_kg" / "viewer" / "core.js"
DIRECTOR = ROOT / "site_kg" / "viewer" / "director.js"
LAUNCHER = ROOT / "site_kg" / "viewer" / "launcher.js"
PLANETS = ROOT / "site_kg" / "viewer" / "scenes" / "planets.js"
PEOPLE = ROOT / "site_kg" / "viewer" / "scenes" / "people.js"
NEURAL = ROOT / "site_kg" / "viewer" / "scenes" / "neural.js"

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
assert.deepEqual(g.stats(), {nodes: 2, edges: 2, pending: 0, dangling: 0});
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
    _run(_prelude(CORE, DIRECTOR, PLANETS, PEOPLE, NEURAL) + """
const g = new SK.LiveGraph(256);
for (let i = 0; i < 160; i++) {
  g.nodeAdded("n" + i, {t: "N" + i, c: i % 4, g: i === 0 ? 150 : 1,
                        r: (160 - i) / 160});
}
for (let i = 1; i <= 150; i++) g.edgeAdded("n0", "n" + i, "ref");
assert.equal(SK.scenes.planets.select(g, -1).length, 160);
assert.equal(SK.scenes.neural.select(g, 5).length, 160);
const focused = SK.scenes.people.select(g, 5);
assert.equal(focused.length, 120);
assert.equal(focused[0], 5);
assert.ok(focused.indexOf(0) >= 0);
const hub = SK.scenes.people.select(g, -1);
assert.equal(hub.length, 120);
assert.equal(hub[0], 0);
assert.deepEqual(SK.scenes.people.select(g, 5), focused);

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
    _run(_prelude(LAUNCHER) + """
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
const director = new SK.Director(g, {name: "planets"}, null, null);
director.state = "ingesting";
assert.equal(director.status().pages, 17);
assert.equal(director.status().state, "ingesting");
""")


def test_launcher_copy_deck_constraints():
    src = (ROOT / "site_kg" / "viewer" / "launcher.js").read_text()
    _run(_prelude(LAUNCHER) + """
const copy = SK.launcher.copy;
assert.equal(copy.line1, "ANY SITE IS A GRAPH.");
assert.equal(copy.line2, "WATCH IT BUILD.");
assert.equal(copy.steps.length, 3);
assert.ok(copy.action && copy.placeholder);
assert.ok(copy.ctaWord && copy.ctaNote);
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
      people: "data:image/jpeg;base64,AAAA",
      hud: "data:image/jpeg;base64,BBBB"
    })};
  }
};
const art = SK.launcher.loadArt();
assert.equal(art.people, "data:image/jpeg;base64,AAAA");
assert.equal(art.hud, "data:image/jpeg;base64,BBBB");
global.document = {getElementById: function () { return null; }};
assert.deepEqual(SK.launcher.loadArt(), {});
global.document = {getElementById: function () {
  return {textContent: "not json"};
}};
assert.deepEqual(SK.launcher.loadArt(), {});
""")
