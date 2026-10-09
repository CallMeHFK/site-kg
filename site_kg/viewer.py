"""Zero-network-dependency static HTML viewer for a site's graph: 3D Three.js.

Purpose: human acceptance and graph-quality inspection (is READY honest? does a
cluster make sense?). AI access stays on the MCP tools. The Three.js bundle
(vendor/three-viewer.min.js, built by tools/build_viewer_bundle.mjs) is embedded
inline so the file works offline. Layout is computed here, seeded, so a rebuild
does not reshuffle the graph.
"""
from __future__ import annotations

import json
import math
import random
from pathlib import Path

VENDOR = Path(__file__).resolve().parent / "vendor" / "three-viewer.min.js"

HEAD = """﻿<!doctype html>
<html><head><meta charset="utf-8"><title>__TITLE__</title>
<style>
 body{margin:0;font:13px/1.4 system-ui,sans-serif;background:#0d1117;color:#e6edf3;overflow:hidden}
 #bar{position:fixed;top:0;left:0;right:0;padding:8px 12px;background:#161b22;
      border-bottom:1px solid #30363d;display:flex;gap:12px;align-items:center;z-index:2}
 #bar input{background:#0d1117;border:1px solid #30363d;color:#e6edf3;
      padding:4px 8px;border-radius:4px;width:230px}
 .swrap{position:relative}
 #sres{display:none;position:absolute;top:calc(100% + 6px);left:0;width:360px;max-height:300px;
       overflow-y:auto;background:#161b22;border:1px solid #30363d;border-radius:6px;z-index:40}
 #sres.open{display:block}
 .sr{padding:6px 9px;cursor:pointer;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
 .sr:hover{background:#1f2937}
 .sr .m{color:#8b949e;font-size:11px;margin-left:8px}
 .mb{background:#0d1117;border:1px solid #30363d;color:#8b949e;padding:3px 9px;
     border-radius:5px;cursor:pointer;font:inherit}
 .mb.on{background:#1f6feb;border-color:#1f6feb;color:#fff}
 #info{position:fixed;right:12px;top:52px;max-width:340px;background:#161b22;
       border:1px solid #30363d;border-radius:6px;padding:10px;display:none}
 #info a{color:#58a6ff}
 #tip{position:fixed;display:none;pointer-events:none;background:#161b22;border:1px solid #30363d;
      border-radius:5px;padding:4px 8px;font-size:12px;z-index:30;max-width:340px;
      white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
 #status{position:fixed;left:12px;bottom:10px;color:#8b949e;font-size:11px;z-index:3}
 .muted{color:#8b949e}
</style></head><body>
<div id="bar"><b>__TITLE__</b><span class="muted">__STATS__</span>
 <button id="m-all" class="mb on" type="button">cloud</button>
 <button id="m-agg" class="mb" type="button">chapters</button>
 <span class="swrap"><input id="q" placeholder="filter / search title… (/)">
 <div id="sres" role="listbox" aria-label="search results"></div></span>
 <span id="cnt" class="muted"></span>
 <span class="muted">drag rotate · wheel zoom · click node = focus · esc clears</span></div>
<div id="info"></div>
<div id="tip"></div>
<div id="status"></div>
<script>
__BUNDLE__
</script>
"""

MAIN_JS = """﻿
<script>
const G = __GRAPH__;
const {THREE, OrbitControls} = T3;
const BAR = 44;
const chapters = [...new Set(G.nodes.map(n=>n.c))];
const palette = [0x58a6ff,0x3fb950,0xd29922,0xf778ba,0xa371f7,0x76e3ea,
                 0xffa657,0xff7b72,0x7ee787,0xe3b341,0x79c0ff,0x56d4bc];
const colorOf = {}; chapters.forEach((c,i)=>colorOf[c]=palette[i%palette.length]);
const renderer = new THREE.WebGLRenderer({antialias:true});
renderer.setSize(innerWidth, innerHeight-BAR);
renderer.domElement.style.marginTop = BAR+"px";
document.body.appendChild(renderer.domElement);
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0d1117);
const camera = new THREE.PerspectiveCamera(60, innerWidth/(innerHeight-BAR), 0.1, 1e5);
const ctl = new OrbitControls(camera, renderer.domElement);
ctl.autoRotate = true; ctl.autoRotateSpeed = 0.6;  /* idle spin until first interaction */
let userTouched = false;
ctl.addEventListener("start", ()=>{ userTouched = true; ctl.autoRotate = false; });

const idx = new Map(G.nodes.map((n,i)=>[n.i,i]));
const Nn = G.nodes.length;
/* ref edges carry the information; hub/contains edges would bury it */
const refE = G.edges.filter(e=>e.k==="ref" && idx.has(e.s) && idx.has(e.t));
const En = refE.length;
const adj = Array.from({length:Nn}, ()=>[]);
refE.forEach((e,ei)=>{ const a=idx.get(e.s), b=idx.get(e.t);
  adj[a].push([b,ei]); adj[b].push([a,ei]); });

/* ---------- node cloud: per-node size by in-degree + additive halo glow ---------- */
const pos = new Float32Array(Nn*3), col = new Float32Array(Nn*3);
const siz = new Float32Array(Nn), ndim = new Float32Array(Nn).fill(1);
let gmax = 1; G.nodes.forEach(n=>{ if((n.g||0)>gmax) gmax = n.g; });
G.nodes.forEach((n,i)=>{
  pos[i*3]=n.x; pos[i*3+1]=n.y; pos[i*3+2]=n.z;
  const c = new THREE.Color(colorOf[n.c] ?? 0x8b949e);
  col[i*3]=c.r; col[i*3+1]=c.g; col[i*3+2]=c.b;
  siz[i] = 2.2 + 3.4*Math.sqrt((n.g||0)/gmax); });
const pgeo = new THREE.BufferGeometry();
pgeo.setAttribute("position", new THREE.BufferAttribute(pos,3));
pgeo.setAttribute("color", new THREE.BufferAttribute(col,3));
pgeo.setAttribute("a_size", new THREE.BufferAttribute(siz,1));
pgeo.setAttribute("a_dim", new THREE.BufferAttribute(ndim,1));
const nodeUni = {u_px:{value:1}};
const NODE_VS = "attribute float a_size; attribute float a_dim; attribute vec3 color;"
  + " uniform float u_px; varying vec3 vC; varying float vD;"
  + " void main(){ vC=color; vD=a_dim;"
  + "   vec4 mv = modelViewMatrix*vec4(position,1.0);"
  + "   gl_PointSize = a_size*u_px*(300.0/max(-mv.z,1.0));"
  + "   gl_Position = projectionMatrix*mv; }";
const cloud = new THREE.Points(pgeo, new THREE.ShaderMaterial({
  uniforms:nodeUni, vertexShader:NODE_VS,
  fragmentShader:"varying vec3 vC; varying float vD;"
    + " void main(){ float r = length(gl_PointCoord-0.5); if(r>0.5) discard;"
    + "   float a = smoothstep(0.5,0.32,r);"
    + "   gl_FragColor = vec4(vC*(0.25+0.75*vD), a*(0.15+0.85*vD)); }",
  transparent:true, depthWrite:false }));
cloud.renderOrder = 2; scene.add(cloud);
/* halo: same geometry, additive, 3.1x point size -> bloom-like glow, no postprocessing */
const halo = new THREE.Points(pgeo, new THREE.ShaderMaterial({
  uniforms:nodeUni, vertexShader:NODE_VS.replace("a_size*u_px","a_size*u_px*3.1"),
  fragmentShader:"varying vec3 vC; varying float vD;"
    + " void main(){ float r = length(gl_PointCoord-0.5); if(r>0.5) discard;"
    + "   float a = pow(smoothstep(0.5,0.0,r),2.2)*0.30*vD;"
    + "   gl_FragColor = vec4(vC, a); }",
  transparent:true, depthWrite:false, blending:THREE.AdditiveBlending }));
halo.renderOrder = 1; scene.add(halo);

/* ---------- edges: GPU distance fade + per-edge focus dim ---------- */
const lpos = new Float32Array(En*6), lcol = new Float32Array(En*6);
const lbase = new Float32Array(En*2), ldim = new Float32Array(En*2).fill(1);
const eMid = new Float32Array(En*3);
refE.forEach((e,i)=>{
  const a=G.nodes[idx.get(e.s)], b=G.nodes[idx.get(e.t)];
  lpos.set([a.x,a.y,a.z, b.x,b.y,b.z], i*6);
  const ca=new THREE.Color(colorOf[a.c] ?? 0x8b949e), cb=new THREE.Color(colorOf[b.c] ?? 0x8b949e);
  lcol.set([ca.r,ca.g,ca.b, cb.r,cb.g,cb.b], i*6);
  lbase[i*2]=0.30; lbase[i*2+1]=0.30;
  eMid[i*3]=(a.x+b.x)/2; eMid[i*3+1]=(a.y+b.y)/2; eMid[i*3+2]=(a.z+b.z)/2; });
const lgeo = new THREE.BufferGeometry();
lgeo.setAttribute("position", new THREE.BufferAttribute(lpos,3));
lgeo.setAttribute("color", new THREE.BufferAttribute(lcol,3));
lgeo.setAttribute("a_base", new THREE.BufferAttribute(lbase,1));
lgeo.setAttribute("a_dim", new THREE.BufferAttribute(ldim,1));
const edgeUni = {u_cull:{value:1e9}, u_cam:{value:new THREE.Vector3()}};
const lines = new THREE.LineSegments(lgeo, new THREE.ShaderMaterial({
  uniforms:edgeUni, transparent:true, depthWrite:false,
  vertexShader:"attribute float a_base; attribute float a_dim; attribute vec3 color;"
    + " uniform float u_cull; uniform vec3 u_cam; varying vec3 vC; varying float vA;"
    + " void main(){ vC=color;"
    + "   float d = distance(position, u_cam);"
    + "   float fade = 1.0 - smoothstep(u_cull*0.55, u_cull, d);"
    + "   vA = a_base*a_dim*fade;"
    + "   gl_Position = projectionMatrix*modelViewMatrix*vec4(position,1.0); }",
  fragmentShader:"varying vec3 vC; varying float vA;"
    + " void main(){ gl_FragColor = vec4(vC, vA); }" }));
scene.add(lines);

/* ---------- framing ---------- */
let cx=0,cy=0,cz=0;
G.nodes.forEach(n=>{cx+=n.x;cy+=n.y;cz+=n.z});
cx/=Math.max(Nn,1); cy/=Math.max(Nn,1); cz/=Math.max(Nn,1);
let R=1;
G.nodes.forEach(n=>{const d=Math.hypot(n.x-cx,n.y-cy,n.z-cz);if(d>R)R=d});
camera.position.set(cx, cy, cz + R*2.2);
ctl.target.set(cx,cy,cz);
const sceneR = R;

/* ---------- search filter + focus neighbourhood ---------- */
const vis = new Uint8Array(Nn).fill(1);
let focusI = -1, mode = "all", statusDirty = true;
const nbrSet = new Set(); let nbrEdges = new Set();
function showInfo(i){
  const el = document.getElementById("info");
  if (i<0){ el.style.display="none"; return; }
  const n = G.nodes[i];
  el.style.display="block";
  el.innerHTML = "<b>"+n.t+"</b><br>"+'<span class="muted">'+n.i+"</span><br>"
    +"chapter: "+n.c+" · in-degree: "+n.g+"<br>"
    +'<a href="'+n.u+'" target="_blank">source ↗</a>';
}
function applyDims(){
  for(let i=0;i<Nn;i++){
    let d = vis[i] ? 1 : 0.10;
    if (focusI>=0) d = nbrSet.has(i) ? (vis[i]?1:0.35) : 0.08;
    ndim[i]=d; }
  pgeo.attributes.a_dim.needsUpdate = true;
  for(let ei=0;ei<En;ei++){
    const a=idx.get(refE[ei].s), b=idx.get(refE[ei].t);
    let d = (vis[a]&&vis[b]) ? 1 : 0.05;
    if (focusI>=0) d = nbrEdges.has(ei) ? 1.8 : 0.04;
    ldim[ei*2]=d; ldim[ei*2+1]=d; }
  lgeo.attributes.a_dim.needsUpdate = true;
  statusDirty = true;
}
function setFocus(i){
  focusI = i; nbrSet.clear(); nbrEdges = new Set();
  if (i>=0){ nbrSet.add(i); adj[i].forEach(pr=>{ nbrSet.add(pr[0]); nbrEdges.add(pr[1]); }); }
  applyDims(); showInfo(i);
}
let Q = "";
function applyFilter(){
  let c=0;
  G.nodes.forEach((n,i)=>{
    const v = (!Q || n.t.toLowerCase().includes(Q)); vis[i]=v?1:0; if(v)c++; });
  document.getElementById("cnt").textContent = c+"/"+Nn;
  applyDims();
}

/* ---------- chapter aggregate: spheres + weighted inter-chapter curves ---------- */
const aggGroup = new THREE.Group(); aggGroup.visible = false; scene.add(aggGroup);
const chStat = chapters.map(c=>{
  let count=0,x=0,y=0,z=0;
  G.nodes.forEach(n=>{ if(n.c===c){ count++; x+=n.x; y+=n.y; z+=n.z; } });
  const m=Math.max(count,1);
  return {c:c, count:count, cx:x/m, cy:y/m, cz:z/m}; });
let cmax=1; chStat.forEach(s=>{ if(s.count>cmax) cmax=s.count; });
const aggSpheres = [];
chStat.forEach(s=>{
  const r = 3 + 9*Math.sqrt(s.count/cmax);
  const colr = new THREE.Color(colorOf[s.c] ?? 0x8b949e);
  const mesh = new THREE.Mesh(new THREE.SphereGeometry(r,24,16),
    new THREE.MeshBasicMaterial({color:colr, transparent:true, opacity:0.92}));
  mesh.position.set(s.cx,s.cy,s.cz); mesh.userData.ch = s.c; mesh.userData.r = r;
  const glow = new THREE.Mesh(new THREE.SphereGeometry(r*1.45,24,16),
    new THREE.MeshBasicMaterial({color:colr, transparent:true, opacity:0.16,
      blending:THREE.AdditiveBlending, depthWrite:false}));
  mesh.add(glow);
  aggGroup.add(mesh); aggSpheres.push(mesh); });
const wMap = new Map();
refE.forEach(e=>{
  const a=G.nodes[idx.get(e.s)], b=G.nodes[idx.get(e.t)];
  if(a.c===b.c) return;
  const k = a.c<b.c ? a.c+"|"+b.c : b.c+"|"+a.c;
  wMap.set(k,(wMap.get(k)||0)+1); });
let wMax=1; wMap.forEach(w=>{ if(w>wMax) wMax=w; });
wMap.forEach((w,k)=>{
  const parts = k.split("|");
  const A = chStat.find(s=>s.c===parts[0]), B = chStat.find(s=>s.c===parts[1]);
  if(!A||!B) return;
  const mid = new THREE.Vector3((A.cx+B.cx)/2,(A.cy+B.cy)/2+R*0.18,(A.cz+B.cz)/2);
  const curve = new THREE.QuadraticBezierCurve3(
    new THREE.Vector3(A.cx,A.cy,A.cz), mid, new THREE.Vector3(B.cx,B.cy,B.cz));
  const gp = new THREE.BufferGeometry().setFromPoints(curve.getPoints(28));
  aggGroup.add(new THREE.Line(gp, new THREE.LineBasicMaterial({
    color: new THREE.Color(colorOf[parts[0]] ?? 0x8b949e), transparent:true,
    opacity: 0.14 + 0.42*Math.sqrt(w/wMax), depthWrite:false }))); });

/* ---------- mode switch ---------- */
const bAll = document.getElementById("m-all"), bAgg = document.getElementById("m-agg");
function setMode(m){
  mode = m; const agg = m==="agg";
  aggGroup.visible = agg; cloud.visible = !agg; halo.visible = !agg; lines.visible = !agg;
  bAll.classList.toggle("on", !agg); bAgg.classList.toggle("on", agg);
  if (focusI>=0) setFocus(-1);
  fitView(m);
  statusDirty = true;
}
bAll.onclick = ()=>{ Q=""; document.getElementById("q").value=""; applyFilter(); setMode("all"); };
bAgg.onclick = ()=>setMode("agg");

/* ---------- search box: dim filter + instant dropdown -> focus ---------- */
const qEl = document.getElementById("q"), sres = document.getElementById("sres");
function buildSres(){
  sres.innerHTML = "";
  if (Q.length < 2){ sres.classList.remove("open"); return; }
  const hits = [];
  G.nodes.forEach((n,i)=>{ if(n.t.toLowerCase().includes(Q)) hits.push([n,i]); });
  hits.sort((a,b)=>(b[0].g||0)-(a[0].g||0));
  hits.slice(0,8).forEach(pr=>{
    const n=pr[0], i=pr[1];
    const d = document.createElement("div"); d.className="sr"; d.setAttribute("role","option");
    d.textContent = n.t;
    const m = document.createElement("span"); m.className="m";
    m.textContent = n.c+" · deg "+(n.g||0); d.appendChild(m);
    d.onclick = ()=>{ sres.classList.remove("open");
      if (mode!=="all") setMode("all");
      Q=""; qEl.value=""; applyFilter();
      setFocus(i); flyTo(i); };
    sres.appendChild(d); });
  sres.classList.toggle("open", hits.length>0);
}
qEl.oninput = ()=>{ Q = qEl.value.trim().toLowerCase(); applyFilter(); buildSres(); };
document.addEventListener("click", e=>{
  if(!sres.contains(e.target)&&e.target!==qEl) sres.classList.remove("open"); });
document.addEventListener("keydown", e=>{
  if (e.key==="/" && document.activeElement!==qEl){ e.preventDefault(); qEl.focus(); }
  if (e.key==="Escape"){ setFocus(-1); sres.classList.remove("open"); qEl.blur(); } });

/* ---------- camera tween ---------- */
let camTween = null;
function fitView(m){
  const b = new THREE.Box3();
  if (m==="agg") aggSpheres.forEach(s=>{ const rr=s.userData.r;
    b.expandByPoint(s.position.clone().add(new THREE.Vector3(rr,rr,rr)));
    b.expandByPoint(s.position.clone().sub(new THREE.Vector3(rr,rr,rr))); });
  else G.nodes.forEach((n,i)=>{ if(vis[i]) b.expandByPoint(new THREE.Vector3(n.x,n.y,n.z)); });
  if (b.isEmpty()) return;
  const c = b.getCenter(new THREE.Vector3());
  const r = Math.max(b.getBoundingSphere(new THREE.Sphere()).radius, 1);
  const dir = camera.position.clone().sub(ctl.target).normalize();
  camTween = {p0:camera.position.clone(), p1:c.clone().add(dir.multiplyScalar(r*2.2)),
              t0:ctl.target.clone(), t1:c, start:performance.now(), dur:600};
}
function flyTo(i){
  const n = G.nodes[i];
  const t = new THREE.Vector3(n.x,n.y,n.z);
  const dir = camera.position.clone().sub(ctl.target).normalize();
  camTween = {p0:camera.position.clone(),
              p1:t.clone().add(dir.multiplyScalar(Math.max(R*0.35,20))),
              t0:ctl.target.clone(), t1:t, start:performance.now(), dur:700};
}

/* ---------- hover + click (focus / chapter isolate) ---------- */
const ray = new THREE.Raycaster(); ray.params.Points.threshold = 4;
const mouse = new THREE.Vector2();
let hoverI = -1, hoverCh = -1, downXY = null, pointerDirty = false, tipX = 0, tipY = 0;
const tip = document.getElementById("tip");
renderer.domElement.addEventListener("pointermove", e=>{
  mouse.set(e.clientX/innerWidth*2-1, -(e.clientY-BAR)/(innerHeight-BAR)*2+1);
  pointerDirty = true; tipX=e.clientX; tipY=e.clientY; });
renderer.domElement.addEventListener("pointerdown", e=>{ downXY=[e.clientX,e.clientY]; });
renderer.domElement.addEventListener("pointerup", e=>{
  if(!downXY) return;
  const moved=Math.hypot(e.clientX-downXY[0], e.clientY-downXY[1]); downXY=null;
  if (moved>6) return;
  if (mode==="agg"){
    if (hoverCh>=0){ const c=chapters[hoverCh]; let cnt=0;
      G.nodes.forEach((n,i)=>{ vis[i] = n.c===c?1:0; cnt+=vis[i]; });
      Q=""; qEl.value="";
      document.getElementById("cnt").textContent = cnt+"/"+Nn;
      applyDims(); setMode("all"); }
    return; }
  if (hoverI>=0) setFocus(hoverI===focusI ? -1 : hoverI); else setFocus(-1); });
function updateHover(){
  pointerDirty=false; ray.setFromCamera(mouse, camera);
  if (mode==="agg"){
    const hit = ray.intersectObjects(aggSpheres,false)[0];
    hoverCh = hit ? chapters.indexOf(hit.object.userData.ch) : -1; hoverI=-1;
    if (hoverCh>=0){ const s=chStat[hoverCh];
      tip.innerHTML = "<b>"+s.c+"</b> · "+s.count+" pages · click to isolate";
      tip.style.display="block"; renderer.domElement.style.cursor="pointer"; }
    else { tip.style.display="none"; renderer.domElement.style.cursor=""; }
  } else {
    const hit = ray.intersectObject(cloud)[0];
    const i = (hit && hit.index!==undefined && vis[hit.index]) ? hit.index : -1;
    hoverCh=-1;
    if (i!==hoverI){ hoverI=i;
      if (i>=0){ const n=G.nodes[i];
        tip.innerHTML = "<b>"+n.t+"</b> · "+n.c+" · deg "+(n.g||0);
        tip.style.display="block"; renderer.domElement.style.cursor="pointer"; }
      else { tip.style.display="none"; renderer.domElement.style.cursor=""; } } }
  if (tip.style.display==="block"){ tip.style.left=(tipX+14)+"px"; tip.style.top=(tipY+12)+"px"; }
}

/* ---------- status bar + FPS-adaptive pixel ratio ---------- */
const statusEl = document.getElementById("status");
let fpsEMA=60, tPrev=performance.now(), frames=0, lastStatus=0;
let keepPct=100, visE=En, pxStep=0;
const PX = [Math.min(devicePixelRatio||1,2), 1.0, 0.75, 0.6];
function applyPx(){ renderer.setPixelRatio(PX[pxStep]); nodeUni.u_px.value = PX[pxStep];
  renderer.setSize(innerWidth, innerHeight-BAR); statusDirty = true; }
applyPx();
function updateCullUniforms(){
  const d = camera.position.distanceTo(ctl.target);
  edgeUni.u_cull.value = d*0.95 + sceneR*0.95;
  edgeUni.u_cam.value.copy(camera.position); }
function updateCull(){
  updateCullUniforms();
  const cull = edgeUni.u_cull.value; let kept=0, v=0;
  for(let i=0;i<En;i++){
    const a=idx.get(refE[i].s), b=idx.get(refE[i].t);
    if(!vis[a]||!vis[b]) continue; v++;
    const mx=eMid[i*3]-camera.position.x, my=eMid[i*3+1]-camera.position.y,
      mz=eMid[i*3+2]-camera.position.z;
    if (mx*mx+my*my+mz*mz <= cull*cull) kept++; }
  visE=v; keepPct = v? Math.round(kept/v*100) : 100; }
function updateStatus(){
  let vn=0; for(let i=0;i<Nn;i++) vn+=vis[i];
  statusEl.textContent = vn+"/"+Nn+" nodes · edges "+visE+"/"+En+" · view "+keepPct+"%"
    +" · FPS "+Math.round(fpsEMA)+" · px "+PX[pxStep].toFixed(2)
    + (mode==="agg"?" · chapters":"") + (focusI>=0?" · focus":"");
}
function loop(){
  requestAnimationFrame(loop);
  const now = performance.now(); const dt = now-tPrev; tPrev = now;
  fpsEMA += (1000/Math.max(dt,1)-fpsEMA)*0.04; frames++;
  if (frames%150===0){
    if (fpsEMA<50 && pxStep<3){ pxStep++; applyPx(); }
    else if (fpsEMA>58 && pxStep>0){ pxStep--; applyPx(); } }
  /* status on a time cadence (~300ms), never on a frame count: at low FPS a
     frame-count cadence leaves the readout seconds stale */
  if (now-lastStatus>300 || statusDirty){
    lastStatus=now; statusDirty=false; updateCull(); updateStatus(); }
  else updateCullUniforms();
  if (camTween){ const k=Math.min(1,(now-camTween.start)/camTween.dur);
    const e=1-Math.pow(1-k,3);
    camera.position.lerpVectors(camTween.p0,camTween.p1,e);
    ctl.target.lerpVectors(camTween.t0,camTween.t1,e);
    if(k>=1){ camTween=null; if(!userTouched) ctl.autoRotate=true; } }
  if (pointerDirty) updateHover();
  ctl.update(); renderer.render(scene,camera); }
addEventListener("resize", ()=>{
  camera.aspect = innerWidth/(innerHeight-BAR);
  camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight-BAR); });
applyFilter(); setMode("all"); updateStatus();
loop();
</script></body></html>
"""


def layout_3d(graph: dict, seed: int = 7, iterations: int = 80) -> None:
    """Seeded spring-repulsion layout in place. ref edges pull; a sampled repulsion
    term pushes; chapters get golden-spiral anchors so clusters stay separable.
    Deterministic: same graph + seed => same coordinates."""
    nodes = graph["nodes"]
    rng = random.Random(seed)
    pos = {n["i"]: [rng.uniform(-50, 50), rng.uniform(-50, 50), rng.uniform(-50, 50)]
           for n in nodes}
    idx = {n["i"]: n for n in nodes}
    springs = [(e["s"], e["t"]) for e in graph["edges"]
               if e["k"] == "ref" and e["s"] in idx and e["t"] in idx]
    # golden-spiral chapter anchors: without a separation force every chapter
    # collapses onto the same centroid and the aggregate view is unreadable
    chapters = sorted({n["c"] for n in nodes})
    golden = math.pi * (3 - math.sqrt(5))
    anchors = {}
    for ci, c in enumerate(chapters):
        z = 1 - 2 * (ci + 0.5) / max(len(chapters), 1)
        rad = math.sqrt(max(0.0, 1 - z * z))
        th = golden * ci
        anchors[c] = [math.cos(th) * rad * 70, z * 70, math.sin(th) * rad * 70]
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
            a = anchors[n["c"]]
            for k in range(3):
                p[k] += (a[k] - p[k]) * 0.015 * cool
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
