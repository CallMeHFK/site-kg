"""Build-process visualization: a self-contained WebGPU viewer that replays how
a graph was constructed.

`render_site` (viewer.py) is the inspection path -- it shows the finished graph.
This module answers a different question: what did the build *do*? It replays
the construction in order: nodes are born along their corpus (crawl/scan) order
onto chapter rings, flying out of the core; then evidence edges (ref, imports,
contains, hub) are drawn; then one wave per derived relation dimension
(relations.py), every edge growing from its source node and binding to its
target with a pulse. A HUD counts nodes, edges and per-dimension edges as they
land, and the replay closes on the graph verdict.

The build order is synthesized from the stored graph -- nodes keep corpus order,
edges are bucketed by kind with evidence first and derived dimensions in
registry order -- so nothing extra is recorded at ingest time and the replay
stays truthful to what build_graph actually produced. The renderer is raw
WebGPU/WGSL with no vendor bundle and no network access: one HTML file, four
pipelines (edge lines, node quads, edge head particles, bind pulses), and a
uniform clock that drives every animation in the shaders.

Binary payloads (node positions, degrees, edge endpoints, per-edge birth times)
are base64-encoded little-endian typed arrays, so a 75k-node kernel site embeds
as a few MB instead of tens of MB of JSON.
"""
from __future__ import annotations

import base64
import json
import math
import random
from array import array
from collections import Counter, defaultdict
from html import escape as _html_escape
from pathlib import Path

# Evidence kinds in the order graph.py resolves them; derived kinds in the
# registry order relations.py documents (docs/RELATIONS.md).
EVIDENCE_ORDER = ("ref", "imports", "contains", "hub")
DERIVED_ORDER = ("mentions_symbol", "similar_to", "sibling_of", "supersedes",
                 "shares_entity", "semantic_similar")

CHAPTER_COLORS = (0x58a6ff, 0x3fb950, 0xd29922, 0xf778ba, 0xa371f7, 0x76e3ea,
                  0xffa657, 0xff7b72, 0x7ee787, 0xe3b341, 0x79c0ff, 0x56d4bc)
KIND_COLORS = {
    "ref": 0x58a6ff, "imports": 0x3fb950, "contains": 0xd29922, "hub": 0xffa657,
    "mentions_symbol": 0x76e3ea, "similar_to": 0xa371f7, "sibling_of": 0xf778ba,
    "supersedes": 0xff7b72, "shares_entity": 0xe3b341, "semantic_similar": 0x79c0ff,
}
FALLBACK_COLOR = 0x8b949e
PHASE_LABELS = {
    "ref": "REF LINKS", "imports": "IMPORTS", "contains": "CONTAINS", "hub": "HUBS",
    "mentions_symbol": "MENTIONS_SYMBOL", "similar_to": "SIMILAR_TO",
    "sibling_of": "SIBLING_OF", "supersedes": "SUPERSEDES",
    "shares_entity": "SHARES_ENTITY", "semantic_similar": "SEMANTIC_SIMILAR",
}

FLIGHT = 0.55          # seconds an edge takes to fly from source to target
R_MAX = 100.0          # ring layout radius, world units
HUB_PULL = 0.30        # how far high-degree nodes migrate toward the core
TITLES_MAX_NODES = 5000   # hover titles only at inspection scale


def _corpus_dur(n: int) -> float:
    """Node phase length: sublinear in corpus size, capped, so a 75k-doc tree
    still finishes its ring in about ten seconds at 1x."""
    return min(10.0, max(1.2, n / 2500.0))


def _phase_dur(count: int) -> float:
    return min(14.0, max(0.4, count / 1500.0))


def _rgb(hexcol: int) -> tuple[float, float, float]:
    return ((hexcol >> 16 & 255) / 255.0, (hexcol >> 8 & 255) / 255.0,
            (hexcol & 255) / 255.0)


def _hex(hexcol: int) -> str:
    return f"#{hexcol:06x}"


def layout_rings(graph: dict, seed: int = 7) -> None:
    """Deterministic ring layout, in place: one ring per chapter (biggest
    outermost), nodes spread by golden angle, high-degree nodes pulled toward
    the core so hubs and inter-ring edges gather into a dense centre.

    Deterministic: same graph + seed => same coordinates, so a rebuild does not
    reshuffle the replay.
    """
    nodes = graph["nodes"]
    if not nodes:
        return
    deg: Counter = Counter()
    for e in graph["edges"]:
        deg[e["s"]] += 1
        deg[e["t"]] += 1
    gmax = max(deg.values(), default=1)
    by_ch: dict[str, list[dict]] = defaultdict(list)
    for nd in nodes:
        by_ch[nd["c"]].append(nd)
    rng = random.Random(seed)
    golden = math.pi * (3 - math.sqrt(5))
    biggest = max(len(m) for m in by_ch.values())
    for ch in sorted(by_ch, key=lambda c: (-len(by_ch[c]), c)):
        members = sorted(by_ch[ch], key=lambda nd: nd["i"])
        base_r = R_MAX * (0.30 + 0.70 * math.sqrt(len(members) / biggest))
        off = rng.uniform(0.0, 2 * math.pi)
        for j, nd in enumerate(members):
            ang = off + j * golden + rng.uniform(-0.35, 0.35)
            pull = 1.0 - HUB_PULL * math.sqrt(deg[nd["i"]] / gmax)
            r = base_r * pull * rng.uniform(0.96, 1.04)
            nd["x"] = round(r * math.cos(ang), 2)
            nd["z"] = round(r * math.sin(ang), 2)
            nd["y"] = round(rng.uniform(-1.0, 1.0) * R_MAX * 0.02, 2)


def build_view_data(graph: dict, meta: dict) -> dict:
    """Everything the viewer needs, packed as base64 typed arrays.

    Node birth times are not stored per node (the shader derives them from the
    corpus window); edge birth times are, because they depend on the phase a
    kind landed in.
    """
    nodes = graph["nodes"]
    edges = graph["edges"]
    n = len(nodes)
    deg: Counter = Counter()
    for e in edges:
        deg[e["s"]] += 1
        deg[e["t"]] += 1
    gmax = max(deg.values(), default=1)

    chapters = sorted({nd["c"] for nd in nodes})
    ch_index = {c: i for i, c in enumerate(chapters)}

    buckets: dict[str, list[int]] = {}
    for i, e in enumerate(edges):
        buckets.setdefault(e["k"], []).append(i)
    order = [k for k in EVIDENCE_ORDER if k in buckets]
    order += [k for k in DERIVED_ORDER if k in buckets]
    order += [k for k in sorted(buckets) if k not in order]  # future kinds

    corpus_dur = _corpus_dur(n)
    phases = [{"name": "CORPUS", "label": "CORPUS", "t0": 0.0,
               "dur": round(corpus_dur, 4), "count": n, "e0": -1, "nodes": True}]
    t = corpus_dur
    edge_order: list[int] = []
    births = [0.0] * len(edges)
    for k in order:
        idxs = buckets[k]
        dur = _phase_dur(len(idxs))
        phases.append({"name": k, "label": PHASE_LABELS.get(k, k.upper()),
                       "t0": round(t, 4), "dur": round(dur, 4),
                       "count": len(idxs), "e0": len(edge_order), "nodes": False})
        m = len(idxs)
        for j, gi in enumerate(idxs):
            edge_order.append(gi)
            births[gi] = t + (j + 1) / m * dur
        t += dur

    node_idx = {nd["i"]: i for i, nd in enumerate(nodes)}
    kind_index = {k: i for i, k in enumerate(order)}

    nf = array("f")
    df = array("f")
    for nd in nodes:
        nf.extend((float(nd.get("x", 0.0)), float(nd.get("y", 0.0)),
                   float(nd.get("z", 0.0)), float(ch_index.get(nd["c"], 0))))
        df.append(math.sqrt(deg[nd["i"]] / gmax) if gmax else 0.0)
    ef = array("I")
    for gi in edge_order:
        e = edges[gi]
        # kind index feeds the 16-slot palette directly; clamp so a registry
        # grown past 16 kinds wraps instead of indexing past the palette
        ef.extend((node_idx[e["s"]], node_idx[e["t"]],
                   kind_index[e["k"]] % 16, 0))
    bf = array("f", (births[gi] for gi in edge_order))

    pal = [0.0] * 128
    for i in range(min(len(chapters), 16)):
        pal[i * 4:i * 4 + 3] = list(_rgb(CHAPTER_COLORS[i % len(CHAPTER_COLORS)]))
    for i in range(min(len(order), 16)):
        col = KIND_COLORS.get(order[i], FALLBACK_COLOR)
        pal[(16 + i) * 4:(16 + i) * 4 + 3] = list(_rgb(col))

    return {
        "title": meta.get("url", "site-kg graph"),
        "verdict": meta.get("verdict", "?"),
        "relationMode": meta.get("relationMode", "off"),
        "docs": meta.get("docs", n),
        "edgesTotal": len(edges),
        "corpusT0": 0.0,
        "corpusDur": round(corpus_dur, 4),
        "flight": FLIGHT,
        "total": round(t, 4),
        "chapters": [{"name": c, "color": _hex(CHAPTER_COLORS[i % 12])}
                     for i, c in enumerate(chapters)],
        "kinds": [{"name": k, "color": _hex(KIND_COLORS.get(k, FALLBACK_COLOR))}
                  for k in order],
        "phases": phases,
        "palB": _b64(array("f", pal)),
        "nodesB": _b64(nf),
        "degsB": _b64(df),
        "edgesB": _b64(ef),
        "birthsB": _b64(bf),
        "titles": [nd.get("t", "") for nd in nodes] if n <= TITLES_MAX_NODES else None,
    }


def _b64(a: array) -> str:
    """Native byte order (little-endian on every platform WebGPU runs on)."""
    return base64.b64encode(a.tobytes()).decode("ascii")


def render_build_viewer(graph: dict, meta: dict, out_path: Path) -> Path:
    """Write the self-contained build-replay HTML for one site's graph."""
    g = {"nodes": [dict(nd) for nd in graph["nodes"]], "edges": graph["edges"]}
    if not g["nodes"]:
        raise ValueError("empty graph: nothing to replay")
    layout_rings(g)
    data = build_view_data(g, meta)
    payload = json.dumps(data, ensure_ascii=False)
    # keep the JSON safe inside a <script> block
    for bad, esc in (("<", "\\u003c"), ("\u2028", "\\u2028"), ("\u2029", "\\u2029")):
        payload = payload.replace(bad, esc)
    stats = (f'{data["docs"]} pages · {data["edgesTotal"]} edges'
             f' · verdict {data["verdict"]} · mode {data["relationMode"]}')
    html = (HEAD
            .replace("__TITLE__", _html_escape(str(data["title"]), quote=True))
            .replace("__STATS__", _html_escape(stats, quote=True))
            + MAIN_JS.replace("__DATA__", payload))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path


HEAD = """<!doctype html>
<html><head><meta charset="utf-8"><title>__TITLE__ — build matrix</title>
<style>
 html,body{margin:0;height:100%;overflow:hidden;background:#04060c}
 body{font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace;
      color:#cfe3ff}
 #bg{position:fixed;inset:0;background:
   radial-gradient(1100px 700px at 60% 36%, rgba(38,78,140,.28), transparent 62%),
   radial-gradient(900px 900px at 18% 88%, rgba(84,38,128,.20), transparent 60%),
   radial-gradient(700px 500px at 88% 12%, rgba(20,90,110,.16), transparent 60%),#04060c}
 #gl{position:fixed;inset:0;width:100%;height:100%;display:block}
 .panel{position:fixed;background:rgba(6,10,18,.80);border:1px solid rgba(110,170,255,.22);
   border-radius:3px;padding:10px 12px;backdrop-filter:blur(3px);z-index:5;
   box-shadow:0 0 26px rgba(20,60,120,.18)}
 .brand{font-size:15px;letter-spacing:.22em;color:#9fd0ff;
   text-shadow:0 0 12px rgba(90,170,255,.55)}
 .brand span{color:#5f7ea6;letter-spacing:.16em}
 .sub{color:#6f87ab;margin-top:2px;word-break:break-all}
 .pill{display:inline-block;margin-top:7px;padding:2px 10px;border-radius:2px;
   letter-spacing:.22em;font-size:11px;border:1px solid}
 .pill.ready{color:#7ee787;border-color:rgba(126,231,135,.5);
   text-shadow:0 0 10px rgba(126,231,135,.6)}
 .pill.notready{color:#ff7b72;border-color:rgba(255,123,114,.5);
   text-shadow:0 0 10px rgba(255,123,114,.6)}
 .ph{color:#5f7ea6;letter-spacing:.18em;font-size:10px;margin-bottom:6px}
 .row{display:flex;justify-content:space-between;gap:10px}
 .row b{font-weight:500;color:#e6f1ff}
 .bar{height:3px;background:rgba(110,170,255,.14);margin:3px 0 9px;border-radius:1px}
 .bar i{display:block;height:100%;width:0;border-radius:1px;
   background:linear-gradient(90deg,#2f6fb0,#8fd0ff);
   box-shadow:0 0 8px rgba(120,190,255,.8)}
 .drow{display:flex;align-items:center;gap:7px;padding:2px 0;color:#8fa6c4}
 .drow .chip{width:8px;height:8px;border-radius:50%;flex:none;
   box-shadow:0 0 6px currentColor}
 .drow .nm{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
 .drow .ct{color:#e6f1ff}
 .drow.on{color:#fff}
 .drow.on .nm{text-shadow:0 0 8px rgba(140,200,255,.8)}
 .prow{display:flex;align-items:center;gap:7px;padding:2px 0;color:#8fa6c4}
 .prow .nm{width:118px;flex:none;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
 .prow .bar{flex:1;margin:0}
 .prow.on{color:#fff}
 #p-left{top:14px;left:14px;width:264px}
 #p-left .body{max-height:calc(100vh - 220px);overflow:auto}
 #p-phases{top:14px;right:14px;width:264px}
 #p-ctl{bottom:14px;left:50%;transform:translateX(-50%);display:flex;gap:7px;
   align-items:center;padding:8px 10px}
 #p-stat{bottom:14px;right:14px;text-align:right;color:#6f87ab;padding:8px 10px}
 .mb{background:rgba(10,18,32,.9);border:1px solid rgba(110,170,255,.3);color:#9fd0ff;
   padding:3px 10px;border-radius:3px;cursor:pointer;font:inherit;letter-spacing:.08em}
 .mb:hover{background:rgba(30,60,110,.6)}
 .mb.on{background:#1f6feb;border-color:#1f6feb;color:#fff}
 #tip{position:fixed;pointer-events:none;display:none;background:rgba(6,10,18,.94);
   border:1px solid rgba(110,170,255,.3);padding:4px 8px;border-radius:3px;z-index:9;
   max-width:340px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
 #finale{position:fixed;inset:0;display:none;place-items:center;z-index:8;pointer-events:none}
 #finale .box{text-align:center;background:rgba(4,8,16,.58);padding:26px 46px;
   border:1px solid rgba(110,170,255,.25);border-radius:4px}
 #finale .big{font-size:40px;letter-spacing:.34em;text-indent:.34em}
 #finale .ln{color:#8fa6c4;margin-top:10px;letter-spacing:.12em}
 #fail{position:fixed;inset:0;display:none;place-items:center;z-index:20}
 #fail .box{max-width:560px;text-align:center;background:rgba(6,10,18,.94);
   border:1px solid rgba(255,123,114,.4);padding:26px 34px;border-radius:4px}
 #fail .big{color:#ff7b72;font-size:18px;letter-spacing:.2em;margin-bottom:10px}
 #fail .sub{margin-top:8px}
</style></head><body>
<div id="bg"></div>
<canvas id="gl"></canvas>
<div class="panel" id="p-left">
  <div class="brand">SITE-KG <span>// BUILD MATRIX</span></div>
  <div class="sub">__TITLE__</div>
  <div><span class="pill" id="verdict"></span></div>
  <div class="sub">__STATS__</div>
  <div class="body">
    <div class="ph" style="margin-top:10px">CONSTRUCTION</div>
    <div class="row"><span>NODES</span><b id="v-nodes">0/0</b></div>
    <div class="bar"><i id="b-nodes"></i></div>
    <div class="row"><span>EDGES</span><b id="v-edges">0/0</b></div>
    <div class="bar"><i id="b-edges"></i></div>
    <div class="ph">RELATION DIMENSIONS</div>
    <div id="dims"></div>
  </div>
</div>
<div class="panel" id="p-phases"><div class="ph">BUILD PHASES</div><div id="phases"></div></div>
<div class="panel" id="p-ctl">
  <button class="mb" id="b-replay" type="button">replay</button>
  <button class="mb" id="b-pause" type="button">pause</button>
  <span class="ph">speed</span>
  <button class="mb spd" type="button" data-s="0.25">0.25x</button>
  <button class="mb spd on" type="button" data-s="1">1x</button>
  <button class="mb spd" type="button" data-s="4">4x</button>
  <button class="mb spd" type="button" data-s="16">16x</button>
  <button class="mb" id="b-skip" type="button">skip</button>
</div>
<div class="panel" id="p-stat"><span id="stat"></span></div>
<div id="tip"></div>
<div id="finale"><div class="box">
  <div class="big" id="fin-verdict"></div>
  <div class="ln" id="fin-line"></div>
</div></div>
<div id="fail"><div class="box">
  <div class="big">WEBGPU UNAVAILABLE</div>
  <div class="sub" id="fail-why"></div>
  <div class="sub">This build replay renders with raw WebGPU. Open it in a
  WebGPU-capable browser (Chrome/Edge 113+, Safari 26+, Firefox 141+ on Windows).</div>
</div></div>
<script>
"""

MAIN_JS = """
const DATA = __DATA__;
const $ = id => document.getElementById(id);
const cv = $('gl');
const uni = new Float32Array(28);
const cam = {yaw: 0.7, pitch: 0.42, dist: 255};
let device, ctx, format, bg, pNode, pEdge, pHead, pPulse, module, ubo;
let N = 0, E = 0, nodes, degs, edges, births;
let t = -0.4, speed = 1, paused = false, last = performance.now();
let lastHud = 0, fps = 60, dragging = false, autoRotate = true;
let px = 0, py = 0, pickDirty = false, hoverI = -1, vpNow = null;

/* ---------- decode ---------- */
function b64(s, Ctor) {
  if (!s) return new Ctor(0);
  const bin = atob(s), n = bin.length, buf = new ArrayBuffer(n);
  const u8 = new Uint8Array(buf);
  for (let i = 0; i < n; i++) u8[i] = bin.charCodeAt(i);
  return new Ctor(buf);
}
nodes = b64(DATA.nodesB, Float32Array);
degs = b64(DATA.degsB, Float32Array);
edges = b64(DATA.edgesB, Uint32Array);
births = b64(DATA.birthsB, Float32Array);
N = nodes.length / 4;
E = edges.length / 4;
const pal = b64(DATA.palB, Float32Array);

/* ---------- shaders ---------- */
const SHADER = `
struct Uni {
  vp: mat4x4f,
  tc: vec4f,    // time, camX, camY, camZ
  misc: vec4f,  // viewportW, viewportH, flight, corpusDur
  corp: vec4f,  // corpusT0, nodeCount, edgeCount, 0
};
@group(0) @binding(0) var<uniform> U: Uni;
@group(0) @binding(1) var<storage, read> nodes: array<vec4f>;  // xyz, chapter
@group(0) @binding(2) var<storage, read> degs: array<f32>;     // sqrt(deg/max)
@group(0) @binding(3) var<storage, read> edges: array<vec4u>;  // s, t, kind, pad
@group(0) @binding(4) var<storage, read> births: array<f32>;
@group(0) @binding(5) var<storage, read> pal: array<vec4f>;    // 16 ch + 16 kinds

struct VOut {
  @location(0) uv: vec2f,
  @location(1) color: vec3f,
  @location(2) alpha: f32,
  @location(3) flash: f32,
  @builtin(position) pos: vec4f,
};
struct EOut {
  @location(0) color: vec3f,
  @location(1) alpha: f32,
  @builtin(position) pos: vec4f,
};

fn quad(i: u32) -> vec2f {
  let x = select(-1.0, 1.0, (i == 1u) || (i == 4u));
  let y = select(-1.0, 1.0, (i == 2u) || (i == 3u));
  return vec2f(x, y);
}
fn hash01(i: u32) -> f32 {
  var x = i * 747796405u + 2891336453u;
  x = ((x >> 16u) ^ x) * 2246822519u;
  x = ((x >> 13u) ^ x) * 3266489917u;
  x = (x >> 16u) ^ x;
  return f32(x) / 4294967295.0;
}
fn birth_of(i: u32) -> f32 {
  return U.corp.x + (f32(i) + 1.0) / U.corp.y * U.misc.w;
}
fn billboard(center: vec3f, corner: vec2f, sizePx: f32) -> vec4f {
  let clip = U.vp * vec4f(center, 1.0);
  var ndc = clip.xy / clip.w;
  ndc = ndc + corner * (2.0 * sizePx / U.misc.xy);
  return vec4f(ndc * clip.w, clip.z, clip.w);
}

@vertex fn vs_node(@builtin(vertex_index) vi: u32) -> VOut {
  let i = vi / 6u;
  let c = quad(vi % 6u);
  let n = nodes[i];
  let age = U.tc.x - birth_of(i);
  let grow = smoothstep(0.0, 0.45, age);
  let g = clamp(degs[i], 0.0, 1.0);
  // nodes materialize out of the core, then fly to their ring slot
  let a = hash01(i) * 6.2831853;
  let r0 = 8.0 + 18.0 * hash01(i + 977u);
  let spawn = vec3f(cos(a) * r0, (hash01(i + 131u) - 0.5) * 12.0, sin(a) * r0);
  let fly = smoothstep(0.0, 0.9, age);
  let sizePx = (1.6 + 4.8 * sqrt(g)) * (0.25 + 0.75 * grow);
  // dim individual sprites as the graph grows, so dense corpora don't blow out
  let dens = clamp(24000.0 / U.corp.y, 0.22, 1.0);
  var out: VOut;
  out.uv = c;
  out.color = pal[u32(n.w) % 16u].rgb;
  out.flash = exp(-max(age, 0.0) * 3.0);
  out.alpha = grow * (0.45 + 0.55 * g) * dens;
  out.pos = billboard(mix(spawn, n.xyz, fly), c, sizePx);
  return out;
}
@fragment fn fs_node(in: VOut) -> @location(0) vec4f {
  let r = length(in.uv);
  if (r > 1.0) { discard; }
  let core = smoothstep(0.5, 0.0, r);
  let halo = pow(smoothstep(1.0, 0.0, r), 2.2) * 0.4;
  let a = (core * 0.8 + halo) * in.alpha;
  return vec4f(in.color * (0.8 + 2.2 * in.flash) * a, a);
}

@vertex fn vs_edge(@builtin(vertex_index) vi: u32) -> EOut {
  let e = vi / 2u;
  let ed = edges[e];
  let a = nodes[ed.x].xyz;
  let b = nodes[ed.y].xyz;
  let p = clamp((U.tc.x - births[e]) / U.misc.z, 0.0, 1.0);
  var out: EOut;
  let pos = select(a, mix(a, b, p), (vi % 2u) == 1u);
  out.pos = U.vp * vec4f(pos, 1.0);
  out.color = pal[16u + ed.z % 16u].rgb * (1.0 + 0.9 * (1.0 - p));
  let edens = clamp(40000.0 / max(U.corp.z, 1.0), 0.18, 1.0);
  out.alpha = smoothstep(0.0, 0.05, p) * 0.42 * edens;
  return out;
}
@fragment fn fs_edge(in: EOut) -> @location(0) vec4f {
  return vec4f(in.color * in.alpha, in.alpha);
}

@vertex fn vs_head(@builtin(vertex_index) vi: u32) -> VOut {
  let e = vi / 6u;
  let ed = edges[e];
  let a = nodes[ed.x].xyz;
  let b = nodes[ed.y].xyz;
  let p = clamp((U.tc.x - births[e]) / U.misc.z, 0.0, 1.0);
  let c = quad(vi % 6u);
  let fade = pow(1.0 - p, 1.5);
  var out: VOut;
  out.uv = c;
  out.color = pal[16u + ed.z % 16u].rgb;
  out.alpha = fade * 1.15;
  out.flash = 0.0;
  out.pos = billboard(mix(a, b, p), c, 3.0 + 6.0 * fade);
  return out;
}

@vertex fn vs_pulse(@builtin(vertex_index) vi: u32) -> VOut {
  let e = vi / 6u;
  let ed = edges[e];
  let b = nodes[ed.y].xyz;
  let ph = (U.tc.x - births[e] - U.misc.z) / 0.75;   // since arrival
  let c = quad(vi % 6u);
  let alive = step(0.0, ph) * step(ph, 1.0);
  var out: VOut;
  out.uv = c;
  out.color = pal[16u + ed.z % 16u].rgb;
  out.alpha = pow(1.0 - clamp(ph, 0.0, 1.0), 1.6) * 0.9 * alive;
  out.flash = 0.0;
  out.pos = billboard(b, c, 8.0 + 40.0 * clamp(ph, 0.0, 1.0));
  return out;
}
@fragment fn fs_pulse(in: VOut) -> @location(0) vec4f {
  let r = length(in.uv);
  if (r > 1.0) { discard; }
  let band = exp(-pow((r - 0.45) * 4.5, 2.0));
  let a = band * in.alpha;
  return vec4f(in.color * a, a);
}
`;

/* ---------- math ---------- */
function perspective(fovy, aspect, near, far) {
  const f = 1 / Math.tan(fovy / 2), nf = 1 / (near - far);
  return new Float32Array([f / aspect, 0, 0, 0, 0, f, 0, 0,
                           0, 0, (far + near) * nf, -1,
                           0, 0, 2 * far * near * nf, 0]);
}
function lookAt(eye, c, up) {
  let z = [eye[0] - c[0], eye[1] - c[1], eye[2] - c[2]];
  const zl = Math.hypot(z[0], z[1], z[2]) || 1;
  z = [z[0] / zl, z[1] / zl, z[2] / zl];
  let x = [up[1] * z[2] - up[2] * z[1], up[2] * z[0] - up[0] * z[2],
           up[0] * z[1] - up[1] * z[0]];
  const xl = Math.hypot(x[0], x[1], x[2]) || 1;
  x = [x[0] / xl, x[1] / xl, x[2] / xl];
  const y = [z[1] * x[2] - z[2] * x[1], z[2] * x[0] - z[0] * x[2],
             z[0] * x[1] - z[1] * x[0]];
  return new Float32Array([x[0], y[0], z[0], 0, x[1], y[1], z[1], 0,
                           x[2], y[2], z[2], 0,
                           -(x[0] * eye[0] + x[1] * eye[1] + x[2] * eye[2]),
                           -(y[0] * eye[0] + y[1] * eye[1] + y[2] * eye[2]),
                           -(z[0] * eye[0] + z[1] * eye[1] + z[2] * eye[2]), 1]);
}
function mul(a, b) {  // a*b, column-major 4x4: C[col i][row j] = sum A[j][k]*B[k][i]
  const o = new Float32Array(16);
  for (let i = 0; i < 4; i++) for (let j = 0; j < 4; j++) {
    let s = 0;
    for (let k = 0; k < 4; k++) s += a[k * 4 + j] * b[i * 4 + k];
    o[i * 4 + j] = s;
  }
  return o;
}
function camPos() {
  const cp = Math.cos(cam.pitch), sp = Math.sin(cam.pitch);
  return [Math.sin(cam.yaw) * cp * cam.dist, sp * cam.dist,
          Math.cos(cam.yaw) * cp * cam.dist];
}

/* ---------- HUD ---------- */
const dimRows = {}, phaseRows = {};
function fmt(x) { return x.toLocaleString('en-US'); }
function buildHud() {
  const dv = $('dims');
  DATA.kinds.forEach(k => {
    const d = document.createElement('div');
    d.className = 'drow';
    d.innerHTML = '<span class="chip" style="color:' + k.color +
      ';background:' + k.color + '"></span><span class="nm">' + k.name +
      '</span><span class="ct">0/0</span>';
    dv.appendChild(d);
    dimRows[k.name] = d;
  });
  const pv = $('phases');
  DATA.phases.forEach(p => {
    const d = document.createElement('div');
    d.className = 'prow';
    d.innerHTML = '<span class="nm">' + p.label + '</span><span class="bar"><i></i>' +
      '</span><span class="ct">' + p.count + '</span>';
    pv.appendChild(d);
    phaseRows[p.label] = d;
  });
  const v = $('verdict');
  v.textContent = DATA.verdict;
  v.className = 'pill ' + (DATA.verdict === 'READY' ? 'ready' : 'notready');
}
function born(p) {
  if (t >= p.t0 + p.dur) return p.count;
  if (t <= p.t0) return 0;
  return Math.round((t - p.t0) / p.dur * p.count);
}
function phaseAt() {
  if (t <= DATA.phases[0].t0) return DATA.phases[0];
  for (const p of DATA.phases) if (t >= p.t0 && t < p.t0 + p.dur) return p;
  return DATA.phases[DATA.phases.length - 1];
}
function kindCount(name) {
  const p = DATA.phases.find(p => p.name === name);
  return p ? p.count : 0;
}
function updateHud() {
  const cur = phaseAt();
  const nb = t >= DATA.corpusT0
    ? Math.min(N, Math.round((t - DATA.corpusT0) / DATA.corpusDur * N)) : 0;
  $('v-nodes').textContent = fmt(nb) + '/' + fmt(N);
  $('b-nodes').style.width = (100 * nb / Math.max(N, 1)) + '%';
  let eb = 0;
  DATA.phases.forEach(p => {
    if (p.nodes) return;
    const c = born(p);
    eb += c;
    const row = phaseRows[p.label];
    if (row) {
      row.classList.toggle('on', p === cur);
      row.querySelector('.bar i').style.width = (100 * c / p.count) + '%';
    }
  });
  $('v-edges').textContent = fmt(eb) + '/' + fmt(E);
  $('b-edges').style.width = (100 * eb / Math.max(E, 1)) + '%';
  DATA.kinds.forEach(k => {
    const row = dimRows[k.name];
    if (!row) return;
    row.querySelector('.ct').textContent =
      fmt(born(DATA.phases.find(p => p.name === k.name))) + '/' + fmt(kindCount(k.name));
    row.classList.toggle('on', cur.name === k.name);
  });
  $('stat').textContent = 'FPS ' + Math.round(fps) + ' · phase ' + cur.label +
    ' · ' + t.toFixed(1) + 's / ' + DATA.total.toFixed(1) + 's · ' + speed + 'x' +
    (paused ? ' · paused' : '') + ' · WebGPU';
  const fin = t >= DATA.total;
  $('finale').style.display = fin ? 'grid' : 'none';
  if (fin) {
    $('fin-verdict').textContent = DATA.verdict;
    $('fin-verdict').style.color = DATA.verdict === 'READY' ? '#7ee787' : '#ff7b72';
    $('fin-line').textContent = fmt(N) + ' nodes · ' + fmt(E) + ' edges' +
      (DATA.kinds.length ? ' · ' + DATA.kinds.map(k =>
        k.name + ' ' + kindCount(k.name)).join(' · ') : '');
  }
}

/* ---------- picking (inspection scale only) ---------- */
function project(i, vp) {
  const x = nodes[i * 4], y = nodes[i * 4 + 1], z = nodes[i * 4 + 2];
  const w = vp[3] * x + vp[7] * y + vp[11] * z + vp[15];
  if (w <= 0) return null;
  const cx = vp[0] * x + vp[4] * y + vp[8] * z + vp[12];
  const cy = vp[1] * x + vp[5] * y + vp[9] * z + vp[13];
  return [innerWidth * (cx / w + 1) / 2, innerHeight * (1 - cy / w) / 2];
}
function pick() {
  pickDirty = false;
  const tip = $('tip');
  if (N > 20000 || !vpNow) { tip.style.display = 'none'; return; }
  let best = -1, bd = 14 * 14;
  for (let i = 0; i < N; i++) {
    const s = project(i, vpNow);
    if (!s) continue;
    const dx = s[0] - px, dy = s[1] - py;
    if (dx * dx + dy * dy < bd) { bd = dx * dx + dy * dy; best = i; }
  }
  hoverI = best;
  if (best < 0) { tip.style.display = 'none'; return; }
  const title = DATA.titles ? DATA.titles[best] : ('node ' + best);
  tip.textContent = title + ' · ' + DATA.chapters[nodes[best * 4 + 3] % 16].name;
  tip.style.display = 'block';
  tip.style.left = (px + 14) + 'px';
  tip.style.top = (py + 12) + 'px';
}

/* ---------- controls ---------- */
function bindControls() {
  $('b-replay').onclick = () => { t = -0.4; paused = false; };
  $('b-pause').onclick = () => {
    paused = !paused;
    $('b-pause').textContent = paused ? 'resume' : 'pause';
  };
  document.querySelectorAll('.spd').forEach(b => {
    b.onclick = () => {
      speed = parseFloat(b.dataset.s);
      document.querySelectorAll('.spd').forEach(o => o.classList.remove('on'));
      b.classList.add('on');
    };
  });
  $('b-skip').onclick = () => { t = DATA.total + 0.05; };
  addEventListener('keydown', e => {
    if (e.code === 'Space') { e.preventDefault(); $('b-pause').onclick(); }
  });
  let downX = 0, downY = 0;
  cv.addEventListener('pointerdown', e => {
    dragging = true; downX = e.clientX; downY = e.clientY;
    cv.setPointerCapture(e.pointerId);
  });
  cv.addEventListener('pointerup', e => {
    dragging = false;
    if (Math.hypot(e.clientX - downX, e.clientY - downY) < 5) {
      px = e.clientX; py = e.clientY; pickDirty = true;
    }
  });
  cv.addEventListener('pointermove', e => {
    if (dragging) {
      cam.yaw -= (e.clientX - downX) * 0.005;
      cam.pitch = Math.max(-1.45, Math.min(1.45,
        cam.pitch + (e.clientY - downY) * 0.005));
      downX = e.clientX; downY = e.clientY;
      autoRotate = false;
    } else {
      px = e.clientX; py = e.clientY;
      if (N <= 20000) pickDirty = true;
    }
  });
  cv.addEventListener('wheel', e => {
    e.preventDefault();
    cam.dist = Math.max(40, Math.min(900, cam.dist * Math.exp(e.deltaY * 0.001)));
  }, {passive: false});
  addEventListener('resize', resize);
}

/* ---------- gpu ---------- */
function resize() {
  const dpr = Math.min(devicePixelRatio || 1, 2);
  cv.width = Math.max(1, Math.round(innerWidth * dpr));
  cv.height = Math.max(1, Math.round(innerHeight * dpr));
  uni[20] = cv.width; uni[21] = cv.height;
}
function storage(arr) {
  const b = device.createBuffer({
    size: Math.max(4, (arr.byteLength + 3) & ~3),
    usage: GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_DST,
  });
  device.queue.writeBuffer(b, 0, arr);
  return b;
}
function fail(why) {
  $('fail-why').textContent = why;
  $('fail').style.display = 'grid';
}
async function main() {
  try {
    if (!navigator.gpu) return fail('navigator.gpu is undefined');
    const adapter = await navigator.gpu.requestAdapter();
    if (!adapter) return fail('requestAdapter() returned null');
    device = await adapter.requestDevice();
    ctx = cv.getContext('webgpu');
    format = navigator.gpu.getPreferredCanvasFormat();
    ctx.configure({device, format, alphaMode: 'premultiplied'});
    resize();
    module = device.createShaderModule({code: SHADER});
    const bgl = device.createBindGroupLayout({entries: [
      {binding: 0, visibility: GPUShaderStage.VERTEX | GPUShaderStage.FRAGMENT,
       buffer: {type: 'uniform'}},
      {binding: 1, visibility: GPUShaderStage.VERTEX, buffer: {type: 'read-only-storage'}},
      {binding: 2, visibility: GPUShaderStage.VERTEX, buffer: {type: 'read-only-storage'}},
      {binding: 3, visibility: GPUShaderStage.VERTEX, buffer: {type: 'read-only-storage'}},
      {binding: 4, visibility: GPUShaderStage.VERTEX, buffer: {type: 'read-only-storage'}},
      {binding: 5, visibility: GPUShaderStage.VERTEX, buffer: {type: 'read-only-storage'}},
    ]});
    ubo = device.createBuffer({size: 128, usage: GPUBufferUsage.UNIFORM |
      GPUBufferUsage.COPY_DST});
    bg = device.createBindGroup({layout: bgl, entries: [
      {binding: 0, resource: {buffer: ubo}},
      {binding: 1, resource: {buffer: storage(nodes)}},
      {binding: 2, resource: {buffer: storage(degs)}},
      {binding: 3, resource: {buffer: storage(edges)}},
      {binding: 4, resource: {buffer: storage(births)}},
      {binding: 5, resource: {buffer: storage(pal)}},
    ]});
    const layout = device.createPipelineLayout({bindGroupLayouts: [bgl]});
    const blend = {color: {srcFactor: 'one', dstFactor: 'one'},
                   alpha: {srcFactor: 'one', dstFactor: 'one'}};
    const mk = (vs, fs, topo) => device.createRenderPipeline({
      layout,
      vertex: {module, entryPoint: vs},
      fragment: {module, entryPoint: fs, targets: [{format, blend}]},
      primitive: {topology: topo},
    });
    pEdge = mk('vs_edge', 'fs_edge', 'line-list');
    pNode = mk('vs_node', 'fs_node', 'triangle-list');
    pHead = mk('vs_head', 'fs_node', 'triangle-list');
    pPulse = mk('vs_pulse', 'fs_pulse', 'triangle-list');
    bindControls();
    requestAnimationFrame(loop);
  } catch (e) {
    fail(String(e && e.message || e));
  }
}

/* ---------- frame ---------- */
function loop(now) {
  requestAnimationFrame(loop);
  const dt = Math.min(0.1, (now - last) / 1000);
  last = now;
  fps += (1000 / Math.max(dt, 1) - fps) * 0.05;
  if (!paused) t += dt * speed;
  if (autoRotate && !dragging) cam.yaw += dt * 0.06;
  const eye = camPos();
  const vp = mul(perspective(0.9, innerWidth / Math.max(innerHeight, 1), 1, 4000),
                 lookAt(eye, [0, 0, 0], [0, 1, 0]));
  vpNow = vp;
  uni.set(vp, 0);
  uni[16] = t; uni[17] = eye[0]; uni[18] = eye[1]; uni[19] = eye[2];
  uni[22] = DATA.flight; uni[23] = DATA.corpusDur;
  uni[24] = DATA.corpusT0; uni[25] = N; uni[26] = E;
  device.queue.writeBuffer(ubo, 0, uni);
  const enc = device.createCommandEncoder();
  const view = ctx.getCurrentTexture().createView();
  const pass = enc.beginRenderPass({colorAttachments: [
    {view, clearValue: {r: 0, g: 0, b: 0, a: 0}, loadOp: 'clear', storeOp: 'store'},
  ]});
  pass.setBindGroup(0, bg);
  pass.setPipeline(pEdge); pass.draw(2 * E);
  pass.setPipeline(pPulse); pass.draw(6 * E);
  pass.setPipeline(pHead); pass.draw(6 * E);
  pass.setPipeline(pNode); pass.draw(6 * N);
  pass.end();
  device.queue.submit([enc.finish()]);
  if (now - lastHud > 120 || pickDirty) { lastHud = now; updateHud(); }
  if (pickDirty && N <= 20000) pick();
}
buildHud();
main();
</script></body></html>
"""
