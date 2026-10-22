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
       more pixels than a planet. With the sizes sync() writes (1.8-3.9) and a
       dpr of 2 this lands at 24-52 px when the cell is ~40 units from the
       camera: 1.8*0.95*2*(300/40) = 25.7, 3.9*0.95*2*(300/40) = 55.6. Below
       that the dendrites collapse into a four-point star. */
    nodeScale: 0.95,
    haloScale: 1.7,
    /* 176 auras at the default gain lit the membrane into candle wax; the
       cells keep their glow but the tissue stays a dark specimen. */
    haloGain: 0.17,
    /* Axons are the second half of "brain made of neurons": at 0.62 they
       vanished into the tissue and the head read as a walnut with glitter. */
    edgeOpacity: 0.95,
    /* A connectome is not wiring: every axon leaves its soma, bows out and
       comes back in. 0.17 of the link's own length is the sagitta, and eight
       segments keep the arc smooth without making the line buffer three times
       the size of the graph. */
    edgeBend: 0.17,
    curveSegs: 8,
    /* A neuron is a body, not a chart marker: grabbing one pulls its own
       synapses along, and the pair cannot pass through each other. */
    draggable: true,
    /* But a cell is anchored in the tissue. Pulling one stretches its
       membrane a short way and lets it snap home when the hand lets go; a
       neuron that stayed where it was dropped would be a graph editor, not a
       head. 2.2 units is about one soma diameter at this scale -- enough to
       read as tension, far too little to look relocated. */
    recoil: true,
    recoilMax: 2.2,
    collidePad: 0.30,
    starfield: true,
    sky: "deepvoid",
    neuralNodes: true,
    /* The tissue is a brain, not a cloud: the cells are neurons on a cortex,
       the mesh around them carries the gyri, and the silhouette is the one
       a head has. */
    brain: true,
    brainRadius: 17,
    /* The head fills the frame better than a live layout does: a 21-unit floor
       (the open-cloud default) frames a 17-unit brain far too small, and the
       default fit margin left it at half the frame. */
    fitMinRadius: 16,
    fitZoom: 0.78,
    /* The cortex is drawn before the cells and never writes depth, so a
       denser sheet does not hide the connectome: the neurons and their axons
       land on top either way. At 0.40 the head was a faint membrane and the
       eye caught only its amber edge, which read as a shell. */
    cortexOpacity: 0.62,
    /* Cool bone-grey, not the old teal or a meaty plum: the cortex is the
       ground the warm cells are read against, so it stays a neutral putty
       with the warmth left to the rim light. */
    cortexColor: 0x7b7484,
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
       dendrites. The drift is small: the cells are ~3 units wide, so 1.1 units
       of wobble tore the coat apart and made every soma wander off its axon. */
    posScale: 2.2,
    fly: 0.18,
    drift: 0.12,
    spin: 0.4,
    nodeMode: "points",
    ego: false,
    edgeLod: true,
    /* A specimen, not an atlas: eleven labels over the head turned the folds
       into a backdrop for tooltips. Six name the tissue and leave the shape
       legible. */
    labelBudget: 6,
    labelBudgetMobile: 4,
    finale: "Synapse sweep",
    select: function (live) {
      var out = new Array(live.n);
      for (var i = 0; i < live.n; i++) out[i] = i;
      return out;
    }
  };
})(window.SK = window.SK || {});
