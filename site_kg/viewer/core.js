/* LiveGraph + binary payload decoders. No DOM access. */
(function (SK) {
  "use strict";

  /* Fallback only: each scene carries its own palette. This is the planets
     set so a scene-less viewer still looks like a star system rather than a
     bag of neon sweets. */
  var PALETTE = [0xc87358, 0x9db4d0, 0x74b4ba, 0xe8c07d, 0xe6d5b0, 0xb097c6,
                 0xf2e6cf, 0x5b90d2, 0xb3656a, 0x8396c4, 0xd8a35f, 0xaab7a4];
  var DERIVED = {mentions_symbol: 1, similar_to: 1, sibling_of: 1,
                 supersedes: 1, shares_entity: 1, semantic_similar: 1,
                 shares_heading: 1, shares_term: 1, duplicate_of: 1,
                 coupled_with: 1, co_cited_with: 1};

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
    /* Prose volume per page: the one number that says how much data a node
       carries, as opposed to how important the graph thinks it is. The
       scenes read it for window light, soma size and world radius. */
    this.words = new Uint32Array(cap);
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
                 ["words", Uint32Array],
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
      if (rec.w != null) this.words[i] = rec.w | 0;
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
    this.words[i] = rec.w == null ? 0 : (rec.w | 0);
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
      var words = dv.getUint32(pos + 36, true);
      pos += 40;
      nodes.push({i: str(io, il), c: clusters[cluster], t: str(to, tl),
                  u: str(uo, ul), g: degree, r: rank, w: words});
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
     cell overshoots on release and rings out.

     ``recoil`` is the anchored variant: a stiffer spring with much less
     damping, because the cell has to visibly bounce back to its home rather
     than settle where the hand left it. */
  SK.DRAG_SPRING = {stiff: 190, damping: 0.62, followStiff: 120, followDamping: 0.54};
  SK.DRAG_SPRING.recoil = {stiff: 230, damping: 0.36,
                           followStiff: 150, followDamping: 0.30};

  /* Deterministic [0,1) noise, no WebGL needed so the shape can be unit
     tested in node. hashSeed takes a number, hashString takes an id, and
     hash3i is the lattice hash the value noise is built on. */
  SK.hashSeed = function (n) {
    var x = Math.imul(Math.round(n * 1000) | 0, 374761393);
    x = Math.imul(x ^ (x >>> 13), 1274126177);
    x = x ^ (x >>> 16);
    return (x >>> 0) / 4294967296;
  };

  SK.hashString = function (s) {
    var h = 2166136261;
    for (var i = 0; i < s.length; i++) {
      h ^= s.charCodeAt(i);
      h = Math.imul(h, 16777619);
    }
    return (h >>> 0) / 4294967296;
  };

  SK.hash3i = function (x, y, z) {
    var n = Math.imul(x | 0, 374761393) + Math.imul(y | 0, 668265263) +
            Math.imul(z | 0, 1274126177);
    n = Math.imul(n ^ (n >>> 13), 1274126177);
    n = n ^ (n >>> 16);
    return (n >>> 0) / 4294967296;
  };

  /* Value noise on a unit lattice, smoothstepped so the folds bend instead of
     stepping. Returns [0,1]. */
  SK.noise3 = function (x, y, z) {
    var xi = Math.floor(x), yi = Math.floor(y), zi = Math.floor(z);
    var xf = x - xi, yf = y - yi, zf = z - zi;
    var u = xf * xf * (3 - 2 * xf), v = yf * yf * (3 - 2 * yf),
        w = zf * zf * (3 - 2 * zf);
    var c000 = SK.hash3i(xi, yi, zi), c100 = SK.hash3i(xi + 1, yi, zi);
    var c010 = SK.hash3i(xi, yi + 1, zi), c110 = SK.hash3i(xi + 1, yi + 1, zi);
    var c001 = SK.hash3i(xi, yi, zi + 1), c101 = SK.hash3i(xi + 1, yi, zi + 1);
    var c011 = SK.hash3i(xi, yi + 1, zi + 1), c111 = SK.hash3i(xi + 1, yi + 1, zi + 1);
    var x00 = c000 + (c100 - c000) * u, x10 = c010 + (c110 - c010) * u;
    var x01 = c001 + (c101 - c001) * u, x11 = c011 + (c111 - c011) * u;
    var y0 = x00 + (x10 - x00) * v, y1 = x01 + (x11 - x01) * v;
    return y0 + (y1 - y0) * w;
  };

  /* Fractal noise, normalised back to [0,1]. Two or three octaves are enough
     for gyri; more only costs time in the rebuild loop. */
  SK.fbm3 = function (x, y, z, oct) {
    var sum = 0, amp = 0.5, freq = 1, norm = 0;
    var n = oct || 3;
    for (var o = 0; o < n; o++) {
      sum += amp * SK.noise3(x * freq, y * freq, z * freq);
      norm += amp;
      amp *= 0.5;
      freq *= 2.07;
    }
    return sum / norm;
  };

  /* ---- the brain -------------------------------------------------------------
     The neural scene is tissue, not a constellation. The graph's nodes are
     neurons, and neurons do not float in space: they sit on a cortex, and the
     cortex has the shape of a head.

     The head is a radial function: for a direction, the distance to the skin.
     A sheared ellipsoid gives the cerebrum, tilted so the frontal pole sits
     lower than the back of the head the way a real one does, and long front
     to back the way a brain is: an egg is the one shape it must not be.
     Three things make that ellipsoid a brain rather than a ball:

     - the basal plane. A cerebrum rests on the floor of the skull, so under
       a plane in the head's own frame the surface is rolled flat instead of
       hanging round. The join is a smooth minimum, so the corner is a fillet
       and the normals stay continuous across it;
     - the interhemispheric fissure. A narrow, deep cleft on the midline,
       carved out of the volume and not out of the folds, so the two
       hemispheres are separate from the first frame of the build. This is
       what the eye reads as "two hemispheres"; a groove painted on a ball
       does not do it;
     - the lobes. The temporal lobe swells each side under the Sylvian
       fissure, the cerebellum hangs under the occiput as its own mass, and
       the brainstem drops from the midline, a little posterior. Bumps are
       added to the radius rather than unioned as separate solids, because a
       union leaves a crease where two solids meet and a crease reads as a
       crack in the tissue from every angle.

     The folds are carved into the same radius. Two families of noise make
     the gyri: the zero contour of a smooth field is the sulcus, and the
     ground between two of them is the gyrus. Two grooves running the way the
     central and Sylvian fissures do divide the lobes. The gyri belong to the
     cerebrum: the cerebellum keeps fine horizontal folia instead, and the
     stalk is smooth. Every carve is radial, so the surface stays star-shaped
     and can never fold inside out.

     Each patch of the head also has an age. The brainstem and the back of
     the head mature first, the frontal pole last, and the tissue forms in
     that order, so the scene grows the way a brain does.

     Canonical units: the cerebrum is about 1.8 long, 1.2 wide and 1.0 high,
     roughly the proportions of a human brain. */
  var BRAIN_AX = 0.58, BRAIN_AY = 0.50, BRAIN_AZ = 0.92;
  /* The head tips forward: the frontal pole hangs lower than the back. */
  var BRAIN_TILT = 0.17;
  /* The floor of the skull, in the head's own frame. Under it the cerebrum is
     flat; the cerebellum and the track of the temporal lobes hang below it. */
  var BRAIN_BASE_Y = -0.28;
  /* How wide the fillet is that joins the flat base to the wall. Smaller is a
     crisper edge, but too small and the normals crease at the corner. */
  var BRAIN_BASE_K = 0.085;
  /* The interhemispheric fissure: half-width and depth, canonical. Deep and
     narrow, so the two hemispheres read from the front, the top and the side.
     It fades out where the basal plane takes over. */
  var FISSURE_W = 0.092, FISSURE_DEPTH = 0.160;
  /* Each hemisphere is a convex mass: the surface climbs away from the cleft
     instead of dropping straight off both sides of it. */
  var HEMI_LIFT = 0.036, HEMI_W = 0.12;
  /* The temporal lobe: lateral-inferior, in the middle third of the
     anterior-posterior axis, and reaching a little past the parietal wall. */
  var TEMPORAL_AMP = 0.075;
  var TEMPORAL_W = [0.34, 0.36, 0.46];
  /* The cerebellum: its own mass under the occiput, set back so it hangs
     behind the temporal lobe as well as below the cerebrum. The widths are
     per axis, so it is wide and fairly flat, the way the real one is. */
  var CEREB_AMP = 0.32;
  var CEREB_W = [0.46, 0.28, 0.30];
  /* The brainstem: a short tapered stalk under the midline, angled a little
     posterior, and smooth. */
  var STEM_AMP = 0.23, STEM_W = 0.25;
  var _brainProf = [0, 0, 0, 0, 0];
  var _brainBase = [0, 0, 0];
  var _brainFrame = [0, 0, 0];
  var _brainGyri = [0, 0];
  /* How the gyri are cut. The cortex is a folded sheet, and the folds are the
     zero set of a smooth field: a sulcus is where the field crosses zero,
     and the ground between two of them is a gyrus. So the field is read
     twice. How close it is to zero says how deep the cleft is, and how far
     from zero says how high the crown stands. The two widths are the scale
     of the cleft and the scale of the crown, in the same units as the
     field, so the fold is always centred: sulci below zero, crowns above.
     The frequencies are what make it read as a brain: at 3.4 the field gave
     four continents of light and shadow, which looked like lava on a rock.
     A human brain carries ten or so gyri across its width, so the folds are
     finer and the sulci narrower. The amplitudes stay low: deep carves made
     the silhouette ragged, like dripping wax, and a brain is a smooth wall
     of rounded gyri with thin grooves between them. */
  var GYRI_FREQ1 = 5.4, GYRI_FREQ2 = 9.5;
  var GYRI_CLEFT1 = 0.082, GYRI_CLEFT2 = 0.052;
  var GYRI_CROWN1 = 0.100, GYRI_CROWN2 = 0.060;
  var GYRI_UP1 = 0.040, GYRI_UP2 = 0.012;
  var GYRI_DOWN1 = 0.050, GYRI_DOWN2 = 0.022;

  function brainSmooth(x, a, b) {
    var t = (x - a) / (b - a);
    if (t < 0) t = 0; else if (t > 1) t = 1;
    return t * t * (3 - 2 * t);
  }

  /* Smooth minimum: exactly min away from the join, C1 everywhere, so the
     fillet between the basal plane and the wall has no crease. */
  function brainSmin(a, b, k) {
    var d = a - b;
    return 0.5 * (a + b - Math.sqrt(d * d + k * k));
  }

  /* A soft mass on the unit direction: a Gaussian around an axis in the
     head's frame, with its own width along each axis, so a lobe can be wide
     and flat while another is tall. Smooth everywhere, so it can be added to
     the radius without a seam. */
  function brainMass(ux, uy, uz, ax, ay, az, wx, wy, wz) {
    var ex = (ux - ax) / wx, ey = (uy - ay) / wy, ez = (uz - az) / wz;
    return Math.exp(-(ex * ex + ey * ey + ez * ez));
  }

  /* The radius of the cerebrum along a unit direction, before the lobes: the
     tilted ellipsoid, rolled flat on the skull base. */
  function brainBase(dx, dy, dz, out) {
    /* The direction in the head's own frame. The tilt is a shear, so a world
       ray is the canonical ray scaled by the radius; the ellipsoid root is
       then already the world radius. */
    var ux = dx, uy = dy + BRAIN_TILT * dz, uz = dz;
    var r = 1 / Math.sqrt((ux * ux) / (BRAIN_AX * BRAIN_AX) +
                          (uy * uy) / (BRAIN_AY * BRAIN_AY) +
                          (uz * uz) / (BRAIN_AZ * BRAIN_AZ));
    /* The basal plane is y = BRAIN_BASE_Y; along this ray it is that far
       away when uy < 0. The smooth minimum with the ellipsoid radius rolls
       the underside flat and leaves a fillet at the edge of the base. */
    if (uy < -1e-3) r = brainSmin(r, BRAIN_BASE_Y / uy, BRAIN_BASE_K);
    /* The temporal lobe of each side, under the Sylvian fissure. */
    var temporal = brainMass(ux, uy, uz, 0.80, -0.44, 0.22,
                             TEMPORAL_W[0], TEMPORAL_W[1], TEMPORAL_W[2]) +
                   brainMass(ux, uy, uz, -0.80, -0.44, 0.22,
                             TEMPORAL_W[0], TEMPORAL_W[1], TEMPORAL_W[2]);
    r += TEMPORAL_AMP * temporal;
    /* The interhemispheric fissure. It is cut here, not into the folds: the
       two hemispheres must be separate at every stage of the build. The mask
       fades only near the basal plane, where the underside takes over and the
       fissure has nowhere to go. */
    var xc = dx * r, yc = uy * r;
    var high = brainSmooth(yc, BRAIN_BASE_Y - 0.01, BRAIN_BASE_Y + 0.10);
    var cleft = Math.exp(-(xc * xc) / (FISSURE_W * FISSURE_W));
    r -= FISSURE_DEPTH * cleft * high;
    /* And each hemisphere climbs away from the cleft, so the midline is a
       valley between two convex masses instead of a scratch on one. */
    r += HEMI_LIFT * (1 - Math.exp(-(xc * xc) / (HEMI_W * HEMI_W))) * high;
    /* The cerebellum: a wide, low mass under the occiput. It is what makes
       the back of the head look like a head and not the back of an egg. */
    var cereb = brainMass(ux, uy, uz, 0.0, -0.52, -0.85,
                          CEREB_W[0], CEREB_W[1], CEREB_W[2]);
    /* The brainstem: a stalk from the midline, angled a little posterior. */
    var stem = brainMass(ux, uy, uz, 0.0, -0.96, -0.30,
                         STEM_W, STEM_W, STEM_W);
    r += CEREB_AMP * cereb + STEM_AMP * stem;
    out[0] = r;
    out[1] = cereb;
    out[2] = stem;
    return out;
  }

  /* The position of the surface point in the head's own frame, used to carve
     the sulci where the brain's anatomy lives. */
  function brainFrame(dx, dy, dz, r, out) {
    out[0] = dx * r;
    out[1] = dy * r + BRAIN_TILT * dz * r;
    out[2] = dz * r;
    return out;
  }

  /* Distance from a point to a segment, in the head's sagittal plane. */
  function brainSeg(px, py, ax, ay, bx, by) {
    var vx = bx - ax, vy = by - ay;
    var wx = px - ax, wy = py - ay;
    var den = vx * vx + vy * vy;
    var t = den > 1e-9 ? (vx * wx + vy * wy) / den : 0;
    if (t < 0) t = 0; else if (t > 1) t = 1;
    var ex = wx - vx * t, ey = wy - vy * t;
    return Math.sqrt(ex * ex + ey * ey);
  }

  /* How old a patch of the head is: 0 is the brainstem, 1 the frontal pole.
     Cheap enough to ask about every node the director reveals. */
  function brainAge(bx, by, bz) {
    var front = brainSmooth(bz, -0.45, 0.85);
    var low = brainSmooth(by, 0.15, -0.55);
    var age = 0.28 + 0.58 * front - 0.22 * low;
    if (age < 0) age = 0; else if (age > 1) age = 1;
    return age;
  }

  /* out = [radius, fold, age] for a unit direction: the distance to the skin,
     how far the folds push the surface in or out, and the age of the patch. */
  function brainProfile(dx, dy, dz, out) {
    var base = brainBase(dx, dy, dz, _brainBase);
    var r = base[0];
    var cereb = base[1], stem = base[2];
    var f = _brainFrame;
    brainFrame(dx, dy, dz, r, f);
    var fx = f[0], fy = f[1], fz = f[2];
    /* Gyri. The cortex is a folded sheet, and the folds are the zero set of
       a smooth field: a sulcus is where the field crosses zero, and the
       ground between two sulci is a gyrus. So the field is read twice. How
       close it is to zero says how deep the cleft is, and how far from zero
       says how high the crown stands. A ridge transform cannot do this: it
       is one-sided, so the folds all lean the same way and the sulci never
       read as clefts at all. */
    var w1 = SK.fbm3(fx * GYRI_FREQ1, fy * GYRI_FREQ1, fz * GYRI_FREQ1, 2) - 0.5;
    var w2 = SK.fbm3(fx * GYRI_FREQ2 + 7.0, fy * GYRI_FREQ2, fz * GYRI_FREQ2, 2) - 0.5;
    var cleft1 = Math.exp(-(w1 * w1) / (GYRI_CLEFT1 * GYRI_CLEFT1));
    var cleft2 = Math.exp(-(w2 * w2) / (GYRI_CLEFT2 * GYRI_CLEFT2));
    var crown1 = 1 - Math.exp(-(w1 * w1) / (GYRI_CROWN1 * GYRI_CROWN1));
    var crown2 = 1 - Math.exp(-(w2 * w2) / (GYRI_CROWN2 * GYRI_CROWN2));
    /* The gyri belong to the cerebrum. The cerebellum has folia and the stalk
       is smooth, so both are masked out of the folded sheet. Without the
       mask the foliation used to be painted over the whole lower half of the
       head, which is what made the side view read as a cinnamon bun. */
    var cortex = (1 - brainSmooth(cereb, 0.05, 0.45)) * (1 - 0.88 * stem);
    /* On the flat base the radius is nearly tangent to the surface, so a
       radial fold would slide the skin sideways and fold it over itself in
       thin fins. The orbital surface is shallow anyway, so the gyri fade out
       before they reach the basal plane. */
    cortex *= brainSmooth(fy, BRAIN_BASE_Y - 0.02, BRAIN_BASE_Y + 0.14);
    var fold = (GYRI_UP1 * crown1 + GYRI_UP2 * crown2 -
                GYRI_DOWN1 * cleft1 - GYRI_DOWN2 * cleft2) * cortex;
    /* Fine horizontal folia, only on the cerebellum, and only where it is
       actually the surface: at the edge of the mass they fade out with it.
       The period has to stay coarser than the grid, or the folia alias into
       flat shelves instead of reading as ridges. */
    fold += brainSmooth(cereb, 0.10, 0.55) * 0.009 *
            Math.sin(fy * 112.0 + fz * 16.0);
    /* The transverse fissure: the crease between the cerebrum and the
       cerebellum. Without it the two masses blend into one lump from the
       side, and the back of the head stops reading as a head. */
    var tent = brainSmooth(-fz, 0.26, 0.60) *
               Math.exp(-Math.pow((fy + 0.30) / 0.060, 2));
    fold -= tent * 0.055;
    /* The Sylvian fissure: the groove that separates the temporal lobe from
       the frontal and parietal lobes. It starts low at the temporal pole and
       climbs a little as it runs back, which is the one diagonal the eye uses
       to find the temporal lobe. */
    var sylv = brainSeg(fz, fy, 0.46, -0.19, -0.28, -0.02);
    var sylvMask = brainSmooth(Math.abs(fx), 0.14, 0.28) *
                   (1 - brainSmooth(fy, 0.16, 0.38));
    fold -= Math.exp(-(sylv * sylv) / 0.0030) * sylvMask * 0.062;
    /* The central sulcus: the groove that divides the frontal lobe from the
       parietal one. Shallow, because the gyri field already supplies most of
       the folds; this is a landmark, not a canyon. */
    var centr = brainSeg(fz, fy, 0.02, 0.46, 0.28, -0.06);
    var centrMask = brainSmooth(Math.abs(fx), 0.12, 0.26) *
                    brainSmooth(fy, 0.16, 0.36);
    fold -= Math.exp(-(centr * centr) / 0.0030) * centrMask * 0.034;
    out[0] = r;
    out[1] = fold;
    out[2] = brainAge(fx, fy, fz);
    return out;
  }

  /* A direction on the scalp, nudged along two tangents so cells do not line
     up on a gyrus. Returns a unit vector. */
  SK.brainJitter = function (dx, dy, dz, seed) {
    var len = Math.sqrt(dx * dx + dy * dy + dz * dz);
    if (!(len > 1e-6)) { dx = 0; dy = 1; dz = 0; len = 1; }
    dx /= len; dy /= len; dz /= len;
    var s = seed == null ? 0.5 : seed;
    var a1 = SK.hashSeed(s * 131 + 3.7) - 0.5;
    var a2 = SK.hashSeed(s * 977 + 11.3) - 0.5;
    /* Any vector not parallel to the direction gives a tangent seed. */
    var tx = 0, ty = 1, tz = 0;
    if (Math.abs(dy) > 0.9) { tx = 1; ty = 0; tz = 0; }
    var ex = dy * tz - dz * ty, ey = dz * tx - dx * tz, ez = dx * ty - dy * tx;
    var el = Math.sqrt(ex * ex + ey * ey + ez * ez) || 1;
    ex /= el; ey /= el; ez /= el;
    var fx = dy * ez - dz * ey, fy = dz * ex - dx * ez, fz = dx * ey - dy * ex;
    dx += ex * a1 * 0.07 + fx * a2 * 0.07;
    dy += ey * a1 * 0.07 + fy * a2 * 0.07;
    dz += ez * a1 * 0.07 + fz * a2 * 0.07;
    var l2 = Math.sqrt(dx * dx + dy * dy + dz * dz) || 1;
    return [dx / l2, dy / l2, dz / l2];
  };

  /* The skin under a direction: the distance to it, how far the folds push it
     in or out, and how mature that patch of cortex is. */
  SK.brainPatch = function (dx, dy, dz, out) {
    var o = out || [0, 0, 0];
    var prof = brainProfile(dx, dy, dz, _brainProf);
    o[0] = prof[0]; o[1] = prof[1]; o[2] = prof[2];
    return o;
  };

  /* The R2 low-discrepancy sequence. Any prefix of it is spread evenly over
     the sphere, so the cells that arrive first do not pile up on one patch of
     cortex: the coat thickens evenly as the graph is built. The site layout
     cannot do this job, because a settled layout leaves whole directions
     empty and others with five cells in them, and a head with bare patches of
     scalp does not read as a head. */
  var BRAIN_R2 = [0.7548776662466927, 0.5698402909980532, 0.6180339887498949];
  SK.brainSequence = function (i) {
    var a = i * BRAIN_R2[0], b = i * BRAIN_R2[1], c = i * BRAIN_R2[2];
    var x = 2 * (a - Math.floor(a)) - 1;
    var y = 2 * (b - Math.floor(b)) - 1;
    var z = 2 * (c - Math.floor(c)) - 1;
    var l = Math.sqrt(x * x + y * y + z * z) || 1;
    return [x / l, y / l, z / l];
  };

  /* Normals from the grid itself, so the gyri catch the light instead of the
     skin looking like a smooth egg. */
  function brainGridNormals(pts, rows, cols, out) {
    var i, j;
    for (i = 0; i < rows; i++) {
      var ip = i > 0 ? i - 1 : 0, inx = i < rows - 1 ? i + 1 : rows - 1;
      var sy = Math.cos(Math.PI * i / (rows - 1));
      var sr = Math.sin(Math.PI * i / (rows - 1));
      for (j = 0; j < cols; j++) {
        var jp = (j + cols - 1) % cols, jn = (j + 1) % cols;
        var a = (ip * cols + j) * 3, b = (inx * cols + j) * 3;
        var c = (i * cols + jp) * 3, d = (i * cols + jn) * 3;
        var t1x = pts[b] - pts[a], t1y = pts[b + 1] - pts[a + 1],
            t1z = pts[b + 2] - pts[a + 2];
        var t2x = pts[d] - pts[c], t2y = pts[d + 1] - pts[c + 1],
            t2z = pts[d + 2] - pts[c + 2];
        var nx = t1y * t2z - t1z * t2y;
        var ny = t1z * t2x - t1x * t2z;
        var nz = t1x * t2y - t1y * t2x;
        var nl = Math.sqrt(nx * nx + ny * ny + nz * nz);
        var ux = sr * Math.cos(2 * Math.PI * j / cols), uy = sy,
            uz = sr * Math.sin(2 * Math.PI * j / cols);
        if (nl < 1e-9) { nx = ux; ny = uy; nz = uz; nl = 1; }
        nx /= nl; ny /= nl; nz /= nl;
        if (nx * ux + ny * uy + nz * uz < 0) { nx = -nx; ny = -ny; nz = -nz; }
        var k = (i * cols + j) * 3;
        out[k] = nx; out[k + 1] = ny; out[k + 2] = nz;
      }
    }
    return out;
  }

  /* The cortex as a lat/long grid. The renderer turns this into the tissue
     the neurons grow on; keeping it here means the shape can be checked in
     node, without a WebGL context.

     ``flat`` is the same surface without the folds, so the renderer can grow
     the gyri in: a young brain is smooth, an old one is folded. ``dev`` is how
     mature each patch is and ``fold`` is how far the folds push it in or out,
     so the shader can darken the sulci and fold the gyri in developmental
     order.

     Returns {positions, normals, flat, flatNormals, dev, fold, indices,
     rows, cols, count}. */
  SK.brainMesh = function (rows, cols) {
    rows = Math.max(8, rows | 0);
    cols = Math.max(8, cols | 0);
    var count = rows * cols;
    var positions = new Float32Array(count * 3);
    var normals = new Float32Array(count * 3);
    var flat = new Float32Array(count * 3);
    var flatNormals = new Float32Array(count * 3);
    var dev = new Float32Array(count);
    var fold = new Float32Array(count);
    var indices = new Uint32Array((rows - 1) * cols * 6);
    var prof = [0, 0, 0], i, j;
    for (i = 0; i < rows; i++) {
      var th = Math.PI * i / (rows - 1);
      var st = Math.sin(th), ct = Math.cos(th);
      for (j = 0; j < cols; j++) {
        var ph = 2 * Math.PI * j / cols;
        var dx = st * Math.cos(ph), dy = ct, dz = st * Math.sin(ph);
        brainProfile(dx, dy, dz, prof);
        var k = (i * cols + j) * 3;
        flat[k] = dx * prof[0];
        flat[k + 1] = dy * prof[0];
        flat[k + 2] = dz * prof[0];
        var rr = prof[0] + prof[1];
        positions[k] = dx * rr;
        positions[k + 1] = dy * rr;
        positions[k + 2] = dz * rr;
        dev[i * cols + j] = prof[2];
        fold[i * cols + j] = prof[1];
      }
    }
    brainGridNormals(positions, rows, cols, normals);
    brainGridNormals(flat, rows, cols, flatNormals);
    var ia = 0;
    for (i = 0; i < rows - 1; i++) {
      for (j = 0; j < cols; j++) {
        var jn = (j + 1) % cols;
        var a = i * cols + j, b = i * cols + jn;
        var c = (i + 1) * cols + j, d = (i + 1) * cols + jn;
        indices[ia++] = a; indices[ia++] = b; indices[ia++] = c;
        indices[ia++] = b; indices[ia++] = d; indices[ia++] = c;
      }
    }
    return {positions: positions, normals: normals, flat: flat,
            flatNormals: flatNormals, dev: dev, fold: fold,
            indices: indices, rows: rows, cols: cols, count: count};
  };

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

  /* Control point of the quadratic that bends one link into an arc.
     Straight segments read as a wiring diagram; a connectome reads as axons,
     which leave a soma, bow out and come back in. ``bend`` is the sagitta as a
     fraction of the link's own length, so long links bow further and short
     ones stay almost straight. The perpendicular is built from a fixed world
     axis with a per-edge sign, so the field is deterministic and two calls
     for the same edge always agree. */
  SK.arcControl = function (ax, ay, az, bx, by, bz, seed, bend) {
    var dx = bx - ax, dy = by - ay, dz = bz - az;
    var len = Math.hypot(dx, dy, dz) || 1e-6;
    var ux = dx / len, uy = dy / len, uz = dz / len;
    /* Any axis that is not the link itself; swap when the link is near-vertical
       so the cross product never collapses. */
    var px = 0, py = 1, pz = 0;
    if (Math.abs(uy) > 0.92) { px = 1; py = 0; pz = 0; }
    var cx = uy * pz - uz * py;
    var cy = uz * px - ux * pz;
    var cz = ux * py - uy * px;
    var cl = Math.hypot(cx, cy, cz) || 1e-6;
    var sign = SK.hashSeed(seed * 7.31 + 1.17) < 0.5 ? -1 : 1;
    var wobble = 0.72 + 0.56 * SK.hashSeed(seed * 3.97 + 5.31);
    var amp = len * (bend == null ? 0.16 : bend) * wobble * sign;
    return [(ax + bx) * 0.5 + (cx / cl) * amp,
            (ay + by) * 0.5 + (cy / cl) * amp,
            (az + bz) * 0.5 + (cz / cl) * amp];
  };

  /* Point on a quadratic Bézier at t, written into ``out`` (length 3). */
  SK.quadPoint = function (a, c, b, t, out) {
    var s = 1 - t;
    out[0] = s * s * a[0] + 2 * s * t * c[0] + t * t * b[0];
    out[1] = s * s * a[1] + 2 * s * t * c[1] + t * t * b[1];
    out[2] = s * s * a[2] + 2 * s * t * c[2] + t * t * b[2];
    return out;
  };

  /* Collision shove for one pair: when two bodies are closer than ``minDist``
     they are pushed apart along their own axis, half each. Returns the unit
     correction to add to the first body (subtract it from the second) and a
     zero vector when the pair already clears. Coincident centres need an
     arbitrary axis, which is seeded so a pile of nodes fans out instead of
     all stacking on the same line. */
  SK.pushApart = function (dx, dy, dz, minDist, seed) {
    var d = Math.hypot(dx, dy, dz);
    if (d >= minDist) return [0, 0, 0];
    var nx, ny, nz;
    if (d < 1e-5) {
      var a = (seed || 0) * 2.399963;
      nx = Math.cos(a); ny = 0.31; nz = Math.sin(a);
      var nl = Math.hypot(nx, ny, nz);
      nx /= nl; ny /= nl; nz /= nl;
      d = 0;
    } else {
      nx = dx / d; ny = dy / d; nz = dz / d;
    }
    var push = (minDist - d) * 0.5;
    return [nx * push, ny * push, nz * push];
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
