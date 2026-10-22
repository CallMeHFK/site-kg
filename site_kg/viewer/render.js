/* Three.js renderer: one Points cloud, one additive halo, one edge LineSegments,
   one merged chapter-ring mesh and one ego InstancedMesh. Node state lives in
   SK.LiveGraph typed arrays; this module only mirrors it. */
(function (SK) {
  "use strict";

  var SCALE = 50;

  function structuralBase(kind) {
    if (kind === "ref" || kind === "imports") return 0.30;
    if (kind === "contains" || kind === "hub") return 0.11;
    return 0.08;
  }

  /* ------------------------------------------------------------------------
     City shaders.

     Four stacked tiers per node, merged into a single indexed mesh: the
     geometry is built once per node count and rewritten when the layout moves,
     so the whole city is one draw call on both backends. The windows are not
     geometry -- the fragment shader cuts a grid out of each facade, which is
     what keeps a 200-tower city under 20,000 vertices.

     Face ids: 0..7 are the eight walls (four wide, four chamfers), 8 the
     crown roof, 9 a setback terrace and 10 the ground quad that all the
     towers stand on. */
  var CITY_VS = [
    "attribute vec3 a_local; attribute float a_face;",
    "attribute vec4 a_spec; attribute float a_birth; attribute vec3 color;",
    "attribute float a_dim;",
    "varying vec3 vLocal; varying float vFace; varying vec4 vSpec;",
    "varying vec3 vCol; varying vec3 vNrm; varying vec3 vWorld;",
    "varying float vDepth; varying float vDim;",
    "uniform float u_time;",
    "void main(){",
    /* A tower is built, not placed: the vertex shader lifts it out of its
       plot, so the skyline assembles floor by floor while the crawl runs. */
    "  float grow = smoothstep(0.0, 0.85, max(0.0, u_time - a_birth));",
    "  vec3 p = position;",
    "  p.y *= grow + 0.00001;",
    "  vLocal = a_local; vFace = a_face; vSpec = a_spec; vCol = color;",
    "  vNrm = normal; vWorld = p; vDim = a_dim;",
    "  vec4 mv = modelViewMatrix * vec4(p, 1.0);",
    "  vDepth = -mv.z;",
    "  gl_Position = projectionMatrix * mv; }"
  ].join("\n");

  var CITY_FS = [
    "varying vec3 vLocal; varying float vFace; varying vec4 vSpec;",
    "varying vec3 vCol; varying vec3 vNrm; varying vec3 vWorld;",
    "varying float vDepth; varying float vDim;",
    "uniform float u_time; uniform float u_finale;",
    "uniform float u_cityR; uniform float u_pitch;",
    "float h21(vec2 p){",
    "  p = fract(p*vec2(127.31, 311.7));",
    "  p += dot(p, p + 34.23);",
    "  return fract(p.x*p.y); }",
    "vec3 hazeCol(){ return vec3(0.030, 0.036, 0.060); }",
    "void main(){",
    "  vec3 col;",
    "  float alpha = 1.0;",
    "  if (vFace > 9.5) {",
    /* The ground: asphalt with a sodium grid where the streets run. The
       lattice puts every tower on a plot centre, so the street lines fall
       exactly on the plot edges. */
    "    vec2 g = vLocal.xy;",
    "    float P = max(u_pitch, 0.5);",
    "    float dx = abs(mod(g.x + P*0.5, P) - P*0.5);",
    "    float dz = abs(mod(g.y + P*0.5, P) - P*0.5);",
    /* The street is not a painted line any more: the asphalt runs out to the
       kerb, a concrete pavement wraps the block, and the dashed centre line
       only exists where a car would see it. ``m`` is the distance to the
       nearest street centre, whichever way that street runs. */
    "    float m = min(dx, dz);",
    "    float kerb = P * 0.21;",
    "    float road = 1.0 - smoothstep(kerb - 0.10, kerb, m);",
    "    float walk = smoothstep(kerb - 0.015, kerb + 0.04, m)"
      + " * (1.0 - smoothstep(kerb + 0.26, kerb + 0.34, m));",
    "    float along = dx < dz ? g.y : g.x;",
    "    float mark = (1.0 - smoothstep(0.018, 0.055, m))"
      + " * step(0.52, fract(along * 0.42));",
    "    float r = length(g) / max(u_cityR, 1.0);",
    /* Close up, the surfaces differ: tarmac, a pale pavement, the block's own
       ground. Far out that difference is one pixel wide and only aliases, so
       the pattern dissolves into flat ground before it turns into moire. */
    "    float near = 1.0 - smoothstep(0.45, 0.88, r);",
    "    col = mix(vec3(0.040, 0.042, 0.052), vec3(0.020, 0.022, 0.030),"
      + " road * near);",
    "    col = mix(col, vec3(0.062, 0.064, 0.076), walk * near);",
    "    col += vec3(0.30, 0.19, 0.086) * road *"
      + " (1.0 - smoothstep(0.0, kerb, m)) * 0.05 * near;",
    "    col += vec3(0.40, 0.30, 0.16) * mark * 0.20 * near;",
    "    col = mix(col, hazeCol(), smoothstep(0.85, 1.60, r));",
    /* Dither the long, shallow ramp into the haze: at 8 bits the steps
       between asphalt and haze would otherwise band across the plane. */
    "    col += (h21(g * 37.0) - 0.5) * 0.003;",
    /* The plane has to dissolve before its own edge, or the city sits on a
       floating square of tarmac with a straight horizon. */
    "    alpha = 1.0 - smoothstep(1.15, 1.85, r);",
    "  } else if (vFace > 8.5) {",
    /* Setback terrace: bare roof, a lip at the edge, no beacon. The tower
       above it shades most of this slab, which is exactly what sells the
       step in the silhouette. */
    "    float e = max(max(abs(vLocal.x), abs(vLocal.y)),"
      + " (abs(vLocal.x) + abs(vLocal.y)) * 0.5952);",
    "    col = vCol * 0.18 + vec3(0.020, 0.024, 0.034);",
    "    col *= 0.84 + 0.24 * fract(vSpec.z * 5.7);",
    "    col += (h21(floor(vLocal.xy * 9.0)) - 0.5) * 0.010;",
    "    col += vCol * 0.36 * smoothstep(0.86, 1.0, e);",
    "  } else if (vFace > 7.5) {",
    /* Roof: gravel, a lit parapet, and a beacon whose brightness is the
       node's fan-out -- the same number the windows carry. */
    "    float e = max(max(abs(vLocal.x), abs(vLocal.y)),"
      + " (abs(vLocal.x) + abs(vLocal.y)) * 0.5952);",
    "    col = vCol * 0.15 + vec3(0.018, 0.022, 0.032);",
    "    col *= 0.82 + 0.30 * fract(vSpec.z * 5.7);",
    "    col += (h21(floor(vLocal.xy * 9.0)) - 0.5) * 0.012;",
    "    col += vCol * 0.44 * smoothstep(0.86, 1.0, e);",
    "    float beacon = 1.0 - smoothstep(0.0, 0.13, length(vLocal.xy));",
    "    beacon *= 0.40 + 1.45 * vSpec.y;",
    "    beacon *= 0.70 + 0.30 * sin(u_time * 2.1 + vSpec.z * 31.0);",
    "    col += vec3(1.0, 0.74, 0.42) * beacon * 1.25;",
    "  } else {",
    /* Facade: a floor grid of glass. ``vSpec.x`` is the floor count, so every
       row of windows is one storey of the building, and ``vSpec.y`` is the
       lit fraction the node's outgoing links bought it. */
    "    vec3 N = normalize(vNrm);",
    /* One sun, not a studio rig: the two faces turned away from it fall into
       shadow, and that contrast is what tells a chamfer from a flat slab. */
    "    vec3 L = normalize(vec3(0.45, 0.72, 0.33));",
    "    float facing = 0.40 + 0.72 * max(dot(N, L), 0.0);",
    "    float sky = 0.09 + 0.13 * (N.y * 0.5 + 0.5);",
    "    col = vCol * (0.80 * facing + sky) * (0.46 + 0.54 * vLocal.y)"
      + " * vec3(0.90, 0.97, 1.08)"
      + " * (0.82 + 0.30 * fract(vSpec.z * 5.7));",
    /* The corner columns catch the sky: without them two neighbouring slabs
       of the same concrete read as one building. On the octagon every edge
       gets one, so the plan is legible all the way round. */
    "    col += vCol * 0.38 * smoothstep(0.86, 1.0, abs(vLocal.x));",
    /* Street light spills up the ground floor, which is what makes a tower
       meet the road instead of hovering over it. */
    "    float up = 1.0 - vLocal.y;",
    "    col += vec3(0.26, 0.17, 0.085) * up * up * up * 0.05;",
    "    float floors = max(vSpec.x, 1.0);",
    /* Column count comes from the frontage: the same four panes on a wider
       tower would read as stretched glass, and the setback tiers would lose
       their grid alignment. */
    "    float u = (vLocal.x * 0.5 + 0.5) * max(vSpec.w, 1.0);",
    "    float v = vLocal.y * floors;",
    "    vec2 cell = floor(vec2(u, v));",
    "    vec2 f = fract(vec2(u, v));",
    "    float win = smoothstep(0.24, 0.30, f.x) * (1.0 - smoothstep(0.70, 0.76, f.x))",
    "              * smoothstep(0.28, 0.36, f.y) * (1.0 - smoothstep(0.70, 0.78, f.y));",
    "    float seed = vSpec.z;",
    "    float pick = h21(cell + vec2(seed*37.0, seed*11.0));",
    "    float lit = step(pick, vSpec.y);",
    /* Finale: a ring of light travels out from the centre. The windows it
       passes switch on, sparkle and fall dark again behind it. */
    "    float ring = u_finale * 1.95 - 0.35;",
    /* Plots start half a pitch in, so the wave has to start there too. */
    "    vec2 q = vWorld.xz - vec2(max(u_pitch, 0.5) * 0.5);",
    "    float dist = length(q) / max(u_cityR, 1.0);",
    "    float band = smoothstep(ring - 0.24, ring, dist)",
    "               * (1.0 - smoothstep(ring, ring + 0.15, dist));",
    "    lit = max(lit, band * step(0.42, pick));",
    "    vec3 lamp = mix(vec3(1.0, 0.84, 0.58), vCol, 0.22);",
    /* A twelfth of the panes burn cooler: offices, not flats. It is enough
       to break the orange wash without giving up the warm/cool split the
       skyline relies on. */
    "    lamp = mix(lamp, vec3(0.80, 0.89, 1.0),"
      + " step(0.88, h21(cell * 1.7 + seed * 5.3)) * 0.75);",
    "    float flick = 0.86 + 0.14 * sin(u_time*1.6 + cell.x*2.7 + cell.y*1.9",
    "                                  + seed*9.0);",
    "    col += vec3(0.013, 0.016, 0.023) * win;",
    /* A pale spandrel under every window: at a glance it is the floor line,
       and a tower with visible storeys stops reading as a coloured box. */
    "    float spandrel = smoothstep(0.84, 0.89, f.y)"
      + " * (1.0 - smoothstep(0.93, 0.97, f.y));",
    "    col += vCol * 0.22 * spandrel * (0.55 + 0.45 * lit);",
    "    col += lamp * win * lit * flick * 1.45;",
    "  }",
    "  float haze = smoothstep(u_cityR * 1.05, u_cityR * 2.30, vDepth);",
    "  col = mix(col, hazeCol(), haze * 0.88);",
    /* A filtered chapter has to leave the street, not merely fade: the tower
       still writes depth, so a ghost would occlude the ones that remain. */
    "  alpha *= vDim;",
    "  if (alpha < 0.01) discard;",
    "  gl_FragColor = vec4(col, alpha); }"
  ].join("\n");

  function Renderer() {
    this.SCALE = SCALE;
    this.posScale = SCALE;
    this.name = "planets";
    this.cfg = null;
    this.cap = 0;
    this.capE = 0;
    /* Sub-segments per link. 1 is the old straight segment; a scene raises it
       to bend its axons into arcs, and the buffers are rebuilt when it does. */
    this.segK = 1;
    this._segK = 1;
    this.lastVersion = -1;
    this.lastDimVersion = -1;
    this.colorDirty = true;
    this.sceneRadius = 1;
    this.sceneN = 0;
    this.selection = null;
    this.selectionSet = null;
    this.hiddenKinds = {};
    this.lastSelectionKey = "";
    this.ringMesh = null;
    this.ringKey = "";
    this.contextLost = false;
    this._onContextLost = null;
    this._onContextRestored = null;
    this.lodReady = false;
    this.lodVersion = -1;
    this.finaleActive = false;
    this.finaleClock = 0;
    this.finaleDuration = 0;
    this.finaleMode = "";
    this.finaleReduced = false;
    this._drawEdges = 0;
    /* Drag offsets are per scene, keyed by page id: a cell pulled aside in
       the head must not shift the same page's tower in the city. */
    this._dragStore = {};
    this._dragOff = this._dragStore.planets = {};
    this._egoItems = null;
    this._egoScale = null;
    /* Resolution is a budget, not a constant: the frame loop lowers it when
       the device cannot hold 45fps and raises it again when it can. */
    this.renderScale = 1;
    this._softwareGL = false;
  }

  /* The pixel ratio every backend draws at: display density, capped at 2,
     times the current render budget. A software rasteriser gets 1.0 at most,
     because on SwiftShader a 2x retina canvas is 4x the fill for no gain. */
  Renderer.prototype.pixelRatio = function () {
    var cap = this._softwareGL ? 1 : Math.min(window.devicePixelRatio || 1, 2);
    return Math.max(0.7, cap * this.renderScale);
  };

  Renderer.prototype.setRenderScale = function (scale) {
    scale = Math.max(0.7, Math.min(1, scale));
    if (Math.abs(scale - this.renderScale) < 0.01) return false;
    this.renderScale = scale;
    this.resize();
    return true;
  };

  Renderer.prototype.mount = function (canvas, live) {
    this._initState(canvas, live);
    this._mountGL();
    this.setScene("planets");
    this.resize();
    return this;
  };

  /* Everything here is scene data, not drawing: the WebGPU backend reuses it
     verbatim and replaces only the draw calls, so the two backends cannot
     drift apart on layout, colour or animation state. */
  Renderer.prototype._initState = function (canvas, live) {
    var THREE = T3.THREE;
    this.canvas = canvas;
    this.live = live;
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(60, 1, 0.1, 1e6);
    this.scene.background = new THREE.Color(0x070a14);

    /* The cosmos is a shader, not a baked image: three parallax star layers
       twinkle over drifting nebulae, a milky-way band crosses the field, and
       a comet now and then. The camera's orbit shifts the whole field, and the
       nebulae churn on their own clock, so the background never reads as a
       still frame. One fullscreen quad, one draw call. */
    this.skyUni = {u_time: {value: 0}, u_drift: {value: new THREE.Vector2()},
                   u_mode: {value: 0},
                   u_res: {value: new THREE.Vector2(1, 1)}};
    this.sky = new THREE.Mesh(
      new THREE.PlaneGeometry(2, 2),
      new THREE.ShaderMaterial({
        uniforms: this.skyUni, depthTest: false, depthWrite: false,
        vertexShader: "varying vec2 vUv; void main(){ vUv = uv;"
          + " gl_Position = vec4(position.xy, 0.0, 1.0); }",
        fragmentShader: [
          "varying vec2 vUv;",
          "uniform float u_time; uniform vec2 u_drift; uniform vec2 u_res;"
          + " uniform float u_mode;",
          "float hash(vec2 p){ p = fract(p*vec2(123.34,456.21));",
          "  p += dot(p,p+45.32); return fract(p.x*p.y); }",
          "float noise(vec2 p){ vec2 i=floor(p), f=fract(p);",
          "  f=f*f*(3.0-2.0*f);",
          "  float a=hash(i), b=hash(i+vec2(1.0,0.0));",
          "  float c=hash(i+vec2(0.0,1.0)), d=hash(i+vec2(1.0,1.0));",
          "  return mix(mix(a,b,f.x), mix(c,d,f.x), f.y); }",
          "float fbm(vec2 p){ float v=0.0, a=0.5;",
          "  for(int i=0;i<4;i++){ v += a*noise(p); p = p*2.02 + 17.3; a *= 0.5; }",
          "  return v; }",
          "vec3 stars(vec2 uv, float scale, float density, float size,",
          "           float seed, float t){",
          "  vec2 g = uv*scale + seed; vec2 id = floor(g);",
          "  vec2 f = fract(g) - 0.5;",
          "  float h = hash(id + seed);",
          "  if(h > density) return vec3(0.0);",
          "  vec2 off = vec2(hash(id+3.1), hash(id+7.7)) - 0.5;",
          "  float d = length(f - off*0.62);",
          "  float tw = 0.42 + 0.58*sin(t*(1.3+2.4*h) + h*61.0);",
          "  float core = smoothstep(size, 0.0, d);",
          "  float glow = smoothstep(size*5.0, 0.0, d)*0.22;",
          "  vec3 tint = mix(vec3(0.72,0.84,1.0), vec3(1.0,0.84,0.62),",
          "                  step(0.86,h));",
          "  return tint*(core+glow)*tw; }",
          /* A comet every few seconds: a bright head with a tapering tail,
             deterministic from the time so nothing has to be animated on the
             CPU. */
          "float comet(vec2 uv, float t){",
          "  float k = floor(t*0.24);",
          "  float h1 = hash(vec2(k, 1.7));",
          "  float h2 = hash(vec2(k, 8.3));",
          "  if(h1 > 0.45) return 0.0;",
          "  float ph = fract(t*0.24);",
          "  vec2 start = vec2(-1.0 + h1*2.0, 0.60 + h2*0.30);",
          "  vec2 dir = normalize(vec2(1.0, -0.62));",
          "  vec2 head = start + dir*(ph*2.0);",
          "  vec2 q = uv - head;",
          "  float along = -dot(q, dir);",
          "  float perp = abs(dot(q, vec2(-dir.y, dir.x)));",
          "  float tail = smoothstep(0.0,0.02,along)*smoothstep(0.42,0.0,along)",
          "             * smoothstep(0.024,0.0,perp);",
          "  float core = smoothstep(0.028,0.0,length(q));",
          "  float fade = (1.0-ph)*(1.0-ph);",
          "  return (tail*0.55 + core*1.3)*fade; }",
          "vec3 cosmos(vec2 uv, float t){",
          "  vec2 flow = vec2(sin(t*0.105)*0.105, cos(t*0.082)*0.082);",
          "  vec2 p = uv*1.32 + u_drift + flow;",
          "  float n1 = fbm(p + vec2(t*0.150, -t*0.108));",
          "  float n2 = fbm(p*1.9 + vec2(-t*0.125, t*0.140) + 7.0);",
          "  float n3 = fbm(p*3.4 + vec2(t*0.082, -t*0.094) + 19.0);",
          "  float mask = smoothstep(0.26, 0.84, n1*0.68 + n2*0.34);",
          /* Indigo space with a little dust, not a violet candy cloud: the
             bodies are the colour in this scene, so the nebula stays a
             backdrop. Magenta and teal are kept to a trace. */
          "  vec3 deep = vec3(0.0035, 0.0045, 0.0130);",
          "  vec3 core = mix(vec3(0.095,0.068,0.235), vec3(0.018,0.115,0.190),",
          "                  smoothstep(0.40,1.0,n2));",
          "  core = mix(core, vec3(0.225,0.080,0.220),",
          "             smoothstep(0.64,1.0,n1)*0.50);",
          "  core = mix(core, vec3(0.020,0.165,0.160),",
          "             smoothstep(0.70,1.0,n3)*0.38);",
          "  vec3 col = deep + core*mask*1.05;",
          "  col += vec3(0.055,0.052,0.120)*smoothstep(0.52,1.0,n3)*0.44;",
          /* Milky-way band: a diagonal lane of dense dust and small stars. */
          "  float lane = exp(-pow((uv.x*0.78 + uv.y*0.62)*3.1, 2.0));",
          "  col += vec3(0.095,0.105,0.175)*lane*(0.60+0.40*sin(t*0.12));",
          /* Each layer streams at its own speed, so the field never sits still:
             the nearest stars travel about nine cells per ten seconds. */
          "  col += stars(uv + u_drift*0.20 + vec2(t*0.0035, t*0.0021),",
          "               90.0, 0.68, 0.055, 1.0, t*0.62);",
          "  col += stars(uv + u_drift*0.45 + vec2(t*0.0062, t*0.0038),",
          "               155.0, 0.56, 0.050, 9.0, t*0.95)*0.78;",
          "  col += stars(uv + u_drift*0.85 + vec2(t*0.0100, t*0.0060),",
          "               240.0, 0.48, 0.045, 23.0, t*1.25)*0.58;",
          "  col += stars(uv + u_drift*0.30 + vec2(t*0.0044, t*0.0026)",
          "               + vec2(0.31,0.17), 320.0,",
          "               mix(0.24, 0.76, lane), 0.038, 41.0, t*0.9)*0.62;",
          "  col += vec3(0.80,0.88,1.0)*comet(uv + u_drift*0.12, t);",
          "  return col; }",
          /* Neural: a dark basin, not tissue. The scene already draws bright
             synapses and halos, so the backdrop stays near-black with one warm
             radial wash and a few faint motes. Nothing here may compete with
             an edge for attention. */
          "vec3 deepvoid(vec2 uv, float t){",
          "  float r = length(uv);",
          "  vec3 col = vec3(0.0040, 0.0036, 0.0038);",
          "  col += vec3(0.024, 0.016, 0.018) * smoothstep(1.02, 0.0, r);",
          /* Dust motes: sparse pinpricks, dim enough to read as texture at the
             frame edge while leaving the graph's own glow uncontested. */
          "  vec2 g = uv*175.0; vec2 id = floor(g); vec2 f = fract(g) - 0.5;",
          "  float h = hash(id);",
          "  float mote = step(0.9962, h) * smoothstep(0.42, 0.0, length(f));",
          "  col += vec3(0.40,0.30,0.22) * mote * (0.50 + 0.50*sin(t*0.8 + h*37.0));",
          "  return col; }",
          /* City: a night sky lit from below by the streets. The glow sits
             where the skyline will be and the stars only survive above it, so
             the towers stand against haze rather than against a black plate. */
          "vec3 skyline(vec2 uv, float t){",
          "  float h = uv.y;",
          "  vec3 zenith = vec3(0.006, 0.008, 0.022);",
          "  vec3 low = vec3(0.044, 0.038, 0.068);",
          "  vec3 col = mix(low, zenith, smoothstep(-0.10, 0.52, h));",
          "  float glow = exp(-pow(max(0.0, h + 0.04) * 3.4, 2.0));",
          "  col += vec3(0.30, 0.17, 0.070) * glow * 0.50;",
          "  col += stars(uv + vec2(t*0.0022, 0.0), 170.0, 0.40, 0.042, 13.0,",
          "               t*0.55) * smoothstep(0.04, 0.42, h);",
          "  float band = sin(h*9.0 + uv.x*1.3 + t*0.05)*0.5 + 0.5;",
          "  col += vec3(0.022, 0.019, 0.030) * pow(band, 3.0)",
          "         * smoothstep(0.42, 0.02, abs(h - 0.12));",
          "  return col; }",
          "void main(){",
          "  vec2 uv = (vUv - 0.5) * vec2(u_res.x/max(u_res.y,1.0), 1.0);",
          "  float t = u_time;",
          "  vec3 col = u_mode > 1.5 ? skyline(uv, t)",
          "           : (u_mode > 0.5 ? deepvoid(uv, t) : cosmos(uv, t));",
          "  float vig = smoothstep(1.32, 0.26, length(vUv-0.5));",
          "  col *= mix(0.45, 1.0, vig);",
          "  gl_FragColor = vec4(col, 1.0); }"
        ].join("\n")
      }));
    this.sky.frustumCulled = false;
    this.sky.renderOrder = -1000;
    this.scene.add(this.sky);

    /* The cortex: the tissue the cells live on. A brain has to be geometry,
       not a backdrop, because the silhouette the eye reads is the gyri of a
       head. One mesh carries the folds, and the shader grows them in as the
       tissue matures, so the head keeps changing while the build runs. */
    this.cortexUni = {u_time: {value: 0}, u_fold: {value: 0},
                      u_opacity: {value: 0.0},
                      u_light: {value: new THREE.Vector3(-0.55, 0.6, 0.75)},
                      u_color: {value: new THREE.Color(0x5b5566)},
                      u_rim: {value: new THREE.Color(0xffc98a)}};
    this.cortex = new THREE.Mesh(
      new THREE.BufferGeometry(),
      new THREE.ShaderMaterial({
        uniforms: this.cortexUni, transparent: true, depthWrite: false,
        side: THREE.FrontSide,
        vertexShader: [
          "attribute vec3 a_flat; attribute vec3 a_flatN;",
          "attribute float a_dev; attribute float a_fold;",
          "varying vec3 vN; varying vec3 vV; varying float vFold;",
          "varying float vDev;",
          "uniform float u_fold;",
          "void main(){",
          /* A young cortex is smooth: the folds grow in as the tissue matures,
             oldest first, so the gyri of the brainstem are there before the
             frontal pole has folded. */
          "  float w = smoothstep(a_dev - 0.25, a_dev + 0.05, u_fold);",
          "  vec3 p = mix(a_flat, position, w);",
          "  vec3 n = normalize(mix(a_flatN, normal, w));",
          "  vN = n; vFold = a_fold; vDev = a_dev;",
          "  vec4 mv = modelViewMatrix * vec4(p, 1.0);",
          "  vV = -mv.xyz;",
          "  gl_Position = projectionMatrix * mv; }"
        ].join("\n"),
        fragmentShader: [
          "varying vec3 vN; varying vec3 vV; varying float vFold;",
          "varying float vDev;",
          "uniform vec3 u_color; uniform vec3 u_light; uniform float u_opacity;",
          "uniform vec3 u_rim; uniform float u_time;",
          "void main(){",
          "  vec3 N = normalize(vN);",
          "  vec3 V = normalize(vV);",
          "  vec3 L = normalize(u_light);",
          "  float diff = max(dot(N, L), 0.0);",
          /* Wrapped light: tissue scatters, so the shadow side never goes
             black and the folds keep a hint of light in them. */
          "  float wrap = max((dot(N, L) + 0.45) / 1.45, 0.0);",
          /* A tight rim: only the true silhouette glows. A broad fresnel
             also lit every fold flank, and the head read as a wireframe
             instead of as a sheet of tissue. */
          "  float fres = pow(1.0 - max(dot(N, V), 0.0), 4.0);",
          /* The fold field is signed: negative is a cleft, positive is a
             crown. The sulci are the low end of that field, so they are
             read off it directly. A single threshold used to hide half the
             sheet and flatten the whole head. */
          "  float sulcus = 1.0 - smoothstep(-0.085, -0.012, vFold);",
          "  float crown  = smoothstep(-0.012, 0.050, vFold);",
          "  float pulse = 0.92 + 0.08*sin(u_time*0.8 + vDev*9.0);",
          /* A cavity: the cleft is in shadow, the crown catches the light. */
          /* A thin sheet still has colour of its own: without a lit body the
             only thing that showed was the rim of each fold, and the head read
             as a wireframe rather than as tissue. */
          /* Colours arrive in linear space and this shader writes straight to
             the sRGB buffer, so the raw product is about half as bright as the
             swatch. The gain restores the putty the palette asks for; without
             it the head sat at a fifth of the value and read as a burnt shell. */
          "  vec3 col = u_color * (0.50 + 0.80*diff + 0.36*wrap) * 1.9 * pulse;",
          /* A little body on the shadow side: without it the far hemisphere
             went to pure black and the head lost its silhouette. */
          "  col += u_color * 0.10 * (0.5 + 0.5*max(dot(N, V), 0.0));",
          /* A sulcus is a groove, not a hole: at 0.42 the clefts read as torn
             gashes and the surface looked like bark. */
          "  col = mix(col, col*0.62, sulcus);",
          "  col += u_color * crown * 0.14;",
          /* The wet sheen on the gyri: a highlight is what reads as a folded
             surface rather than a painted blob. */
          "  vec3 H = normalize(L + V);",
          "  float spec = pow(max(dot(N, H), 0.0), 26.0) * (1.0 - sulcus);",
          "  col += mix(u_rim, vec3(1.0), 0.55) * spec * 0.32;",
          /* The rim is a rim, not a wash: at 0.75 it lit every fold flank and
             the membrane took the rim colour instead of its own. */
          "  col += u_rim * fres * 0.34;",
          /* The tissue is see-through: a thin sheet over the cells, not a body
             of smoke. Facing the camera it is almost clear, so the neurons and
             their axons read through the front of the head; at the silhouette
             the path through the sheet is long and the rim glows. That ratio,
             not the overall level, is what makes it a membrane. */
          "  float a = u_opacity * (0.58 + 1.20*fres) * (1.0 - 0.34*sulcus);",
          "  gl_FragColor = vec4(col, clamp(a, 0.0, 1.0)); }"
        ].join("\n")
      }));
    this.cortex.frustumCulled = false;
    this.cortex.renderOrder = 0;
    this.cortex.visible = false;
    this.scene.add(this.cortex);

    /* The city: one merged mesh of towers plus its ground. It writes depth,
       so the towers occlude each other and the edges behind them; the ground
       is the only translucent part, fading out before its own rim. Everything
       else in the scene is transparent and depth-write off. */
    this.cityUni = {u_time: {value: 0}, u_finale: {value: 0},
                    u_cityR: {value: 30}, u_pitch: {value: 3}};
    this.cityGeo = new THREE.BufferGeometry();
    this.cityMesh = new THREE.Mesh(this.cityGeo, new THREE.ShaderMaterial({
      uniforms: this.cityUni, vertexShader: CITY_VS, fragmentShader: CITY_FS,
      transparent: true, depthTest: true, depthWrite: true, side: THREE.DoubleSide
    }));
    this.cityMesh.frustumCulled = false;
    this.cityMesh.renderOrder = -1;
    this.cityMesh.visible = false;
    this.scene.add(this.cityMesh);
    this._cityN = -1;

    this.nodeUni = {u_px: {value: 1}, u_scale: {value: 1}, u_minpx: {value: 2},
                    u_time: {value: 0}, u_neural: {value: 0},
                    u_fly: {value: 1}, u_driftA: {value: 0},
                    u_vol: {value: 0},
                    u_tint: {value: new THREE.Color(0xffedd0)},
                    u_cam: {value: new THREE.Vector3()},
                    u_light: {value: new THREE.Vector3(-0.55, 0.6, 0.75).normalize()}};
    this.haloUni = {u_px: this.nodeUni.u_px, u_scale: this.nodeUni.u_scale,
                    u_minpx: this.nodeUni.u_minpx, u_time: this.nodeUni.u_time,
                    u_neural: this.nodeUni.u_neural,
                    u_fly: this.nodeUni.u_fly, u_driftA: this.nodeUni.u_driftA,
                    u_vol: this.nodeUni.u_vol,
                    u_cam: this.nodeUni.u_cam,
                    u_light: this.nodeUni.u_light,
                    u_gain: {value: 0.31},
                    u_halo: {value: 3.1}};
    /* Node sprites are shaded as spheres: the point coordinate gives a normal,
       one directional light gives the terminator, and a fixed view vector gives
       the specular highlight. a_birth grows a node in when it is first revealed;
       nodes present at load keep birth 0 so no wall of animation fires. */
    var vs = "attribute float a_size; attribute float a_dim; attribute float a_ring;"
      + " attribute float a_birth; attribute float a_seed; attribute float a_act;"
      + " attribute vec3 color;"
      + " uniform float u_px; uniform float u_scale; uniform float u_minpx;"
      + " uniform float u_time; uniform float u_fly; uniform float u_driftA;"
      + " uniform float u_vol; uniform vec3 u_cam;"
      + " varying vec3 vC; varying float vD; varying float vR; varying float vAge;"
      + " varying float vGrow; varying float vSprout;"
      + " varying vec3 vTail; varying float vSeed; varying float vFace;"
      + " varying float vAct;"
      /* The phase is a stable per-cell seed, never a hash of the position: a
         position hash changes as soon as the cell moves, so a dragged or
         springing node re-rolled its drift every frame and shook itself apart.
         The seed arrives as an attribute, and the two edge endpoints that
         share a cell carry the same seed, so an axon never tears off its soma. */
      + " void main(){ vC=color; vD=a_dim; vR=a_ring; vAct=a_act;"
      + "   float ph = a_seed;"
      /* The seed is per cell, not per colour: a cluster of a hundred nodes
         would otherwise be a hundred copies of the same neuron. */
      + "   vSeed = ph;"
      + "   float age = max(0.0, u_time-a_birth);"
      + "   float born = smoothstep(0.0,0.45,age); vAge = born;"
      /* Maturation runs on a longer clock than the fade-in: a neuron is born
         as a soma and keeps extending its dendrites for a couple of seconds
         after that, so tissue grows instead of merely appearing. */
      + "   vGrow = clamp(age/2.2, 0.0, 1.0);"
      /* A cell does not stop when it has finished its first growth: the fine
         branches and the beads keep arriving for as long as the scene runs,
         so the tissue thickens instead of merely being there. */
      + "   vSprout = clamp((age - 1.4) / 16.0, 0.0, 1.0);"
      /* Depth cue: on a closed shell the cells on the far side must recede,
         or the head reads as a flat disc of dots instead of a volume. The
         outward normal of the cell and the direction to the camera give the
         facing term; the far side is dimmed rather than hidden, so the web
         behind the tissue still shows through. */
      + "   float face = 1.0;"
      + "   if (u_vol > 0.5) {"
      + "     float pl = length(position);"
      + "     if (pl > 1e-4) {"
      + "       vec3 nrm = position/pl;"
      + "       vec3 toCam = u_cam - position;"
      + "       float tl = length(toCam);"
      + "       float f = tl > 1e-4 ? dot(nrm, toCam/tl) : 1.0;"
      + "       face = mix(0.13, 1.0, smoothstep(-0.55, 0.90, f));"
      + "     }"
      + "   }"
      + "   vFace = face;"
      /* Birth flight: a new node streaks in from outside the frame and settles
         onto its layout slot, so the build reads as matter arriving rather
         than sprites fading up where they stand. */
      + "   float k = clamp(age/1.15, 0.0, 1.0);"
      + "   float ease = 1.0 - pow(1.0-k, 2.4);"
      + "   float rr = length(position);"
      + "   vec3 dir = rr > 1e-3 ? position/rr"
      + "     : normalize(vec3(sin(ph*6.283), cos(ph*4.11), sin(ph*2.77)));"
      + "   vec3 side = normalize(cross(dir, vec3(0.31, 0.91, 0.27)));"
      + "   vec3 start = position + dir*(26.0 + 34.0*ph)"
      + "              + side*(12.0*(ph-0.5));"
      + "   vec3 p = position + (start-position)*u_fly*(1.0-ease);"
      /* Screen-space motion axis for the comet tail: the world-space velocity
         projected to view xy (already screen-aligned), plus how much flight is
         left as the tail's weight. */
      + "   float flight = u_fly*(1.0-ease);"
      + "   vec3 velV = (modelViewMatrix*vec4(position-start, 0.0)).xyz;"
      + "   vec2 td = vec2(velV.x, -velV.y);"
      + "   float tl = length(td);"
      + "   vTail = vec3(tl > 1e-4 ? td/tl : vec2(0.0, 1.0), flight);"
      /* Drift: the tissue keeps breathing after the flight lands. A cell that
         carries more text wanders a little further, so the plexus has a
         visible hierarchy of motion instead of a uniform jitter. */
      + "   p += u_driftA * (0.55 + 0.9*vAct) * vec3(sin(u_time*0.55 + ph*6.283),"
      + "                        cos(u_time*0.47 + ph*11.0),"
      + "                        sin(u_time*0.63 + ph*17.0));"
      + "   float grow = (0.35+0.65*born)*(1.0+0.18*sin(born*3.14159));"
      + "   vec4 mv = modelViewMatrix*vec4(p,1.0);"
      + "   float size = a_size*u_scale*u_px*(300.0/max(-mv.z,1.0));"
      + "   size *= mix(1.0,1.42,vR)*grow*(1.0+flight*5.0);"
      /* On the closed head the near hemisphere carries the eye: the cells
         facing the camera keep their size and the far side shrinks back, so
         the front of the head reads as a surface of neurons instead of a flat
         scatter of dots. */
      + "   if (u_vol > 0.5) size *= mix(0.62, 1.0, face);"
      + "   gl_PointSize = clamp(size, u_minpx*0.7, (220.0 + 280.0*flight)*u_px);"
      + "   gl_Position = projectionMatrix*mv; }";
    this.pgeo = new THREE.BufferGeometry();
    this.cloud = new THREE.Points(this.pgeo, new THREE.ShaderMaterial({
      uniforms: this.nodeUni, vertexShader: vs,
      fragmentShader: "varying vec3 vC; varying float vD; varying float vR;"
        + " varying float vAge; varying float vGrow; varying float vSprout;"
        + " varying vec3 vTail; varying float vSeed; varying float vFace;"
        + " varying float vAct;"
        + " uniform vec3 u_light; uniform float u_neural; uniform vec3 u_tint;"
        + " uniform float u_time;"
        + " void main(){ vec2 sp = gl_PointCoord*2.0-1.0; vec2 b = sp;"
        /* While a node streaks in the body shrinks toward the leading edge,
           leaving the trailing half of the sprite free for the comet tail. */
        + "   float flight = vTail.z;"
        + "   vec2 tdir = vTail.xy;"
        + "   vec2 tperp = vec2(-tdir.y, tdir.x);"
        /* The tapering streak is drawn here in the body sprite's own frame,
           where the width constants stay proportional to the planet; the
           halo's frame is u_halo times coarser and only adds a thin aura. */
        + "   float sA = 0.0; vec3 sC = vC;"
        + "   if (flight > 0.002) {"
        + "     b = (b - tdir*0.30*flight) * (1.0 + 1.6*flight);"
        + "     float hb = 0.30*flight - dot(sp, tdir);"
        + "     float back = clamp(hb / (1.0 + 0.30*flight), 0.0, 1.0);"
        + "     float w = mix(0.30, 0.03, back);"
        + "     float streak = smoothstep(w, 0.0, abs(dot(sp, tperp)))"
        + "                  * pow(1.0 - back, 1.2) * step(0.0, hb);"
        + "     sA = clamp(streak * min(1.0, 0.45 + 1.1*flight), 0.0, 1.0);"
        + "     sC = mix(mix(vC, vec3(1.0), 0.35), vC, back);"
        + "   }"
        /* A neuron is a soma with dendrites, not a ball: a solid cell body with
           a nucleus, six primary dendrites that bend and taper, and twelve
           finer branches between them. Everything lives inside the point
           sprite, so the scene still costs one draw call. */
        + "   if (u_neural > 0.5) {"
        /* The phase comes from the cell's own seed, not from its colour: every
           node of a cluster shares one palette entry, so a colour-derived phase
           made all 176 cells of the dominant cluster identical copies. */
        + "     float sd = vSeed;"
        + "     float r = length(b);"
        + "     float coreR = 0.38 + 0.02*sin(sd*9.0);"
        /* Maturation: the cell body swells from a bud to a soma, the primary
           dendrites reach further out, and the finer branches and the beads
           arrive later, in the order a neuron actually puts itself together. */
        + "     float grow = vGrow;"
        + "     float cellR = coreR*(0.42 + 0.58*smoothstep(0.0, 0.30, grow));"
        /* Bend the polar frame so dendrites wander instead of forming a
           perfect snowflake; the displacement is zero at the soma. */
        + "     float ang = atan(b.y, b.x);"
        + "     float bend = sin(ang*3.0 + sd*6.283)*0.20"
        + "                + sin(ang*7.0 - sd*11.0)*0.10;"
        + "     float rr = r*(1.0 + bend*smoothstep(0.18, 0.95, r));"
        + "     float ad = atan(b.y, b.x) + bend*0.55*smoothstep(0.2, 1.0, r);"
        + "     float taper = 1.0 - 0.24*smoothstep(cellR*1.2, 1.0, rr);"
        /* How far the dendrites have reached: early they stop just past the
           soma, later they reach the edge of the sprite. */
        + "     float reach = mix(cellR*0.85, 0.80 + 0.10*vSprout,"
        + "                         smoothstep(0.0, 0.72, grow));"
        + "     float rrm = min(rr, reach);"
        /* six primary dendrites, then twelve shorter branches */
        + "     float a6 = smoothstep(0.40, 0.96, cos(ad*6.0 + sd*6.283));"
        + "     float arms = a6 * smoothstep(reach, cellR*0.55, rrm)"
        + "                * smoothstep(cellR*0.35, cellR*1.45, rrm);"
        + "     float a12 = smoothstep(0.70, 0.99, cos(ad*12.0 - sd*9.0));"
        + "     arms += a12 * smoothstep(reach*0.88, cellR*1.3, rrm)"
        + "             * smoothstep(cellR*0.55, cellR*1.9, rrm) * 0.38"
        + "             * smoothstep(0.30, 0.85, grow)"
        + "             * (0.30 + 0.70*vSprout);"
        /* varicosities: small swellings along the branches */
        + "     float bead = smoothstep(0.72, 1.0,"
        + "       sin(rr*15.0 - sd*7.0)*0.5 + 0.5);"
        + "     arms *= taper*(1.0 + 0.55*bead*smoothstep(0.55, 1.0, grow)"
        + "                      *(0.35 + 0.65*vSprout));"
        /* The branches fade as they reach the edge of the sprite: at full
           strength the six straight spokes read as a star, not as a cell. */
        + "     arms *= 1.0 - 0.42*smoothstep(0.55, 1.0, rrm);"
        /* the soma: a filled body with a brighter membrane and nucleus */
        + "     float soma = smoothstep(cellR+0.07, cellR-0.03, r);"
        + "     float membrane = smoothstep(cellR+0.07, cellR-0.01, r)"
        + "                    - smoothstep(cellR-0.03, cellR-0.13, r);"
        + "     vec2 nu = b - vec2(0.035, 0.045);"
        + "     float nucleus = smoothstep(0.130, 0.0, length(nu));"
        + "     float aura = exp(-r*2.8)*0.24;"
        /* Activity is data volume: a page with more text fires more often,
           and the flash sits in the nucleus so it reads as a cell firing
           rather than as a blinking light. */
        + "     float fire = pow(max(0.0, sin(u_time*1.7 + sd*37.0)), 12.0)*vAct;"
        /* The cell has to hold its own against the tissue: the core is
           brighter and the dendrites carry more of it, while the sprite stays
           the size it was. Visibility comes from contrast, not from size. */
        + "     vec3 col = vC*(0.44 + 1.38*arms*(1.0 + 0.22*vSprout))"
        + "              + mix(vC, u_tint, 0.55)*soma*(1.02 + 0.16*vSprout)"
        + "              + u_tint*(membrane*0.80 + nucleus*(1.18 + 1.4*fire))"
        + "              + vC*aura;"
        + "     float a = clamp(soma*0.98 + arms*0.78 + membrane*0.45"
        + "                    + nucleus*0.9 + aura + fire*0.35, 0.0, 1.0);"
        + "     col = mix(col, sC, sA*(1.0-soma)); a = max(a, sA*(1.0-soma));"
        /* The far hemisphere recedes instead of reading as a second, equally
           bright layer of dots on top of the near one. */
        + "     col *= vFace; a *= vFace;"
        + "     if (a < 0.02) discard;"
        + "     gl_FragColor = vec4(col, a*(0.18+0.82*vD)*vAge);"
        + "     return;"
        + "   }"
        + "   float scale = mix(1.0,1.42,vR); vec2 p = b*scale;"
        + "   float r2 = dot(p,p);"
        + "   float inside = 1.0-smoothstep(0.96,1.0,r2);"
        + "   vec3 n = vec3(p.x,-p.y,sqrt(max(0.0,1.0-r2)));"
        + "   vec3 L = normalize(u_light);"
        + "   float diff = max(dot(n,L),0.0);"
        + "   float spec = pow(max(dot(n,normalize(L+vec3(0.0,0.0,1.0))),0.0),18.0);"
        + "   float rim = pow(1.0-n.z,2.6);"
        + "   float seed = fract(dot(vC, vec3(12.9898, 78.233, 37.719)));"
        /* Continents and cloud belts give each world a surface instead of a
           uniform plastic ball; the seed comes from the node colour so the
           pattern varies without another vertex attribute. */
        + "   float landA = sin(p.x*6.0 + seed*19.0)*sin(p.y*7.5 - seed*11.0);"
        + "   float land = smoothstep(0.10, 0.58, landA*0.5 + 0.5);"
        + "   float cloud = smoothstep(0.55, 0.96,"
        + "     sin(p.y*13.0 + seed*6.28 + sin(p.x*5.0)*0.9)*0.5 + 0.5);"
        + "   vec3 albedo = mix(vC*0.84, vC*1.08, land);"
        + "   albedo = mix(albedo, mix(vC, vec3(1.0), 0.5), cloud*0.26);"
        + "   float polar = smoothstep(0.62, 0.95, abs(p.y));"
        + "   albedo = mix(albedo, vec3(0.80,0.90,1.0), polar*0.24*seed);"
        + "   vec3 rimC = mix(vC, vec3(0.78,0.87,1.0), 0.42);"
        + "   vec3 col = albedo*(0.09+0.91*diff)"
        + "            + vec3(0.80,0.90,1.0)*spec*0.34"
        + "            + rimC*rim*1.05 + vC*0.040;"
        + "   float e = abs(length(vec2(b.x,b.y*3.4))-0.86);"
        + "   float band = (1.0-smoothstep(0.0,0.035,e))*vR;"
        + "   band *= (b.y < 0.0 ? (1.0-inside) : 1.0);"
        + "   col += (vC*1.5+0.10)*band*(0.4+0.6*diff);"
        + "   float a = max(inside, band*0.75);"
        /* The streak paints only outside the body disc, so it never seams
           across the planet face; the displaced head covers the join. */
        + "   float sVis = sA*(1.0-inside);"
        + "   col = mix(col, sC, sVis); a = max(a, sVis);"
        + "   if(a < 0.01) discard;"
        + "   gl_FragColor = vec4(col, a*(0.15+0.85*vD)*vAge); }",
      transparent: true, depthWrite: false}));
    this.cloud.frustumCulled = false;
    this.cloud.renderOrder = 2;
    this.scene.add(this.cloud);

    this.halo = new THREE.Points(this.pgeo, new THREE.ShaderMaterial({
      uniforms: this.haloUni,
      vertexShader: vs.replace("uniform float u_minpx;",
                               "uniform float u_minpx; uniform float u_halo;")
                       .replace("a_size*u_scale*u_px",
                                "a_size*u_scale*u_px*u_halo")
                       .replace("clamp(size, u_minpx*0.7",
                                "clamp(size, u_minpx*1.2"),
      fragmentShader: "varying vec3 vC; varying float vD; varying float vR;"
        + " varying float vAge; varying vec3 vTail; varying float vFace;"
        + " uniform vec3 u_light; uniform float u_neural; uniform float u_gain;"
        + " void main(){ vec2 p = gl_PointCoord*2.0-1.0;"
        + "   float r2 = dot(p,p); if(r2 > 1.0) discard;"
        /* Comet tail: while a node streaks in, its glow shrinks to a bright
           head with only a faint narrow aura behind it — the tapering streak
           itself is drawn in the node sprite's finer frame. Settled nodes
           keep the plain round halo. */
        + "   vec2 tdir = vTail.xy;"
        + "   vec2 tperp = vec2(-tdir.y, tdir.x);"
        + "   float flight = clamp(vTail.z, 0.0, 1.0);"
        + "   vec2 hp = p - tdir*0.30*flight;"
        + "   float hd = length(hp) * (1.0 + 2.0*flight);"
        + "   vec3 n = vec3(hp.x,-hp.y,sqrt(max(0.0,1.0-min(hd*hd,1.0))));"
        + "   float lit = 0.22+0.78*max(dot(n,normalize(u_light)),0.0);"
        + "   float sharp = mix(2.2, 1.5, u_neural);"
        + "   float gain = u_gain;"
        + "   float shade = mix(lit, 1.0, u_neural*0.75);"
        + "   float body = pow(smoothstep(1.0,0.0,min(hd,1.0)),sharp)"
        + "              *gain*vD*shade;"
        + "   float hb = 0.30*flight - dot(p, tdir);"
        + "   float back = clamp(hb / (1.0 + 0.30*flight), 0.0, 1.0);"
        + "   float w = mix(0.16, 0.02, back);"
        + "   float tail = smoothstep(w, 0.0, abs(dot(p, tperp)))"
        + "              * pow(1.0 - back, 2.6) * step(0.0, hb);"
        + "   tail *= flight * 0.6 * vD;"
        /* The aura follows its cell: a halo on the far hemisphere would
           otherwise punch straight back through the near tissue. */
        + "   gl_FragColor = vec4(vC, (body + tail) * vAge * vFace); }",
      transparent: true, depthWrite: false, blending: THREE.AdditiveBlending}));
    this.halo.frustumCulled = false;
    this.halo.renderOrder = 1;
    this.scene.add(this.halo);

    this.edgeUni = {u_cull: {value: 1e9}, u_cam: {value: new THREE.Vector3()},
                    u_opacity: {value: 1}, u_sweepX: {value: 0},
                    u_sweepAmp: {value: 0}, u_time: {value: 0},
                    u_neural: {value: 0}, u_fly: {value: 1},
                    u_driftA: {value: 0}};
    this.lgeo = new THREE.BufferGeometry();
    this.lines = new THREE.LineSegments(this.lgeo, new THREE.ShaderMaterial({
      uniforms: this.edgeUni, transparent: true, depthWrite: false,
      vertexShader: "attribute float a_base; attribute float a_dim; attribute vec3 color;"
        + " attribute float a_birth; attribute float a_t; attribute float a_seed;"
        + " uniform float u_cull; uniform float u_opacity; uniform vec3 u_cam;"
        + " uniform float u_sweepX; uniform float u_sweepAmp; uniform float u_time;"
        + " uniform float u_neural; uniform float u_fly; uniform float u_driftA;"
        + " varying vec3 vC; varying float vA;"
        /* Same flight/drift maths as the node sprite, and the same stable seed,
           so an axon endpoint never detaches from the cell it belongs to. */
        + " void main(){ vC=color;"
        + "   float ph = a_seed;"
        + "   float age = max(0.0, u_time-a_birth);"
        + "   float k = clamp(age/1.15, 0.0, 1.0);"
        + "   float ease = 1.0 - pow(1.0-k, 2.4);"
        + "   float rr = length(position);"
        + "   vec3 dir = rr > 1e-3 ? position/rr"
        + "     : normalize(vec3(sin(ph*6.283), cos(ph*4.11), sin(ph*2.77)));"
        + "   vec3 side = normalize(cross(dir, vec3(0.31, 0.91, 0.27)));"
        + "   vec3 start = position + dir*(26.0 + 34.0*ph)"
        + "              + side*(12.0*(ph-0.5));"
        + "   vec3 pos = position + (start-position)*u_fly*(1.0-ease);"
        + "   pos += u_driftA * vec3(sin(u_time*0.55 + ph*6.283),"
        + "                          cos(u_time*0.47 + ph*11.0),"
        + "                          sin(u_time*0.63 + ph*17.0));"
        + "   float d = distance(pos, u_cam);"
        + "   float fade = 1.0 - smoothstep(u_cull*0.55, u_cull, d);"
        + "   float wave = smoothstep(0.0,0.5,age-a_t*0.22);"
        + "   vA = a_base*u_opacity*a_dim*fade*wave;"
        /* A connectome needs its wires: on a dark head the structural base of
           0.08 is invisible, so the neural scene lifts every axon into view
           while the planets scene keeps its faint orbital threads. */
        + "   vA *= mix(1.0, 2.6, u_neural);"
        + "   vA += u_sweepAmp*exp(-abs(pos.x-u_sweepX)*0.18)*0.9;"
        /* Neural scene: action potentials travel along the axons as a spatial
           wave, so the web reads as living tissue rather than static lines. */
        + "   vA += u_neural * a_base * 0.85 *"
        + "         (0.5+0.5*sin(pos.x*0.55 + pos.y*0.38"
        + "                     + pos.z*0.31 + u_time*2.1 + a_t*4.0));"
        + "   gl_Position = projectionMatrix*modelViewMatrix*vec4(pos,1.0); }",
      fragmentShader: "varying vec3 vC; varying float vA;"
        + " void main(){ gl_FragColor = vec4(vC, vA); }"}));
    this.lines.frustumCulled = false;
    this.scene.add(this.lines);

    var sphere = new THREE.SphereGeometry(1, 10, 8);
    this.egoMat = new THREE.MeshBasicMaterial({color: 0xffffff, transparent: true,
                                               opacity: 0.94, depthWrite: false});
    this.egoMesh = new THREE.InstancedMesh(sphere, this.egoMat, 120);
    this.egoMesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    this.egoMesh.frustumCulled = false;
    this.egoMesh.count = 0;
    this.egoMesh.visible = false;
    this.scene.add(this.egoMesh);
    this._dummy = new THREE.Object3D();

    this._v = new THREE.Vector3();
  };

  /* A rasteriser that runs on the CPU (SwiftShader, llvmpipe) cannot afford
     MSAA: four samples per pixel quadruples the fragment work on the one
     device that has none to spare. Ask a throwaway context before the real
     one is created, because antialias is a context attribute. */
  function softwareRasteriser() {
    try {
      var probe = document.createElement("canvas");
      var gl = probe.getContext("webgl2") || probe.getContext("webgl");
      if (!gl) return false;
      var info = gl.getExtension("WEBGL_debug_renderer_info");
      var name = String(info ? gl.getParameter(info.UNMASKED_RENDERER_WEBGL)
                             : gl.getParameter(gl.RENDERER));
      return /swiftshader|llvmpipe|softpipe|software|basic render/i.test(name);
    } catch (e) {
      return false;
    }
  }

  Renderer.prototype._mountGL = function () {
    var THREE = T3.THREE, self = this;
    this._softwareGL = softwareRasteriser();
    this.renderer = new THREE.WebGLRenderer({canvas: this.canvas,
                                             antialias: !this._softwareGL,
                                             powerPreference: "high-performance"});
    this.renderer.setPixelRatio(this.pixelRatio());
    this._onContextLost = function (event) {
      if (event && event.preventDefault) event.preventDefault();
      self.contextLost = true;
    };
    this._onContextRestored = function () {
      self.contextLost = false;
      self.colorDirty = true;
      self.lastVersion = -1;
      self.lastDimVersion = -1;
      self._clearEdgeIndex();
      self.renderer.setPixelRatio(self.pixelRatio());
      self.resize();
    };
    this.canvas.addEventListener("webglcontextlost", this._onContextLost, false);
    this.canvas.addEventListener("webglcontextrestored", this._onContextRestored, false);
  };

  Renderer.prototype.setScene = function (name) {
    var cfg = (SK.scenes && SK.scenes[name]) || (SK.scenes && SK.scenes.planets);
    if (!cfg) return;
    if (this._drag) this._finishDrag();
    /* Pulses address edge slots and segment counts from the old scene; the
       next sync rebuilds both, so drop the wave rather than index into it. */
    this._pulse = null;
    this._cityFlash = null;
    this.name = name;
    /* Each scene keeps its own pulled-aside cells: the same page is a tower in
       one scene and a neuron in the next, and one scene's offsets must not
       follow it into the other. */
    this._dragStore = this._dragStore || {};
    this._dragOff = this._dragStore[name] || (this._dragStore[name] = {});
    this.cfg = cfg;
    this.scene.background = new T3.THREE.Color(cfg.bg);
    if (this.sky) this.sky.visible = cfg.starfield !== false;
    this._ringThreshold = cfg.ringRank == null ? 2 : cfg.ringRank;
    var ld = cfg.lightDir || [-0.55, 0.6, 0.75];
    this.nodeUni.u_light.value.set(ld[0], ld[1], ld[2]).normalize();
    this.haloUni.u_halo.value = cfg.haloScale;
    /* How much a halo lifts the pixels behind it. A dense head lights its own
       tissue: 176 cells at 0.30 washed the whole membrane into their colour,
       so the neural scene turns the auras down and keeps the membrane dark. */
    this.haloUni.u_gain.value = cfg.haloGain == null ? 0.31 : cfg.haloGain;
    this.edgeUni.u_opacity.value = cfg.edgeOpacity;
    this.nodeUni.u_scale.value = cfg.nodeScale;
    /* The sky and the node sprites each carry three drawings; a scene picks
       one without adding a draw call or a second material. */
    if (this.skyUni) {
      this.skyUni.u_mode.value = cfg.sky === "deepvoid" ? 1
        : (cfg.sky === "skyline" ? 2 : 0);
    }
    this.nodeUni.u_neural.value = cfg.neuralNodes ? 1 : 0;
    this.edgeUni.u_neural.value = cfg.neuralNodes ? 1 : 0;
    /* The pale colour of a cell's membrane and nucleus; it used to be a hard
       cyan in the shader, which tinted every palette cool. */
    this.nodeUni.u_tint.value.setHex(cfg.cellTint == null ? 0xffedd0 : cfg.cellTint);
    /* Volume shading is for the head: the cells are on a closed surface and
       the far side must fall away. Other scenes are open clouds, where a facing
       term would only dim half the picture. */
    this.nodeUni.u_vol.value = cfg.brain ? 1 : 0;
    /* How far a newborn node streaks in from, and how much the settled graph
       breathes; both are per scene because planets want arrivals and the neural
       wants a slow drift. */
    var fly = cfg.fly == null ? 1 : cfg.fly;
    var drift = cfg.drift == null ? 0 : cfg.drift;
    this.nodeUni.u_fly.value = fly;
    this.nodeUni.u_driftA.value = drift;
    this.edgeUni.u_fly.value = fly;
    this.edgeUni.u_driftA.value = drift;
    /* Scene-wide position spread: the layout is shared, a scene may open it up
       so cells and planets get the room their sprites need. */
    this.posScale = SCALE * (cfg.posScale || 1);
    /* Curved links are a property of the scene, not of the graph: switching
       from the head to the city re-cuts every axon into one straight street
       link, and the geometry is rebuilt when the count changes. */
    this.segK = Math.max(1, cfg.curveSegs || 1);
    this.colorDirty = true;
    this.lastVersion = -1;
    this.lastSelectionKey = "";
    this._syncVisibility();
    this.resetFinale();
    this._clearEdgeIndex();
    var planet = !!cfg.shells;
    if (this.ringMesh) this.ringMesh.visible = planet;
    /* The brain scene needs its tissue: the mesh that carries the silhouette
       and the cells that grow on it. */
    if (cfg.brain) {
      this._buildCortex();
      this.cortexUni.u_opacity.value = cfg.cortexOpacity == null ? 0.32 : cfg.cortexOpacity;
      this.cortexUni.u_color.value.setHex(cfg.cortexColor == null ? 0x5b5566 : cfg.cortexColor);
      this.cortexUni.u_rim.value.setHex(cfg.cortexRim == null ? 0xffc98a : cfg.cortexRim);
      this.cortexUni.u_light.value.set(ld[0], ld[1], ld[2]).normalize();
    }
    this._brain = null;
    this.brainAge = null;
    /* A city is a different layout, not a different colour: switching back
       has to rebuild the towers on the lattice. */
    this._cityN = -1;
    this.cityUni.u_pitch.value = cfg.cityPitch || 3;
    this.cityUni.u_finale.value = 0;
  };

  Renderer.prototype._syncVisibility = function () {
    var people = !!(this.cfg && this.cfg.nodeMode === "instanced");
    var city = !!(this.cfg && this.cfg.city);
    /* In the city the towers are the nodes, so the sprite cloud and its halos
       step aside; leaving them on would print a second, floating copy of every
       building over the roofs. */
    if (this.cloud) this.cloud.visible = !people && !city;
    if (this.halo) this.halo.visible = !people && !city;
    if (this.egoMesh) this.egoMesh.visible = people;
    if (this.cityMesh) this.cityMesh.visible = city;
    if (this.cortex) this.cortex.visible = !!(this.cfg && this.cfg.brain);
  };

  /* The cortex geometry. The shape never changes, only how far it has
     developed, so the mesh is built once when the scene is chosen. */
  Renderer.prototype._buildCortex = function () {
    var THREE = T3.THREE;
    if (!this.cortex || !SK.brainMesh) return;
    /* 128x256 keeps the gyri smooth at 32,768 vertices: half of what an
       unsigned short index can address, so nothing here needs a 32-bit index
       buffer. */
    var m = SK.brainMesh(128, 256);
    var geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(m.positions, 3));
    geo.setAttribute("normal", new THREE.BufferAttribute(m.normals, 3));
    geo.setAttribute("a_flat", new THREE.BufferAttribute(m.flat, 3));
    geo.setAttribute("a_flatN", new THREE.BufferAttribute(m.flatNormals, 3));
    geo.setAttribute("a_dev", new THREE.BufferAttribute(m.dev, 1));
    geo.setAttribute("a_fold", new THREE.BufferAttribute(m.fold, 1));
    /* 16 bit indices: the grid stays under 65536 vertices, and a head is not
       worth needing an extension for. */
    geo.setIndex(new THREE.BufferAttribute(new Uint16Array(m.indices), 1));
    if (this.cortex.geometry) this.cortex.geometry.dispose();
    this.cortex.geometry = geo;
    var maxR = 0;
    for (var i = 0; i < m.count; i++) {
      var r = Math.hypot(m.positions[i * 3], m.positions[i * 3 + 1],
                         m.positions[i * 3 + 2]);
      if (r > maxR) maxR = r;
    }
    this._brainMaxR = maxR || 1;
    var want = (this.cfg && this.cfg.brainRadius) || 13;
    this._brainScale = want / this._brainMaxR;
    this.cortex.scale.setScalar(this._brainScale);
  };

  /* Where every cell sits on the cortex.

     The directions come from the R2 sequence, so the coat of cells is even at
     every stage of the build and no cell lands on top of its neighbour. The
     site layout is deliberately not used: a settled layout leaves whole
     directions empty and others with five cells in them, and a head wearing
     bald patches does not read as a head. The graph is still in the picture,
     in the axons that join the cells.

     The result is cached per node count: the placement is stable, so the
     head does not re-form every time the layout settles. */
  Renderer.prototype.ensureBrain = function (live) {
    if (!live || !this.cfg || !this.cfg.brain) return null;
    var n = live.n;
    if (this._brain && this._brain.n === n) return this._brain;
    if (!SK.brainSequence || !SK.brainJitter || !SK.brainPatch) return null;
    var dirs = new Float32Array(n * 3);
    for (var i = 0; i < n; i++) {
      var s = SK.brainSequence(i);
      dirs[i * 3] = s[0]; dirs[i * 3 + 1] = s[1]; dirs[i * 3 + 2] = s[2];
    }
    /* The R2 lattice hands out the same direction every so often. A doubled
       cell reads as a mistake, so a repeat is turned a little on the cortex
       until it has the room its neighbours have. */
    var minDot = Math.cos(0.06);
    for (var a = 0; a < n; a++) {
      for (var attempt = 0; attempt < 8; attempt++) {
        var hit = -1;
        for (var b = 0; b < a; b++) {
          var dot = dirs[a * 3] * dirs[b * 3] + dirs[a * 3 + 1] * dirs[b * 3 + 1] +
                    dirs[a * 3 + 2] * dirs[b * 3 + 2];
          if (dot >= minDot) { hit = b; break; }
        }
        if (hit < 0) break;
        /* Turn the cell about the axis that joins the two directions. */
        var ax = dirs[a * 3 + 1] * dirs[hit * 3 + 2] - dirs[a * 3 + 2] * dirs[hit * 3 + 1];
        var ay = dirs[a * 3 + 2] * dirs[hit * 3] - dirs[a * 3] * dirs[hit * 3 + 2];
        var az = dirs[a * 3] * dirs[hit * 3 + 1] - dirs[a * 3 + 1] * dirs[hit * 3];
        var al = Math.sqrt(ax * ax + ay * ay + az * az);
        if (al < 1e-6) { ax = 1; ay = 0; az = 0; al = 1; }
        ax /= al; ay /= al; az /= al;
        var ang = 0.12 + 0.06 * SK.hashSeed(a * 5.3 + 1.7);
        var ca = Math.cos(ang), sa = Math.sin(ang);
        var vx = dirs[a * 3], vy = dirs[a * 3 + 1], vz = dirs[a * 3 + 2];
        var cx = vy * az - vz * ay, cy = vz * ax - vx * az, cz = vx * ay - vy * ax;
        var dt = vx * ax + vy * ay + vz * az;
        var nx = vx * ca + cx * sa + ax * dt * (1 - ca);
        var ny = vy * ca + cy * sa + ay * dt * (1 - ca);
        var nz = vz * ca + cz * sa + az * dt * (1 - ca);
        var nl = Math.sqrt(nx * nx + ny * ny + nz * nz) || 1;
        dirs[a * 3] = nx / nl; dirs[a * 3 + 1] = ny / nl; dirs[a * 3 + 2] = nz / nl;
      }
    }
    var point = new Float32Array(n * 3);
    var age = new Float32Array(n);
    var prof = [0, 0, 0];
    for (var k = 0; k < n; k++) {
      /* A cell does not sit on a grid vertex: it is nudged along the cortex so
         the coat breaks up into tissue instead of a lat/long lattice. */
      var dir = SK.brainJitter(dirs[k * 3], dirs[k * 3 + 1], dirs[k * 3 + 2],
                               k * 0.618 + 1.7);
      SK.brainPatch(dir[0], dir[1], dir[2], prof);
      /* The soma is lifted clear of the membrane. The cortex is drawn as
         triangles between grid vertices, so the interpolated surface can stand
         proud of the analytic one (worst measured chord error was -0.020 in
         canonical units over 5000 samples at 96x192); 0.030 keeps every cell
         on the outside of the tissue, and a little hash makes the depth vary so
         the coat is not a shell of constant thickness. */
      var r = prof[0] + prof[1] + 0.030 + 0.008 * SK.hashSeed(k * 9.1 + 4.3);
      point[k * 3] = dir[0] * r;
      point[k * 3 + 1] = dir[1] * r;
      point[k * 3 + 2] = dir[2] * r;
      /* How mature the patch of cortex under this cell is: the brainstem is
         the first thing that forms, the frontal pole the last. */
      age[k] = prof[2];
    }
    this._brain = {n: n, point: point, age: age};
    this.brainAge = age;
    return this._brain;
  };

  /* ---- city geometry -----------------------------------------------------

     Every tower is four stacked tiers -- a wider podium, the shaft, a setback
     and a crown slab -- merged with the rest of the city into one indexed
     mesh. A single box reads as an extruded rectangle from every angle: the
     chamfered plan gives the silhouette eight edges, and the setback gives it
     a step while the camera is still far enough away that windows are pixels.

     The table below is the static half: it holds the unit plan, the surface
     normal, the facade coordinates and the face id. ``_writeCity`` scales it
     by the tower's own footprint and tier width and stamps the height
     fractions on top, so a layout change rewrites coordinates without
     reallocating buffers. At 144 vertices a tower, a two-hundred page crawl is
     a single draw call and under thirty thousand vertices.

     Face ids: 0..7 walls, 8 the crown roof, 9 a setback terrace, 10 the
     ground. Only the crown carries the beacon and the lit parapet; a light on
     every setback would read as a string of lanterns down the tower. */
  var CELL_ZERO = [0, 0];
  var CITY_TIERS = 4;
  var CITY_VERTS = CITY_TIERS * 9 * 4;      /* 144: eight walls + cap */
  var CITY_INDICES = CITY_TIERS * 9 * 6;    /* 216 */
  var CITY_ROOF_FACE = 8, CITY_TERRACE_FACE = 9, CITY_GROUND_FACE = 10;

  /* The plan is an octagon: a square with its corners sliced off. A plain box
     has two faces and a silhouette; eight faces catch the sky at eight
     angles, and the chamfer lines run the full height of the tower where the
     eye expects the edge of a building. ``CH`` is the slice in side units. */
  var CITY_CHAMFER = 0.16;
  var CITY_QUADS = (function () {
    var h = 0.5, c = 0.5 - CITY_CHAMFER, k = Math.SQRT1_2, out = [];
    /* Cyclic corners of the octagon, counter-clockwise seen from above. */
    var ring = [[h, -c], [h, c], [c, h], [-c, h], [-h, c], [-h, -c],
                [-c, -h], [c, -h]];
    for (var i = 0; i < 8; i++) {
      var a = ring[i], b = ring[(i + 1) % 8];
      var nx = b[1] - a[1], nz = -(b[0] - a[0]);
      var nl = Math.hypot(nx, nz) || 1;
      out.push({
        n: [nx / nl, 0, nz / nl],
        /* The normal is enough to wind the quad; the id only drives shading. */
        c: [[a[0], -0.5, a[1]], [b[0], -0.5, b[1]],
            [b[0], 0.5, b[1]], [a[0], 0.5, a[1]]],
        /* Share of the frontage this wall covers, for the window count. */
        wf: Math.hypot(b[0] - a[0], b[1] - a[1])
      });
    }
    /* A chamfer carries one pane; a full face carries the tower's columns. */
    for (var j = 0; j < out.length; j++) out[j].wf = Math.min(1, out[j].wf);
    return out;
  })();
  var CITY_ROOF = {n: [0, 1, 0], face: CITY_ROOF_FACE,
    c: [[-0.5, 0.5, -0.5], [0.5, 0.5, -0.5], [0.5, 0.5, 0.5], [-0.5, 0.5, 0.5]]};

  /* The static half of a tower. Winding is decided by the cross product
     rather than by hand, so a face can never end up inside out. */
  function cityVertexTable() {
    var unit = new Float32Array(CITY_VERTS * 3);
    var nrm = new Float32Array(CITY_VERTS * 3);
    var loc = new Float32Array(CITY_VERTS * 3);
    var face = new Float32Array(CITY_VERTS);
    var tier = new Float32Array(CITY_VERTS);
    var wf = new Float32Array(CITY_VERTS);
    var ground = null;
    var v = 0;

    function emit(c, n, faceId, t, y0, y1, share) {
      var ax = c[1][0] - c[0][0], ay = c[1][1] - c[0][1], az = c[1][2] - c[0][2];
      var bx = c[2][0] - c[0][0], by = c[2][1] - c[0][1], bz = c[2][2] - c[0][2];
      var nx = ay * bz - az * by, ny = az * bx - ax * bz, nz = ax * by - ay * bx;
      var order = (nx * n[0] + ny * n[1] + nz * n[2]) >= 0
        ? [0, 1, 2, 3] : [0, 3, 2, 1];
      /* Horizontal coordinate along the wall, -1 at one end and +1 at the
         other, so the window grid spans the face whichever way it runs. */
      var tx = ax, tz = az, tl = Math.hypot(tx, tz) || 1;
      tx /= tl; tz /= tl;
      for (var k = 0; k < 4; k++) {
        var p = c[order[k]];
        var edge = p[1] > 0 ? 1 : 0;
        var yf = edge ? y1 : y0;
        unit[v * 3] = p[0]; unit[v * 3 + 1] = edge; unit[v * 3 + 2] = p[2];
        nrm[v * 3] = n[0]; nrm[v * 3 + 1] = n[1]; nrm[v * 3 + 2] = n[2];
        if (faceId > 7) {
          /* Cap: the pattern is normalised to this tier's own footprint. */
          loc[v * 3] = p[0] * 2; loc[v * 3 + 1] = p[2] * 2;
        } else {
          loc[v * 3] = ((p[0] - c[0][0]) * tx + (p[2] - c[0][2]) * tz) / tl * 2 - 1;
          loc[v * 3 + 1] = yf;
        }
        loc[v * 3 + 2] = 0;
        face[v] = faceId;
        tier[v] = t;
        wf[v] = share == null ? 1 : share;
        v++;
      }
      return order;
    }

    for (var t = 0; t < CITY_TIERS; t++) {
      for (var q = 0; q < CITY_QUADS.length; q++) {
        emit(CITY_QUADS[q].c, CITY_QUADS[q].n, q, t, 0, 1, CITY_QUADS[q].wf);
      }
      /* The top tier's roof is the crown; the others are setback terraces. */
      var top = t === CITY_TIERS - 1;
      var ordered = emit(CITY_ROOF.c, CITY_ROOF.n,
                         top ? CITY_ROOF_FACE : CITY_TERRACE_FACE, t, 0, 1, 1);
      if (top) {
        /* The ground reuses the roof quad, so it has to keep exactly the
           winding emit chose: the raw corners would make the plane
           back-facing and the streets vanish. */
        ground = ordered.map(function (k) {
          return [CITY_ROOF.c[k][0], 0, CITY_ROOF.c[k][2]];
        });
      }
    }
    return {unit: unit, nrm: nrm, loc: loc, face: face, tier: tier, wf: wf,
            ground: ground};
  }

  Renderer.prototype._ensureCity = function (live) {
    var n = live.n;
    if (this._cityN === n && this.cityGeo.getAttribute("position")) return;
    var THREE = T3.THREE;
    var table = cityVertexTable();
    var verts = n * CITY_VERTS + 4;
    this._cityN = n;
    this._cityTable = table;
    this._cityH = new Float32Array(n);
    this._cityW = new Float32Array(n);
    this._cityD = new Float32Array(n);
    this._cityFloors = new Float32Array(n);
    this._cityLit = new Float32Array(n);
    this._cityCols = new Float32Array(n);
    /* Three cut heights per tower: podium top, setback, crown base. The
       fourth level is always the roof. */
    this._cityTiers = new Float32Array(n * 4);
    /* How wide each tier stands against the plot: a tower whose crown is
       narrower than its shaft reads as a spire, one that flares reads as a
       thirties block. Same index as the cut heights. */
    this._cityTierW = new Float32Array(n * 4);
    this._cityPos = new Float32Array(verts * 3);
    this._cityCol = new Float32Array(verts * 3);
    this._cityLoc = new Float32Array(verts * 3);
    this._cityNrm = new Float32Array(verts * 3);
    this._cityFace = new Float32Array(verts);
    this._citySpec = new Float32Array(verts * 4);
    this._cityBirth = new Float32Array(verts);
    /* One dim per vertex, like the point cloud: a chapter filter has to remove
       the tower from the street, not only from the legend. The ground keeps 1
       so the city never loses its floor. */
    this._cityDim = new Float32Array(verts);
    this._cityDim.fill(1);
    var quads = CITY_TIERS * 9;
    var idx = new Uint32Array(n * CITY_INDICES + 6);
    for (var i = 0; i < n; i++) {
      var o = i * CITY_VERTS;
      for (var k = 0; k < CITY_VERTS; k++) {
        /* x is the tier's own edge, y is stamped in _writeCity. */
        this._cityLoc[(o + k) * 3] = table.loc[k * 3];
        this._cityNrm[(o + k) * 3] = table.nrm[k * 3];
        this._cityNrm[(o + k) * 3 + 1] = table.nrm[k * 3 + 1];
        this._cityNrm[(o + k) * 3 + 2] = table.nrm[k * 3 + 2];
        this._cityFace[o + k] = table.face[k];
      }
      for (var f = 0; f < quads; f++) {
        var src = o + f * 4, dst = (i * quads + f) * 6;
        idx[dst] = src; idx[dst + 1] = src + 1; idx[dst + 2] = src + 2;
        idx[dst + 3] = src; idx[dst + 4] = src + 2; idx[dst + 5] = src + 3;
      }
    }
    var g = n * CITY_VERTS;
    idx[n * CITY_INDICES] = g; idx[n * CITY_INDICES + 1] = g + 1;
    idx[n * CITY_INDICES + 2] = g + 2;
    idx[n * CITY_INDICES + 3] = g; idx[n * CITY_INDICES + 4] = g + 2;
    idx[n * CITY_INDICES + 5] = g + 3;
    for (var gi = 0; gi < 4; gi++) {
      this._cityFace[g + gi] = CITY_GROUND_FACE;
      this._cityNrm[(g + gi) * 3 + 1] = 1;
    }
    var geo = this.cityGeo;
    geo.setAttribute("position", new THREE.BufferAttribute(this._cityPos, 3));
    geo.setAttribute("normal", new THREE.BufferAttribute(this._cityNrm, 3));
    geo.setAttribute("color", new THREE.BufferAttribute(this._cityCol, 3));
    geo.setAttribute("a_local", new THREE.BufferAttribute(this._cityLoc, 3));
    geo.setAttribute("a_face", new THREE.BufferAttribute(this._cityFace, 1));
    geo.setAttribute("a_spec", new THREE.BufferAttribute(this._citySpec, 4));
    geo.setAttribute("a_birth", new THREE.BufferAttribute(this._cityBirth, 1));
    geo.setAttribute("a_dim", new THREE.BufferAttribute(this._cityDim, 1));
    geo.setIndex(new THREE.BufferAttribute(idx, 1));
  };

  /* Writes the towers into the vertex buffers. Called whenever the layout or
     the graph's numbers change, never per frame. */
  Renderer.prototype._writeCity = function (live) {
    var n = live.n;
    if (!this._cityPos || this._cityN !== n) return;
    var table = this._cityTable, tier = table.tier, unit = table.unit;
    var pos = this._cityPos, col = this._cityCol, spec = this._citySpec;
    var loc = this._cityLoc, birth = this._cityBirth, dim = this._cityDim;
    var tierW = this._cityTierW;
    var maxR = 4;
    for (var i = 0; i < n; i++) {
      var cx = this.nPos[i * 3], cz = this.nPos[i * 3 + 2];
      var h = this._cityH[i], w = this._cityW[i], d = this._cityD[i];
      var levels = this._cityTiers, li = i * 4;
      var c = this._color(live._nodeCluster ? live._nodeCluster[i] : 0, i);
      var lit = this._cityLit[i];
      var floors = this._cityFloors[i];
      var cols = this._cityCols[i];
      var seed = SK.hashSeed(i * 1.71 + 0.37);
      var b = this.nBirth ? this.nBirth[i] : 0;
      var base = i * CITY_VERTS;
      for (var k = 0; k < CITY_VERTS; k++) {
        var v = base + k, o = v * 3;
        var t = tier[k];
        /* -0.5 marks the tier base, +0.5 its top; the table carries the flag
           in unit.y so no extra attribute is needed. */
        var yf = unit[k * 3 + 1]
          ? (t === CITY_TIERS - 1 ? 1 : levels[li + t + 1])
          : levels[li + t];
        var sc = tierW ? tierW[li + t] : 1;
        pos[o] = cx + unit[k * 3] * w * sc;
        pos[o + 1] = yf * h;
        pos[o + 2] = cz + unit[k * 3 + 2] * d * sc;
        /* Walls carry their height in local.y. A cap must keep the plan
           coordinate the table wrote for it instead, or the roof pattern
           reads a constant and the beacon never lights. */
        loc[o + 1] = table.face[k] > 7 ? table.loc[k * 3 + 1] : yf;
        col[o] = c.r; col[o + 1] = c.g; col[o + 2] = c.b;
        spec[v * 4] = floors; spec[v * 4 + 1] = lit;
        spec[v * 4 + 2] = seed;
        /* Pane count follows the frontage of this face: a chamfer is a
           quarter of a wall and gets a quarter of the columns, so the glass
           stays the same size all the way round the tower. */
        spec[v * 4 + 3] = cols * table.wf[k];
        birth[v] = b;
        dim[v] = live.dim[i];
      }
      var rr = Math.hypot(cx, cz) + Math.max(w, d) * 0.5;
      if (rr > maxR) maxR = rr;
    }
    /* The ground reaches past the last block and fades into the haze, so the
       city has no visible edge floating in the sky. */
    var R = Math.max(26, maxR * 1.55);
    this.cityUni.u_cityR.value = R;
    var g = n * CITY_VERTS;
    var roof = this._cityTable.ground || CITY_ROOF.c;
    for (var gi = 0; gi < 4; gi++) {
      var p = roof[gi], vg = g + gi;
      pos[vg * 3] = p[0] * R * 4;
      pos[vg * 3 + 1] = -0.03;
      pos[vg * 3 + 2] = p[2] * R * 4;
      col[vg * 3] = 0.05; col[vg * 3 + 1] = 0.05; col[vg * 3 + 2] = 0.06;
      this._cityLoc[vg * 3] = p[0] * R * 4;
      this._cityLoc[vg * 3 + 1] = p[2] * R * 4;
      spec[vg * 4] = 0; spec[vg * 4 + 1] = 0; spec[vg * 4 + 2] = 0;
      birth[vg] = 0;
      dim[vg] = 1;
    }
    var geo = this.cityGeo;
    var names = ["position", "color", "a_local", "a_spec", "a_birth", "a_dim"];
    for (var a = 0; a < names.length; a++) {
      var attr = geo.getAttribute(names[a]);
      if (attr) attr.needsUpdate = true;
    }
  };

  /* A drag moves one tower (and its neighbours): rewrite just those vertices
     instead of the whole city buffer. */
  Renderer.prototype._cityMove = function (i) {
    if (!this._cityPos || this._cityN <= 0 || i < 0 || i >= this._cityN) return;
    var table = this._cityTable, pos = this._cityPos;
    var tier = table.tier, unit = table.unit, levels = this._cityTiers;
    var tierW = this._cityTierW;
    var cx = this.nPos[i * 3], cz = this.nPos[i * 3 + 2];
    var h = this._cityH[i], w = this._cityW[i], d = this._cityD[i];
    var base = i * CITY_VERTS, li = i * 4;
    for (var k = 0; k < CITY_VERTS; k++) {
      var o = (base + k) * 3;
      var t = tier[k];
      var yf = unit[k * 3 + 1]
        ? (t === CITY_TIERS - 1 ? 1 : levels[li + t + 1])
        : levels[li + t];
      var sc = tierW ? tierW[li + t] : 1;
      pos[o] = cx + unit[k * 3] * w * sc;
      pos[o + 1] = yf * h;
      pos[o + 2] = cz + unit[k * 3 + 2] * d * sc;
    }
    var attr = this.cityGeo.getAttribute("position");
    if (attr) attr.needsUpdate = true;
  };

  Renderer.prototype._ensure = function () {
    var live = this.live;
    var THREE = T3.THREE;
    if (this.cap !== live.cap) {
      this.cap = live.cap;
      this.nPos = new Float32Array(this.cap * 3);
      this.nCol = new Float32Array(this.cap * 3);
      this.nSize = new Float32Array(this.cap);
      this.nDim = new Float32Array(this.cap);
      this.nRing = new Float32Array(this.cap);
      this.nBirth = new Float32Array(this.cap);
      /* Activity: how much text this page carries, normalised. The neural
         shader turns it into a firing rate, so a hub that actually says
         something pulses hard while a stub with one link only breathes. */
      this.nAct = new Float32Array(this.cap);
      /* A stable phase per cell, independent of where the cell currently is.
         See the vertex shader: a position-derived phase makes a moving cell
         shake, because the phase re-rolls with every frame of the drag. */
      this.nSeed = new Float32Array(this.cap);
      for (var sd = 0; sd < this.cap; sd++) {
        this.nSeed[sd] = SK.hashSeed(sd * 7.13 + 1.71);
      }
      this.pgeo.setAttribute("position", new THREE.BufferAttribute(this.nPos, 3));
      this.pgeo.setAttribute("color", new THREE.BufferAttribute(this.nCol, 3));
      this.pgeo.setAttribute("a_size", new THREE.BufferAttribute(this.nSize, 1));
      this.pgeo.setAttribute("a_dim", new THREE.BufferAttribute(this.nDim, 1));
      this.pgeo.setAttribute("a_ring", new THREE.BufferAttribute(this.nRing, 1));
      this.pgeo.setAttribute("a_birth", new THREE.BufferAttribute(this.nBirth, 1));
      this.pgeo.setAttribute("a_seed", new THREE.BufferAttribute(this.nSeed, 1));
      this.pgeo.setAttribute("a_act", new THREE.BufferAttribute(this.nAct, 1));
      if (this._birthQueue) {
        for (var q = 0; q < this._birthQueue.length; q++) {
          var bq = this._birthQueue[q];
          if (bq[0] >= 0 && bq[0] < this.nBirth.length) this.nBirth[bq[0]] = bq[1];
        }
        this._birthQueue = null;
      }
      this.colorDirty = true;
      this.lastDimVersion = -1;
      this.sceneN = 0;
    }
    if (this.capE !== live.capE || this._segK !== this.segK) {
      this.capE = live.capE;
      this._segK = this.segK;
      var k2 = this.segK * 2;
      this.ePos = new Float32Array(this.capE * k2 * 3);
      this.eCol = new Float32Array(this.capE * k2 * 3);
      this.eBase = new Float32Array(this.capE * k2);
      this.eDim = new Float32Array(this.capE * k2);
      this.eBirth = new Float32Array(this.capE * k2);
      this.eT = new Float32Array(this.capE * k2);
      /* One seed per endpoint: an axon inherits the phase of the soma at each
         end, so both drift together instead of sliding off the cell. */
      this.eSeed = new Float32Array(this.capE * k2);
      for (var t = 0; t < this.capE; t++) {
        for (var sj = 0; sj < this.segK; sj++) {
          var tb = (t * this.segK + sj) * 2;
          this.eT[tb] = sj / this.segK;
          this.eT[tb + 1] = (sj + 1) / this.segK;
        }
      }
      this._edgeSlots = new Int32Array(this.capE);
      this._edgeSlots.fill(-1);
      this.lgeo.setAttribute("position", new THREE.BufferAttribute(this.ePos, 3));
      this.lgeo.setAttribute("color", new THREE.BufferAttribute(this.eCol, 3));
      this.lgeo.setAttribute("a_base", new THREE.BufferAttribute(this.eBase, 1));
      this.lgeo.setAttribute("a_dim", new THREE.BufferAttribute(this.eDim, 1));
      this.lgeo.setAttribute("a_birth", new THREE.BufferAttribute(this.eBirth, 1));
      this.lgeo.setAttribute("a_t", new THREE.BufferAttribute(this.eT, 1));
      this.lgeo.setAttribute("a_seed", new THREE.BufferAttribute(this.eSeed, 1));
      if (this._edgeBirthQueue) {
        for (var eq = 0; eq < this._edgeBirthQueue.length; eq++) {
          var eb = this._edgeBirthQueue[eq];
          if (eb[0] >= 0 && eb[0] < this.capE) {
            for (var ev = 0; ev < k2; ev++) {
              this.eBirth[eb[0] * k2 + ev] = eb[1];
            }
          }
        }
        this._edgeBirthQueue = null;
      }
      this.colorDirty = true;
      this.lastDimVersion = -1;
    }
  };

  Renderer.prototype.markBirth = function (i, t) {
    if (this.nBirth && i >= 0 && i < this.nBirth.length) {
      this.nBirth[i] = t;
      var attr = this.pgeo.getAttribute("a_birth");
      if (attr) attr.needsUpdate = true;
    } else {
      (this._birthQueue || (this._birthQueue = [])).push([i, t]);
    }
  };

  Renderer.prototype.markEdgeBirth = function (e, t) {
    if (this.eBirth && e >= 0 && e < this.capE) {
      var k2 = this.segK * 2;
      for (var v = 0; v < k2; v++) this.eBirth[e * k2 + v] = t;
      var attr = this.lgeo.getAttribute("a_birth");
      if (attr) attr.needsUpdate = true;
    } else {
      (this._edgeBirthQueue || (this._edgeBirthQueue = [])).push([e, t]);
    }
  };

  /* Everything already on screen is mature: snapshots and preloaded graphs
     must not replay the build animation as one full-screen flash. */
  Renderer.prototype.resetBirth = function () {
    this._birthQueue = null;
    this._edgeBirthQueue = null;
    if (this.nBirth) this.nBirth.fill(0);
    if (this.eBirth) this.eBirth.fill(0);
    var na = this.nBirth && this.pgeo.getAttribute("a_birth");
    var ea = this.eBirth && this.lgeo.getAttribute("a_birth");
    if (na) na.needsUpdate = true;
    if (ea) ea.needsUpdate = true;
  };

  /* Palette colour for a cluster. ``i`` is the node index, when the caller has
     one: a cluster is a single hue, so without a per-cell offset the 176 cells
     of the dominant cluster are 176 identical dots and the coat reads as one
     flat colour. The offset is small enough that the cluster still reads as a
     cluster; it only breaks the copies. */
  Renderer.prototype._color = function (clusterId, i) {
    var palette = (this.cfg && this.cfg.palette) || SK.PALETTE;
    var hex = palette[clusterId % palette.length];
    var c = this._tmpColor || (this._tmpColor = new T3.THREE.Color());
    c.setHex(hex);
    if (i != null && i >= 0) {
      var h = SK.hashSeed(i * 1.37 + 3.1) - 0.5;
      var s = SK.hashSeed(i * 2.71 + 8.3) - 0.5;
      var l = SK.hashSeed(i * 4.19 + 5.9) - 0.5;
      c.offsetHSL(h * 0.07, s * 0.20, l * 0.20);
    }
    return c;
  };

  Renderer.prototype._shellRadius = function (clusterId) {
    if (this.cfg && this.cfg.shellRadius) return this.cfg.shellRadius(clusterId);
    /* increasing radius with a shrinking gap, so every cluster gets its own
       shell instead of piling onto a cap at shell 15 */
    return 12 + 4.2 * Math.sqrt(Math.max(0, clusterId));
  };

  Renderer.prototype._selectionKey = function () {
    var hidden = "";
    if (this.hiddenKinds) {
      var names = Object.keys(this.hiddenKinds).sort();
      for (var h = 0; h < names.length; h++) {
        if (this.hiddenKinds[names[h]]) hidden += names[h] + ",";
      }
    }
    return (this.selection ? this.selection.join(",") : "") + "|" + hidden;
  };

  Renderer.prototype.sync = function (live) {
    this.live = live;
    if (this._dragRun !== live.runId) {
      this._dragRun = live.runId;
      this._dragStore = {};
      this._dragOff = this._dragStore[this.name] = {};
      this._drag = null;
    }
    this._ensure();
    this._syncVisibility();
    if (this.lodReady && this.lodVersion !== live.version) this._clearEdgeIndex();
    var selKey = this._selectionKey();
    var rebuild = this.colorDirty || live.version !== this.lastVersion ||
                  selKey !== this.lastSelectionKey;
    if (rebuild) {
      this.lastVersion = live.version;
      this.lastSelectionKey = selKey;
      this.colorDirty = false;
      this.lastDimVersion = -1;
      var gmax = 1, rmax = 0, wmax = 1, i, e;
      for (i = 0; i < live.n; i++) {
        if (live.degree[i] > gmax) gmax = live.degree[i];
        if (live.rank[i] > rmax) rmax = live.rank[i];
        if (live.words[i] > wmax) wmax = live.words[i];
      }
      var shells = !!(this.cfg && this.cfg.shells);
      var neural = this.name === "neural";
      /* The neural scene is a brain, not a blob: the cells are neurons on a
         cortex, and the tissue around them carries the silhouette. */
      var brain = !!(this.cfg && this.cfg.brain);
      /* The city is a lattice, not a layout: every node gets a plot, the plot
         decides the coordinates, and the graph's numbers decide the tower.
         Children are the links the page emits -- the windows they light. */
      var city = !!(this.cfg && this.cfg.city);
      var pitch = city ? (this.cfg.cityPitch || 3) : 0;
      var cells = city ? SK.cityCells(live.n) : null;
      var kids = null, kidsMax = 1;
      if (city) {
        if (!this._cityKids || this._cityKids.length < live.n) {
          this._cityKids = new Uint16Array(live.n);
        } else {
          this._cityKids.fill(0, 0, live.n);
        }
        kids = this._cityKids;
        for (e = 0; e < live.m; e++) {
          var src = live.eSrc[e];
          if (src < live.n && kids[src] < 65535) kids[src]++;
        }
        for (i = 0; i < live.n; i++) if (kids[i] > kidsMax) kidsMax = kids[i];
        this._ensureCity(live);
      }
      var maxR = 1;
      for (i = 0; i < live.n; i++) {
        var x = live.x[i] * this.posScale, y = live.y[i] * this.posScale,
            z = live.z[i] * this.posScale;
        if (shells) {
          var cid = live._nodeCluster ? live._nodeCluster[i] : 0;
          var shellR = this._shellRadius(cid);
          var rr = Math.sqrt(x * x + y * y + z * z);
          if (rr > 1e-4) {
            x *= shellR / rr;
            y *= shellR / rr;
            z *= shellR / rr;
          } else {
            var theta = i * 2.399963229728653;
            var u = ((i * 0.618033988749895) % 1) * 2 - 1;
            var ring = Math.sqrt(Math.max(0, 1 - u * u));
            x = Math.cos(theta) * ring * shellR;
            y = u * shellR;
            z = Math.sin(theta) * ring * shellR;
          }
        } else if (brain) {
          /* Every cell is a neuron on the cortex: its place is fixed by the
             R2 sequence, so the head keeps the same shape while the build
             fills it, and no cell sits in the middle of the skull. */
          var place = this.ensureBrain(live);
          if (place) {
            x = place.point[i * 3] * this._brainScale;
            y = place.point[i * 3 + 1] * this._brainScale;
            z = place.point[i * 3 + 2] * this._brainScale;
          }
        } else if (city) {
          var spec = SK.citySpec({rank: live.rank[i], rankMax: rmax,
                                  degree: live.degree[i], degreeMax: gmax,
                                  kids: kids[i], kidsMax: kidsMax, seed: i,
                                  words: live.words[i], wordsMax: wmax,
                                  floor: this.cfg.cityFloor});
          var cell = cells[i] || CELL_ZERO;
          x = (cell[0] + 0.5) * pitch;
          z = (cell[1] + 0.5) * pitch;
          /* The node sits at the middle of its tower, not at street level:
             labels, picking, the camera fit and the links all use one point,
             and that point has to be the building. */
          y = spec.h * 0.5;
          this._cityH[i] = spec.h;
          this._cityW[i] = spec.w;
          this._cityD[i] = spec.d;
          this._cityFloors[i] = spec.floors;
          this._cityLit[i] = spec.lit;
          this._cityCols[i] = spec.cols;
          /* Tier bases, street to crown. The spec hands back the three cut
             heights; the first base is the ground itself, and the crown's
             top is the roof line. */
          this._cityTiers[i * 4] = 0;
          this._cityTiers[i * 4 + 1] = spec.tiers[0];
          this._cityTiers[i * 4 + 2] = spec.tiers[1];
          this._cityTiers[i * 4 + 3] = spec.tiers[2];
          this._cityTierW[i * 4] = spec.tierw[0];
          this._cityTierW[i * 4 + 1] = spec.tierw[1];
          this._cityTierW[i * 4 + 2] = spec.tierw[2];
          this._cityTierW[i * 4 + 3] = spec.tierw[3];
        }
        var cluster = live._nodeCluster ? live._nodeCluster[i] : 0;
        var dragKey = live.ids && live.ids[i] ? live.ids[i] : i;
        var off = this._dragOff && this._dragOff[dragKey];
        if (off) {
          x += off.x; y += off.y; z += off.z;
        }
        var c = this._color(cluster, i);
        this.nPos[i * 3] = x;
        this.nPos[i * 3 + 1] = y;
        this.nPos[i * 3 + 2] = z;
        this.nCol[i * 3] = c.r;
        this.nCol[i * 3 + 1] = c.g;
        this.nCol[i * 3 + 2] = c.b;
        var degPart = Math.sqrt((live.degree[i] || 0) / gmax);
        /* Rank only arrives with the layout patch, which lands after the first
           edges. Until it does every node would be the minimum size and the
           growth would look like dust condensing instead of worlds being born;
           degree is already known from the edges that just arrived, so it
           stands in until rank data exists. */
        var rankPart = rmax > 1e-6 ? Math.sqrt((live.rank[i] || 0) / rmax) : degPart;
        /* Volume is the third axis: a page with ten thousand words is a
           bigger body than a stub with the same rank. Worlds read it as
           radius, neurons as soma and plexus, towers as lit glass. */
        var volPart = Math.sqrt((live.words[i] || 0) / wmax);
        this.nAct[i] = volPart;
        if (shells) this.nSize[i] = 2.0 + 3.0 * rankPart + 5.0 * volPart + 1.4 * degPart;
        /* A neuron sprite is a soma plus dendrites: the soma is only 30% of the
           quad, so the point must be bigger than a planet to read as a cell.
           On the cortex the cells are packed shoulder to shoulder, so they
           have to be small enough to leave the tissue visible between them. */
        else if (neural) this.nSize[i] = 1.7 + 1.4 * degPart + 2.1 * volPart;
        /* Nothing is drawn for a tower, but the hit box and the label anchor
           still read the size: a tall building gets a bigger target. */
        else if (city) this.nSize[i] = 2.6 + 4.4 * rankPart;
        else this.nSize[i] = 2.2 + 3.4 * degPart;
        /* giant worlds carry a visible band; leaves stay smooth spheres */
        this.nRing[i] = shells && (0.7 * rankPart + 0.3 * degPart) >=
          this._ringThreshold ? 1 : 0;
        var wr = Math.hypot(x, y, z);
        if (wr > maxR) maxR = wr;
      }
      this.sceneRadius = maxR;
      this.sceneN = live.n;
      if (city) this._writeCity(live);
      if (this.cfg && this.cfg.ego) {
        this._packEgo(live, rmax);
      } else {
        this._edgeSlots = null;
        this.selectionSet = null;
        this._drawEdges = live.m;
        this._egoItems = null;
        this._egoScale = null;
        for (e = 0; e < live.m; e++) this._packLink(e, e);
      }
      this._mark("position", this.nPos, this.pgeo);
      this._mark("color", this.nCol, this.pgeo);
      this._mark("a_size", this.nSize, this.pgeo);
      this._mark("a_act", this.nAct, this.pgeo);
      this._mark("a_ring", this.nRing, this.pgeo);
      this._mark("position", this.ePos, this.lgeo);
      this._mark("color", this.eCol, this.lgeo);
      this._mark("a_base", this.eBase, this.lgeo);
      this._mark("a_seed", this.eSeed, this.lgeo);
      this.pgeo.setDrawRange(0, live.n);
      this.lgeo.setDrawRange(0, this._drawEdges * this.segK * 2);
      this._ensureRings(live);
      this.colorDirty = false;
    }
    this.updateDims(live);
    return this;
  };

  /* One link, resampled into ``segK`` pieces. Every link owns segK*2 vertices
     in one slot, so the LOD index, the drag rewrite and both backends address
     a link by slot without caring about its shape.

     Straight (segK 1) is the old two-vertex line. Above that the link is a
     quadratic through SK.arcControl: the sagitta scales with the link's own
     length, so a short synapse stays almost straight and a long projection
     sweeps. ``slot`` is the edge's own index unless ego mode packed a subset;
     ``posOnly`` skips the colour/base/seed writes when only the ends moved. */
  Renderer.prototype._packLink = function (e, slot, posOnly) {
    var live = this.live;
    if (!live || e < 0 || e >= live.m || slot == null || slot < 0) return;
    var a = live.eSrc[e], b = live.eDst[e];
    var ax = this.nPos[a * 3], ay = this.nPos[a * 3 + 1], az = this.nPos[a * 3 + 2];
    var bx = this.nPos[b * 3], by = this.nPos[b * 3 + 1], bz = this.nPos[b * 3 + 2];
    var K = this.segK, pos = this.ePos;
    var o = slot * K * 2 * 3;
    if (K === 1) {
      pos[o] = ax; pos[o + 1] = ay; pos[o + 2] = az;
      pos[o + 3] = bx; pos[o + 4] = by; pos[o + 5] = bz;
    } else {
      var c = SK.arcControl(ax, ay, az, bx, by, bz, e,
                            this.cfg ? this.cfg.edgeBend : 0.12);
      var cx = c[0], cy = c[1], cz = c[2];
      for (var j = 0; j < K; j++) {
        var t0 = j / K, t1 = (j + 1) / K;
        var s0 = 1 - t0, s1 = 1 - t1;
        var v0 = (slot * K + j) * 2 * 3;
        pos[v0] = s0 * s0 * ax + 2 * s0 * t0 * cx + t0 * t0 * bx;
        pos[v0 + 1] = s0 * s0 * ay + 2 * s0 * t0 * cy + t0 * t0 * by;
        pos[v0 + 2] = s0 * s0 * az + 2 * s0 * t0 * cz + t0 * t0 * bz;
        pos[v0 + 3] = s1 * s1 * ax + 2 * s1 * t1 * cx + t1 * t1 * bx;
        pos[v0 + 4] = s1 * s1 * ay + 2 * s1 * t1 * cy + t1 * t1 * by;
        pos[v0 + 5] = s1 * s1 * az + 2 * s1 * t1 * cz + t1 * t1 * bz;
      }
    }
    if (posOnly) return;
    var ca = this._color(live._nodeCluster ? live._nodeCluster[a] : 0, a);
    var cr = ca.r, cg = ca.g, cb = ca.b;
    var db = this._color(live._nodeCluster ? live._nodeCluster[b] : 0, b);
    var dr = db.r, dg = db.g, dbl = db.b;
    var base = structuralBase(live.kinds[live.eKind[e]]);
    for (var v = 0; v < K * 2; v++) {
      var at = (slot * K * 2 + v);
      var f = v / (K * 2 - 1);
      this.eCol[at * 3] = cr + (dr - cr) * f;
      this.eCol[at * 3 + 1] = cg + (dg - cg) * f;
      this.eCol[at * 3 + 2] = cb + (dbl - cb) * f;
      this.eBase[at] = base;
      this.eSeed[at] = v % 2 === 0 ? this.nSeed[a] : this.nSeed[b];
    }
  };

  Renderer.prototype._packEgo = function (live, rmax) {
    var sel = this.selection || [];
    var set = new Set(sel);
    this.selectionSet = set;
    if (!this._edgeSlots || this._edgeSlots.length < this.capE) {
      this._edgeSlots = new Int32Array(this.capE);
    }
    this._edgeSlots.fill(-1);
    var hidden = this.hiddenKinds || {};
    var k = 0, e;
    for (e = 0; e < live.m; e++) {
      var a = live.eSrc[e], b = live.eDst[e];
      if (!set.has(a) || !set.has(b)) continue;
      if (hidden[live.kinds[live.eKind[e]]]) continue;
      this._packLink(e, k);
      this._edgeSlots[e] = k;
      k++;
    }
    this._drawEdges = k;
    var mesh = this.egoMesh;
    var count = Math.min(sel.length, 120);
    mesh.count = count;
    var items = new Array(count);
    var scales = new Float32Array(count);
    for (var s = 0; s < count; s++) {
      var idx = sel[s];
      var scale = 0.8 + 2.8 * (rmax > 0 ? Math.sqrt((live.rank[idx] || 0) / rmax) : 0);
      items[s] = idx;
      scales[s] = scale;
      this._dummy.position.set(this.nPos[idx * 3], this.nPos[idx * 3 + 1],
                               this.nPos[idx * 3 + 2]);
      this._dummy.scale.setScalar(scale);
      this._dummy.updateMatrix();
      mesh.setMatrixAt(s, this._dummy.matrix);
      mesh.setColorAt(s, this._color(live._nodeCluster[idx], idx));
    }
    this._egoItems = items;
    this._egoScale = scales;
    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
  };

  Renderer.prototype._ensureRings = function (live) {
    var planet = !!(this.cfg && this.cfg.shells) && live.n > 0;
    var count = planet ? Math.min(Math.max(live.clusters.length, 1), 16) : 0;
    var key = String(count);
    if (key !== this.ringKey) {
      this._disposeRings();
      this.ringKey = key;
      if (count) {
        var THREE = T3.THREE;
        var palette = (this.cfg && this.cfg.palette) || SK.PALETTE;
        var segs = 128;
        var verts = count * (segs * 2 + 2);
        var pts = new Float32Array(verts * 3);
        var col = new Float32Array(verts * 3);
        var at = 0, c = new THREE.Color();
        for (var s = 0; s < count; s++) {
          var radius = this._shellRadius(s);
          var phase = s * 0.37;
          var cosP = Math.cos(phase), sinP = Math.sin(phase);
          /* each orbit sits on its own plane: inclination grows with the shell,
             roll keeps neighbouring rings from reading as one flat target */
          var inc = 0.18 + s * 0.115;
          var roll = 0.35 + s * 0.53;
          var cosI = Math.cos(inc), sinI = Math.sin(inc);
          var cosR = Math.cos(roll), sinR = Math.sin(roll);
          c.setHex(palette[s % palette.length]);
          /* Each orbit gets its own faint tint as well, so two rings that
             share a palette entry do not read as the same line drawn twice. */
          c.offsetHSL((SK.hashSeed(s * 3.7 + 1.9) - 0.5) * 0.024,
                      (SK.hashSeed(s * 5.1 + 4.7) - 0.5) * 0.08,
                      (SK.hashSeed(s * 7.3 + 2.3) - 0.5) * 0.08);
          for (var q = 0; q < segs; q++) {
            var a0 = q / segs * Math.PI * 2;
            var a1 = (q + 1) / segs * Math.PI * 2;
            var x0 = Math.cos(a0) * radius, z0 = Math.sin(a0) * radius;
            var x1 = Math.cos(a1) * radius, z1 = Math.sin(a1) * radius;
            var y0 = -z0 * sinI, zz0 = z0 * cosI;
            var y1 = -z1 * sinI, zz1 = z1 * cosI;
            pts[at * 3] = x0 * cosR - y0 * sinR;
            pts[at * 3 + 1] = x0 * sinR + y0 * cosR;
            pts[at * 3 + 2] = zz0;
            col[at * 3] = c.r; col[at * 3 + 1] = c.g; col[at * 3 + 2] = c.b;
            at++;
            pts[at * 3] = x1 * cosR - y1 * sinR;
            pts[at * 3 + 1] = x1 * sinR + y1 * cosR;
            pts[at * 3 + 2] = zz1;
            col[at * 3] = c.r; col[at * 3 + 1] = c.g; col[at * 3 + 2] = c.b;
            at++;
          }
          var tick = radius + Math.max(1.2, radius * 0.05);
          var tx = radius * cosP, tz = radius * sinP;
          var ty = -tz * sinI, tzz = tz * cosI;
          pts[at * 3] = tx * cosR - ty * sinR;
          pts[at * 3 + 1] = tx * sinR + ty * cosR;
          pts[at * 3 + 2] = tzz;
          col[at * 3] = c.r; col[at * 3 + 1] = c.g; col[at * 3 + 2] = c.b;
          at++;
          var ux = tick * cosP, uz = tick * sinP;
          var uy = -uz * sinI, uzz = uz * cosI;
          pts[at * 3] = ux * cosR - uy * sinR;
          pts[at * 3 + 1] = ux * sinR + uy * cosR;
          pts[at * 3 + 2] = uzz;
          col[at * 3] = c.r; col[at * 3 + 1] = c.g; col[at * 3 + 2] = c.b;
          at++;
        }
        var geo = new THREE.BufferGeometry();
        geo.setAttribute("position", new THREE.BufferAttribute(pts, 3));
        geo.setAttribute("color", new THREE.BufferAttribute(col, 3));
        var mat = new THREE.LineBasicMaterial({vertexColors: true, transparent: true,
                                                opacity: (this.cfg && this.cfg.ringOpacity) || 0.18,
                                                depthWrite: false});
        this.ringMesh = new THREE.LineSegments(geo, mat);
        this.ringMesh.frustumCulled = false;
        this.scene.add(this.ringMesh);
      }
    }
    if (this.ringMesh) this.ringMesh.visible = planet;
  };

  Renderer.prototype._disposeRings = function () {
    if (!this.ringMesh) return;
    if (this.scene) this.scene.remove(this.ringMesh);
    this.ringMesh.geometry.dispose();
    this.ringMesh.material.dispose();
    this.ringMesh = null;
  };

  Renderer.prototype.nodeWorld = function (i, out) {
    if (this.nPos && i >= 0 && i < this.sceneN) {
      out.set(this.nPos[i * 3], this.nPos[i * 3 + 1], this.nPos[i * 3 + 2]);
    } else if (this.live && i >= 0 && i < this.live.n) {
      out.set(this.live.x[i] * this.posScale, this.live.y[i] * this.posScale,
              this.live.z[i] * this.posScale);
    } else {
      out.set(0, 0, 0);
    }
    return out;
  };

  /* ---- per-node drag -------------------------------------------------------
     Placement belongs to the scene (the planet scene projects nodes onto orbit
     shells), so a drag is stored as a world-space offset keyed by page id and
     re-applied after every rebuild. It therefore survives streaming updates and
     layout patches, and a new crawl starts from a clean map. */
  Renderer.prototype._pointerRay = function (x, y) {
    var THREE = T3.THREE;
    var ndc = new THREE.Vector2((x / Math.max(this.width || 1, 1)) * 2 - 1,
                                -(y / Math.max(this.height || 1, 1)) * 2 + 1);
    var ray = new THREE.Raycaster();
    ray.setFromCamera(ndc, this.camera);
    return ray;
  };

  Renderer.prototype._dragEdges = function (i) {
    var live = this.live, out = [];
    if (!live) return out;
    for (var e = 0; e < live.m; e++) {
      var slot = this._edgeSlots ? this._edgeSlots[e] : e;
      if (slot == null || slot < 0 || slot >= this._drawEdges) continue;
      if (live.eSrc[e] === i || live.eDst[e] === i) out.push([e, slot]);
    }
    return out;
  };

  /* Neighbours that trail the dragged cell: one and two hops out, each ring
     pulled along at a fraction of the pointer's delta. The cluster flexes with
     the drag instead of the whole graph translating as one block. */
  /* Whether this scene lets a body be picked up. A city tower stands on its
     plot; a neuron and a world are free bodies. */
  Renderer.prototype.canDrag = function () {
    return !(this.cfg && this.cfg.draggable === false);
  };

  /* Answer a press that cannot drag: flash the bodies this one is wired to.
     The city lights windows, the head sends a current down its axons. */
  Renderer.prototype.pulse = function (i) {
    var live = this.live;
    if (!live || i < 0 || i >= live.n) return;
    /* A held drag re-fires the pulse; settle the previous wave first or the
       saved baselines would ratchet up and never come back down. */
    if (this._pulse && this._pulse.fire && this.nAct) {
      for (var r = 0; r < this._pulse.fire.ids.length; r++) {
        this.nAct[this._pulse.fire.ids[r]] = this._pulse.fire.base[r];
      }
    }
    this._pulse = {edges: this._dragEdges(i), start: this.time || 0,
                   dur: this.cfg && this.cfg.neuralNodes ? 1.7 : 1.1,
                   fire: null};
    /* The current does not stop at the synapse: the cells at the far end fire
       too. Their baseline activity is saved so the flash can be removed
       without leaving a neuron permanently lit. */
    if (this.cfg && this.cfg.neuralNodes && this.nAct) {
      var adj = live.adjacency(), list = adj[i] || [];
      /* The grabbed cell is part of the current: its soma lights first, the
         far ends answer a beat later in _tickPulse. */
      var ids = [i], base = [this.nAct[i]];
      for (var k = 0; k < list.length && ids.length < 40; k++) {
        var j = list[k][0];
        if (live.dim[j] < 0.15) continue;
        ids.push(j);
        base.push(this.nAct[j]);
      }
      if (ids.length) this._pulse.fire = {ids: ids, base: base};
    }
    if (this.cfg && this.cfg.city) this._pokeCity(i);
  };

  /* A scene whose bodies are fixed still answers a press: the city flashes the
     towers this one feeds, the rest do nothing. Kept separate from beginDrag
     so the click-to-open path in ui.js is untouched. */
  Renderer.prototype.poke = function (i) {
    if (!this.cfg || !this.cfg.poke) return false;
    this.pulse(i);
    return true;
  };

  /* Window light is the city's only language: a poked tower and everything it
     links to brighten, then fall back to the fan-out they had. */
  Renderer.prototype._pokeCity = function (i) {
    var live = this.live, adj = live.adjacency();
    var nodes = {};
    nodes[i] = 0.45;
    var list = adj[i] || [];
    for (var k = 0; k < list.length && k < 40; k++) nodes[list[k][0]] = 1;
    this._cityFlash = {nodes: nodes, start: this.time || 0, dur: 1.5};
  };

  Renderer.prototype._tickCityFlash = function () {
    var f = this._cityFlash;
    if (!f || !this._citySpec || !this._cityLit) return;
    var t = (this.time || 0) - f.start;
    var done = t > f.dur;
    var fade = done ? 0 : Math.pow(1 - t / f.dur, 1.4);
    var spec = this._citySpec, lit = this._cityLit;
    for (var key in f.nodes) {
      if (!Object.prototype.hasOwnProperty.call(f.nodes, key)) continue;
      var i = +key, base = i * CITY_VERTS;
      if (i >= this._cityN) continue;
      var peak = f.nodes[key] * fade;
      for (var k = 0; k < CITY_VERTS; k++) {
        var v = base + k;
        spec[v * 4 + 1] = Math.min(1, lit[i] + (1 - lit[i]) * peak * 0.95);
      }
    }
    var attr = this.cityGeo.getAttribute("a_spec");
    if (attr) attr.needsUpdate = true;
    if (done) this._cityFlash = null;
  };

  /* The axon current. Edge dim is per vertex, so a band whose phase runs with
     a_t travels from soma to soma instead of blinking the whole link. */
  Renderer.prototype._tickPulse = function () {
    var p = this._pulse;
    if (!p) return;
    var t = (this.time || 0) - p.start;
    if (t > p.dur) {
      /* Put the cells back the way they were before the current arrived. */
      if (p.fire && this.nAct) {
        for (var r = 0; r < p.fire.ids.length; r++) {
          this.nAct[p.fire.ids[r]] = p.fire.base[r];
        }
        var back = this.pgeo.getAttribute("a_act");
        if (back) back.needsUpdate = true;
      }
      this._pulse = null;
      this.lastDimVersion = -1;
      if (this.live) this.updateDims(this.live);
      return;
    }
    var k2 = this.segK * 2, dim = this.eDim, fade = 1 - t / p.dur;
    /* A stretched membrane carries a hotter current: the further the cell is
       pulled from home, the brighter the axons run. */
    var load = 1 + 0.9 * (this._stretch || 0);
    var eDim = this.live && this.live.eDim;
    for (var q = 0; q < p.edges.length; q++) {
      var e = p.edges[q][0], base = p.edges[q][1] * k2;
      /* A pulse must not resurrect a kind the user disabled: the flash is
         scaled by the link's own visibility. */
      var vis = eDim ? eDim[e] : 1;
      for (var v = 0; v < k2; v++) {
        var at = base + v;
        var phase = this.eT ? this.eT[at] : 0;
        var amp = 0.5 + 0.5 * Math.sin(t * 9.0 - phase * 7.0);
        dim[at] = Math.min(1, (0.35 + 1.1 * amp) * fade * 1.6 * load) * vis;
      }
    }
    var attr = this.lgeo.getAttribute("a_dim");
    if (attr) attr.needsUpdate = true;
    /* The far end of the axon depolarises a beat after the near end. */
    if (p.fire && this.nAct) {
      var wave = Math.pow(fade, 0.7);
      for (var f = 0; f < p.fire.ids.length; f++) {
        var j = p.fire.ids[f];
        this.nAct[j] = Math.min(1, p.fire.base[f] + 0.85 * wave);
      }
      var aact = this.pgeo.getAttribute("a_act");
      if (aact) aact.needsUpdate = true;
    }
  };

  Renderer.prototype._dragFollow = function (i) {
    var live = this.live, out = [];
    if (!live || !live.adjacency) return out;
    var adj = live.adjacency(), self = this;
    var seen = {};
    seen[i] = 1;
    (function collect(src, depth, w) {
      if (depth > 2 || out.length >= 40) return;
      var list = adj[src] || [];
      for (var k = 0; k < list.length; k++) {
        var j = list[k][0];
        if (seen[j]) continue;
        seen[j] = 1;
        if (live.dim[j] >= 0.15) {
          out.push({i: j, key: live.ids[j] || String(j), w: w,
                    edges: self._dragEdges(j)});
        }
        if (out.length >= 40) return;
        collect(j, depth + 1, w * 0.42);
      }
    })(i, 1, 0.45);
    return out;
  };

  Renderer.prototype.beginDrag = function (i, x, y) {
    var THREE = T3.THREE, live = this.live;
    if (!this.canDrag()) return false;
    if (!live || i < 0 || i >= live.n || live.dim[i] < 0.15) return false;
    /* Settle any previous gesture first: its spring offsets are still in the
       buffers, and the grab point has to be the cell's real home. */
    this._finishDrag();
    var p = new THREE.Vector3();
    this.nodeWorld(i, p);
    var dir = new THREE.Vector3();
    this.camera.getWorldDirection(dir);
    /* Drag on the plane that faces the camera and passes through the node, so
       the cell tracks the pointer one-to-one instead of sliding in depth. */
    var plane = new THREE.Plane().setFromNormalAndCoplanarPoint(dir, p);
    var hit = new THREE.Vector3();
    if (!this._pointerRay(x, y).ray.intersectPlane(plane, hit)) return false;
    this._dragOff = this._dragOff || {};
    var key = live.ids[i] || String(i);
    /* An anchored scene has no drop history: the cell starts from the layout
       home it will spring back to, never from where a previous drag ended. */
    var anchored = !!(this.cfg && this.cfg.recoil);
    var prev = anchored ? {x: 0, y: 0, z: 0}
                        : (this._dragOff[key] || {x: 0, y: 0, z: 0});
    var follow = this._dragFollow(i);
    /* Each body carries its own spring state. ``base`` is where the layout put
       it, ``cur``/``vel`` are the live offset from that base, and ``target``
       is where the pointer wants it. Nothing is written straight to the
       buffer: advanceDrag integrates every frame, so the node trails the
       pointer and settles with weight instead of teleporting. */
    this._drag = {
      i: i, key: key, plane: plane, grab: hit.clone(),
      main: {base: {x: p.x - prev.x, y: p.y - prev.y, z: p.z - prev.z},
             cur: {x: prev.x, y: prev.y, z: prev.z},
             target: {x: prev.x, y: prev.y, z: prev.z},
             vel: {x: 0, y: 0, z: 0}},
      edges: this._dragEdges(i),
      follow: follow.map(function (nb) {
        var np = new THREE.Vector3();
        this.nodeWorld(nb.i, np);
        var off = anchored ? {x: 0, y: 0, z: 0}
                           : (this._dragOff[nb.key] || {x: 0, y: 0, z: 0});
        nb.base = {x: np.x - off.x, y: np.y - off.y, z: np.z - off.z};
        /* Start each follower where it already sits, so grabbing a cell does
           not yank neighbours back to the layout position they were dragged
           away from in an earlier gesture. */
        nb.cur = {x: off.x, y: off.y, z: off.z};
        nb.target = {x: off.x, y: off.y, z: off.z};
        nb.vel = {x: 0, y: 0, z: 0};
        return nb;
      }, this),
      released: false
    };
    /* A dragged cell fires: its synapses carry a pulse for as long as the
       hand holds it, refreshed in advanceDrag. */
    if (this.cfg && this.cfg.neuralNodes) this.pulse(i);
    return true;
  };

  /* Keeps an anchored pull inside the membrane's reach. Pure geometry, so the
     limit can be unit tested without a renderer. */
  Renderer.prototype._clampTarget = function (t) {
    var lim = this.cfg && this.cfg.recoilMax ? this.cfg.recoilMax : 2.2;
    var r = Math.sqrt(t.x * t.x + t.y * t.y + t.z * t.z);
    if (r > lim) {
      var s = lim / r;
      t.x *= s;
      t.y *= s;
      t.z *= s;
    }
    return t;
  };

  Renderer.prototype.moveDrag = function (x, y) {
    var d = this._drag;
    if (!d || !this.live) return;
    var hit = new T3.THREE.Vector3();
    if (!this._pointerRay(x, y).ray.intersectPlane(d.plane, hit)) return;
    var dx = hit.x - d.grab.x, dy = hit.y - d.grab.y, dz = hit.z - d.grab.z;
    d.main.target.x += dx;
    d.main.target.y += dy;
    d.main.target.z += dz;
    /* An anchored cell stretches only so far: past the limit the pointer
       stops pulling and the membrane tension takes the rest. */
    if (this.cfg && this.cfg.recoil) this._clampTarget(d.main.target);
    d.grab.copy(hit);
    /* Followers aim at a fraction of the main target; their springs are
       softer, so the cluster ripples out behind the grabbed cell. */
    for (var f = 0; f < d.follow.length; f++) {
      var nb = d.follow[f];
      nb.target.x = d.main.target.x * nb.w;
      nb.target.y = d.main.target.y * nb.w;
      nb.target.z = d.main.target.z * nb.w;
    }
  };

  /* Bodies cannot occupy the same point. Only the grabbed body and the links
     that trail it are in the list, so an unrelated node is never nudged --
     the shove is part of the same "only related nodes move" rule. Three
     passes settle a short chain without letting the pile oscillate. */
  Renderer.prototype._separateBodies = function (d) {
    var live = this.live, pad = this.cfg ? this.cfg.collidePad : 0;
    if (!live || !pad) return;
    var bodies = [{i: d.i, b: d.main}];
    for (var f = 0; f < d.follow.length; f++) {
      bodies.push({i: d.follow[f].i, b: d.follow[f]});
    }
    for (var iter = 0; iter < 3; iter++) {
      for (var a = 0; a < bodies.length; a++) {
        for (var b = a + 1; b < bodies.length; b++) {
          var A = bodies[a], B = bodies[b];
          var dx = (A.b.base.x + A.b.cur.x) - (B.b.base.x + B.b.cur.x);
          var dy = (A.b.base.y + A.b.cur.y) - (B.b.base.y + B.b.cur.y);
          var dz = (A.b.base.z + A.b.cur.z) - (B.b.base.z + B.b.cur.z);
          var min = (this.nSize[A.i] + this.nSize[B.i]) * pad;
          var push = SK.pushApart(dx, dy, dz, min, A.i * 7.13 + B.i * 3.71);
          if (!push[0] && !push[1] && !push[2]) continue;
          A.b.cur.x += push[0]; A.b.cur.y += push[1]; A.b.cur.z += push[2];
          B.b.cur.x -= push[0]; B.b.cur.y -= push[1]; B.b.cur.z -= push[2];
        }
      }
    }
  };

  /* Writes the current spring offsets into the point/edge buffers. */
  Renderer.prototype._dragPlace = function (d) {
    this._separateBodies(d);
    var n = this.nPos, m = d.main;
    var px = m.base.x + m.cur.x, py = m.base.y + m.cur.y,
        pz = m.base.z + m.cur.z;
    n[d.i * 3] = px; n[d.i * 3 + 1] = py; n[d.i * 3 + 2] = pz;
    /* Only free bodies remember a drop point. An anchored cell writes its
       live stretch to the buffers and nothing to the store, so the next sync
       finds it at home. */
    if (!(this.cfg && this.cfg.recoil)) {
      var off = this._dragOff[d.key];
      if (!off) off = this._dragOff[d.key] = {x: 0, y: 0, z: 0};
      off.x = m.cur.x; off.y = m.cur.y; off.z = m.cur.z;
    }
    if (this.ePos) {
      for (var k = 0; k < d.edges.length; k++) {
        this._packLink(d.edges[k][0], d.edges[k][1], true);
      }
    }
    for (var f = 0; f < d.follow.length; f++) {
      var nb = d.follow[f];
      var fx = nb.base.x + nb.cur.x, fy = nb.base.y + nb.cur.y,
          fz = nb.base.z + nb.cur.z;
      n[nb.i * 3] = fx; n[nb.i * 3 + 1] = fy; n[nb.i * 3 + 2] = fz;
      if (!(this.cfg && this.cfg.recoil)) {
        var noff = this._dragOff[nb.key];
        if (!noff) noff = this._dragOff[nb.key] = {x: 0, y: 0, z: 0};
        noff.x = nb.cur.x; noff.y = nb.cur.y; noff.z = nb.cur.z;
      }
      if (this.ePos) {
        for (var q = 0; q < nb.edges.length; q++) {
          this._packLink(nb.edges[q][0], nb.edges[q][1], true);
        }
      }
    }
    this._mark("position", this.nPos, this.pgeo);
    if (this.ePos) this._mark("position", this.ePos, this.lgeo);
    /* The towers follow their nodes: a dragged building has to move with the
       hit point that grabbed it. */
    if (this.cfg && this.cfg.city) {
      this._cityMove(d.i);
      for (var g = 0; g < d.follow.length; g++) this._cityMove(d.follow[g].i);
    }
  };

  /* Integrates the drag springs. Runs every frame while a cell is held or
     still settling, so the motion is frame-rate independent. */
  Renderer.prototype.advanceDrag = function (dt) {
    var d = this._drag;
    if (!d || !this.live) return;
    var anchored = !!(this.cfg && this.cfg.recoil);
    /* Keep the current alive while the held cell keeps moving: an axon that
       went quiet under the hand would read as a dropped connection. An
       anchored cell is being stretched, so it fires in shorter bursts. */
    if (this.cfg && this.cfg.neuralNodes &&
        (!this._pulse ||
         (this.time || 0) - this._pulse.start > (anchored ? 0.45 : 0.75))) {
      this.pulse(d.i);
    }
    var step = Math.min(0.05, Math.max(0.001, dt || 0.016));
    /* Tuned in SK.DRAG_SPRING so the feel is unit-tested without WebGL: the
       cell trails the pointer, then overshoots its drop point and rings out.
       An anchored cell uses the livelier recoil tuning -- it has to bounce. */
    var spring = SK.DRAG_SPRING || {};
    var tune = (anchored && spring.recoil) ? spring.recoil
             : (spring.stiff ? spring
                : {stiff: 190, damping: 0.62,
                   followStiff: 120, followDamping: 0.54});
    var stiff = tune.stiff, damp = 2 * Math.sqrt(stiff) * tune.damping;
    var fStiff = tune.followStiff;
    var fDamp = 2 * Math.sqrt(fStiff) * tune.followDamping;

    var m = d.main;
    var ax = SK.springStep(m.cur.x, m.vel.x, m.target.x, stiff, damp, step);
    var ay = SK.springStep(m.cur.y, m.vel.y, m.target.y, stiff, damp, step);
    var az = SK.springStep(m.cur.z, m.vel.z, m.target.z, stiff, damp, step);
    m.cur.x = ax[0]; m.vel.x = ax[1];
    m.cur.y = ay[0]; m.vel.y = ay[1];
    m.cur.z = az[0]; m.vel.z = az[1];

    /* How far the membrane is stretched, 0..1. The axon current brightens
       with it, so pulling harder visibly loads the synapses. */
    if (anchored) {
      var lim = this.cfg && this.cfg.recoilMax ? this.cfg.recoilMax : 2.2;
      this._stretch = Math.min(1, Math.sqrt(m.cur.x * m.cur.x +
                                            m.cur.y * m.cur.y +
                                            m.cur.z * m.cur.z) / lim);
    } else {
      this._stretch = 0;
    }

    var settled = Math.abs(m.target.x - m.cur.x) < 0.003
               && Math.abs(m.target.y - m.cur.y) < 0.003
               && Math.abs(m.target.z - m.cur.z) < 0.003
               && Math.abs(m.vel.x) + Math.abs(m.vel.y) + Math.abs(m.vel.z) < 0.01;
    for (var f = 0; f < d.follow.length; f++) {
      var nb = d.follow[f];
      var bx = SK.springStep(nb.cur.x, nb.vel.x, nb.target.x, fStiff, fDamp, step);
      var by = SK.springStep(nb.cur.y, nb.vel.y, nb.target.y, fStiff, fDamp, step);
      var bz = SK.springStep(nb.cur.z, nb.vel.z, nb.target.z, fStiff, fDamp, step);
      nb.cur.x = bx[0]; nb.vel.x = bx[1];
      nb.cur.y = by[0]; nb.vel.y = by[1];
      nb.cur.z = bz[0]; nb.vel.z = bz[1];
      if (Math.abs(nb.target.x - nb.cur.x) > 0.003
          || Math.abs(nb.target.y - nb.cur.y) > 0.003
          || Math.abs(nb.target.z - nb.cur.z) > 0.003) settled = false;
    }
    this._dragPlace(d);
    if (d.released && settled) this._finishDrag();
  };

  /* Snap the springs to their targets and stop integrating. */
  Renderer.prototype._finishDrag = function () {
    var d = this._drag;
    if (!d) return;
    var m = d.main;
    /* An anchored cell always ends at home, even if a scene switch or a
       reset interrupts the bounce half-way. */
    if (this.cfg && this.cfg.recoil) {
      m.target.x = m.target.y = m.target.z = 0;
      for (var rf = 0; rf < d.follow.length; rf++) {
        d.follow[rf].target.x = 0;
        d.follow[rf].target.y = 0;
        d.follow[rf].target.z = 0;
      }
    }
    m.cur.x = m.target.x; m.cur.y = m.target.y; m.cur.z = m.target.z;
    for (var f = 0; f < d.follow.length; f++) {
      var nb = d.follow[f];
      nb.cur.x = nb.target.x; nb.cur.y = nb.target.y; nb.cur.z = nb.target.z;
      nb.vel.x = nb.vel.y = nb.vel.z = 0;
    }
    this._dragPlace(d);
    this._drag = null;
    this._stretch = 0;
  };

  Renderer.prototype.endDrag = function () {
    var d = this._drag;
    if (!d || d.released) return;
    d.released = true;
    /* An anchored cell is never dropped: letting go retracts it to the home
       position it never really left, and the spring overshoots on the way. */
    if (this.cfg && this.cfg.recoil) {
      d.main.target.x = d.main.target.y = d.main.target.z = 0;
      for (var f = 0; f < d.follow.length; f++) {
        d.follow[f].target.x = 0;
        d.follow[f].target.y = 0;
        d.follow[f].target.z = 0;
      }
    }
  };

  Renderer.prototype.startFinale = function (reduced) {
    this.resetFinale();
    this.finaleMode = this.name;
    this.finaleReduced = !!reduced;
    this.finaleDuration = reduced ? 0.001 : 2.6;
    this.finaleClock = 0;
    this.finaleActive = !reduced;
    if (this.name === "neural") {
      this.edgeUni.u_sweepAmp.value = reduced ? 0 : 1;
      this.edgeUni.u_sweepX.value = -this.sceneRadius * 1.1;
    }
    if (this.name === "city") this.cityUni.u_finale.value = reduced ? 0 : 0.001;
  };

  Renderer.prototype.resetFinale = function () {
    this.finaleActive = false;
    this.finaleClock = 0;
    this.finaleMode = "";
    if (this.edgeUni) {
      this.edgeUni.u_sweepAmp.value = 0;
      this.edgeUni.u_sweepX.value = 0;
    }
    if (this.cityUni) this.cityUni.u_finale.value = 0;
    if (this.ringMesh) this.ringMesh.rotation.y = 0;
    if (this._egoItems) this._applyEgo(0);
  };

  Renderer.prototype._applyEgo = function (t) {
    if (!this.egoMesh || !this._egoItems) return;
    var items = this._egoItems, scales = this._egoScale, p = this.nPos;
    var dummy = this._dummy;
    for (var k = 0; k < items.length; k++) {
      var i = items[k];
      dummy.position.set(p[i * 3], p[i * 3 + 1], p[i * 3 + 2]);
      var s = scales[k] * (t ? 1 + 0.22 * Math.sin(t * 5 + k * 0.7) : 1);
      dummy.scale.setScalar(s);
      dummy.updateMatrix();
      this.egoMesh.setMatrixAt(k, dummy.matrix);
    }
    this.egoMesh.instanceMatrix.needsUpdate = true;
  };

  Renderer.prototype.advanceFinale = function (dt) {
    if (!this.finaleActive) return false;
    if (this.finaleReduced) {
      this.finaleActive = false;
      return true;
    }
    this.finaleClock += dt;
    var k = Math.min(1, this.finaleClock / Math.max(0.001, this.finaleDuration));
    var e = 1 - Math.pow(1 - k, 3);
    if (this.finaleMode === "planets") {
      if (this.ringMesh) this.ringMesh.rotation.y = -0.6 * e;
    } else if (this.finaleMode === "people") {
      this._applyEgo(this.finaleClock);
    } else if (this.finaleMode === "neural") {
      this.edgeUni.u_sweepX.value = -this.sceneRadius * 1.1
        + e * this.sceneRadius * 2.2;
    } else if (this.finaleMode === "city") {
      /* A ring of light crosses the city: each tower it reaches switches its
         windows on, then falls dark again as the wave moves on. */
      this.cityUni.u_finale.value = 0.001 + e * 1.0;
    }
    if (k >= 1) {
      this.finaleActive = false;
      if (this.finaleMode === "neural") this.edgeUni.u_sweepAmp.value = 0;
      if (this.finaleMode === "people") this._applyEgo(0);
    }
    return k >= 1;
  };

  Renderer.prototype._clearEdgeIndex = function () {
    if (this.lgeo) this.lgeo.setIndex(null);
    this.lodReady = false;
    this.lodVersion = -1;
  };

  Renderer.prototype.prepareEdgeLod = function () {
    var live = this.live;
    if (!live || !this.cfg || !this.cfg.edgeLod || !this.nPos ||
        this.sceneN !== live.n) {
      this._clearEdgeIndex();
      return;
    }
    var m = live.m;
    if (!m) {
      this._clearEdgeIndex();
      return;
    }
    var slots = this._edgeSlots;
    /* Ego mode packs only the selected neighbourhood, so an edge with slot
       -1 has no vertices of its own: including it would index another link's
       buffer. The LOD list is built over what is actually drawn. */
    var ids = [];
    for (var e0 = 0; e0 < live.m; e0++) {
      if (slots && slots[e0] < 0) continue;
      ids.push(e0);
    }
    var m = ids.length;
    if (!m) {
      this._clearEdgeIndex();
      return;
    }
    var lens = new Float32Array(m);
    var order = new Array(m);
    for (var q = 0; q < m; q++) {
      var e = ids[q];
      order[q] = q;
      var a = live.eSrc[e] * 3, b = live.eDst[e] * 3;
      var dx = this.nPos[a] - this.nPos[b];
      var dy = this.nPos[a + 1] - this.nPos[b + 1];
      var dz = this.nPos[a + 2] - this.nPos[b + 2];
      lens[q] = dx * dx + dy * dy + dz * dz;
    }
    order.sort(function (a, b) { return lens[a] - lens[b] || ids[a] - ids[b]; });
    var K = this.segK, idx = new Uint32Array(m * K * 2);
    for (var k = 0; k < m; k++) {
      var edge = ids[order[k]];
      var slot = slots ? slots[edge] : edge;
      var base = slot * K * 2;
      for (var j = 0; j < K * 2; j++) idx[k * K * 2 + j] = base + j;
    }
    this.lgeo.setIndex(new T3.THREE.BufferAttribute(idx, 1));
    this._lodCount = m;
    this.lodReady = true;
    this.lodVersion = live.version;
  };

  Renderer.prototype._mark = function (name, data, geo) {
    var attr = geo.getAttribute(name);
    if (attr) {
      attr.needsUpdate = true;
    } else {
      geo.setAttribute(name, new T3.THREE.BufferAttribute(data, name === "position" || name === "color" ? 3 : 1));
    }
  };

  Renderer.prototype.updateDims = function (live) {
    if (this.lastDimVersion === live.dimVersion && this._lastDims === live) return;
    this.lastDimVersion = live.dimVersion;
    this._lastDims = live;
    var i;
    for (i = 0; i < live.n; i++) this.nDim[i] = live.dim[i];
    var slots = this._edgeSlots, k2 = this.segK * 2;
    for (i = 0; i < live.m; i++) {
      if (slots) {
        var slot = slots[i];
        if (slot < 0) continue;
        for (var sv = 0; sv < k2; sv++) this.eDim[slot * k2 + sv] = live.eDim[i];
      } else {
        for (var ev = 0; ev < k2; ev++) this.eDim[i * k2 + ev] = live.eDim[i];
      }
    }
    if (this.pgeo.getAttribute("a_dim")) this.pgeo.getAttribute("a_dim").needsUpdate = true;
    if (this.lgeo.getAttribute("a_dim")) this.lgeo.getAttribute("a_dim").needsUpdate = true;
    /* The city is a mesh, not a point cloud: every vertex of a filtered tower
       has to carry the same dim, or the chapter toggle leaves it standing. */
    if (this._cityDim && this._cityN === live.n && live.n > 0) {
      var cd = this._cityDim;
      for (i = 0; i < live.n; i++) {
        var dv = live.dim[i], base = i * CITY_VERTS;
        for (var ck = 0; ck < CITY_VERTS; ck++) cd[base + ck] = dv;
      }
      var cattr = this.cityGeo.getAttribute("a_dim");
      if (cattr) cattr.needsUpdate = true;
    }
  };

  Renderer.prototype.render = function () {
    if (this.contextLost) return;
    this._tickCityFlash();
    this._tickPulse();
    this.nodeUni.u_time.value = this.time || 0;
    this.edgeUni.u_time.value = this.time || 0;
    this.cityUni.u_time.value = this.time || 0;
    if (this.cortex && this.cortex.visible) {
      /* The folds grow in with the build: the gyri of the brainstem are there
         before the frontal pole has folded, and the head is still smooth
         while the first cells arrive. */
      var live = this.live, seen = 0;
      if (live) {
        for (var s = 0; s < live.n; s++) if (live.revealed[s]) seen++;
      }
      this.cortexUni.u_fold.value = live && live.n ? seen / live.n : 0;
      this.cortexUni.u_time.value = this.time || 0;
    }
    var cam = this.camera;
    if (this.sky && this.sky.visible) {
      /* orbiting the camera slides the star layers instead of leaving a
         painted backdrop pinned to the screen */
      var clen = Math.max(cam.position.length(), 1);
      this.skyUni.u_time.value = this.time || 0;
      this.skyUni.u_drift.value.set(Math.atan2(cam.position.x, cam.position.z) * 0.16,
                                    Math.asin(cam.position.y / clen) * 0.24);
    }
    var targetDist = this.camDistance || cam.position.length() || 1;
    this.edgeUni.u_cull.value = targetDist * 1.15 + this.sceneRadius * 0.95;
    this.edgeUni.u_cam.value.copy(cam.position);
    this.nodeUni.u_cam.value.copy(cam.position);
    if (this.lodReady && this.lodVersion === this.live.version) {
      var ratio = targetDist / Math.max(this.sceneRadius, 1);
      var fraction = ratio > 2.4 ? 0.3 : (ratio > 1.5 ? 0.6 : 1);
      var segs = Math.max(1, Math.floor((this._lodCount || this.live.m) * fraction));
      this.lgeo.setDrawRange(0, segs * this.segK * 2);
    } else {
      this.lgeo.setDrawRange(0, this._drawEdges * this.segK * 2);
    }
    this.renderer.render(this.scene, cam);
  };

  Renderer.prototype.resize = function () {
    var w = this.canvas.clientWidth || window.innerWidth;
    var h = this.canvas.clientHeight || (window.innerHeight - 44);
    if (!w || !h) return;
    this.width = w;
    this.height = h;
    this.renderer.setPixelRatio(this.pixelRatio());
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    if (this.skyUni) this.skyUni.u_res.value.set(w, h);
    this.nodeUni.u_px.value = this.renderer.getPixelRatio();
    this.nodeUni.u_minpx.value = 1.6 * this.renderer.getPixelRatio();
  };

  Renderer.prototype.pick = function (x, y) {
    var live = this.live;
    if (!live) return -1;
    var best = -1, bestD = 0;
    var v = this._v, cam = this.camera;
    var w = this.width || 1, h = this.height || 1;
    var list = this.selectionSet ? this.selection : null;
    var count = list ? list.length : live.n;
    for (var k = 0; k < count; k++) {
      var i = list ? list[k] : k;
      if (live.dim[i] < 0.15) continue;
      this.nodeWorld(i, v);
      v.project(cam);
      if (v.z < -1 || v.z > 1) continue;
      var sx = (v.x * 0.5 + 0.5) * w;
      var sy = (-v.y * 0.5 + 0.5) * h;
      var dx = sx - x, dy = sy - y;
      var d = dx * dx + dy * dy;
      /* Every node gets at least a 24px hit box and big worlds grow past it:
         a strict 15px ring is why small cells felt impossible to grab. */
      var sz = (this.nSize && this.nSize[i]) || 3;
      var rad = 24 + Math.min(18, sz * 2);
      if (d < rad * rad && (best < 0 || d < bestD)) {
        bestD = d;
        best = i;
      }
    }
    return best;
  };

  Renderer.prototype.samplePoints = function (live, limit) {
    limit = limit || 6000;
    var count = 0, i;
    for (i = 0; i < live.n; i++) if (live.dim[i] > 0.1) count++;
    if (!count) return new Float32Array(0);
    var useScene = this.nPos && this.sceneN === live.n;
    var stride = Math.max(1, Math.floor(count / limit));
    var out = new Float32Array(Math.ceil(count / stride) * 3);
    var k = 0, seen = 0;
    for (i = 0; i < live.n; i++) {
      if (live.dim[i] <= 0.1) continue;
      if (seen++ % stride === 0) {
        if (useScene) {
          out[k++] = this.nPos[i * 3];
          out[k++] = this.nPos[i * 3 + 1];
          out[k++] = this.nPos[i * 3 + 2];
        } else {
          out[k++] = live.x[i] * this.posScale;
          out[k++] = live.y[i] * this.posScale;
          out[k++] = live.z[i] * this.posScale;
        }
      }
    }
    return out.subarray(0, k);
  };

  /* Geometry and materials are backend agnostic: the WebGPU teardown has no
     WebGLRenderer to dispose, but the scene graph still has to go. */
  Renderer.prototype._disposeScene = function () {
    this._disposeRings();
    if (this.sky) {
      this.sky.geometry.dispose();
      this.sky.material.dispose();
      this.sky = null;
    }
    if (this.cortex) {
      this.cortex.geometry.dispose();
      this.cortex.material.dispose();
      this.cortex = null;
    }
    if (this.cityMesh) {
      this.cityGeo.dispose();
      this.cityMesh.material.dispose();
      this.scene.remove(this.cityMesh);
      this.cityMesh = null;
    }
    this.pgeo.dispose();
    this.lgeo.dispose();
    this.egoMesh.geometry.dispose();
    this.cloud.material.dispose();
    this.halo.material.dispose();
    this.lines.material.dispose();
    this.egoMat.dispose();
  };

  Renderer.prototype.dispose = function () {
    if (!this.renderer) return;
    if (this.canvas) {
      if (this._onContextLost) {
        this.canvas.removeEventListener("webglcontextlost", this._onContextLost, false);
      }
      if (this._onContextRestored) {
        this.canvas.removeEventListener("webglcontextrestored", this._onContextRestored, false);
      }
    }
    this._disposeScene();
    this.renderer.dispose();
  };

  SK.Renderer = Renderer;
})(window.SK = window.SK || {});
