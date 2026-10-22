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
    var cfg = this.renderer.cfg || {};
    var budget = this.width < 720
      ? (cfg.labelBudgetMobile || 12)
      : (cfg.labelBudget || this.budget);
    var focus = opts.focus == null ? -1 : opts.focus;
    if (focus >= 0) budget = Math.min(budget, this.focusBudget);
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
      this.renderer.nodeWorld(idx, v);
      v.project(camera);
      if (v.z < -1 || v.z > 1) continue;
      var sx = (v.x * 0.5 + 0.5) * this.width;
      var sy = (-v.y * 0.5 + 0.5) * this.height;
      if (sx < -40 || sy < 12 || sx > this.width + 40 || sy > this.height - 8) continue;
      var title = live.titles[idx] || live.ids[idx] || "";
      if (title.length > 34) title = title.slice(0, 33) + "\u2026";
      var tw = ctx.measureText(title).width;
      var x0 = sx - tw / 2 - 7, y0 = sy - 22;
      var box = [x0, y0, x0 + tw + 14, y0 + 18];
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
      /* A glass capsule rather than a flat white chip: the scene is mostly
         dark, so a translucent indigo plate with a thin cold rim reads as part
         of the sky instead of a sticker pasted on top of it. */
      var w = box[2] - box[0], h = box[3] - box[1], rr = h / 2;
      ctx.beginPath();
      if (ctx.roundRect) {
        ctx.roundRect(box[0], box[1], w, h, rr);
      } else {
        ctx.moveTo(box[0] + rr, box[1]);
        ctx.arcTo(box[2], box[1], box[2], box[3], rr);
        ctx.arcTo(box[2], box[3], box[0], box[3], rr);
        ctx.arcTo(box[0], box[3], box[0], box[1], rr);
        ctx.arcTo(box[0], box[1], box[2], box[1], rr);
      }
      ctx.fillStyle = idx === focus ? "rgba(56,132,255,.90)" : "rgba(8,13,32,.62)";
      ctx.fill();
      ctx.lineWidth = 1;
      ctx.strokeStyle = idx === focus
        ? "rgba(190,225,255,.85)" : "rgba(124,196,255,.26)";
      ctx.stroke();
      ctx.fillStyle = idx === focus ? "#ffffff" : "#d3e3ff";
      ctx.fillText(title, x0 + 7, y0 + 9);
    }
    this._finale(ctx, opts.finale);
  };

  /* The closing caption lives on this canvas instead of in its own fixed DOM
     panel. A separately created fixed element over the WebGL canvas leaves a
     stale compositor tile (a dark ghost of the panel near the top of the
     scene); this canvas is cleared and repainted every frame, so there is no
     layer to go stale. */
  Labels.prototype._finale = function (ctx, note) {
    if (!note || !note.title) return;
    ctx.textAlign = "center";
    ctx.font = "600 14px system-ui,-apple-system,'Segoe UI',sans-serif";
    var tw = ctx.measureText(note.title).width;
    ctx.font = "12px system-ui,-apple-system,'Segoe UI',sans-serif";
    var dw = note.detail ? ctx.measureText(note.detail).width : 0;
    var w = Math.max(tw, dw) + 36;
    var h = note.detail ? 58 : 36;
    var x = (this.width - w) / 2;
    var y = this.height - 30 - h;
    ctx.beginPath();
    if (ctx.roundRect) ctx.roundRect(x, y, w, h, 10);
    else ctx.rect(x, y, w, h);
    ctx.fillStyle = "rgba(7,11,28,.72)";
    ctx.fill();
    ctx.lineWidth = 1;
    ctx.strokeStyle = "rgba(124,196,255,.30)";
    ctx.stroke();
    ctx.fillStyle = "#eef5ff";
    ctx.fillText(note.title, this.width / 2, y + (note.detail ? 22 : 18));
    if (note.detail) {
      ctx.fillStyle = "rgba(180,204,238,.86)";
      ctx.fillText(note.detail, this.width / 2, y + 42);
    }
    ctx.textAlign = "left";
    ctx.font = "12px system-ui,-apple-system,'Segoe UI',sans-serif";
  };

  Labels.prototype.clear = function () {
    this.ctx.clearRect(0, 0, this.width, this.height);
  };

  SK.Labels = Labels;
})(window.SK = window.SK || {});
