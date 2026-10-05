"""Zero-network-dependency static HTML viewer for a site's graph: 3D Three.js.

Purpose: human acceptance and graph-quality inspection (is READY honest? does a
cluster make sense?). AI access stays on the MCP tools. The Three.js bundle
(vendor/three-viewer.min.js, built by tools/build_viewer_bundle.mjs) is embedded
inline so the file works offline. Layout is computed here, seeded, so a rebuild
does not reshuffle the graph.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

VENDOR = Path(__file__).resolve().parent / "vendor" / "three-viewer.min.js"

HEAD = """<!doctype html>
<html><head><meta charset="utf-8"><title>__TITLE__</title>
<style>
 body{margin:0;font:13px/1.4 system-ui,sans-serif;background:#0d1117;color:#e6edf3;overflow:hidden}
 #bar{position:fixed;top:0;left:0;right:0;padding:8px 12px;background:#161b22;
      border-bottom:1px solid #30363d;display:flex;gap:12px;align-items:center;z-index:2}
 #bar input{background:#0d1117;border:1px solid #30363d;color:#e6edf3;
      padding:4px 8px;border-radius:4px}
 #info{position:fixed;right:12px;top:52px;max-width:340px;background:#161b22;
       border:1px solid #30363d;border-radius:6px;padding:10px;display:none}
 #info a{color:#58a6ff}
 .muted{color:#8b949e}
</style></head><body>
<div id="bar"><b>__TITLE__</b><span class="muted">__STATS__</span>
 <input id="q" placeholder="filter title…"><span id="cnt" class="muted"></span>
 <span class="muted">drag rotate · wheel zoom</span></div>
<div id="info"></div>
<script>
__BUNDLE__
</script>
"""

MAIN_JS = """
<script>
const G = __GRAPH__;
const {THREE, OrbitControls} = T3;
const chapters = [...new Set(G.nodes.map(n=>n.c))];
const palette = [0x58a6ff,0x3fb950,0xd29922,0xf778ba,0xa371f7,0x76e3ea,
                 0xffa657,0xff7b72,0x7ee787,0xe3b341,0x79c0ff,0x56d4bc];
const colorOf = {}; chapters.forEach((c,i)=>colorOf[c]=palette[i%palette.length]);
const renderer = new THREE.WebGLRenderer({antialias:true});
renderer.setSize(innerWidth, innerHeight-44);
renderer.domElement.style.marginTop = "44px";
document.body.appendChild(renderer.domElement);
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0d1117);
const camera = new THREE.PerspectiveCamera(60, innerWidth/(innerHeight-44), 0.1, 1e5);
const ctl = new OrbitControls(camera, renderer.domElement);

const idx = new Map(G.nodes.map((n,i)=>[n.i,i]));
const pts = new Float32Array(G.nodes.length*3);
const cols = new Float32Array(G.nodes.length*3);
G.nodes.forEach((n,i)=>{
  pts[i*3]=n.x; pts[i*3+1]=n.y; pts[i*3+2]=n.z;
  const c = new THREE.Color(colorOf[n.c] ?? 0x8b949e);
  cols[i*3]=c.r; cols[i*3+1]=c.g; cols[i*3+2]=c.b;});
const pgeo = new THREE.BufferGeometry();
pgeo.setAttribute("position", new THREE.BufferAttribute(pts,3));
pgeo.setAttribute("color", new THREE.BufferAttribute(cols,3));
const cloud = new THREE.Points(pgeo, new THREE.PointsMaterial({size:3.5, vertexColors:true}));
scene.add(cloud);
// ref edges carry the information; hub/contains edges would bury it
const refE = G.edges.filter(e=>e.k==="ref" && idx.has(e.s) && idx.has(e.t));
const lpts = new Float32Array(refE.length*6);
refE.forEach((e,i)=>{
  const a=G.nodes[idx.get(e.s)], b=G.nodes[idx.get(e.t)];
  lpts.set([a.x,a.y,a.z, b.x,b.y,b.z], i*6);});
const lgeo = new THREE.BufferGeometry();
lgeo.setAttribute("position", new THREE.BufferAttribute(lpts,3));
const lines = new THREE.LineSegments(lgeo,
  new THREE.LineBasicMaterial({color:0x58a6ff, transparent:true, opacity:0.22}));
scene.add(lines);

let cx=0,cy=0,cz=0;
G.nodes.forEach(n=>{cx+=n.x;cy+=n.y;cz+=n.z});
const N=Math.max(G.nodes.length,1);
cx/=N;cy/=N;cz/=N;
// frame the actual cloud: camera distance from the bounding radius, not node count
let R=1;
G.nodes.forEach(n=>{const d=Math.hypot(n.x-cx,n.y-cy,n.z-cz);if(d>R)R=d});
camera.position.set(cx, cy, cz + R*2.2);
ctl.target.set(cx,cy,cz);

let Q="";
document.getElementById("q").oninput=e=>{
  Q=e.target.value.toLowerCase();
  G.nodes.forEach((n,i)=>{
    const vis = !Q || n.t.toLowerCase().includes(Q);
    const c = new THREE.Color(colorOf[n.c] ?? 0x8b949e);
    cols[i*3]=vis?c.r:0.03; cols[i*3+1]=vis?c.g:0.03; cols[i*3+2]=vis?c.b:0.03;});
  pgeo.attributes.color.needsUpdate=true;
  let c=0; G.nodes.forEach(n=>{if(!Q||n.t.toLowerCase().includes(Q))c++});
  document.getElementById("cnt").textContent=c+"/"+N;};
document.getElementById("cnt").textContent=N+"/"+N;

const ray = new THREE.Raycaster();
ray.params.Points.threshold = 4;
const mouse = new THREE.Vector2();
renderer.domElement.onclick = e=>{
  mouse.set(e.clientX/innerWidth*2-1, -(e.clientY-44)/(innerHeight-44)*2+1);
  ray.setFromCamera(mouse, camera);
  const hit = ray.intersectObject(cloud)[0];
  if(hit!==undefined){
    const n = G.nodes[hit.index];
    const el = document.getElementById("info");
    el.style.display="block";
    el.innerHTML=`<b>${n.t}</b><br><span class="muted">${n.i}</span><br>`
      +`chapter: ${n.c} · in-degree: ${n.g}<br>`
      +`<a href="${n.u}" target="_blank">source ↗</a>`;}};

function loop(){ ctl.update(); renderer.render(scene, camera); requestAnimationFrame(loop); }
addEventListener("resize", ()=>{
  camera.aspect = innerWidth/(innerHeight-44);
  camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight-44);});
loop();
</script></body></html>
"""


def layout_3d(graph: dict, seed: int = 7, iterations: int = 80) -> None:
    """Seeded spring-repulsion layout in place. ref edges pull; a sampled repulsion
    term pushes. Deterministic: same graph + seed => same coordinates."""
    nodes = graph["nodes"]
    rng = random.Random(seed)
    pos = {n["i"]: [rng.uniform(-50, 50), rng.uniform(-50, 50), rng.uniform(-50, 50)]
           for n in nodes}
    idx = {n["i"]: n for n in nodes}
    springs = [(e["s"], e["t"]) for e in graph["edges"]
               if e["k"] == "ref" and e["s"] in idx and e["t"] in idx]
    for it in range(iterations):
        cool = 1.0 - it / iterations
        for a, b in springs:
            pa, pb = pos[a], pos[b]
            d = [pb[k] - pa[k] for k in range(3)]
            dist = max(sum(x * x for x in d) ** 0.5, 1.0)
            f = (dist - 30.0) * 0.02 * cool
            for k in range(3):
                pa[k] += d[k] / dist * f
                pb[k] -= d[k] / dist * f
        for n in nodes:
            p = pos[n["i"]]
            rx = ry = rz = 0.0
            for _ in range(min(20, len(nodes))):
                q = pos[nodes[rng.randrange(len(nodes))]["i"]]
                d = [p[k] - q[k] for k in range(3)]
                d2 = max(sum(x * x for x in d), 25.0)
                f = 200.0 / d2 * cool
                dist = d2 ** 0.5
                rx += d[0] / dist * f
                ry += d[1] / dist * f
                rz += d[2] / dist * f
            p[0] += rx
            p[1] += ry
            p[2] += rz
    for n in nodes:
        n["x"], n["y"], n["z"] = (round(v, 1) for v in pos[n["i"]])


def render_viewer(graph: dict, meta: dict, out_path: Path) -> Path:
    g = {"nodes": [dict(n) for n in graph["nodes"]], "edges": graph["edges"]}
    if not all("z" in n for n in g["nodes"]):
        layout_3d(g)
    bundle = VENDOR.read_text(encoding="utf-8")
    stats = (f'{meta.get("docs", len(g["nodes"]))} pages'
             f' · {len(g["edges"])} edges'
             f' · verdict {meta.get("verdict", "?")}')
    html = (HEAD
            .replace("__TITLE__", meta.get("url", "site-kg graph"))
            .replace("__STATS__", stats)
            .replace("__BUNDLE__", bundle)
            + MAIN_JS.replace("__GRAPH__", json.dumps(g, ensure_ascii=False)))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path
