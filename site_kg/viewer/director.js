/* Director: owns live/archive state, reveal replay, visibility, finale. */
(function (SK) {
  "use strict";

  function Director(live, renderer, camera, labels) {
    this.live = live;
    this.renderer = renderer;
    this.camera = camera;
    this.labels = labels;
    this.ui = null;
    this.mode = "archive";
    this.state = "live";
    this.query = "";
    this.chapter = null;
    this.focusIndex = -1;
    this.ingestDone = false;
    this.layoutSettled = false;
    this.finaleDone = false;
    this.replay = null;
    this.time = 0;
    this.fps = 60;
    this.labelTime = 0;
    this.statusTime = 0;
    this._visKey = "";
    this._fitNext = false;
  }

  Director.prototype.attach = function (ui) {
    this.ui = ui;
    return this;
  };

  Director.prototype.start = function (mode, payload) {
    this.mode = mode;
    this.state = "live";
    if (mode === "archive" && payload) {
      this.live.loadGraph(payload.graph, payload.layout);
      var patches = (payload.layout && payload.layout.patches) || [];
      for (var p = 0; p < patches.length; p++) {
        this.live.applyLayout(patches[p].rows);
      }
      this.live.timeline = payload.timeline || null;
      var tl = this.live.timeline;
      var nodes = (tl && tl.nodes) || [];
      var edges = (tl && tl.edges) || [];
      var batches = (tl && tl.batches) || [];
      var totalMs = batches.length ? batches[batches.length - 1].ms : 0;
      this.replay = {
        batches: batches, nodes: nodes, edges: edges,
        nodeAt: 0, edgeAt: 0, idx: 0, elapsed: 0,
        scale: totalMs > 0 ? Math.min(1, 2200 / totalMs) : 1
      };
      this.ingestDone = true;
      this.layoutSettled = false;
      if (!batches.length) {
        this._revealAll();
        this.layoutSettled = true;
      }
      this.state = "settling";
      this.camera.fit(this._sampleAll(6000), {robust: true, duration: 700});
    } else {
      this.ingestDone = false;
      this.layoutSettled = false;
      this.state = "live";
      this._fitNext = true;
    }
    return this;
  };

  Director.prototype._sampleAll = function (limit) {
    var live = this.live;
    if (!live.n) return new Float32Array(0);
    var stride = Math.max(1, Math.ceil(live.n / limit));
    var out = new Float32Array(Math.ceil(live.n / stride) * 3);
    var k = 0;
    for (var i = 0; i < live.n; i += stride) {
      out[k++] = live.x[i] * this.renderer.SCALE;
      out[k++] = live.y[i] * this.renderer.SCALE;
      out[k++] = live.z[i] * this.renderer.SCALE;
    }
    return out.subarray(0, k);
  };

  Director.prototype._revealAll = function () {
    var live = this.live, i;
    for (i = 0; i < live.n; i++) live.revealed[i] = 1;
    for (i = 0; i < live.m; i++) live.eRevealed[i] = 1;
  };

  Director.prototype._advanceReplay = function () {
    var rp = this.replay;
    if (!rp) return;
    var reached = rp.elapsed * rp.scale;
    while (rp.idx < rp.batches.length && rp.batches[rp.idx].ms <= reached) {
      var b = rp.batches[rp.idx++];
      var nEnd = Math.min(rp.nodes.length, b.node_start + b.node_n);
      for (; rp.nodeAt < nEnd; rp.nodeAt++) {
        var ni = rp.nodes[rp.nodeAt];
        if (ni < this.live.n) this.live.revealed[ni] = 1;
      }
      var eEnd = Math.min(rp.edges.length, b.edge_start + b.edge_n);
      for (; rp.edgeAt < eEnd; rp.edgeAt++) {
        var ei = rp.edges[rp.edgeAt];
        if (ei < this.live.m) this.live.eRevealed[ei] = 1;
      }
    }
    if (rp.idx >= rp.batches.length) {
      this._revealAll();
      this.replay = null;
      this.layoutSettled = true;
    }
  };

  Director.prototype.onEvents = function (events) {
    if (!events) return this;
    if (!Array.isArray(events)) events = [events];
    var live = this.live;
    for (var k = 0; k < events.length; k++) {
      var ev = events[k];
      if (!ev || !ev.t) continue;
      var t = ev.t;
      if (t === "batch" && Array.isArray(ev.events)) {
        this.onEvents(ev.events);
        continue;
      }
      if (t === "node_added" || t === "node_updated" || t === "edge_added") {
        var n0 = live.n, m0 = live.m;
        live.applyBatch([ev]);
        for (var i = n0; i < live.n; i++) live.revealed[i] = 1;
        for (var e = m0; e < live.m; e++) live.eRevealed[e] = 1;
        if (live.n && !this._fittedOnce) this._fitNext = true;
      } else if (t === "layout_patch") {
        var rows = ev.rows || ev.patch || [];
        live.applyLayout(rows);
        for (var r = 0; r < rows.length; r++) {
          if (rows[r].i < live.n) live.revealed[rows[r].i] = 1;
        }
      } else if (t === "ingest_done") {
        this.ingestDone = true;
        this.state = "settling";
        this.camera.fitLive({robust: true, duration: 700});
      } else if (t === "settled") {
        this.layoutSettled = true;
      }
    }
    this._visKey = "";
    return this;
  };

  Director.prototype.setQuery = function (q) {
    this.query = String(q || "").trim().toLowerCase();
    this._visKey = "";
  };

  Director.prototype.setChapter = function (chapter) {
    this.chapter = this.chapter === chapter ? null : (chapter == null ? null : chapter);
    this._visKey = "";
    return this.chapter;
  };

  Director.prototype.focus = function (index) {
    if (index === this.focusIndex) index = -1;
    this.focusIndex = index;
    this._visKey = "";
    if (index >= 0) this.camera.focus(index);
    return this.focusIndex;
  };

  Director.prototype.clear = function () {
    this.query = "";
    this.chapter = null;
    this.focusIndex = -1;
    this._visKey = "";
  };

  Director.prototype.fit = function () {
    this.camera.fitLive({robust: true, duration: 650});
  };

  Director.prototype.switchScene = function (name) {
    if (!SK.scenes || !SK.scenes[name]) return;
    this.renderer.setScene(name);
    this.renderer.sync(this.live);
    this.camera.setAutoRotate(!this.camera.userInteracting);
    if (this.ui) this.ui.setSceneActive(name);
  };

  Director.prototype._recomputeDims = function () {
    var live = this.live;
    var vis = this._vis || (this._vis = new Uint8Array(live.cap));
    if (vis.length < live.n) vis = this._vis = new Uint8Array(live.cap);
    var focus = this.focusIndex;
    var nbr = null, focusEdges = null;
    if (focus >= 0 && focus < live.n) {
      nbr = new Set();
      focusEdges = new Set();
      nbr.add(focus);
      var adj = live.adjacency();
      for (var j = 0; j < adj[focus].length; j++) {
        nbr.add(adj[focus][j][0]);
        focusEdges.add(adj[focus][j][1]);
      }
    }
    var q = this.query, ch = this.chapter;
    for (var i = 0; i < live.n; i++) {
      var on = !!live.revealed[i];
      if (on && q && (live.titles[i] || "").toLowerCase().indexOf(q) < 0) on = false;
      if (on && ch != null && live.clusters[live._nodeCluster[i]] !== ch) on = false;
      vis[i] = on ? 1 : 0;
      live.dim[i] = !on ? 0 : (nbr ? (nbr.has(i) ? 1.2 : 0.07) : 1);
    }
    var kinds = live.kinds;
    for (var e = 0; e < live.m; e++) {
      var a = live.eSrc[e], b = live.eDst[e];
      var edgeOn = live.eRevealed[e] && vis[a] && vis[b];
      if (nbr) {
        live.eDim[e] = edgeOn && focusEdges.has(e) ? 1.7 : 0.035;
      } else {
        live.eDim[e] = edgeOn ? 1 : 0;
      }
      if (edgeOn && kinds[live.eKind[e]] === "hub") live.eDim[e] *= 0.6;
    }
    live.dimVersion++;
  };

  Director.prototype.status = function () {
    var live = this.live, i, revealed = 0, visible = 0;
    for (i = 0; i < live.n; i++) {
      if (live.revealed[i]) revealed++;
      if (live.dim[i] > 0.5) visible++;
    }
    return {nodes: live.n, edges: live.m, revealed: revealed, visible: visible,
            pending: live.pendingCount, state: this.state,
            scene: this.renderer.name, mode: this.mode,
            fps: Math.round(this.fps)};
  };

  Director.prototype._updateState = function () {
    if (this.state === "live" && this.ingestDone) this.state = "settling";
    if (this.state === "settling" && this.ingestDone && this.layoutSettled &&
        this.camera.settled) {
      this.state = "complete";
      if (!this.finaleDone) {
        this.finaleDone = true;
        if (this.mode === "archive") this.camera.pushIn(0.86);
      }
    }
  };

  Director.prototype.tick = function (dt) {
    this.time += dt;
    if (dt > 0) this.fps += (1 / dt - this.fps) * 0.05;
    if (this.replay) {
      this.replay.elapsed += dt * 1000;
      this._advanceReplay();
    }
    var key = [this.query, this.chapter, this.focusIndex, this.live.n, this.live.m,
               this.live.version, this.replay ? this.replay.idx : -1].join("|");
    if (key !== this._visKey) {
      this._visKey = key;
      this._recomputeDims();
    }
    if (this._fitNext && this.live.n) {
      this._fitNext = false;
      if (!this.camera.tween) {
        this.camera.fitLive({robust: true, duration: 500});
        this._fittedOnce = true;
      }
    }
    this.camera.update(dt);
    this.renderer.sync(this.live);
    this.renderer.render();
    if (this.time - this.labelTime > 0.18) {
      this.labelTime = this.time;
      this.labels.update(this.live, this.camera, {focus: this.focusIndex});
    }
    if (this.ui && this.time - this.statusTime > 0.3) {
      this.statusTime = this.time;
      this.ui.updateStatus(this.status());
    }
    this._updateState();
  };

  SK.Director = Director;
})(window.SK = window.SK || {});
