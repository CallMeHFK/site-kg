/* Three.js renderer: one Points cloud, one additive halo, one LineSegments.
   Node state lives in SK.LiveGraph typed arrays; this module only mirrors it. */
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
  }

  Renderer.prototype.mount = function (canvas, live) {
    var THREE = T3.THREE;
    this.canvas = canvas;
    this.live = live;
    this.renderer = new THREE.WebGLRenderer({canvas: canvas, antialias: true,
                                             powerPreference: "high-performance"});
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(60, 1, 0.1, 1e6);
    this.scene.background = new THREE.Color(0x0d1117);

    this.nodeUni = {u_px: {value: 1}, u_scale: {value: 1}};
    this.haloUni = {u_px: this.nodeUni.u_px, u_scale: this.nodeUni.u_scale,
                    u_halo: {value: 3.1}};
    var vs = "attribute float a_size; attribute float a_dim; attribute vec3 color;"
      + " uniform float u_px; uniform float u_scale; varying vec3 vC; varying float vD;"
      + " void main(){ vC=color; vD=a_dim;"
      + "   vec4 mv = modelViewMatrix*vec4(position,1.0);"
      + "   gl_PointSize = a_size*u_scale*u_px*(300.0/max(-mv.z,1.0));"
      + "   gl_Position = projectionMatrix*mv; }";
    this.pgeo = new THREE.BufferGeometry();
    this.cloud = new THREE.Points(this.pgeo, new THREE.ShaderMaterial({
      uniforms: this.nodeUni, vertexShader: vs,
      fragmentShader: "varying vec3 vC; varying float vD;"
        + " void main(){ float r = length(gl_PointCoord-0.5); if(r>0.5) discard;"
        + "   float a = smoothstep(0.5,0.32,r);"
        + "   gl_FragColor = vec4(vC*(0.25+0.75*vD), a*(0.15+0.85*vD)); }",
      transparent: true, depthWrite: false}));
    this.cloud.frustumCulled = false;
    this.cloud.renderOrder = 2;
    this.scene.add(this.cloud);

    this.halo = new THREE.Points(this.pgeo, new THREE.ShaderMaterial({
      uniforms: this.haloUni,
      vertexShader: vs.replace("a_size*u_scale*u_px", "a_size*u_scale*u_px*u_halo"),
      fragmentShader: "varying vec3 vC; varying float vD;"
        + " void main(){ float r = length(gl_PointCoord-0.5); if(r>0.5) discard;"
        + "   float a = pow(smoothstep(0.5,0.0,r),2.2)*0.30*vD;"
        + "   gl_FragColor = vec4(vC, a); }",
      transparent: true, depthWrite: false, blending: THREE.AdditiveBlending}));
    this.halo.frustumCulled = false;
    this.halo.renderOrder = 1;
    this.scene.add(this.halo);

    this.edgeUni = {u_cull: {value: 1e9}, u_cam: {value: new THREE.Vector3()},
                    u_opacity: {value: 1}};
    this.lgeo = new THREE.BufferGeometry();
    this.lines = new THREE.LineSegments(this.lgeo, new THREE.ShaderMaterial({
      uniforms: this.edgeUni, transparent: true, depthWrite: false,
      vertexShader: "attribute float a_base; attribute float a_dim; attribute vec3 color;"
        + " uniform float u_cull; uniform float u_opacity; uniform vec3 u_cam;"
        + " varying vec3 vC; varying float vA;"
        + " void main(){ vC=color;"
        + "   float d = distance(position, u_cam);"
        + "   float fade = 1.0 - smoothstep(u_cull*0.55, u_cull, d);"
        + "   vA = a_base*u_opacity*a_dim*fade;"
        + "   gl_Position = projectionMatrix*modelViewMatrix*vec4(position,1.0); }",
      fragmentShader: "varying vec3 vC; varying float vA;"
        + " void main(){ gl_FragColor = vec4(vC, vA); }"}));
    this.lines.frustumCulled = false;
    this.scene.add(this.lines);

    this._v = new THREE.Vector3();
    this.setScene("planets");
    this.resize();
    return this;
  };

  Renderer.prototype.setScene = function (name) {
    var cfg = (SK.scenes && SK.scenes[name]) || (SK.scenes && SK.scenes.planets);
    if (!cfg) return;
    this.name = name;
    this.cfg = cfg;
    this.scene.background = new T3.THREE.Color(cfg.bg);
    this.haloUni.u_halo.value = cfg.haloScale;
    this.edgeUni.u_opacity.value = cfg.edgeOpacity;
    this.nodeUni.u_scale.value = cfg.nodeScale;
    this.colorDirty = true;
    this.lastVersion = -1;
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
      this.pgeo.setAttribute("position", new THREE.BufferAttribute(this.nPos, 3));
      this.pgeo.setAttribute("color", new THREE.BufferAttribute(this.nCol, 3));
      this.pgeo.setAttribute("a_size", new THREE.BufferAttribute(this.nSize, 1));
      this.pgeo.setAttribute("a_dim", new THREE.BufferAttribute(this.nDim, 1));
      this.colorDirty = true;
      this.lastDimVersion = -1;
    }
    if (this.capE !== live.capE) {
      this.capE = live.capE;
      this.ePos = new Float32Array(this.capE * 6);
      this.eCol = new Float32Array(this.capE * 6);
      this.eBase = new Float32Array(this.capE * 2);
      this.eDim = new Float32Array(this.capE * 2);
      this.lgeo.setAttribute("position", new THREE.BufferAttribute(this.ePos, 3));
      this.lgeo.setAttribute("color", new THREE.BufferAttribute(this.eCol, 3));
      this.lgeo.setAttribute("a_base", new THREE.BufferAttribute(this.eBase, 1));
      this.lgeo.setAttribute("a_dim", new THREE.BufferAttribute(this.eDim, 1));
      this.colorDirty = true;
      this.lastDimVersion = -1;
    }
  };

  Renderer.prototype._color = function (clusterId) {
    var palette = (this.cfg && this.cfg.palette) || SK.PALETTE;
    var hex = palette[clusterId % palette.length];
    var c = this._tmpColor || (this._tmpColor = new T3.THREE.Color());
    c.setHex(hex);
    return c;
  };

  Renderer.prototype.sync = function (live) {
    this.live = live;
    this._ensure();
    var rebuild = this.colorDirty || live.version !== this.lastVersion;
    if (rebuild) {
      this.lastVersion = live.version;
      this.colorDirty = false;
      var gmax = 1, i, e;
      for (i = 0; i < live.n; i++) if (live.degree[i] > gmax) gmax = live.degree[i];
      var maxR = 1;
      for (i = 0; i < live.n; i++) {
        var x = live.x[i] * SCALE, y = live.y[i] * SCALE, z = live.z[i] * SCALE;
        var cluster = live._nodeCluster ? live._nodeCluster[i] : 0;
        var c = this._color(cluster);
        this.nPos[i * 3] = x;
        this.nPos[i * 3 + 1] = y;
        this.nPos[i * 3 + 2] = z;
        this.nCol[i * 3] = c.r;
        this.nCol[i * 3 + 1] = c.g;
        this.nCol[i * 3 + 2] = c.b;
        this.nSize[i] = 2.2 + 3.4 * Math.sqrt((live.degree[i] || 0) / gmax);
        var rr = Math.hypot(x, y, z);
        if (rr > maxR) maxR = rr;
      }
      this.sceneRadius = maxR;
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
      this._mark("position", this.nPos, this.pgeo);
      this._mark("color", this.nCol, this.pgeo);
      this._mark("a_size", this.nSize, this.pgeo);
      this._mark("position", this.ePos, this.lgeo);
      this._mark("color", this.eCol, this.lgeo);
      this._mark("a_base", this.eBase, this.lgeo);
      this.pgeo.setDrawRange(0, live.n);
      this.lgeo.setDrawRange(0, live.m * 2);
    }
    this.updateDims(live);
    return this;
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
    for (i = 0; i < live.m; i++) {
      this.eDim[i * 2] = live.eDim[i];
      this.eDim[i * 2 + 1] = live.eDim[i];
    }
    if (this.pgeo.getAttribute("a_dim")) this.pgeo.getAttribute("a_dim").needsUpdate = true;
    if (this.lgeo.getAttribute("a_dim")) this.lgeo.getAttribute("a_dim").needsUpdate = true;
  };

  Renderer.prototype.render = function () {
    var cam = this.camera;
    var targetDist = this.camDistance || cam.position.length() || 1;
    this.edgeUni.u_cull.value = targetDist * 1.15 + this.sceneRadius * 0.95;
    this.edgeUni.u_cam.value.copy(cam.position);
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
  };

  Renderer.prototype.pick = function (x, y) {
    var live = this.live;
    if (!live) return -1;
    var best = -1, bestD = 15 * 15;
    var v = this._v, cam = this.camera;
    var w = this.width || 1, h = this.height || 1;
    for (var i = 0; i < live.n; i++) {
      if (live.dim[i] < 0.15) continue;
      v.set(live.x[i] * SCALE, live.y[i] * SCALE, live.z[i] * SCALE);
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
    var stride = Math.max(1, Math.floor(count / limit));
    var out = new Float32Array(Math.ceil(count / stride) * 3);
    var k = 0, seen = 0;
    for (i = 0; i < live.n; i++) {
      if (live.dim[i] <= 0.1) continue;
      if (seen++ % stride === 0) {
        out[k++] = live.x[i] * SCALE;
        out[k++] = live.y[i] * SCALE;
        out[k++] = live.z[i] * SCALE;
      }
    }
    return out.subarray(0, k);
  };

  Renderer.prototype.dispose = function () {
    if (!this.renderer) return;
    this.pgeo.dispose();
    this.lgeo.dispose();
    this.cloud.material.dispose();
    this.halo.material.dispose();
    this.lines.material.dispose();
    this.renderer.dispose();
  };

  SK.Renderer = Renderer;
})(window.SK = window.SK || {});
