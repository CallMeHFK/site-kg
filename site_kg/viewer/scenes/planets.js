(function (SK) {
  "use strict";
  SK.scenes = SK.scenes || {};
  SK.sceneOrder = SK.sceneOrder || ["planets", "neural"];
  SK.scenes.planets = {
    name: "planets",
    label: "Planets",
    hint: "clusters as worlds",
    bg: 0x03040c,
    palette: [0x7cc4ff, 0x8b7bff, 0x4fe3ff, 0x43e0c8, 0xff6fb5, 0xa78bfa,
              0xffc46b, 0x9fb8ff, 0x6ff0d8, 0xff9ad2, 0x5b9dff, 0xd8c2ff],
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
