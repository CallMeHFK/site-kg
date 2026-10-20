/* LiveGraph + binary payload decoders. No DOM access. */
(function (SK) {
  "use strict";

  var PALETTE = [0x7cc4ff, 0x8b7bff, 0x4fe3ff, 0x43e0c8, 0xff6fb5, 0xa78bfa,
                 0xffc46b, 0x9fb8ff, 0x6ff0d8, 0xff9ad2, 0x5b9dff, 0xd8c2ff];
  var DERIVED = {mentions_symbol: 1, similar_to: 1, sibling_of: 1,
                 supersedes: 1, shares_entity: 1, semantic_similar: 1};

  SK.PALETTE = PALETTE;
  SK.DERIVED = DERIVED;

  function isDerived(kind) {
    return !!DERIVED[String(kind == null ? "" : kind)];
  }

  function defaultHiddenKinds() {
    var hidden = {};
    for (var kind in DERIVED) {
      if (Object.prototype.hasOwnProperty.call(DERIVED, kind)) hidden[kind] = true;
    }
    return hidden;
  }

  function kindLegend(kinds, hidden) {
    hidden = hidden || {};
    var seen = {}, rows = [];
    function add(kind) {
      kind = String(kind == null ? "" : kind);
      if (!kind || seen[kind]) return;
      seen[kind] = 1;
      rows.push({kind: kind, on: !hidden[kind], derived: isDerived(kind)});
    }
    add("ref");
    add("imports");
    (kinds || []).forEach(add);
    return rows;
  }

  SK.isDerived = isDerived;
  SK.defaultHiddenKinds = defaultHiddenKinds;
  SK.kindLegend = kindLegend;

  function LiveGraph(capacity) {
    this.palette = PALETTE.slice();
    this.reset(capacity || 1024);
  }

  LiveGraph.prototype.reset = function (capacity) {
    var cap = Math.max(16, capacity | 0);
    this.cap = cap;
    this.capE = cap;
    this.n = 0;
    this.m = 0;
    this.ids = [];
    this.titles = [];
    this.urls = [];
    this.clusters = [];
    this.clusterIndex = {};
    this.kinds = [];
    this.kindIndex = {};
    this.degree = new Uint32Array(cap);
    this.rank = new Float32Array(cap);
    this.x = new Float32Array(cap);
    this.y = new Float32Array(cap);
    this.z = new Float32Array(cap);
    this.depth = new Uint8Array(cap);
    this.dim = new Float32Array(cap);
    this.revealed = new Uint8Array(cap);
    this._nodeCluster = new Uint16Array(cap);
    this.indexById = new Map();
    this.eSrc = new Uint32Array(this.capE);
    this.eDst = new Uint32Array(this.capE);
    this.eKind = new Uint16Array(this.capE);
    this.eDim = new Float32Array(this.capE);
    this.eRevealed = new Uint8Array(this.capE);
    this.edgeKeys = new Set();
    this.pending = new Map();
    this.pendingCount = 0;
    this.dangling = 0;
    this.pages = 0;
    this.discovered = 0;
    this.discoverFailed = 0;
    this.version = 0;
    this.dimVersion = 0;
    this.graphHash = 0;
    this.timeline = null;
    this.runId = "";
    this.lastSeq = 0;
  };

  LiveGraph.prototype._growNodes = function () {
    var cap = Math.ceil(this.cap * 1.5);
    var pairs = [["degree", Uint32Array], ["rank", Float32Array],
                 ["x", Float32Array], ["y", Float32Array], ["z", Float32Array],
                 ["dim", Float32Array], ["revealed", Uint8Array],
                 ["depth", Uint8Array], ["_nodeCluster", Uint16Array]];
    for (var p = 0; p < pairs.length; p++) {
      var name = pairs[p][0], Ctor = pairs[p][1];
      var next = new Ctor(cap);
      next.set(this[name]);
      this[name] = next;
    }
    this.cap = cap;
  };

  LiveGraph.prototype._growEdges = function () {
    var cap = Math.ceil(this.capE * 1.5);
    var cols = [["eSrc", Uint32Array], ["eDst", Uint32Array],
                ["eKind", Uint16Array], ["eDim", Float32Array],
                ["eRevealed", Uint8Array]];
    for (var p = 0; p < cols.length; p++) {
      var name = cols[p][0], Ctor = cols[p][1];
      var next = new Ctor(cap);
      next.set(this[name]);
      this[name] = next;
    }
    this.capE = cap;
  };

  LiveGraph.prototype.clusterId = function (name) {
    name = String(name == null ? "" : name);
    var id = this.clusterIndex[name];
    if (id === undefined) {
      id = this.clusters.length;
      this.clusterIndex[name] = id;
      this.clusters.push(name);
    }
    return id;
  };

  LiveGraph.prototype.kindId = function (name) {
    name = String(name || "ref");
    var id = this.kindIndex[name];
    if (id === undefined) {
      id = this.kinds.length;
      this.kindIndex[name] = id;
      this.kinds.push(name);
    }
    return id;
  };

  LiveGraph.prototype.nodeAdded = function (id, rec, fromEvent) {
    id = String(id);
    rec = rec || {};
    // On event objects `t` is the event type ("node_updated"), never a title:
    // only snapshot-style records may use `t` as the title fallback.
    var titleFallback = fromEvent ? id : (rec.t != null ? rec.t : id);
    var i = this.indexById.get(id);
    if (i !== undefined) {
      if (rec.title != null || (!fromEvent && rec.t != null)) this.titles[i] = String(rec.title != null ? rec.title : rec.t);
      if (rec.url != null || rec.u != null) this.urls[i] = String(rec.url != null ? rec.url : rec.u);
      if (rec.c != null) this._nodeCluster[i] = this.clusterId(rec.c);
      if (rec.g != null) this.degree[i] = rec.g | 0;
      if (rec.r != null) this.rank[i] = +rec.r || 0;
      this.version++;
      return i;
    }
    if (this.n === this.cap) this._growNodes();
    i = this.n++;
    this.ids[i] = id;
    this.titles[i] = String(rec.title != null ? rec.title : titleFallback);
    this.urls[i] = String(rec.url != null ? rec.url : (rec.u != null ? rec.u : ""));
    var cluster = rec.c == null ? "" : String(rec.c);
    this._nodeCluster[i] = this.clusterId(cluster);
    this.degree[i] = rec.g | 0;
    this.rank[i] = +rec.r || 0;
    this.x[i] = +rec.x || 0;
    this.y[i] = +rec.y || 0;
    this.z[i] = +rec.z || 0;
    this.depth[i] = rec.d == null ? 0 : rec.d & 255;
    this.dim[i] = 1;
    this.revealed[i] = 0;
    this.indexById.set(id, i);
    this.version++;
    this._resolvePending(id);
    return i;
  };

  LiveGraph.prototype._bufferEdge = function (e) {
    var miss = this.indexById.has(e.s) ? e.d : e.s;
    var list = this.pending.get(miss);
    if (!list) {
      list = [];
      this.pending.set(miss, list);
    }
    list.push({s: e.s, d: e.d, kind: e.kind, late: e.late || 0});
    this.pendingCount++;
    this.dangling = this.pendingCount;
  };

  LiveGraph.prototype._appendEdge = function (si, di, kind) {
    var key = si + "|" + di + "|" + kind;
    if (this.edgeKeys.has(key)) return -1;
    if (this.m === this.capE) this._growEdges();
    var ei = this.m++;
    this.eSrc[ei] = si;
    this.eDst[ei] = di;
    this.eKind[ei] = this.kindId(kind);
    this.eDim[ei] = 1;
    this.eRevealed[ei] = 0;
    this.edgeKeys.add(key);
    this.version++;
    return ei;
  };

  LiveGraph.prototype.edgeAdded = function (s, d, kind, late) {
    s = String(s);
    d = String(d);
    kind = String(kind || "ref");
    var si = this.indexById.get(s);
    var di = this.indexById.get(d);
    if (si === undefined || di === undefined) {
      this._bufferEdge({s: s, d: d, kind: kind, late: late || 0});
      return -1;
    }
    return this._appendEdge(si, di, kind);
  };

  LiveGraph.prototype._resolvePending = function (id) {
    var list = this.pending.get(id);
    if (!list) return;
    this.pending.delete(id);
    for (var k = 0; k < list.length; k++) {
      var e = list[k];
      this.pendingCount--;
      var si = this.indexById.get(e.s);
      var di = this.indexById.get(e.d);
      if (si === undefined || di === undefined) this._bufferEdge(e);
      else this._appendEdge(si, di, e.kind);
    }
    this.dangling = this.pendingCount;
  };

  /* Pages crawl counts exceed node counts (frames without graph nodes, skips);
     fold the max so the counter never regresses. */
  LiveGraph.prototype._foldPages = function (value) {
    var pages = value | 0;
    if (pages > this.pages) this.pages = pages;
  };

  LiveGraph.prototype.applyBatch = function (events) {
    if (!events) return this;
    for (var k = 0; k < events.length; k++) {
      var ev = events[k];
      if (!ev || !ev.t) continue;
      if (ev.t === "node_added" || ev.t === "node_updated") {
        this.nodeAdded(ev.id, ev, true);
      } else if (ev.t === "edge_added") {
        this.edgeAdded(ev.s, ev.d, ev.kind, ev.late);
      } else if (ev.t === "progress") {
        if (ev.pages != null) this._foldPages(ev.pages);
      } else if (ev.t === "discover") {
        /* discovery is monotonic within a run: a late/duplicate frame from a
           reconnect can never make the HUD count down */
        if (ev.found != null) {
          this.discovered = Math.max(this.discovered, ev.found | 0);
        }
        if (ev.failed != null) {
          this.discoverFailed = Math.max(this.discoverFailed, ev.failed | 0);
        }
      } else if (ev.t === "ingest_done") {
        this.dangling = this.pendingCount;
        if (ev.pages != null) this._foldPages(ev.pages);
      }
    }
    return this;
  };

  LiveGraph.prototype.loadGraph = function (graph, layout) {
    var nodes = graph.nodes || [];
    this.reset(Math.max(64, nodes.length + 8));
    for (var i = 0; i < nodes.length; i++) {
      var nd = nodes[i];
      this.nodeAdded(nd.i, nd);
      var pos = layout && layout.nodes ? layout.nodes[i] : null;
      if (pos) {
        this.x[i] = pos.x;
        this.y[i] = pos.y;
        this.z[i] = pos.z;
        this.depth[i] = pos.d == null ? 0 : pos.d & 255;
      }
    }
    var edges = graph.edges || [];
    for (var j = 0; j < edges.length; j++) {
      this.edgeAdded(edges[j].s, edges[j].t, edges[j].k);
    }
    this.graphHash = graph.hash || 0;
    this.pages = this.n;
    this.version++;
    return this;
  };

  LiveGraph.prototype.loadSnapshot = function (snap) {
    var nodes = snap.nodes || [];
    var prevPages = this.pages; /* pages survives reset: never regress */
    var prevDiscovered = this.discovered;
    var prevFailed = this.discoverFailed;
    this.reset(Math.max(64, nodes.length + 8));
    this.discovered = prevDiscovered;
    this.discoverFailed = prevFailed;
    var rows = (snap.layout && snap.layout.nodes) || [];
    var byIndex = {};
    for (var r = 0; r < rows.length; r++) byIndex[rows[r].i] = rows[r];
    for (var i = 0; i < nodes.length; i++) {
      var nd = nodes[i];
      var index = this.nodeAdded(nd.i, nd);
      var pos = byIndex[index];
      if (pos) {
        this.x[index] = +pos.x || 0;
        this.y[index] = +pos.y || 0;
        this.z[index] = +pos.z || 0;
        this.depth[index] = pos.d == null ? 0 : pos.d & 255;
      }
    }
    var edges = snap.edges || [];
    for (var e = 0; e < edges.length; e++) {
      this.edgeAdded(edges[e].s, edges[e].t, edges[e].k, edges[e].late);
    }
    this.runId = String(snap.run_id || "");
    this.lastSeq = snap.last_seq || 0;
    this.dangling = snap.dangling == null ? this.pendingCount : snap.dangling;
    /* A reconnect snapshot carries fewer nodes than pages crawled (skipped
       frames etc.); keep the server's real page count when it has one, and
       never step backwards from what the stream already reported. */
    this.pages = Math.max(this.n, prevPages,
                          (snap.progress && snap.progress.pages) | 0);
    this.version++;
    return this;
  };

  LiveGraph.prototype.applyLayout = function (rows) {
    if (!rows) return;
    for (var k = 0; k < rows.length; k++) {
      var row = rows[k];
      var i = row.i;
      if (i === undefined || i < 0 || i >= this.n) continue;
      this.x[i] = row.x;
      this.y[i] = row.y;
      this.z[i] = row.z;
    }
    this.version++;
  };

  LiveGraph.prototype.adjacency = function () {
    if (this._adj && this._adjVersion === this.version) return this._adj;
    var adj = new Array(this.n);
    for (var i = 0; i < this.n; i++) adj[i] = [];
    for (var e = 0; e < this.m; e++) {
      var a = this.eSrc[e], b = this.eDst[e];
      adj[a].push([b, e]);
      adj[b].push([a, e]);
    }
    this._adj = adj;
    this._adjVersion = this.version;
    return adj;
  };

  LiveGraph.prototype.stats = function () {
    return {nodes: this.n, edges: this.m, pending: this.pendingCount,
            dangling: this.dangling, discovered: this.discovered,
            discoverFailed: this.discoverFailed};
  };

  SK.LiveGraph = LiveGraph;

  function Batcher(live, opts) {
    opts = opts || {};
    this.live = live;
    this.windowMs = opts.windowMs == null ? 300 : opts.windowMs;
    this.maxNodes = opts.maxNodes == null ? 100 : opts.maxNodes;
    this.onFlush = opts.onFlush || null;
    this._now = opts.now || function () { return Date.now(); };
    this._timerOff = opts.timer === false;
    /* Paced mode drains the queue over time instead of applying whatever
       arrived in the window. A local crawl can finish in under a second, and
       without this the whole graph appears between two frames: the build looks
       like "finding pages" followed by the finished scene. The rate is derived
       from the discovered page count so the growth is legible for a small
       site and still bounded for a 75k-page one. */
    this.paced = !!opts.paced;
    this.nodeCost = opts.nodeCost == null ? 6 : opts.nodeCost;
    this.maxBatch = opts.maxBatch == null ? 4000 : opts.maxBatch;
    /* One node's worth of credit to begin with so the very first world lands on
       the first tick instead of after a visible empty beat. */
    this._credit = this.nodeCost;
    this.queue = [];
    this._seqs = new Set();
    this.nodeCount = 0;
    this.skipped = 0;
    this.lastSeq = opts.lastSeq || 0;
    this.lastFlushAt = this._now();
    this.dirtyNodes = [live.n, live.n];
    this.dirtyEdges = [live.m, live.m];
    this.timer = null;
  }

  Batcher.prototype.reset = function (lastSeq) {
    if (this.timer != null) {
      clearTimeout(this.timer);
      this.timer = null;
    }
    this.queue = [];
    this._seqs.clear();
    this.nodeCount = 0;
    this._credit = this.nodeCost;
    this.lastSeq = lastSeq || 0;
    this.lastFlushAt = this._now();
    this.dirtyNodes = [this.live.n, this.live.n];
    this.dirtyEdges = [this.live.m, this.live.m];
  };

  Batcher.prototype.close = function () {
    this.reset(this.lastSeq);
    this.onFlush = null;
  };

  Batcher.prototype._arm = function () {
    if (this._timerOff || this.timer != null || !this.queue.length) return;
    if (typeof setTimeout !== "function") return;
    var self = this;
    var delay = Math.max(0, this.windowMs - (this._now() - this.lastFlushAt));
    this.timer = setTimeout(function () {
      self.timer = null;
      self.flush();
    }, delay);
    if (this.timer && typeof this.timer.unref === "function") this.timer.unref();
  };

  /* Safety net for throttled timers: a background tab can starve setTimeout,
     which used to leave the queue full and the build frozen mid-growth. The
     render loop calls pump() every frame, so a pending backlog always drains
     while the scene is alive. */
  Batcher.prototype.pump = function () {
    if (!this.queue.length) return null;
    /* An armed timer is allowed to run late by a few windows before the render
       loop steps in: four windows is 400ms, far past any scheduling jitter but
       short enough that a throttled tab never looks frozen. */
    if (this.timer != null && this._now() - this.lastFlushAt < this.windowMs * 4) {
      return null;
    }
    return this.flush();
  };

  Batcher.prototype.push = function (ev, now) {
    if (!ev) return false;
    if (ev.t === "batch" && Array.isArray(ev.events)) {
      var any = false;
      for (var b = 0; b < ev.events.length; b++) {
        if (this.push(ev.events[b], now)) any = true;
      }
      return any;
    }
    if (ev.seq != null) {
      if (ev.seq <= this.lastSeq || this._seqs.has(ev.seq)) {
        this.skipped++;
        return false;
      }
      this._seqs.add(ev.seq);
    }
    /* Ambient counters (pages discovered, progress, skipped pages) carry no
       geometry and must not wait behind a paced node backlog: the build panel
       would otherwise report a zero page count while the graph is visibly
       growing. */
    if (this.paced && (ev.t === "discover" || ev.t === "progress"
                       || ev.t === "node_skipped")) {
      this.live.applyBatch([ev]);
      return true;
    }
    this.queue.push(ev);
    if (ev.t === "node_added" || ev.t === "node_updated") this.nodeCount++;
    now = now == null ? this._now() : now;
    if ((!this.paced && this.nodeCount >= this.maxNodes) ||
        now - this.lastFlushAt >= this.windowMs) {
      this.flush(now);
      return true;
    }
    this._arm();
    return false;
  };

  /* Cost of one event in pacing units: a node is a much bigger visual beat
     than a single edge, so nodes arrive at a readable cadence while links
     stream between them. Episode-level events are free. */
  Batcher.prototype._units = function (ev) {
    if (ev.t === "node_added" || ev.t === "node_updated") return this.nodeCost;
    if (ev.t === "edge_added") return 1;
    return 0;
  };

  Batcher.prototype._rate = function () {
    var found = this.live && this.live.discovered ? this.live.discovered : 0;
    /* Roughly one page per second: a 40-page crawl unfolds over ~7s, a
       2000-page one over the same time, and a 75k-page one is capped at
       ~19s. The floor keeps the first few pages from crawling while the
       discover count is still zero. */
    return Math.min(24000, Math.max(30, found));
  };

  Batcher.prototype.flush = function (now, force) {
    if (this.timer != null) {
      clearTimeout(this.timer);
      this.timer = null;
    }
    if (!this.queue.length) {
      this.lastFlushAt = now == null ? this._now() : now;
      this._credit = 0;
      return null;
    }
    now = now == null ? this._now() : now;
    var dt = now - this.lastFlushAt;
    /* A full batch flushes immediately; paced mode only flushes on the clock. */
    var byCount = !this.paced && this.nodeCount >= this.maxNodes;
    if (!force && !byCount && dt < this.windowMs) {
      this._arm();
      return null;
    }
    var take = this.queue.length;
    if (!force && !byCount && this.paced) {
      var rate = this._rate();
      this._credit += rate * Math.min(dt, 1000) / 1000;
      /* Never let a long frame bank a huge burst; two nodes is the floor so a
         short stall does not swallow the next beat entirely. */
      var cap = Math.max(this.nodeCost * 2, rate * 0.15);
      if (this._credit > cap) this._credit = cap;
      var units = 0;
      take = 0;
      while (take < this.queue.length && take < this.maxBatch) {
        var cost = this._units(this.queue[take]);
        if (cost > 0 && units + cost > this._credit) break;
        units += cost;
        take++;
      }
      if (!take) {
        this.lastFlushAt = now;
        this._arm();
        return null;
      }
      this._credit -= units;
    }
    var q = this.queue.slice(0, take);
    q.sort(function (a, b) {
      var as = a.seq == null ? Number.MAX_SAFE_INTEGER : a.seq;
      var bs = b.seq == null ? Number.MAX_SAFE_INTEGER : b.seq;
      return as - bs;
    });
    var live = this.live;
    var n0 = live.n, m0 = live.m;
    var nodeLo = Infinity, nodeHi = -Infinity;
    var edgeLo = Infinity, edgeHi = -Infinity;
    var events = [];
    var appliedNodes = 0;
    for (var k = 0; k < q.length; k++) {
      var ev = q[k], t = ev.t;
      if (t === "node_added" || t === "node_updated") {
        appliedNodes++;
        var idx = live.indexById.get(String(ev.id));
        if (idx !== undefined) {
          if (idx < nodeLo) nodeLo = idx;
          if (idx + 1 > nodeHi) nodeHi = idx + 1;
        }
        live.applyBatch([ev]);
      } else if (t === "edge_added") {
        live.applyBatch([ev]);
      } else if (t === "layout_patch") {
        var rows = ev.rows || ev.patch || [];
        live.applyLayout(rows);
        for (var r = 0; r < rows.length; r++) {
          var ri = rows[r].i;
          if (ri >= 0 && ri < live.n) {
            if (ri < nodeLo) nodeLo = ri;
            if (ri + 1 > nodeHi) nodeHi = ri + 1;
          }
        }
      } else {
        live.applyBatch([ev]);
      }
      if (ev.seq != null) {
        if (ev.seq > this.lastSeq) this.lastSeq = ev.seq;
        if (this.paced) this._seqs.delete(ev.seq);
      }
      events.push(ev);
    }
    if (live.n > n0) {
      if (n0 < nodeLo) nodeLo = n0;
      if (live.n > nodeHi) nodeHi = live.n;
    }
    if (!isFinite(nodeLo)) nodeLo = nodeHi = live.n;
    if (live.m > m0) {
      edgeLo = m0;
      edgeHi = live.m;
    } else {
      edgeLo = edgeHi = live.m;
    }
    this.queue = this.queue.slice(take);
    if (!this.paced) this._seqs.clear();
    this.nodeCount -= appliedNodes;
    if (this.nodeCount < 0) this.nodeCount = 0;
    this.lastFlushAt = now;
    this.dirtyNodes = [nodeLo, nodeHi];
    this.dirtyEdges = [edgeLo, edgeHi];
    var rec = {
      nodes: live.n, edges: live.m,
      applied: events.length, skipped: this.skipped,
      dirtyNodes: this.dirtyNodes, dirtyEdges: this.dirtyEdges,
      nodeStart: n0, edgeStart: m0,
      nodesChanged: live.n > n0, edgesChanged: live.m > m0,
      lastSeq: this.lastSeq, events: events
    };
    if (this.queue.length) this._arm();
    if (this.onFlush) this.onFlush(rec);
    return rec;
  };

  SK.Batcher = Batcher;

  function decodeGraph(input) {
    var u8 = input;
    var dv = new DataView(u8.buffer, u8.byteOffset, u8.byteLength);
    var magic = String.fromCharCode(u8[0], u8[1], u8[2], u8[3]);
    if (magic !== "SKGG") throw new Error("bad graph payload magic " + magic);
    if (dv.getUint16(4, true) !== 1) throw new Error("unsupported graph payload version");
    var n = dv.getUint32(8, true), e = dv.getUint32(12, true);
    var nc = dv.getUint32(16, true), nk = dv.getUint32(20, true);
    var poolLen = dv.getUint32(24, true), hash = dv.getUint32(28, true);
    var pos = 32, refs = [], i;
    for (i = 0; i < nc; i++) {
      refs.push([dv.getUint32(pos, true), dv.getUint32(pos + 4, true)]);
      pos += 12;
    }
    var kindRefs = [];
    for (i = 0; i < nk; i++) {
      kindRefs.push([dv.getUint32(pos, true), dv.getUint32(pos + 4, true)]);
      pos += 8;
    }
    var pool = u8.subarray(pos, pos + poolLen);
    pos += poolLen;
    var dec = new TextDecoder();
    function str(off, len) { return dec.decode(pool.subarray(off, off + len)); }
    var clusters = refs.map(function (r) { return str(r[0], r[1]); });
    var kinds = kindRefs.map(function (r) { return str(r[0], r[1]); });
    var nodes = [];
    for (i = 0; i < n; i++) {
      var io = dv.getUint32(pos, true), il = dv.getUint32(pos + 4, true);
      var to = dv.getUint32(pos + 8, true), tl = dv.getUint32(pos + 12, true);
      var uo = dv.getUint32(pos + 16, true), ul = dv.getUint32(pos + 20, true);
      var cluster = dv.getUint16(pos + 24, true);
      var degree = dv.getUint32(pos + 28, true);
      var rank = dv.getFloat32(pos + 32, true);
      pos += 40;
      nodes.push({i: str(io, il), c: clusters[cluster], t: str(to, tl),
                  u: str(uo, ul), g: degree, r: rank});
    }
    var edges = [];
    for (i = 0; i < e; i++) {
      var src = dv.getUint32(pos, true), dst = dv.getUint32(pos + 4, true);
      var kind = dv.getUint16(pos + 8, true);
      pos += 16;
      edges.push({s: nodes[src].i, t: nodes[dst].i, k: kinds[kind]});
    }
    return {nodes: nodes, edges: edges, clusters: clusters, kinds: kinds, hash: hash};
  }

  function decodeLayout(input) {
    var u8 = input;
    var dv = new DataView(u8.buffer, u8.byteOffset, u8.byteLength);
    var magic = String.fromCharCode(u8[0], u8[1], u8[2], u8[3]);
    if (magic !== "SKGL") throw new Error("bad layout payload magic " + magic);
    if (dv.getUint16(4, true) !== 1) throw new Error("unsupported layout payload version");
    var n = dv.getUint32(8, true), nPatches = dv.getUint32(12, true);
    var scale = dv.getFloat32(16, true);
    if (!(scale > 0)) throw new Error("layout scale must be positive");
    var pos = 24, nodes = [], i, k;
    for (i = 0; i < n; i++) {
      nodes.push({x: dv.getInt16(pos, true) / scale,
                  y: dv.getInt16(pos + 2, true) / scale,
                  z: dv.getInt16(pos + 4, true) / scale,
                  c: dv.getUint16(pos + 6, true),
                  d: dv.getUint8(pos + 8)});
      pos += 10;
    }
    var patches = [];
    for (i = 0; i < nPatches; i++) {
      var seq = dv.getUint32(pos, true), ms = dv.getUint32(pos + 4, true);
      var rowsN = dv.getUint16(pos + 8, true);
      pos += 12;
      var rows = [];
      for (k = 0; k < rowsN; k++) {
        rows.push({i: dv.getUint32(pos, true),
                   x: dv.getInt16(pos + 4, true) / scale,
                   y: dv.getInt16(pos + 6, true) / scale,
                   z: dv.getInt16(pos + 8, true) / scale});
        pos += 12;
      }
      patches.push({seq: seq, ms: ms, rows: rows});
    }
    var final = {seq: dv.getUint32(pos, true), nodes: dv.getUint32(pos + 8, true)};
    return {nodes: nodes, patches: patches, final: final, scale: scale};
  }

  function decodeTimeline(input) {
    var u8 = input;
    var dv = new DataView(u8.buffer, u8.byteOffset, u8.byteLength);
    var magic = String.fromCharCode(u8[0], u8[1], u8[2], u8[3]);
    if (magic !== "SKGT") throw new Error("bad timeline payload magic " + magic);
    if (dv.getUint16(4, true) !== 1) throw new Error("unsupported timeline payload version");
    var nNodes = dv.getUint32(8, true), nEdges = dv.getUint32(12, true);
    var nBatches = dv.getUint32(16, true), idLen = dv.getUint32(20, true);
    var pos = 32;
    var runId = new TextDecoder().decode(u8.subarray(pos, pos + idLen));
    pos += idLen;
    var nodes = [], edges = [], i;
    for (i = 0; i < nNodes; i++) {
      nodes.push(dv.getUint32(pos, true));
      pos += 4;
    }
    for (i = 0; i < nEdges; i++) {
      edges.push(dv.getUint32(pos, true));
      pos += 4;
    }
    var batches = [];
    for (i = 0; i < nBatches; i++) {
      batches.push({seq: dv.getUint32(pos, true), ms: dv.getUint32(pos + 4, true),
                    node_start: dv.getUint32(pos + 8, true),
                    edge_start: dv.getUint32(pos + 12, true),
                    node_n: dv.getUint16(pos + 16, true),
                    edge_n: dv.getUint16(pos + 18, true)});
      pos += 20;
    }
    return {run_id: runId, nodes: nodes, edges: edges, batches: batches};
  }

  function splitArchive(u8) {
    var dv = new DataView(u8.buffer, u8.byteOffset, u8.byteLength);
    var magic = String.fromCharCode(u8[0], u8[1], u8[2], u8[3]);
    if (magic !== "SKGA") throw new Error("bad archive payload magic " + magic);
    var gl = dv.getUint32(4, true), ll = dv.getUint32(8, true);
    var tl = dv.getUint32(12, true), pos = 16;
    return {
      graph: u8.subarray(pos, pos + gl),
      layout: u8.subarray(pos + gl, pos + gl + ll),
      timeline: u8.subarray(pos + gl + ll, pos + gl + ll + tl)
    };
  }

  /* Feel of a dragged cell. ``damping`` is a fraction of critical (2*sqrt(k)),
     so the values stay meaningful if the stiffness changes. Under 1 means the
     cell overshoots on release and rings out. */
  SK.DRAG_SPRING = {stiff: 190, damping: 0.62, followStiff: 120, followDamping: 0.54};

  /* One step of a damped spring, shared by the per-node drag. Kept here so the
     feel is testable without a WebGL context: callers own position/velocity
     and get the next [position, velocity]. Underdamped by default, so a
     released node overshoots its last target slightly and settles back
     instead of snapping into place. */
  SK.springStep = function (pos, vel, target, stiffness, damping, dt) {
    var accel = (target - pos) * stiffness - vel * damping;
    var next = vel + accel * dt;
    return [pos + next * dt, next];
  };

  SK.decodeGraph = decodeGraph;
  SK.decodeLayout = decodeLayout;
  SK.decodeTimeline = decodeTimeline;
  SK.splitArchive = splitArchive;

  SK.decodeArchiveBuffer = function (u8) {
    var parts = splitArchive(u8);
    return {graph: decodeGraph(parts.graph), layout: decodeLayout(parts.layout),
            timeline: decodeTimeline(parts.timeline)};
  };

  SK.decodeArchiveParts = function (g, l, t) {
    return {graph: decodeGraph(g), layout: decodeLayout(l), timeline: decodeTimeline(t)};
  };

  SK.inflateBase64 = function (text) {
    var bin = atob(String(text || "").trim());
    var u8 = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
    if (typeof DecompressionStream === "undefined") {
      return Promise.reject(new Error("DecompressionStream is not supported"));
    }
    var stream = new Blob([u8]).stream().pipeThrough(new DecompressionStream("gzip"));
    return new Response(stream).arrayBuffer().then(function (ab) {
      return new Uint8Array(ab);
    });
  };
})(window.SK = window.SK || {});
