(function (SK) {
  "use strict";
  SK.scenes = SK.scenes || {};
  SK.sceneOrder = SK.sceneOrder || ["planets", "neural"];
  SK.scenes.neural = {
    name: "neural",
    label: "Neural",
    hint: "synapses between pages",
    bg: 0x020508,
    /* Wetware, not nebula: ionised teal and magenta in a dark spinal fluid. */
    palette: [0x7ef9e0, 0x4ad8ff, 0x9c8bff, 0xff5fc8, 0x6cf2b4, 0x8fd4ff,
              0x6fd8ff, 0xff9d6b, 0xb18cff, 0xff8ad8, 0x3ee6c4, 0xe6f4ff],
    nodeScale: 1.0,
    haloScale: 2.2,
    edgeOpacity: 0.62,
    starfield: true,
    sky: "wetware",
    neuralNodes: true,
    /* Wetware is sparse: open the layout up so a cell has room for its
       dendrites, and keep the whole tissue drifting slowly instead of
       sitting still like a printed diagram. */
    posScale: 2.2,
    fly: 0.55,
    drift: 1.1,
    spin: 0.4,
    nodeMode: "points",
    ego: false,
    edgeLod: true,
    labelBudget: 18,
    labelBudgetMobile: 8,
    finale: "Synapse sweep",
    select: function (live) {
      var out = new Array(live.n);
      for (var i = 0; i < live.n; i++) out[i] = i;
      return out;
    }
  };
})(window.SK = window.SK || {});
