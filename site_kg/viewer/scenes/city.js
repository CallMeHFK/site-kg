(function (SK) {
  "use strict";
  SK.scenes = SK.scenes || {};
  SK.sceneOrder = SK.sceneOrder || ["planets", "neural"];
  if (SK.sceneOrder.indexOf("city") < 0) SK.sceneOrder.push("city");
  SK.scenes.city = {
    name: "city",
    label: "City",
    hint: "one page, one tower",
    bg: 0x05070e,
    /* Night facades: steel, brick, slate teal, sandstone, plum. The hues stay
       close to the grey so a district reads as a neighbourhood, and the
       windows supply the warmth: lit glass is the only saturated thing in the
       frame. Slot 0 is the dominant cluster, so the steel blue sits there. */
    palette: [0x6f7f96, 0x8a6a52, 0x5f7d7a, 0xb08a58, 0x7a6a86, 0x9a8f7a,
              0x6a7f8f, 0x8f7a68, 0x5e6b82, 0x7d8a6f, 0x96775f, 0x6b6f7d],
    starfield: true,
    sky: "skyline",
    lightDir: [0.42, 0.78, 0.34],
    city: true,
    /* Street pitch and floor height in world units. A 3.0 pitch with a 1.1 to
       1.5 footprint leaves a street wide enough to read at the fit distance. */
    cityPitch: 3.0,
    cityFloor: 0.62,
    edgeOpacity: 0.42,
    edgeLod: true,
    fly: 0,
    drift: 0,
    spin: 0.22,
    nodeMode: "points",
    ego: false,
    /* The camera sits among the towers, so the graph's labels have to stay
       few: eight names the skyline, forty turns it into a wall of chips. */
    labelBudget: 8,
    labelBudgetMobile: 4,
    fitMinRadius: 20,
    fitZoom: 0.72,
    finale: "City lights",
    select: function (live) {
      var out = new Array(live.n);
      for (var i = 0; i < live.n; i++) out[i] = i;
      return out;
    }
  };

  /* ------------------------------------------------------------------
     Lattice cells, centre outward in a square spiral.

     The layout has to be stable while the crawl appends nodes: a grid with a
     fixed column count re-flows every building as soon as one more page
     arrives, and the whole city jumps. The spiral is a pure function of the
     node index, so a building that exists keeps its plot for the whole run,
     and the footprint stays square because the ordering is invisible -- only
     the lattice positions show, and those are a plain city grid.

     Cells are cached and extended one step at a time, so building the layout
     for n nodes costs O(n) once, not O(n^2) per rebuild. */
  var cells = [[0, 0]];
  var walk = {x: 0, z: 0, dir: 0, leg: 1, done: 0, legs: 0};
  var DIRS = [[1, 0], [0, 1], [-1, 0], [0, -1]];
  SK.cityCells = function (n) {
    while (cells.length < n) {
      var d = DIRS[walk.dir];
      walk.x += d[0];
      walk.z += d[1];
      cells.push([walk.x, walk.z]);
      walk.done++;
      if (walk.done === walk.leg) {
        walk.done = 0;
        walk.dir = (walk.dir + 1) % 4;
        /* Every second turn the leg grows: 1,1,2,2,3,3 ... */
        if (walk.legs % 2 === 1) walk.leg++;
        walk.legs++;
      }
    }
    return cells;
  };

  function clamp01(v) {
    return v < 0 ? 0 : (v > 1 ? 1 : v);
  }

  /* One building, from the numbers the graph already carries.

     Height is importance: the graph's rank sets the floors, and the node's
     degree adds a little, so the skyline is the ranking of the site read as
     architecture. Children (the links the page emits) become lit windows, so
     a hub is not only taller but awake.

     Floors are quantised, which is what makes a tower read as a building
     instead of a stretched box: every window row is one floor of glass. */
  SK.citySpec = function (o) {
    var floor = o.floor || 0.62;
    var rankPart = Math.sqrt(clamp01(o.rank / Math.max(o.rankMax, 1e-9)));
    var degPart = Math.sqrt(clamp01(o.degree / Math.max(o.degreeMax, 1)));
    var floors = Math.round((2.4 + 10.4 * rankPart + 3.0 * degPart) / floor);
    floors = Math.max(3, Math.min(30, floors));
    var h = floors * floor;
    /* Footprint varies per plot: a city of identical squares reads as a
       diagram, not as a place. */
    var w = 1.05 + 0.45 * SK.hashSeed(o.seed * 3.71 + 1.3);
    var d = 1.05 + 0.45 * SK.hashSeed(o.seed * 5.17 + 7.9);
    /* No children, no lights beyond a token lobby: the lit fraction is the
       fan-out, normalised against the busiest page in the run. A page with a
       handful of links must not turn its whole tower into glass, so the gain
       stays well under half: the dark mass is what makes the lights read. */
    var lit = o.kids > 0
      ? 0.10 + 0.34 * Math.sqrt(clamp01(o.kids / Math.max(o.kidsMax, 1)))
      : 0.02;
    return {floors: floors, h: h, w: w, d: d, lit: lit};
  };
})(window.SK = window.SK || {});
