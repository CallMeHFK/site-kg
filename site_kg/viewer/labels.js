/* Screen-space label layer on a 2D canvas: budgeted, collision-avoided. */
(function (SK) {
  "use strict";

  function Labels(canvas, renderer) {
    this.canvas = canvas;
    this.renderer = renderer;
    this.ctx = canvas.getContext("2d");
    this.dpr = 1;
    this.budget = 40;
    this.focusBudget = 12;
    this._v = new T3.THREE.Vector3();
    this.resize();
  }

  Labels.prototype.resize = function () {
    var dpr = Math.min(window.devicePixelRatio || 1, 2);
    var w = this.canvas.clientWidth || window.innerWidth;
    var h = this.canvas.clientHeight || (window.innerHeight - 44);
    this.dpr = dpr;
    this.width = w;
    this.height = h;
    this.canvas.width = Math.max(1, Math.floor(w * dpr));
    this.canvas.height = Math.max(1, Math.floor(h * dpr));
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  };

  Labels.prototype.update = function (live, cameraModule, opts) {
    var ctx = this.ctx;
    if (!ctx) return;
    opts = opts || {};
    ctx.clearRect(0, 0, this.width, this.height);
    var camera = this.renderer.camera;
    var focus = opts.focus == null ? -1 : opts.focus;
    var budget = focus >= 0 ? this.focusBudget : this.budget;
    var candidates = [];
    var v = this._v;
    for (var i = 0; i < live.n; i++) {
      if (live.dim[i] < 0.45) continue;
      var score = (live.degree[i] || 0) + (live.rank[i] || 0) * 400;
      if (focus >= 0) {
        if (i !== focus && live.dim[i] < 0.9) continue;
        score += i === focus ? 1e9 : 0;
      }
      candidates.push([score, i]);
    }
    candidates.sort(function (a, b) { return b[0] - a[0]; });
    if (candidates.length > budget) candidates.length = budget;
    var boxes = [];
    ctx.font = "12px system-ui,-apple-system,'Segoe UI',sans-serif";
    ctx.textBaseline = "middle";
    for (var k = 0; k < candidates.length; k++) {
      var idx = candidates[k][1];
      v.set(live.x[idx] * this.renderer.SCALE, live.y[idx] * this.renderer.SCALE,
            live.z[idx] * this.renderer.SCALE);
      v.project(camera);
      if (v.z < -1 || v.z > 1) continue;
      var sx = (v.x * 0.5 + 0.5) * this.width;
      var sy = (-v.y * 0.5 + 0.5) * this.height;
      if (sx < -40 || sy < 12 || sx > this.width + 40 || sy > this.height - 8) continue;
      var title = live.titles[idx] || live.ids[idx] || "";
      if (title.length > 34) title = title.slice(0, 33) + "\u2026";
      var tw = ctx.measureText(title).width;
      var x0 = sx - tw / 2 - 5, y0 = sy - 20;
      var box = [x0, y0, x0 + tw + 10, y0 + 17];
      var hit = false;
      for (var b = 0; b < boxes.length; b++) {
        var o = boxes[b];
        if (box[0] < o[2] && box[2] > o[0] && box[1] < o[3] && box[3] > o[1]) {
          hit = true;
          break;
        }
      }
      if (hit) continue;
      boxes.push(box);
      ctx.fillStyle = idx === focus ? "rgba(31,111,235,.92)" : "rgba(13,17,23,.76)";
      ctx.fillRect(box[0], box[1], box[2] - box[0], 17);
      ctx.fillStyle = idx === focus ? "#ffffff" : "#c9d1d9";
      ctx.fillText(title, x0 + 5, y0 + 9);
    }
  };

  Labels.prototype.clear = function () {
    this.ctx.clearRect(0, 0, this.width, this.height);
  };

  SK.Labels = Labels;
})(window.SK = window.SK || {});
