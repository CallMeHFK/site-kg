(function (SK) {
  "use strict";
  SK.scenes = SK.scenes || {};
  SK.sceneOrder = SK.sceneOrder || ["planets", "people", "neural"];
  SK.scenes.neural = {
    name: "neural",
    label: "Neural",
    hint: "synapses between pages",
    bg: 0x05080f,
    palette: [0x79c0ff, 0x56d4bc, 0xa371f7, 0x76e3ea, 0x3fb950, 0x58a6ff,
              0xd2a8ff, 0x7ee787, 0x56d4bc, 0x8b949e, 0x39c5cf, 0xb392f0],
    nodeScale: 0.9,
    haloScale: 3.8,
    edgeOpacity: 1.25,
    spin: 0.75,
    nodeMode: "points",
    ego: false,
    edgeLod: true,
    labelBudget: 36,
    labelBudgetMobile: 10,
    finale: "Synapse sweep",
    select: function (live) {
      var out = new Array(live.n);
      for (var i = 0; i < live.n; i++) out[i] = i;
      return out;
    }
  };
})(window.SK = window.SK || {});
