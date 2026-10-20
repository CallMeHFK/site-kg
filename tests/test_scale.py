"""Scale gates for the 75,415-node / 61,615-edge corpus floor.

Default runs cover allocation-free static budgets, binary payload size, Node-side
batch cost and the archive HTML size gate. The layout solve and the browser perf
probe are marked ``slow`` and run manually with ``-m slow``.
"""
from __future__ import annotations

import itertools
import json
import math
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from site_kg import layout, payload
from site_kg.viewer import assemble_bundle, render_viewer

ROOT = Path(__file__).resolve().parents[1]
VIEWER = ROOT / "site_kg" / "viewer"
CORE = VIEWER / "core.js"
SCENES = VIEWER / "scenes"
MIB = 1024 * 1024
N_NODES = 75_415
N_EDGES = 61_615


def synthetic_graph() -> dict:
    """Deterministic graph at the corpus scale floor, with unit-sphere coords."""
    golden = math.pi * (3 - math.sqrt(5))
    nodes = []
    for i in range(N_NODES):
        u = 1 - (2 * i + 1) / N_NODES
        ring = math.sqrt(max(0.0, 1 - u * u))
        theta = golden * i
        nodes.append({
            "i": f"p{i}", "c": f"c{i % 12}", "t": f"Doc {i}",
            "u": f"https://example.com/d/{i}", "g": i % 37, "r": i / N_NODES,
            "x": 0.98 * math.cos(theta) * ring,
            "y": 0.98 * u,
            "z": 0.98 * math.sin(theta) * ring,
        })
    edges = []
    for i in range(1, N_EDGES + 1):
        edges.append({"s": f"p{(i - 1) // 2}", "t": f"p{i}",
                      "k": "imports" if i % 7 == 0 else "ref"})
    return {"nodes": nodes, "edges": edges}


@pytest.fixture(scope="module")
def graph75k() -> dict:
    return synthetic_graph()


@pytest.fixture(scope="module")
def scale_artifacts(graph75k, tmp_path_factory):
    started = time.perf_counter()
    graph_bytes = payload.encode_graph(graph75k)
    rows = [{"x": n["x"], "y": n["y"], "z": n["z"], "c": 0, "d": 0}
            for n in graph75k["nodes"]]
    layout_bytes = payload.encode_layout(rows, [], final=0)
    events = []
    seq = 1
    for node in graph75k["nodes"]:
        events.append({"t": "node_added", "id": node["i"], "seq": seq, "ms": seq * 2})
        seq += 1
    for edge in graph75k["edges"]:
        events.append({"t": "edge_added", "s": edge["s"], "d": edge["t"],
                       "kind": edge["k"], "seq": seq, "ms": seq * 2})
        seq += 1
    timeline_bytes = payload.encode_timeline("scale-75415", events, batch_size=512)
    blob = payload.pack_archive_payload(graph_bytes, layout_bytes, timeline_bytes)
    out_dir = tmp_path_factory.mktemp("scale75k")
    out = out_dir / "viewer.html"
    render_viewer(graph75k, {"url": "https://example.com/", "docs": N_NODES,
                             "verdict": "READY", "run_id": "scale-75415"},
                  out, scene="planets")
    return {"graph_bytes": len(graph_bytes), "blob": len(blob),
            "html_bytes": out.stat().st_size, "html_path": out,
            "seconds": time.perf_counter() - started}


def test_encode_graph_size_budget(scale_artifacts):
    assert scale_artifacts["graph_bytes"] < 8 * MIB, (
        f"encode_graph is {scale_artifacts['graph_bytes']} bytes, over the 8 MiB budget")


def test_archive_payload_size_budget(scale_artifacts):
    assert scale_artifacts["blob"] < 8 * MIB, (
        f"gzip+base64 payload is {scale_artifacts['blob']} bytes, over the 8 MiB budget")


def test_archive_html_size_gate(scale_artifacts):
    size = scale_artifacts["html_bytes"]
    assert size < 8 * MIB, (
        f"archive viewer is {size} bytes at 75k nodes; CI size gate is 8 MiB")


@pytest.mark.slow
def test_settle_graph_75k_is_finite_and_under_60s(graph75k):
    started = time.perf_counter()
    positions = layout.settle_graph(graph75k)
    elapsed = time.perf_counter() - started
    assert len(positions) == N_NODES
    assert elapsed < 60.0, f"settle_graph took {elapsed:.1f}s"
    for i in range(0, N_NODES, 97):
        x, y, z = positions[f"p{i}"]
        assert math.isfinite(x) and math.isfinite(y) and math.isfinite(z)
        assert abs(x) <= 1.0 and abs(y) <= 1.0 and abs(z) <= 1.0


# applyBatch(10k) takes ~0.4 ms on a laptop, but shared CI runners have been
# measured at 3-8x that. This is an algorithmic smoke gate, not a micro
# benchmark: an accidental linear scan per event over the 75k-node graph would
# land in the hundreds of milliseconds, so 10 ms still catches the regression
# that matters while surviving a busy runner.
BATCH_BUDGET_MS = 10.0


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_livegraph_growth_and_10k_batch_budget():
    script = f"""
const assert = require("node:assert/strict");
global.window = {{}};
require({json.dumps(str(CORE))});
const SK = global.window.SK;
const N = {N_NODES}, E = {N_EDGES};
const g = new SK.LiveGraph(16);
for (let i = 0; i < N; i++) {{
  g.nodeAdded("p" + i, {{t: "Doc " + i, c: "c" + (i % 12),
                        g: i % 37, r: i / N}});
}}
assert.equal(g.n, N);
assert.ok(g.cap >= N);
assert.equal(g.ids[0], "p0");
assert.equal(g.titles[N - 1], "Doc " + (N - 1));
assert.equal(g.indexById.get("p50000"), 50000);
for (let i = 1; i <= E; i++) {{
  g.edgeAdded("p" + ((i - 1) >> 1), "p" + i,
              i % 7 === 0 ? "imports" : "ref");
}}
assert.equal(g.m, E);
assert.ok(g.capE >= E);
const events = [];
for (let i = 0; i < 10000; i++) {{
  events.push({{t: "node_updated", id: "p" + i, g: i % 17, r: 0.5}});
}}
for (let w = 0; w < 3; w++) g.applyBatch(events);
let best = Infinity;
for (let k = 0; k < 7; k++) {{
  const t0 = process.hrtime.bigint();
  g.applyBatch(events);
  const ms = Number(process.hrtime.bigint() - t0) / 1e6;
  if (ms < best) best = ms;
}}
assert.ok(best < {BATCH_BUDGET_MS},
          "applyBatch(10k) took " + best.toFixed(3) + " ms");
console.log(JSON.stringify({{bestMs: best, cap: g.cap, capE: g.capE,
                            n: g.n, m: g.m}}));
"""
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                          timeout=120, cwd=str(ROOT))
    assert proc.returncode == 0, proc.stderr + "\n" + proc.stdout
    result = json.loads(proc.stdout.strip().splitlines()[-1])
    assert result["n"] == N_NODES and result["m"] == N_EDGES
    assert result["bestMs"] < BATCH_BUDGET_MS


def test_label_budgets_are_capped():
    budgets = {"planets": (40, 12), "neural": (18, 8)}
    for name, (desktop, mobile) in budgets.items():
        src = (SCENES / f"{name}.js").read_text(encoding="utf-8")
        found_desktop = int(re.search(r"labelBudget:\s*(\d+)", src).group(1))
        found_mobile = int(re.search(r"labelBudgetMobile:\s*(\d+)", src).group(1))
        assert found_desktop == desktop and found_mobile == mobile
        assert found_desktop <= 40 and found_mobile <= 12


def test_renderer_draw_call_and_object_budget():
    src = (VIEWER / "render.js").read_text(encoding="utf-8")
    points = len(re.findall(r"new THREE\.Points\b", src))
    lines = len(re.findall(r"new THREE\.LineSegments\b", src))
    instanced = len(re.findall(r"new THREE\.InstancedMesh\b", src))
    # one fullscreen quad carries the animated nebula + parallax starfield;
    # everything else stays merged
    meshes = len(re.findall(r"new THREE\.Mesh\(", src))
    assert (points, lines, instanced) == (2, 2, 1), (
        "renderer must keep one cloud+halo, one edge mesh and one merged ring mesh")
    # Two meshes: the fullscreen sky shader, and the cortex the neural scene
    # grows its neurons on. The cortex is the one deliberate addition to the
    # budget, because the silhouette of a brain cannot be carried by points
    # and lines: it has to be a surface.
    assert meshes == 2 and "this.sky = new THREE.Mesh(" in src, (
        "the meshes are the fullscreen sky shader and the cortex substrate")
    assert "this.cortex = new THREE.Mesh(" in src
    assert points + lines + instanced + meshes <= 7
    rings = src[src.index("Renderer.prototype._ensureRings"):
                src.index("Renderer.prototype._disposeRings")]
    assert len(re.findall(r"new THREE\.LineSegments\b", rings)) == 1, (
        "all chapter rings must merge into one LineSegments draw call")
    assert "this.ringMesh = new THREE.LineSegments" in rings
    assert src.count("new THREE.Object3D") == 1  # one reusable instance dummy
    assert "new THREE.Sprite" not in src
    assert "new THREE.InstancedMesh(sphere, this.egoMat, 120)" in src


def test_renderer_handles_context_loss_and_restore():
    src = (VIEWER / "render.js").read_text(encoding="utf-8")
    assert '"webglcontextlost"' in src
    assert '"webglcontextrestored"' in src
    assert "preventDefault" in src
    assert "contextLost" in src


def test_two_d_fallback_is_capped_and_points_at_archive():
    fallback = VIEWER / "fallback2d.js"
    assert fallback.exists(), "canvas 2D fallback module is missing"
    src = fallback.read_text(encoding="utf-8")
    assert "MAX_NODES = 5000" in src
    assert 'getContext("2d")' in src
    assert "webgl2" in src.lower()
    assert "archive" in src.lower()
    ui = (VIEWER / "ui.js").read_text(encoding="utf-8")
    assert "SK.hasWebGL2" in ui and "new SK.Fallback2D" in ui
    bundle = assemble_bundle()
    assert "SK.Fallback2D" in bundle and "MAX_NODES = 5000" in bundle


@pytest.mark.slow
def test_playwright_archive_perf_and_no_oom(scale_artifacts):
    sync_api = pytest.importorskip("playwright.sync_api")
    playwright = sync_api.sync_playwright().start()
    try:
        try:
            browser = playwright.chromium.launch(headless=True, args=[
                "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
                "--disable-gpu-sandbox",
            ])
        except Exception as exc:  # browser binary not installed
            pytest.skip(f"chromium unavailable: {exc}")
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            errors: list[str] = []
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            page.on("console", lambda msg: errors.append(msg.text)
                    if msg.type == "error" else None)
            page.set_default_timeout(120_000)
            page.goto(scale_artifacts["html_path"].as_uri(), wait_until="load",
                      timeout=120_000)
            page.wait_for_function(
                "window.SK && !document.querySelector('#fatal.on')", timeout=60_000)
            if page.evaluate("!!document.querySelector('#fallback-note')"):
                pytest.skip("WebGL2 is not available in this browser")
            page.evaluate("""() => {
                window.__skFrames = [];
                const loop = (t) => {
                    window.__skFrames.push(t);
                    requestAnimationFrame(loop);
                };
                requestAnimationFrame(loop);
            }""")
            page.wait_for_timeout(30_000)
            frames = page.evaluate("window.__skFrames")
            deltas = [b - a for a, b in itertools.pairwise(frames)]
            assert len(deltas) > 120, "animation stalled during the 30s probe"
            deltas.sort()
            p95 = deltas[max(0, int(len(deltas) * 0.95) - 1)]
            assert p95 < 500.0, f"p95 frame gap {p95:.1f} ms exceeds 500 ms"
            assert not errors, f"browser errors during run: {errors[:5]}"
            assert not any("out of memory" in e.lower() or "oom" in e.lower()
                           for e in errors)
        finally:
            browser.close()
    finally:
        playwright.stop()


def test_scene_sky_names_resolve_to_a_shader():
    """A scene's ``sky`` name must exist as ``vec3 <name>(`` in the shader and
    be the one the u_mode switch selects. A rename that misses either side
    silently falls back to the cosmos nebula — the neural scene would then sit
    on a bright backdrop that fights its own edges."""
    src = (VIEWER / "render.js").read_text(encoding="utf-8")
    named = 0
    for scene in sorted(SCENES.glob("*.js")):
        text = scene.read_text(encoding="utf-8")
        m = re.search(r'sky:\s*"([a-z_]+)"', text)
        if not m:
            continue
        name = m.group(1)
        named += 1
        assert f"vec3 {name}(" in src, f"{scene.name}: no shader for sky {name!r}"
        assert f'cfg.sky === "{name}"' in src, \
            f"{scene.name}: u_mode does not select {name!r}"
    assert named >= 1, "at least one scene should name its sky"


def test_neural_backdrop_is_quiet():
    """The neural scene draws bright synapses; its backdrop must stay near
    black instead of carrying filaments of its own."""
    src = (VIEWER / "render.js").read_text(encoding="utf-8")
    block = src[src.index("vec3 deepvoid("):]
    block = block[:block.index("void main()")]
    assert "wetware" not in block
    for term in ("fil1", "fil2", "syn", "0.38, 0.06, 0.34", "0.07, 0.44, 0.58"):
        assert term not in block, f"neural backdrop still contains {term}"


def test_hud_keeps_chapters_on_the_bar():
    """The toolbar is one row on desktop: the button used to drop to a second
    line when the search box and the scene switcher ran out of room."""
    css = (ROOT / "site_kg" / "templates" / "viewer.html").read_text(encoding="utf-8")
    assert re.search(r"#bar\{display:flex;flex-wrap:nowrap", css), \
        "the command bar must not wrap; the search box shrinks instead"
    assert re.search(r"\.hud-tools\{[^}]*flex-wrap:nowrap", css)
    assert re.search(r"\.mb\{[^}]*flex:none", css), "buttons keep their intrinsic width"
    # narrow screens still get the deliberate two-row layout
    mobile = css[css.index("@media (max-width:760px){"):]
    assert re.search(r"#bar\{[^}]*flex-wrap:wrap", mobile[:400])
