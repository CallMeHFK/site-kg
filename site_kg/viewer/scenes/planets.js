(function (SK) {
  "use strict";
  SK.scenes = SK.scenes || {};
  SK.sceneOrder = SK.sceneOrder || ["planets", "neural"];
  SK.scenes.planets = {
    name: "planets",
    label: "Planets",
    hint: "clusters as worlds",
    bg: 0x04050b,
    /* A mineral system, not an arcade: desert gold, lunar steel, mars rust,
       ocean blue, sand, ice. The hues separate the clusters, the muted
       chroma keeps them reading as bodies lit by one star. Slot 3 is the
       dominant cluster's colour, so the desert gold sits there and the
       system reads as one warm sun with cold outliers. */
    palette: [0xc87358, 0x9db4d0, 0x74b4ba, 0xe8c07d, 0xe6d5b0, 0xb097c6,
              0xf2e6cf, 0x5b90d2, 0xb3656a, 0x8396c4, 0xd8a35f, 0xaab7a4],
    nodeScale: 1.22,
    haloScale: 3.6,
    edgeOpacity: 0.78,
    ringOpacity: 0.30,
    starfield: true,
    lightDir: [-0.55, 0.6, 0.75],
    ringRank: 0.55,
    spin: 0.6,
    /* Every world streaks in from outside the frame and settles into orbit. */
    fly: 1.0,
    drift: 0,
    nodeMode: "points",
    ego: false,
    shells: true,
    /* Orbital threads are sightlines, not tissue: a slight bow keeps them from
       looking ruled, but a planet is still a body that can be dragged. */
    edgeBend: 0.06,
    curveSegs: 4,
    draggable: true,
    collidePad: 0.46,
    labelBudget: 40,
    labelBudgetMobile: 12,
    finale: "Orbital alignment",
    shellRadius: function (shell) {
      return 13 + 7.0 * Math.sqrt(Math.max(0, shell));
    },
    select: function (live) {
      var out = new Array(live.n);
      for (var i = 0; i < live.n; i++) out[i] = i;
      return out;
    }
  };
})(window.SK = window.SK || {});
