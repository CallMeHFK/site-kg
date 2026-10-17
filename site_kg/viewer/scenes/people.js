(function (SK) {
  "use strict";
  SK.scenes = SK.scenes || {};
  SK.sceneOrder = SK.sceneOrder || ["planets", "people", "neural"];

  function better(live, a, b) {
    var da = live.degree[a] || 0, db = live.degree[b] || 0;
    if (da !== db) return da > db;
    var ra = live.rank[a] || 0, rb = live.rank[b] || 0;
    if (ra !== rb) return ra > rb;
    return a < b;
  }

  function allNodes(live, focus) {
    var out = new Array(live.n);
    for (var i = 0; i < live.n; i++) out[i] = i;
    if (focus >= 0 && focus < live.n && focus !== 0) {
      out.splice(focus, 1);
      out.unshift(focus);
    }
    return out;
  }

  function ego(live, focus, cap) {
    var hub = focus >= 0 && focus < live.n ? focus : -1;
    if (hub < 0) {
      for (var i = 0; i < live.n; i++) {
        if (hub < 0 || better(live, i, hub)) hub = i;
      }
    }
    if (hub < 0) return [];
    var adj = live.adjacency();
    var hop = new Uint8Array(live.n);
    var seen = new Uint8Array(live.n);
    var out = [hub], queue = [hub];
    seen[hub] = 1;
    for (var qi = 0; qi < queue.length && out.length < cap; qi++) {
      var cur = queue[qi];
      if (hop[cur] >= 2) continue;
      var nbrs = [];
      for (var j = 0; j < adj[cur].length; j++) {
        var other = adj[cur][j][0], edge = adj[cur][j][1];
        if (SK.isDerived(live.kinds[live.eKind[edge]])) continue;
        if (!seen[other]) nbrs.push(other);
      }
      nbrs.sort(function (a, b) {
        if (better(live, a, b)) return -1;
        if (better(live, b, a)) return 1;
        return 0;
      });
      for (var s = 0; s < nbrs.length && out.length < cap; s++) {
        var nb = nbrs[s];
        if (seen[nb]) continue;
        seen[nb] = 1;
        hop[nb] = hop[cur] + 1;
        out.push(nb);
        queue.push(nb);
      }
    }
    return out;
  }

  SK.scenes.people = {
    name: "people",
    label: "People",
    hint: "constellation of authors",
    bg: 0x130f0c,
    palette: [0xffa657, 0xf778ba, 0xe3b341, 0xff7b72, 0xd2a8ff, 0xffd33d,
              0x7ee787, 0xff9b72, 0xbc8cff, 0xf0883e, 0xffdfb3, 0xdb6d8e],
    nodeScale: 1.2,
    haloScale: 2.5,
    edgeOpacity: 0.85,
    spin: 0.45,
    nodeMode: "instanced",
    ego: true,
    maxEgo: 120,
    labelBudget: 24,
    labelBudgetMobile: 10,
    finale: "Handshake pulse",
    select: function (live, focus) {
      var cap = this.maxEgo || 120;
      if (live.n <= cap) return allNodes(live, focus);
      return ego(live, focus, cap);
    }
  };
})(window.SK = window.SK || {});
