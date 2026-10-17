/* LiveGraph + binary payload decoders. No DOM access. */
(function (SK) {
  "use strict";

  var PALETTE = [0x58a6ff, 0x3fb950, 0xd29922, 0xf778ba, 0xa371f7, 0x76e3ea,
                 0xffa657, 0xff7b72, 0x7ee787, 0xe3b341, 0x79c0ff, 0x56d4bc];
  var DERIVED = {mentions_symbol: 1, similar_to: 1, sibling_of: 1,
                 supersedes: 1, shares_entity: 1, semantic_similar: 1};

  SK.PALETTE = PALETTE;
  SK.DERIVED = DERIVED;

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
    this.version = 0;
    this.dimVersion = 0;
    this.graphHash = 0;
    this.timeline = null;
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

  LiveGraph.prototype.nodeAdded = function (id, rec) {
    id = String(id);
    rec = rec || {};
    var i = this.indexById.get(id);
    if (i !== undefined) {
      if (rec.title != null || rec.t != null) this.titles[i] = String(rec.title != null ? rec.title : rec.t);
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
    this.titles[i] = String(rec.title != null ? rec.title : (rec.t != null ? rec.t : id));
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
    list.push(e);
    this.pendingCount++;
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

  LiveGraph.prototype.edgeAdded = function (s, d, kind) {
    s = String(s);
    d = String(d);
    kind = String(kind || "ref");
    var si = this.indexById.get(s);
    var di = this.indexById.get(d);
    if (si === undefined || di === undefined) {
      this._bufferEdge({s: s, d: d, kind: kind});
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
  };

  LiveGraph.prototype.applyBatch = function (events) {
    if (!events) return this;
    for (var k = 0; k < events.length; k++) {
      var ev = events[k];
      if (!ev || !ev.t) continue;
      if (ev.t === "node_added" || ev.t === "node_updated") {
        this.nodeAdded(ev.id, ev);
      } else if (ev.t === "edge_added") {
        this.edgeAdded(ev.s, ev.d, ev.kind);
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
    this.version++;
    return this;
  };

  LiveGraph.prototype.loadSnapshot = function (snap) {
    this.reset(Math.max(64, (snap.nodes || []).length + 8));
    this.applyBatch(snap.nodes || []);
    this.applyBatch(snap.edges || []);
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
            dangling: this.dangling};
  };

  SK.LiveGraph = LiveGraph;

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
