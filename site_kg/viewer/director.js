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
    this.state = "ingesting";
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
    this.runId = "";
    this.lastSeq = 0;
    this.batcher = null;
    this.transport = null;
    this.error = null;
    this.hiddenKinds = SK.defaultHiddenKinds();
    this.selection = null;
    this._selectionSet = null;
    this.archive = null;
    this._lodVersion = -1;
    this._revealedPrev = null;
    this._eRevealedPrev = null;
    this.recent = [];
    this.reducedMotion = !!(window.matchMedia &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  }

  Director.prototype.attach = function (ui) {
    this.ui = ui;
    return this;
  };

  Director.prototype.start = function (mode, payload) {
    this.mode = mode;
    this.state = "ingesting";
    this.finaleDone = false;
    this._lodVersion = -1;
    this.recent = [];
    if (this.renderer && this.renderer.resetFinale) this.renderer.resetFinale();
    if (this.ui && this.ui.hideFinale) this.ui.hideFinale();
    if (mode === "archive" && payload) {
      this.archive = payload;
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
        scale: totalMs > 2200 ? totalMs / 2200 : 1
      };
      this.ingestDone = true;
      this.layoutSettled = false;
      if (!batches.length) {
        this._revealAll();
        this.layoutSettled = true;
        this._mature();
      }
      this.state = "settling";
      this.camera.fit(this._sampleAll(6000), {robust: true, duration: 700});
    } else {
      this.ingestDone = false;
      this.layoutSettled = false;
      this.state = "ingesting";
      this._fitNext = true;
    }
    return this;
  };

  Director.prototype._sampleAll = function (limit) {
    var live = this.live;
    if (!live.n) return new Float32Array(0);
    /* The renderer may have re-placed nodes (planet shells); sample what is
       actually drawn, otherwise the camera frames the raw layout and clips
       the outermost shells. */
    if (this.renderer && this.renderer.samplePoints) {
      var placed = this.renderer.samplePoints(live, limit);
      if (placed.length) return placed;
    }
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

  /* Take the current reveal mask as the baseline, and make everything on it
     mature, so only later reveals animate. */
  Director.prototype._mature = function () {
    var live = this.live;
    if (this.renderer && this.renderer.resetBirth) this.renderer.resetBirth();
    this._revealedPrev = new Uint8Array(Math.max(live.cap, 1));
    this._eRevealedPrev = new Uint8Array(Math.max(live.capE, 1));
    for (var i = 0; i < live.n; i++) this._revealedPrev[i] = live.revealed[i];
    for (var e = 0; e < live.m; e++) this._eRevealedPrev[e] = live.eRevealed[e];
  };

  Director.prototype._noteNode = function (title, id) {
    var text = String(title == null || title === "" ? (id == null ? "" : id) : title);
    text = text.replace(/\s+/g, " ").trim();
    if (!text) return;
    this.recent.push(text);
    if (this.recent.length > 12) this.recent.shift();
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
      this._mature();
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
        if (t === "node_added") this._noteNode(ev.title, ev.id);
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
      } else if (t === "discover") {
        live.applyBatch([ev]);
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
    this.finaleDone = false;
    this._lodVersion = -1;
    if (this.renderer.resetFinale) this.renderer.resetFinale();
    if (this.ui && this.ui.hideFinale) this.ui.hideFinale();
    this._visKey = "";
    if (this.ui) this.ui.setSceneActive(name);
  };

  Director.prototype.toggleKind = function (kind) {
    kind = String(kind || "");
    if (!kind) return true;
    this.hiddenKinds[kind] = !this.hiddenKinds[kind];
    this._visKey = "";
    if (this.renderer) this.renderer.lastVersion = -1;
    return !!this.hiddenKinds[kind];
  };

  Director.prototype.skipToFinale = function () {
    this.ingestDone = true;
    this.layoutSettled = true;
    this.replay = null;
    this._revealAll();
    this._mature();
    this.state = "complete";
    if (this.camera && this.camera.settleNow) this.camera.settleNow();
    this._startFinale();
    return this;
  };

  Director.prototype.replayArchive = function () {
    if (this.mode !== "archive" || !this.archive) return false;
    this.start("archive", this.archive);
    return true;
  };

  Director.prototype._startFinale = function () {
    if (this.finaleDone) return;
    this.finaleDone = true;
    if (this.renderer && this.renderer.startFinale) {
      this.renderer.startFinale(this.reducedMotion);
    }
    if (this.camera && this.camera.pushIn && !this.reducedMotion) {
      this.camera.pushIn(0.9);
    }
    if (this.ui && this.ui.showFinale) {
      this.ui.showFinale(this.renderer.name, this.status());
    }
  };

  Director.prototype.connectEvents = function (runId, lastSeq, opts) {
    opts = opts || {};
    var self = this;
    var live = this.live;
    var cfg = (this.ui && this.ui.cfg) || {};
    var base = String(opts.base != null ? opts.base : (cfg.liveUrl || ""))
      .replace(/\/+$/, "");
    var ES = opts.EventSource ||
             (typeof EventSource === "function" ? EventSource : null);
    if (!ES) throw new Error("EventSource is not supported");
    var fetchFn = opts.fetch || (typeof fetch === "function" ? fetch : null);

    if (this.transport) this.transport.close();
    this.runId = String(runId || "");
    this.lastSeq = Number(lastSeq) || 0;
    this.error = null;

    var handle = {base: base, runId: this.runId, source: null, closed: false,
                  refreshing: false, retryTimer: null};
    var batcher = new SK.Batcher(live, {
      windowMs: opts.windowMs, maxNodes: opts.maxNodes, timer: opts.timer,
      lastSeq: this.lastSeq,
      onFlush: function (rec) { self.lastSeq = rec.lastSeq; onFlush(rec); }
    });
    this.batcher = batcher;
    handle.batcher = batcher;

    function resetRun(newRunId) {
      handle.runId = String(newRunId || "");
      self.runId = handle.runId;
      live.runId = handle.runId;
      live.reset(1024);
      batcher.reset(0);
      self.ingestDone = false;
      self.layoutSettled = false;
      self.finaleDone = false;
      self.state = "ingesting";
      self.error = null;
      self._fittedOnce = false;
      self._fitNext = true;
      self._visKey = "";
      self.recent = [];
      if (self._revealedPrev) self._revealedPrev.fill(0);
      if (self._eRevealedPrev) self._eRevealedPrev.fill(0);
    }

    function onFlush(rec) {
      var events = rec.events || [];
      for (var k = 0; k < events.length; k++) {
        var ev = events[k];
        if (ev.t === "layout_patch") {
          var rows = ev.rows || ev.patch || [];
          for (var r = 0; r < rows.length; r++) {
            if (rows[r].i < live.n) live.revealed[rows[r].i] = 1;
          }
        } else if (ev.t === "ingest_done") {
          self.ingestDone = true;
          self.state = "settling";
          if (self.camera && self.camera.fitLive) {
            self.camera.fitLive({robust: true, duration: 700});
          }
          /* Fit again once sync has placed the newest nodes in their shells. */
          self._fitNext = true;
        } else if (ev.t === "settled") {
          self.layoutSettled = true;
        } else if (ev.t === "error") {
          self.error = String(ev.error || "live run failed");
          self.state = "failed";
        }
        if (ev.t === "node_added") self._noteNode(ev.title, ev.id);
      }
      var d = rec.dirtyNodes || [0, 0];
      for (var i = d[0]; i < d[1]; i++) live.revealed[i] = 1;
      var de = rec.dirtyEdges || [0, 0];
      for (var e = de[0]; e < de[1]; e++) live.eRevealed[e] = 1;
      self._visKey = "";
      if (rec.nodesChanged && self.ui && self.ui._buildLegend) {
        self.ui._buildLegend();
      }
    }

    function applySnapshot(snap, health) {
      if (!snap) return;
      if (snap.run_id && handle.runId && String(snap.run_id) !== handle.runId) {
        resetRun(snap.run_id);
      } else if (!handle.runId && snap.run_id) {
        handle.runId = self.runId = String(snap.run_id);
      }
      live.loadSnapshot(snap);
      batcher.reset(snap.last_seq || 0);
      self.lastSeq = batcher.lastSeq;
      for (var i = 0; i < live.n; i++) live.revealed[i] = 1;
      for (var e = 0; e < live.m; e++) live.eRevealed[e] = 1;
      self._mature();
      var tail = Math.max(0, live.n - 5);
      for (var t = tail; t < live.n; t++) self._noteNode(live.titles[t], live.ids[t]);
      self._visKey = "";
      if (health && health.state === "complete") {
        self.ingestDone = true;
        self.layoutSettled = true;
        self.state = "complete";
      } else if (health && health.state === "failed") {
        self.state = "failed";
        self.error = String(health.error || self.error || "live run failed");
      }
      if (self.ui) {
        if (self.ui._buildLegend) self.ui._buildLegend();
        if (self.ui.setSceneActive) self.ui.setSceneActive(self.renderer.name);
      }
    }

    function getJSON(path) {
      if (!fetchFn) return Promise.reject(new Error("fetch is not available"));
      var init = {cache: "no-store"};
      if (opts.token) init.headers = {"X-Site-KG-Token": String(opts.token)};
      return fetchFn(base + path, init).then(function (res) {
        if (!res.ok) throw new Error(path + " failed: " + res.status);
        return res.json();
      });
    }

    function refresh() {
      return Promise.all([
        getJSON("/snapshot"),
        getJSON("/health").catch(function () { return null; })
      ]).then(function (res) {
        if (handle.closed) return;
        applySnapshot(res[0], res[1]);
        open(res[0].last_seq || batcher.lastSeq);
      });
    }

    function scheduleRetry() {
      if (handle.closed || handle.retryTimer != null) return;
      handle.retryTimer = setTimeout(function () {
        handle.retryTimer = null;
        recover();
      }, opts.retryMs == null ? 1500 : opts.retryMs);
      if (handle.retryTimer && handle.retryTimer.unref) {
        handle.retryTimer.unref();
      }
    }

    function recover() {
      if (handle.closed || handle.refreshing) return;
      handle.refreshing = true;
      if (handle.source) {
        try { handle.source.close(); } catch (err) { /* already closed */ }
        handle.source = null;
      }
      refresh().then(function () {
        handle.refreshing = false;
      }, function () {
        handle.refreshing = false;
        scheduleRetry();
      });
    }

    function onMessage(raw) {
      var ev;
      try {
        ev = JSON.parse(raw);
      } catch (err) {
        return;
      }
      if (!ev || !ev.t) return;
      if (ev.run_id && handle.runId && String(ev.run_id) !== handle.runId) {
        resetRun(ev.run_id);
        return;
      }
      batcher.push(ev);
    }

    function open(since) {
      if (handle.closed) return;
      if (handle.source) {
        try { handle.source.close(); } catch (err) { /* already closed */ }
      }
      var url = base + "/events?since=" + (since || 0);
      if (opts.token) url += "&token=" + encodeURIComponent(opts.token);
      var src = new ES(url);
      handle.source = src;
      src.onmessage = function (msg) { onMessage(msg && msg.data); };
      src.onerror = function () { recover(); };
    }

    handle.reconnect = function () { recover(); };
    handle.close = function () {
      if (handle.closed) return;
      handle.closed = true;
      if (handle.retryTimer != null) {
        clearTimeout(handle.retryTimer);
        handle.retryTimer = null;
      }
      if (handle.source) {
        try { handle.source.close(); } catch (err) { /* already closed */ }
        handle.source = null;
      }
      batcher.close();
      if (self.transport === handle) self.transport = null;
    };

    this.transport = handle;
    open(this.lastSeq);
    return handle;
  };

  Director.prototype._recomputeDims = function () {
    var live = this.live;
    var vis = this._vis || (this._vis = new Uint8Array(live.cap));
    if (vis.length < live.n) vis = this._vis = new Uint8Array(live.cap);
    /* Stamp first-seen times before anything else: the renderer's shaders grow
       a node or an edge over 0.45s from its birth, and reads the same clock. */
    var prev = this._revealedPrev;
    if (!prev || prev.length < live.cap) {
      var np = new Uint8Array(Math.max(live.cap, 1));
      if (prev) np.set(prev);
      prev = this._revealedPrev = np;
    }
    var eprev = this._eRevealedPrev;
    if (!eprev || eprev.length < live.capE) {
      var nep = new Uint8Array(Math.max(live.capE, 1));
      if (eprev) nep.set(eprev);
      eprev = this._eRevealedPrev = nep;
    }
    var r = this.renderer;
    for (var b = 0; b < live.n; b++) {
      if (live.revealed[b] && !prev[b] && r && r.markBirth) r.markBirth(b, this.time);
      prev[b] = live.revealed[b];
    }
    for (var be = 0; be < live.m; be++) {
      if (live.eRevealed[be] && !eprev[be] && r && r.markEdgeBirth) {
        r.markEdgeBirth(be, this.time);
      }
      eprev[be] = live.eRevealed[be];
    }
    var focus = this.focusIndex;
    var scene = (SK.scenes && SK.scenes[this.renderer.name]) || {};
    if (scene.ego && scene.select) {
      this.selection = scene.select(live, focus);
      this._selectionSet = new Set(this.selection);
    } else {
      this.selection = null;
      this._selectionSet = null;
    }
    this.renderer.selection = this.selection;
    this.renderer.hiddenKinds = this.hiddenKinds;
    var selected = this._selectionSet;
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
      if (on && selected && !selected.has(i)) on = false;
      if (on && q && (live.titles[i] || "").toLowerCase().indexOf(q) < 0) on = false;
      if (on && ch != null && live.clusters[live._nodeCluster[i]] !== ch) on = false;
      vis[i] = on ? 1 : 0;
      live.dim[i] = !on ? 0 : (nbr ? (nbr.has(i) ? 1.2 : 0.07) : 1);
    }
    var kinds = live.kinds;
    for (var e = 0; e < live.m; e++) {
      var a = live.eSrc[e], b = live.eDst[e];
      var kind = kinds[live.eKind[e]];
      var edgeOn = live.eRevealed[e] && vis[a] && vis[b] && !this.hiddenKinds[kind];
      if (nbr) {
        live.eDim[e] = edgeOn && focusEdges.has(e) ? 1.7 : 0.035;
      } else {
        live.eDim[e] = edgeOn ? 1 : 0;
      }
      if (edgeOn && kind === "hub") live.eDim[e] *= 0.6;
    }
    live.dimVersion++;
  };

  Director.prototype.status = function () {
    var live = this.live, i, revealed = 0, visible = 0;
    for (i = 0; i < live.n; i++) {
      if (live.revealed[i]) revealed++;
      if (live.dim[i] > 0.5) visible++;
    }
    return {nodes: live.n, edges: live.m, pages: live.pages, revealed: revealed,
            visible: visible, pending: live.pendingCount, state: this.state,
            discovered: live.discovered, discoverFailed: live.discoverFailed,
            error: this.error,
            scene: this.renderer.name, mode: this.mode, finale: this.finaleDone,
            ego: this.selection ? this.selection.length : 0,
            fps: Math.round(this.fps)};
  };

  Director.prototype._updateState = function () {
    if (this.state === "ingesting" && this.ingestDone) this.state = "settling";
    if (this.state === "settling" && this.ingestDone && this.layoutSettled &&
        this.camera.settled) {
      this.state = "complete";
    }
    if (this.state === "complete" && !this.finaleDone) this._startFinale();
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
    this.camera.update(dt);
    if (this.renderer) this.renderer.time = this.time;
    this.renderer.sync(this.live);
    if (this._fitNext && this.live.n && !this.camera.tween) {
      this._fitNext = false;
      this.camera.fitLive({robust: true, duration: 500});
      this._fittedOnce = true;
    }
    if (this.layoutSettled && this.renderer.prepareEdgeLod &&
        this._lodVersion !== this.live.version) {
      this.renderer.prepareEdgeLod();
      this._lodVersion = this.live.version;
    }
    if (this.renderer.advanceFinale) this.renderer.advanceFinale(dt);
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
