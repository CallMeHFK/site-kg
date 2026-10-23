/* Canvas 2D fallback for machines without WebGL2. Mirrors the renderer's
   public surface for Director/Camera/Labels but caps the preview at 5000 nodes
   so the full 75k archive stays a Three.js-only experience. */
(function (SK) {
  "use strict";

  var SCALE = 50;
  var MAX_NODES = 5000;
  var MAX_EDGES = 12000;
  var TAU = Math.PI * 2;

  function hasWebGL2(canvas) {
    try {
      var probe = canvas || document.createElement("canvas");
      return !!probe.getContext("webgl2");
    } catch (err) {
      return false;
    }
  }

  function Fallback2D(canvas) {
    var ctx = canvas.getContext("2d");
    if (!ctx && canvas.parentNode) {
      // A failed WebGL mount leaves the canvas locked to its WebGL context;
      // a fresh clone is the only way back to a 2D context.
      var fresh = canvas.cloneNode(false);
      canvas.parentNode.replaceChild(fresh, canvas);
      canvas = fresh;
      ctx = canvas.getContext("2d");
    }
    if (!ctx) throw new Error("canvas 2D is unavailable");
    this.canvas = canvas;
    this.SCALE = SCALE;
    this.name = "planets";
    this.cfg = null;
    this.live = null;
    this.selection = null;
    this.hiddenKinds = {};
    this.sceneRadius = 50;
    this.camDistance = 1;
    this.width = 1;
    this.height = 1;
    this.dpr = 1;
    this.ctx = ctx;
    var THREE = T3.THREE;
    this.camera = new THREE.PerspectiveCamera(60, 1, 0.1, 1e6);
    this.camera.position.set(0, 18, 90);
    this.camera.lookAt(0, 0, 0);
    this._v = new THREE.Vector3();
    this._color = new THREE.Color();
    this._screen = new Float32Array(MAX_NODES * 3);
    this._indices = new Int32Array(MAX_NODES);
    this._selN = 0;
    this._edges = new Int32Array(MAX_EDGES * 2);
    this._edgeN = 0;
    this._version = -1;
    this._dimVersion = -1;
    this._notice = null;
    this.resize();
    this._showNotice();
  }

  Fallback2D.prototype.setScene = function (name) {
    this.name = name;
    this.cfg = (SK.scenes && SK.scenes[name]) || (SK.scenes && SK.scenes.planets) || {};
    this._version = -1;
    this._dimVersion = -1;
    return this;
  };

  Fallback2D.prototype._showNotice = function () {
    if (typeof document === "undefined") return;
    if (!this._notice) {
      this._notice = document.createElement("div");
      this._notice.id = "fallback-note";
      this._notice.className = "panel";
      this._notice.style.cssText = "position:fixed;right:12px;bottom:12px;max-width:390px;"
        + "padding:10px 12px;z-index:8;color:#8b949e;line-height:1.45";
      document.body.appendChild(this._notice);
    }
    var total = this.live ? this.live.n : 0;
    this._notice.textContent = "2D fallback: WebGL2 is unavailable, showing at most "
      + MAX_NODES.toLocaleString() + (total ? " of " + total.toLocaleString() : "")
      + " nodes. For the full graph, render the archive viewer with `site-kg render`.";
  };

  Fallback2D.prototype.resize = function () {
    var w = this.canvas.clientWidth || window.innerWidth;
    var h = this.canvas.clientHeight || (window.innerHeight - 44);
    if (!w || !h) return;
    this.dpr = Math.min(window.devicePixelRatio || 1, 2);
    this.width = w;
    this.height = h;
    this.canvas.width = Math.max(1, Math.floor(w * this.dpr));
    this.canvas.height = Math.max(1, Math.floor(h * this.dpr));
    this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  };

  Fallback2D.prototype.sync = function (live) {
    this.live = live;
    if (!live) return this;
    if (this._version !== live.version || this._dimVersion !== live.dimVersion) {
      this._version = live.version;
      this._dimVersion = live.dimVersion;
      this._select(live);
    }
    return this;
  };

  Fallback2D.prototype._select = function (live) {
    var sel = this.selection;
    var n = 0, i, k;
    if (sel && sel.length) {
      for (k = 0; k < sel.length && n < MAX_NODES; k++) {
        i = sel[k];
        if (i >= 0 && i < live.n && live.revealed[i] && live.dim[i] > 0.1) {
          this._indices[n++] = i;
        }
      }
    } else {
      var stride = Math.max(1, Math.ceil(live.n / MAX_NODES));
      for (i = 0; i < live.n && n < MAX_NODES; i += stride) {
        if (live.revealed[i] && live.dim[i] > 0.1) this._indices[n++] = i;
      }
    }
    this._selN = n;
    var slot = new Map();
    for (k = 0; k < n; k++) slot.set(this._indices[k], k);
    var e = 0, count = 0;
    while (e < live.m && count < MAX_EDGES) {
      var a = slot.get(live.eSrc[e]);
      if (a !== undefined) {
        var b = slot.get(live.eDst[e]);
        if (b !== undefined && !this.hiddenKinds[live.kinds[live.eKind[e]]]) {
          this._edges[count * 2] = a;
          this._edges[count * 2 + 1] = b;
          count++;
        }
      }
      e++;
    }
    this._edgeN = count;
    var radius = 50;
    for (k = 0; k < n; k++) {
      i = this._indices[k];
      var r = Math.hypot(live.x[i], live.y[i], live.z[i]) * SCALE;
      if (r > radius) radius = r;
    }
    this.sceneRadius = Math.max(radius, 1);
    this._showNotice();
  };

  Fallback2D.prototype.nodeWorld = function (i, out) {
    var live = this.live;
    if (live && i >= 0 && i < live.n) {
      out.set(live.x[i] * SCALE, live.y[i] * SCALE, live.z[i] * SCALE);
    } else {
      out.set(0, 0, 0);
    }
    return out;
  };

  Fallback2D.prototype.samplePoints = function (live, limit) {
    live = live || this.live;
    limit = limit || 6000;
    if (!live || !live.n) return new Float32Array(0);
    var stride = Math.max(1, Math.ceil(live.n / limit));
    var count = Math.ceil(live.n / stride);
    var out = new Float32Array(count * 3);
    var k = 0;
    for (var i = 0; i < live.n; i += stride) {
      out[k++] = live.x[i] * SCALE;
      out[k++] = live.y[i] * SCALE;
      out[k++] = live.z[i] * SCALE;
    }
    return out.subarray(0, k);
  };

  Fallback2D.prototype.render = function () {
    var ctx = this.ctx;
    var live = this.live;
    if (!ctx || !live) return;
    var cam = this.camera;
    var screen = this._screen, sel = this._indices, sn = this._selN;
    var width = this.width, height = this.height;
    var v = this._v;
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    this.camDistance = cam.position.length();
    var k, i;
    for (k = 0; k < sn; k++) {
      i = sel[k];
      v.set(live.x[i] * SCALE, live.y[i] * SCALE, live.z[i] * SCALE);
      v.project(cam);
      screen[k * 3] = (v.x * 0.5 + 0.5) * width;
      screen[k * 3 + 1] = (-v.y * 0.5 + 0.5) * height;
      screen[k * 3 + 2] = v.z;
    }
    ctx.globalAlpha = 0.28;
    ctx.strokeStyle = "#8b949e";
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (var e = 0; e < this._edgeN; e++) {
      var a = this._edges[e * 2], b = this._edges[e * 2 + 1];
      if (screen[a * 3 + 2] < -1 || screen[a * 3 + 2] > 1 ||
          screen[b * 3 + 2] < -1 || screen[b * 3 + 2] > 1) continue;
      ctx.moveTo(screen[a * 3], screen[a * 3 + 1]);
      ctx.lineTo(screen[b * 3], screen[b * 3 + 1]);
    }
    ctx.stroke();
    ctx.globalAlpha = 1;
    var palette = (this.cfg && this.cfg.palette) || SK.PALETTE;
    var gmax = 1;
    for (k = 0; k < sn; k++) {
      if ((live.degree[sel[k]] || 0) > gmax) gmax = live.degree[sel[k]];
    }
    var color = this._color;
    for (k = 0; k < sn; k++) {
      i = sel[k];
      if (screen[k * 3 + 2] < -1 || screen[k * 3 + 2] > 1) continue;
      color.setHex(palette[(live._nodeCluster[i] || 0) % palette.length]);
      ctx.fillStyle = "rgb(" + Math.round(color.r * 255) + ","
        + Math.round(color.g * 255) + "," + Math.round(color.b * 255) + ")";
      var radius = 1.5 + 3 * Math.sqrt((live.degree[i] || 0) / gmax);
      ctx.beginPath();
      ctx.arc(screen[k * 3], screen[k * 3 + 1], radius, 0, TAU);
      ctx.fill();
    }
  };

  Fallback2D.prototype.pick = function (x, y) {
    var best = -1, bestD = 15 * 15;
    for (var k = 0; k < this._selN; k++) {
      var dx = this._screen[k * 3] - x;
      var dy = this._screen[k * 3 + 1] - y;
      var d = dx * dx + dy * dy;
      if (d < bestD) {
        bestD = d;
        best = this._indices[k];
      }
    }
    return best;
  };

  /* ui.js drives the same drag surface on both renderers; nothing can be
     picked up in 2D, so beginDrag declines and the press ends in a no-op
     poke instead of a TypeError. */
  Fallback2D.prototype.beginDrag = function (i, x, y) { return false; };
  Fallback2D.prototype.moveDrag = function (x, y) {};
  Fallback2D.prototype.endDrag = function () {};
  Fallback2D.prototype.poke = function (i) {};

  Fallback2D.prototype.prepareEdgeLod = function () {};
  Fallback2D.prototype.startFinale = function () {};
  Fallback2D.prototype.resetFinale = function () {};
  Fallback2D.prototype.advanceFinale = function () { return true; };

  Fallback2D.prototype.dispose = function () {
    if (this._notice && this._notice.parentNode) {
      this._notice.parentNode.removeChild(this._notice);
    }
    this._notice = null;
    this.live = null;
  };

  SK.hasWebGL2 = hasWebGL2;
  SK.Fallback2D = Fallback2D;
})(window.SK = window.SK || {});
