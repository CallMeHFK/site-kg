"""Zero-dependency static HTML viewer for a site's graph.

Purpose: human acceptance and graph-quality inspection (is READY honest? does a
cluster make sense?). AI access stays on the MCP tools. One self-contained file:
nodes/edges embedded, 2D canvas force layout in vanilla JS, color by chapter.
"""
from __future__ import annotations

import json
from pathlib import Path

TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8"><title>__TITLE__</title>
<style>
 body{margin:0;font:13px/1.4 system-ui,sans-serif;background:#0d1117;color:#e6edf3}
 #bar{position:fixed;top:0;left:0;right:0;padding:8px 12px;background:#161b22;
      border-bottom:1px solid #30363d;display:flex;gap:12px;align-items:center;z-index:2}
 #bar input{background:#0d1117;border:1px solid #30363d;color:#e6edf3;
      padding:4px 8px;border-radius:4px}
 #info{position:fixed;right:12px;top:52px;max-width:340px;background:#161b22;
       border:1px solid #30363d;border-radius:6px;padding:10px;display:none}
 #info a{color:#58a6ff}
 canvas{display:block}
 .muted{color:#8b949e}
</style></head><body>
<div id="bar"><b>__TITLE__</b><span class="muted">__STATS__</span>
 <input id="q" placeholder="filter title…"><span id="cnt" class="muted"></span></div>
<div id="info"></div>
<canvas id="cv"></canvas>
<script>
const G = __GRAPH__;
const chapters = [...new Set(G.nodes.map(n=>n.c))];
const palette = ["#58a6ff","#3fb950","#d29922","#f778ba","#a371f7","#76e3ea",
 "#ffa657","#ff7b72","#7ee787","#e3b341","#79c0ff","#56d4bc"];
const colorOf = {}; chapters.forEach((c,i)=>colorOf[c]=palette[i%palette.length]);
const nodes = G.nodes.map(n=>({...n,x:Math.random()*800,y:Math.random()*600,vx:0,vy:0}));
const idx = new Map(nodes.map(n=>[n.i,n]));
const edges = G.edges.filter(e=>idx.has(e.s)&&idx.has(e.t)).map(e=>[idx.get(e.s),idx.get(e.t),e.k]);
// hub/contains edges dim the layout; ref edges carry the information
const layoutEdges = edges.filter(e=>e[2]==="ref");
const cv = document.getElementById("cv"), ctx = cv.getContext("2d");
let W,H,hover=null;
function resize(){W=cv.width=innerWidth;H=cv.height=innerHeight-44;cv.style.marginTop="44px"}
addEventListener("resize",resize);resize();
function tick(){
  for(const n of nodes){n.vx+=(W/2-n.x)*0.0005;n.vy+=(H/2-n.y)*0.0005}
  for(let i=0;i<nodes.length;i++)for(let j=i+1;j<nodes.length;j++){
    const a=nodes[i],b=nodes[j];let dx=a.x-b.x,dy=a.y-b.y,d2=dx*dx+dy*dy+100;
    const f=Math.min(400/d2,2);dx*=f*0.05;dy*=f*0.05;a.vx+=dx;a.vy+=dy;b.vx-=dx;b.vy-=dy}
  for(const [a,b] of layoutEdges){
    let dx=b.x-a.x,dy=b.y-a.y;const d=Math.hypot(dx,dy)||1,f=(d-60)*0.002;
    dx/=d;dy/=d;a.vx+=dx*f;a.vy+=dy*f;b.vx-=dx*f;b.vy-=dy*f}
  for(const n of nodes){n.vx*=0.85;n.vy*=0.85;n.x+=n.vx;n.y+=n.vy}
  draw();requestAnimationFrame(tick)}
function vis(n){return !Q||n.t.toLowerCase().includes(Q)}
function draw(){
  ctx.clearRect(0,0,W,H);
  ctx.globalAlpha=0.25;
  for(const [a,b,k] of edges){ if(!vis(a)||!vis(b))continue;
    ctx.strokeStyle=k==="hub"?"#30363d":k==="contains"?"#484f58":"#58a6ff";
    ctx.beginPath();ctx.moveTo(a.x,a.y+44);ctx.lineTo(b.x,b.y+44);ctx.stroke()}
  ctx.globalAlpha=1;
  for(const n of nodes){ if(!vis(n))continue;
    ctx.fillStyle=colorOf[n.c]||"#8b949e";ctx.beginPath();
    ctx.arc(n.x,n.y+44,n===hover?6:3+Math.min(n.g,6),0,7);ctx.fill()}
  let c=0;for(const n of nodes)if(vis(n))c++;
  document.getElementById("cnt").textContent=c+"/"+nodes.length}
let Q="";
document.getElementById("q").oninput=e=>{Q=e.target.value.toLowerCase()};
cv.onclick=e=>{const r=cv.getBoundingClientRect();let best=null,bd=1e9;
  for(const n of nodes){const d=Math.hypot(n.x-(e.clientX-r.left),n.y+44-(e.clientY-r.top));
    if(d<bd){bd=d;best=n}}
  if(best&&bd<20){hover=best;const el=document.getElementById("info");el.style.display="block";
    el.innerHTML=`<b>${best.t}</b><br><span class="muted">${best.i}</span><br>`
      +`chapter: ${best.c} · in-degree: ${best.g}<br>`
      +`<a href="${best.u}" target="_blank">source ↗</a>`}};
tick();
</script></body></html>"""


def render_viewer(graph: dict, meta: dict, out_path: Path) -> Path:
    stats = (f'{meta.get("docs", len(graph["nodes"]))} pages'
             f' · {len(graph["edges"])} edges'
             f' · verdict {meta.get("verdict", "?")}')
    html = (TEMPLATE
            .replace("__TITLE__", meta.get("url", "site-kg graph"))
            .replace("__STATS__", stats)
            .replace("__GRAPH__", json.dumps(graph, ensure_ascii=False)))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path
