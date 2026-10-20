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
    /* A neuron sprite is a soma plus dendrites inside the quad, so it needs
       more pixels than a planet. With the sizes sync() writes (1.6-3.5) and a
       dpr of 2 this lands at 18-40 px when the cell is ~45 units from the
       camera: 1.6*0.85*2*(300/45) = 18.1, 3.5*0.85*2*(300/45) = 39.7. */
    nodeScale: 0.85,
    haloScale: 1.7,
    edgeOpacity: 0.62,
    starfield: true,
    sky: "deepvoid",
    neuralNodes: true,
    /* The tissue is a brain, not a cloud: the cells are neurons on a cortex,
       the mesh around them carries the gyri, and the silhouette is the one
       a head has. */
    brain: true,
    brainRadius: 17,
    /* The head fills the frame better than a live layout does: a 21-unit floor
       (the open-cloud default) frames a 17-unit brain far too small. */
    fitMinRadius: 15,
    cortexOpacity: 0.46,
    cortexColor: 0x2c7f92,
    /* Neurons mature in waves instead of all at once, so the head keeps
       sprouting while the scene runs, and they wait for the tissue under
       them: the brainstem is built first, the frontal pole last. */
    developGap: 0.5,
    developWaves: 16,
    developSpan: 7.5,
    /* Wetware is sparse: open the layout up so a cell has room for its
       dendrites, and keep the whole tissue drifting slowly instead of
       sitting still like a printed diagram. */
    posScale: 2.2,
    fly: 0.18,
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
