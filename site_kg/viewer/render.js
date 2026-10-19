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

  function Renderer() {
    this.SCALE = SCALE;
    this.name = "planets";
    this.cfg = null;
    this.cap = 0;
    this.capE = 0;
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
    this._egoItems = null;
    this._egoScale = null;
  }

  Renderer.prototype.mount = function (canvas, live) {
    var THREE = T3.THREE;
    this.canvas = canvas;
    this.live = live;
    this.renderer = new THREE.WebGLRenderer({canvas: canvas, antialias: true,
                                             powerPreference: "high-performance"});
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    var self = this;
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
      self.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
      self.resize();
    };
    canvas.addEventListener("webglcontextlost", this._onContextLost, false);
    canvas.addEventListener("webglcontextrestored", this._onContextRestored, false);
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(60, 1, 0.1, 1e6);
    this.scene.background = new THREE.Color(0x0d1117);

    this.nodeUni = {u_px: {value: 1}, u_scale: {value: 1}, u_minpx: {value: 2},
                    u_time: {value: 0},
                    u_light: {value: new THREE.Vector3(-0.55, 0.6, 0.75).normalize()}};
    this.haloUni = {u_px: this.nodeUni.u_px, u_scale: this.nodeUni.u_scale,
                    u_minpx: this.nodeUni.u_minpx, u_time: this.nodeUni.u_time,
                    u_light: this.nodeUni.u_light,
                    u_halo: {value: 3.1}};
    /* Node sprites are shaded as spheres: the point coordinate gives a normal,
       one directional light gives the terminator, and a fixed view vector gives
       the specular highlight. a_birth grows a node in when it is first revealed;
       nodes present at load keep birth 0 so no wall of animation fires. */
    var vs = "attribute float a_size; attribute float a_dim; attribute float a_ring;"
      + " attribute float a_birth; attribute vec3 color;"
      + " uniform float u_px; uniform float u_scale; uniform float u_minpx;"
      + " uniform float u_time;"
      + " varying vec3 vC; varying float vD; varying float vR; varying float vAge;"
      + " void main(){ vC=color; vD=a_dim; vR=a_ring;"
      + "   float age = max(0.0, u_time-a_birth);"
      + "   float born = smoothstep(0.0,0.45,age); vAge = born;"
      + "   float grow = (0.55+0.45*born)*(1.0+0.18*sin(born*3.14159));"
      + "   vec4 mv = modelViewMatrix*vec4(position,1.0);"
      + "   float size = a_size*u_scale*u_px*(300.0/max(-mv.z,1.0));"
      + "   size *= mix(1.0,1.42,vR)*grow;"
      + "   gl_PointSize = clamp(size, u_minpx*0.7, 220.0*u_px);"
      + "   gl_Position = projectionMatrix*mv; }";
    this.pgeo = new THREE.BufferGeometry();
    this.cloud = new THREE.Points(this.pgeo, new THREE.ShaderMaterial({
      uniforms: this.nodeUni, vertexShader: vs,
      fragmentShader: "varying vec3 vC; varying float vD; varying float vR;"
        + " varying float vAge; uniform vec3 u_light;"
        + " void main(){ vec2 b = gl_PointCoord*2.0-1.0;"
        + "   float scale = mix(1.0,1.42,vR); vec2 p = b*scale;"
        + "   float r2 = dot(p,p);"
        + "   float inside = 1.0-smoothstep(0.96,1.0,r2);"
        + "   vec3 n = vec3(p.x,-p.y,sqrt(max(0.0,1.0-r2)));"
        + "   vec3 L = normalize(u_light);"
        + "   float diff = max(dot(n,L),0.0);"
        + "   float spec = pow(max(dot(n,normalize(L+vec3(0.0,0.0,1.0))),0.0),42.0);"
        + "   float rim = pow(1.0-n.z,2.2);"
        + "   vec3 col = vC*(0.18+0.82*diff) + vec3(0.85,0.92,1.0)*spec*0.55"
        + "            + vC*rim*0.55;"
        + "   float e = abs(length(vec2(b.x,b.y*3.4))-0.86);"
        + "   float band = (1.0-smoothstep(0.0,0.035,e))*vR;"
        + "   band *= (b.y < 0.0 ? (1.0-inside) : 1.0);"
        + "   col += (vC*1.5+0.10)*band*(0.4+0.6*diff);"
        + "   float a = max(inside, band*0.75);"
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
        + " varying float vAge; uniform vec3 u_light;"
        + " void main(){ vec2 p = gl_PointCoord*2.0-1.0;"
        + "   float r2 = dot(p,p); if(r2 > 1.0) discard;"
        + "   vec3 n = vec3(p.x,-p.y,sqrt(max(0.0,1.0-r2)));"
        + "   float lit = 0.22+0.78*max(dot(n,normalize(u_light)),0.0);"
        + "   float a = pow(smoothstep(1.0,0.0,sqrt(r2)),2.2)*0.28*vD*lit*vAge;"
        + "   gl_FragColor = vec4(vC, a); }",
      transparent: true, depthWrite: false, blending: THREE.AdditiveBlending}));
    this.halo.frustumCulled = false;
    this.halo.renderOrder = 1;
    this.scene.add(this.halo);

    this.edgeUni = {u_cull: {value: 1e9}, u_cam: {value: new THREE.Vector3()},
                    u_opacity: {value: 1}, u_sweepX: {value: 0},
                    u_sweepAmp: {value: 0}, u_time: {value: 0}};
    this.lgeo = new THREE.BufferGeometry();
    this.lines = new THREE.LineSegments(this.lgeo, new THREE.ShaderMaterial({
      uniforms: this.edgeUni, transparent: true, depthWrite: false,
      vertexShader: "attribute float a_base; attribute float a_dim; attribute vec3 color;"
        + " attribute float a_birth; attribute float a_t;"
        + " uniform float u_cull; uniform float u_opacity; uniform vec3 u_cam;"
        + " uniform float u_sweepX; uniform float u_sweepAmp; uniform float u_time;"
        + " varying vec3 vC; varying float vA;"
        + " void main(){ vC=color;"
        + "   float d = distance(position, u_cam);"
        + "   float fade = 1.0 - smoothstep(u_cull*0.55, u_cull, d);"
        + "   float age = max(0.0, u_time-a_birth);"
        + "   float wave = smoothstep(0.0,0.5,age-a_t*0.22);"
        + "   vA = a_base*u_opacity*a_dim*fade*wave;"
        + "   vA += u_sweepAmp*exp(-abs(position.x-u_sweepX)*0.18)*0.9;"
        + "   gl_Position = projectionMatrix*modelViewMatrix*vec4(position,1.0); }",
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
    this.setScene("planets");
    this.resize();
    return this;
  };

  /* A flat background image built in-page: a few thousand stars in three
     brightness classes plus low-alpha nebula blooms. It is drawn once, used as
     scene.background, and never becomes a draw call of its own. */
  Renderer.prototype._makeStarfield = function () {
    var w = 1024, h = 512;
    var started = (typeof performance !== "undefined" && performance.now)
      ? performance.now() : 0;
    for (var attempt = 0; attempt < 2; attempt++) {
      var canvas = document.createElement("canvas");
      canvas.width = w;
      canvas.height = h;
      var ctx = canvas.getContext("2d");
      if (!ctx) return null;
      ctx.fillStyle = "#05070c";
      ctx.fillRect(0, 0, w, h);
      var seed = 20261019;
      var rnd = function () {
        seed = (seed * 1664525 + 1013904223) >>> 0;
        return seed / 4294967296;
      };
      var blooms = [[0.24, 0.38, 0.30], [0.71, 0.26, 0.24], [0.55, 0.78, 0.22]];
      for (var b = 0; b < blooms.length; b++) {
        var bx = blooms[b][0] * w, by = blooms[b][1] * h;
        var br = blooms[b][2] * Math.min(w, h);
        var grad = ctx.createRadialGradient(bx, by, 0, bx, by, br);
        grad.addColorStop(0, b === 1 ? "rgba(56,84,160,0.16)"
                                     : "rgba(40,120,140,0.10)");
        grad.addColorStop(0.55, "rgba(12,24,49,0.05)");
        grad.addColorStop(1, "rgba(5,7,12,0)");
        ctx.fillStyle = grad;
        ctx.fillRect(bx - br, by - br, br * 2, br * 2);
      }
      for (var i = 0; i < 2200; i++) {
        var x = rnd() * w, y = rnd() * h;
        var roll = rnd();
        var r = roll > 0.99 ? 1.7 + rnd() * 1.0
              : (roll > 0.88 ? 0.9 + rnd() * 0.55 : 0.35 + rnd() * 0.45);
        var alpha = 0.25 + rnd() * 0.65;
        var tint = rnd();
        ctx.fillStyle = tint > 0.9 ? "rgba(255,226,190," + alpha + ")"
          : (tint > 0.6 ? "rgba(178,206,255," + alpha + ")"
                        : "rgba(236,242,255," + alpha + ")");
        ctx.beginPath();
        ctx.arc(x, y, r, 0, Math.PI * 2);
        ctx.fill();
        if (r > 1.6) {
          var halo = ctx.createRadialGradient(x, y, 0, x, y, r * 4);
          halo.addColorStop(0, "rgba(210,228,255,0.22)");
          halo.addColorStop(1, "rgba(210,228,255,0)");
          ctx.fillStyle = halo;
          ctx.fillRect(x - r * 4, y - r * 4, r * 8, r * 8);
        }
      }
      var cost = started && performance.now ? performance.now() - started : 0;
      /* a slow first bake falls back to half resolution rather than paying the
         cost on every launch */
      if (attempt === 0 && cost > 50 && w > 512) {
        w = 512;
        h = 256;
        started = performance.now();
        continue;
      }
      var tex = new T3.THREE.CanvasTexture(canvas);
      if ("colorSpace" in tex && T3.THREE.SRGBColorSpace) {
        tex.colorSpace = T3.THREE.SRGBColorSpace;
      }
      tex.needsUpdate = true;
      return tex;
    }
    return null;
  };

  Renderer.prototype.setScene = function (name) {
    var cfg = (SK.scenes && SK.scenes[name]) || (SK.scenes && SK.scenes.planets);
    if (!cfg) return;
    this.name = name;
    this.cfg = cfg;
    if (cfg.starfield) {
      if (!this._starTex) this._starTex = this._makeStarfield();
      this.scene.background = this._starTex ||
        new T3.THREE.Color(cfg.bg);
    } else {
      this.scene.background = new T3.THREE.Color(cfg.bg);
    }
    this._ringThreshold = cfg.ringRank == null ? 2 : cfg.ringRank;
    var ld = cfg.lightDir || [-0.55, 0.6, 0.75];
    this.nodeUni.u_light.value.set(ld[0], ld[1], ld[2]).normalize();
    this.haloUni.u_halo.value = cfg.haloScale;
    this.edgeUni.u_opacity.value = cfg.edgeOpacity;
    this.nodeUni.u_scale.value = cfg.nodeScale;
    this.colorDirty = true;
    this.lastVersion = -1;
    this.lastSelectionKey = "";
    this._syncVisibility();
    this.resetFinale();
    this._clearEdgeIndex();
    var planet = !!cfg.shells;
    if (this.ringMesh) this.ringMesh.visible = planet;
  };

  Renderer.prototype._syncVisibility = function () {
    var people = !!(this.cfg && this.cfg.nodeMode === "instanced");
    if (this.cloud) this.cloud.visible = !people;
    if (this.halo) this.halo.visible = !people;
    if (this.egoMesh) this.egoMesh.visible = people;
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
      this.pgeo.setAttribute("position", new THREE.BufferAttribute(this.nPos, 3));
      this.pgeo.setAttribute("color", new THREE.BufferAttribute(this.nCol, 3));
      this.pgeo.setAttribute("a_size", new THREE.BufferAttribute(this.nSize, 1));
      this.pgeo.setAttribute("a_dim", new THREE.BufferAttribute(this.nDim, 1));
      this.pgeo.setAttribute("a_ring", new THREE.BufferAttribute(this.nRing, 1));
      this.pgeo.setAttribute("a_birth", new THREE.BufferAttribute(this.nBirth, 1));
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
    if (this.capE !== live.capE) {
      this.capE = live.capE;
      this.ePos = new Float32Array(this.capE * 6);
      this.eCol = new Float32Array(this.capE * 6);
      this.eBase = new Float32Array(this.capE * 2);
      this.eDim = new Float32Array(this.capE * 2);
      this.eBirth = new Float32Array(this.capE * 2);
      this.eT = new Float32Array(this.capE * 2);
      for (var t = 0; t < this.capE; t++) {
        this.eT[t * 2] = 0;
        this.eT[t * 2 + 1] = 1;
      }
      this._edgeSlots = new Int32Array(this.capE);
      this._edgeSlots.fill(-1);
      this.lgeo.setAttribute("position", new THREE.BufferAttribute(this.ePos, 3));
      this.lgeo.setAttribute("color", new THREE.BufferAttribute(this.eCol, 3));
      this.lgeo.setAttribute("a_base", new THREE.BufferAttribute(this.eBase, 1));
      this.lgeo.setAttribute("a_dim", new THREE.BufferAttribute(this.eDim, 1));
      this.lgeo.setAttribute("a_birth", new THREE.BufferAttribute(this.eBirth, 1));
      this.lgeo.setAttribute("a_t", new THREE.BufferAttribute(this.eT, 1));
      if (this._edgeBirthQueue) {
        for (var eq = 0; eq < this._edgeBirthQueue.length; eq++) {
          var eb = this._edgeBirthQueue[eq];
          if (eb[0] >= 0 && eb[0] < this.capE) {
            this.eBirth[eb[0] * 2] = eb[1];
            this.eBirth[eb[0] * 2 + 1] = eb[1];
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
      this.eBirth[e * 2] = t;
      this.eBirth[e * 2 + 1] = t;
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

  Renderer.prototype._color = function (clusterId) {
    var palette = (this.cfg && this.cfg.palette) || SK.PALETTE;
    var hex = palette[clusterId % palette.length];
    var c = this._tmpColor || (this._tmpColor = new T3.THREE.Color());
    c.setHex(hex);
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
      var gmax = 1, rmax = 0, i, e;
      for (i = 0; i < live.n; i++) {
        if (live.degree[i] > gmax) gmax = live.degree[i];
        if (live.rank[i] > rmax) rmax = live.rank[i];
      }
      var shells = !!(this.cfg && this.cfg.shells);
      var neural = this.name === "neural";
      var maxR = 1;
      for (i = 0; i < live.n; i++) {
        var x = live.x[i] * SCALE, y = live.y[i] * SCALE, z = live.z[i] * SCALE;
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
        }
        var cluster = live._nodeCluster ? live._nodeCluster[i] : 0;
        var c = this._color(cluster);
        this.nPos[i * 3] = x;
        this.nPos[i * 3 + 1] = y;
        this.nPos[i * 3 + 2] = z;
        this.nCol[i * 3] = c.r;
        this.nCol[i * 3 + 1] = c.g;
        this.nCol[i * 3 + 2] = c.b;
        var degPart = Math.sqrt((live.degree[i] || 0) / gmax);
        var rankPart = rmax > 0 ? Math.sqrt((live.rank[i] || 0) / rmax) : 0;
        if (shells) this.nSize[i] = 2.0 + 6.4 * rankPart + 1.4 * degPart;
        else if (neural) this.nSize[i] = 1.0 + 1.9 * degPart;
        else this.nSize[i] = 2.2 + 3.4 * degPart;
        /* giant worlds carry a visible band; leaves stay smooth spheres */
        this.nRing[i] = shells && (0.7 * rankPart + 0.3 * degPart) >=
          this._ringThreshold ? 1 : 0;
        var wr = Math.hypot(x, y, z);
        if (wr > maxR) maxR = wr;
      }
      this.sceneRadius = maxR;
      this.sceneN = live.n;
      if (this.cfg && this.cfg.ego) {
        this._packEgo(live, rmax);
      } else {
        this._edgeSlots = null;
        this.selectionSet = null;
        this._drawEdges = live.m;
        this._egoItems = null;
        this._egoScale = null;
        for (e = 0; e < live.m; e++) {
          var a = live.eSrc[e], b = live.eDst[e];
          this.ePos[e * 6] = this.nPos[a * 3];
          this.ePos[e * 6 + 1] = this.nPos[a * 3 + 1];
          this.ePos[e * 6 + 2] = this.nPos[a * 3 + 2];
          this.ePos[e * 6 + 3] = this.nPos[b * 3];
          this.ePos[e * 6 + 4] = this.nPos[b * 3 + 1];
          this.ePos[e * 6 + 5] = this.nPos[b * 3 + 2];
          var ca = this._color(live._nodeCluster ? live._nodeCluster[a] : 0);
          this.eCol[e * 6] = ca.r;
          this.eCol[e * 6 + 1] = ca.g;
          this.eCol[e * 6 + 2] = ca.b;
          var cb = this._color(live._nodeCluster ? live._nodeCluster[b] : 0);
          this.eCol[e * 6 + 3] = cb.r;
          this.eCol[e * 6 + 4] = cb.g;
          this.eCol[e * 6 + 5] = cb.b;
          var base = structuralBase(live.kinds[live.eKind[e]]);
          this.eBase[e * 2] = base;
          this.eBase[e * 2 + 1] = base;
        }
      }
      this._mark("position", this.nPos, this.pgeo);
      this._mark("color", this.nCol, this.pgeo);
      this._mark("a_size", this.nSize, this.pgeo);
      this._mark("a_ring", this.nRing, this.pgeo);
      this._mark("position", this.ePos, this.lgeo);
      this._mark("color", this.eCol, this.lgeo);
      this._mark("a_base", this.eBase, this.lgeo);
      this.pgeo.setDrawRange(0, live.n);
      this.lgeo.setDrawRange(0, this._drawEdges * 2);
      this._ensureRings(live);
      this.colorDirty = false;
    }
    this.updateDims(live);
    return this;
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
      this.ePos[k * 6] = this.nPos[a * 3];
      this.ePos[k * 6 + 1] = this.nPos[a * 3 + 1];
      this.ePos[k * 6 + 2] = this.nPos[a * 3 + 2];
      this.ePos[k * 6 + 3] = this.nPos[b * 3];
      this.ePos[k * 6 + 4] = this.nPos[b * 3 + 1];
      this.ePos[k * 6 + 5] = this.nPos[b * 3 + 2];
      var ca = this._color(live._nodeCluster[a]);
      this.eCol[k * 6] = ca.r;
      this.eCol[k * 6 + 1] = ca.g;
      this.eCol[k * 6 + 2] = ca.b;
      var cb = this._color(live._nodeCluster[b]);
      this.eCol[k * 6 + 3] = cb.r;
      this.eCol[k * 6 + 4] = cb.g;
      this.eCol[k * 6 + 5] = cb.b;
      var base = structuralBase(live.kinds[live.eKind[e]]);
      this.eBase[k * 2] = base;
      this.eBase[k * 2 + 1] = base;
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
      mesh.setColorAt(s, this._color(live._nodeCluster[idx]));
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
      out.set(this.live.x[i] * SCALE, this.live.y[i] * SCALE,
              this.live.z[i] * SCALE);
    } else {
      out.set(0, 0, 0);
    }
    return out;
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
  };

  Renderer.prototype.resetFinale = function () {
    this.finaleActive = false;
    this.finaleClock = 0;
    this.finaleMode = "";
    if (this.edgeUni) {
      this.edgeUni.u_sweepAmp.value = 0;
      this.edgeUni.u_sweepX.value = 0;
    }
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
    var lens = new Float32Array(m);
    var order = new Array(m);
    for (var e = 0; e < m; e++) {
      order[e] = e;
      var a = live.eSrc[e] * 3, b = live.eDst[e] * 3;
      var dx = this.nPos[a] - this.nPos[b];
      var dy = this.nPos[a + 1] - this.nPos[b + 1];
      var dz = this.nPos[a + 2] - this.nPos[b + 2];
      lens[e] = dx * dx + dy * dy + dz * dz;
    }
    order.sort(function (a, b) { return lens[a] - lens[b] || a - b; });
    var idx = new Uint32Array(m * 2);
    for (var k = 0; k < m; k++) {
      var edge = order[k];
      idx[k * 2] = edge * 2;
      idx[k * 2 + 1] = edge * 2 + 1;
    }
    this.lgeo.setIndex(new T3.THREE.BufferAttribute(idx, 1));
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
    var slots = this._edgeSlots;
    for (i = 0; i < live.m; i++) {
      if (slots) {
        var slot = slots[i];
        if (slot < 0) continue;
        this.eDim[slot * 2] = live.eDim[i];
        this.eDim[slot * 2 + 1] = live.eDim[i];
      } else {
        this.eDim[i * 2] = live.eDim[i];
        this.eDim[i * 2 + 1] = live.eDim[i];
      }
    }
    if (this.pgeo.getAttribute("a_dim")) this.pgeo.getAttribute("a_dim").needsUpdate = true;
    if (this.lgeo.getAttribute("a_dim")) this.lgeo.getAttribute("a_dim").needsUpdate = true;
  };

  Renderer.prototype.render = function () {
    if (this.contextLost) return;
    this.nodeUni.u_time.value = this.time || 0;
    this.edgeUni.u_time.value = this.time || 0;
    var cam = this.camera;
    var targetDist = this.camDistance || cam.position.length() || 1;
    this.edgeUni.u_cull.value = targetDist * 1.15 + this.sceneRadius * 0.95;
    this.edgeUni.u_cam.value.copy(cam.position);
    if (this.lodReady && this.lodVersion === this.live.version) {
      var ratio = targetDist / Math.max(this.sceneRadius, 1);
      var fraction = ratio > 2.4 ? 0.3 : (ratio > 1.5 ? 0.6 : 1);
      var segs = Math.max(1, Math.floor(this.live.m * fraction));
      this.lgeo.setDrawRange(0, segs * 2);
    } else {
      this.lgeo.setDrawRange(0, this._drawEdges * 2);
    }
    this.renderer.render(this.scene, cam);
  };

  Renderer.prototype.resize = function () {
    var w = this.canvas.clientWidth || window.innerWidth;
    var h = this.canvas.clientHeight || (window.innerHeight - 44);
    if (!w || !h) return;
    this.width = w;
    this.height = h;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.nodeUni.u_px.value = this.renderer.getPixelRatio();
    this.nodeUni.u_minpx.value = 1.6 * this.renderer.getPixelRatio();
  };

  Renderer.prototype.pick = function (x, y) {
    var live = this.live;
    if (!live) return -1;
    var best = -1, bestD = 15 * 15;
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
      if (d < bestD) {
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
          out[k++] = live.x[i] * SCALE;
          out[k++] = live.y[i] * SCALE;
          out[k++] = live.z[i] * SCALE;
        }
      }
    }
    return out.subarray(0, k);
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
    this._disposeRings();
    if (this._starTex) {
      this._starTex.dispose();
      this._starTex = null;
    }
    this.pgeo.dispose();
    this.lgeo.dispose();
    this.egoMesh.geometry.dispose();
    this.cloud.material.dispose();
    this.halo.material.dispose();
    this.lines.material.dispose();
    this.egoMat.dispose();
    this.renderer.dispose();
  };

  SK.Renderer = Renderer;
})(window.SK = window.SK || {});
