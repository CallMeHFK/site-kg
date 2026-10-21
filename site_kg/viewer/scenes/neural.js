(function (SK) {
  "use strict";
  SK.scenes = SK.scenes || {};
  SK.sceneOrder = SK.sceneOrder || ["planets", "neural"];
  SK.scenes.neural = {
    name: "neural",
    label: "Neural",
    hint: "synapses between pages",
    bg: 0x040404,
    /* Tissue under a dark-field scope: gold somata, amber and rose axons,
       bone and slate-violet for the deep cells. No neon -- the head has to
       read as a specimen, not as a hologram, so every hue keeps a little
       grey in it. Slot 3 is the colour of the dominant cluster, so the
       pale gold sits there: it is the tissue's signature hue. */
    palette: [0xc76a84, 0x7f95cc, 0x63b0b2, 0xf0d29a, 0xd98f52, 0x9e7fc0,
              0xb34f5b, 0xf6ecd8, 0xb9a06a, 0x8e7b9e, 0xe39a72, 0xc96f4f],
    /* A neuron sprite is a soma plus dendrites inside the quad, so it needs
       more pixels than a planet. With the sizes sync() writes (1.6-3.5) and a
       dpr of 2 this lands at 18-40 px when the cell is ~45 units from the
       camera: 1.6*0.85*2*(300/45) = 18.1, 3.5*0.85*2*(300/45) = 39.7. */
    nodeScale: 0.85,
    haloScale: 1.7,
    /* 176 auras at the default gain lit the membrane into candle wax; the
       cells keep their glow but the tissue stays a dark specimen. */
    haloGain: 0.17,
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
    cortexOpacity: 0.34,
    /* Dark plum-grey tissue, not the old teal: a membrane that keeps its own
       colour behind the gold cells instead of swallowing them. */
    cortexColor: 0x5b5566,
    /* Amber silhouette: the edge is what separates the head from the basin. */
    cortexRim: 0xffc98a,
    /* Membrane and nucleus of a cell. A cool cyan nucleus on warm somata was
       the last trace of the hologram look. */
    cellTint: 0xffedd0,
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
