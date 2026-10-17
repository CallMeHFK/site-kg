(function (SK) {
  "use strict";
  SK.scenes = SK.scenes || {};
  SK.sceneOrder = SK.sceneOrder || ["planets", "people", "neural"];
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
    spin: 0.45
  };
})(window.SK = window.SK || {});
