(function (SK) {
  "use strict";
  SK.scenes = SK.scenes || {};
  SK.sceneOrder = SK.sceneOrder || ["planets", "people", "neural"];
  SK.scenes.planets = {
    name: "planets",
    label: "Planets",
    hint: "clusters as worlds",
    bg: 0x0d1117,
    palette: [0x58a6ff, 0x3fb950, 0xd29922, 0xf778ba, 0xa371f7, 0x76e3ea,
              0xffa657, 0xff7b72, 0x7ee787, 0xe3b341, 0x79c0ff, 0x56d4bc],
    nodeScale: 1.0,
    haloScale: 3.1,
    edgeOpacity: 1.0,
    spin: 0.6
  };
})(window.SK = window.SK || {});
