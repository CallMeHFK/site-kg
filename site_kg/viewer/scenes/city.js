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
       frame. Slot 0 is the dominant cluster, so the steel blue sits there.
       Luma zigzags 0.35-0.62: at the facade gain below, that is the only
       thing that keeps two neighbouring towers from melting together. */
    palette: [0x8496b0, 0x6f6257, 0x5c7a76, 0x9d8058, 0x565e78, 0x9a9388,
              0x49606e, 0x8a7c6a, 0x6f7f96, 0x7d7464, 0x5f7d7a, 0x7f7876],
    starfield: true,
    sky: "skyline",
    lightDir: [0.42, 0.78, 0.34],
    city: true,
    /* Street pitch and floor height in world units. A 4.8 pitch against a 1.45
       to 2.10 footprint leaves a street about one and a half towers wide: the
       chamfered plan does most of the work of separating the blocks, and
       pushing the pitch further turns the grid into a car park. A 0.56 storey
       keeps the tallest tower at about nine times its width. */
    cityPitch: 4.8,
    cityFloor: 0.56,
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
    fitZoom: 0.62,
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
    /* The coefficients are chosen so the clamp is load-bearing at both ends:
       an unknown page lands on the three-floor minimum and the top-ranked hub
       on the thirty-floor maximum at the default floor height. */
    var floors = Math.round((1.2 + 14.2 * rankPart + 3.2 * degPart) / floor);
    floors = Math.max(3, Math.min(30, floors));
    var h = floors * floor;
    /* Footprint varies per plot: a city of identical squares reads as a
       diagram, not as a place. Taller towers are also wider -- a 30-floor
       shaft on a six-floor plot is a needle, and a skyline full of needles
       reads as pins. */
    var fat = 0.90 + 0.20 * rankPart;
    var w = (1.45 + 0.65 * SK.hashSeed(o.seed * 3.71 + 1.3)) * fat;
    var d = (1.45 + 0.65 * SK.hashSeed(o.seed * 5.17 + 7.9)) * fat;
    /* No children, no lights beyond a token lobby: the lit fraction is the
       fan-out, normalised against the busiest page in the run. A page with a
       handful of links must not turn its whole tower into glass, so the gain
       stays well under half: the dark mass is what makes the lights read. */
    var lit = o.kids > 0
      ? 0.10 + 0.34 * Math.sqrt(clamp01(o.kids / Math.max(o.kidsMax, 1)))
      : 0.02;
    /* Three cut heights make the massing: a podium that meets the street, a
       setback that gives the outline a step, and a crown slab. They are
       snapped to floor lines so a terrace never lands in the middle of a row
       of glass, and monotonic so no tier has zero height. */
    var podiumFloors = floors >= 8 ? 2 : 1;
    var podium = podiumFloors / floors;
    var setback = Math.round(floors * (0.60 + 0.18 * SK.hashSeed(o.seed * 6.31 + 3.7))) / floors;
    setback = Math.min(0.88, Math.max(setback, podium + 2 / floors));
    var crown = Math.min(0.97, setback + Math.max(2 / floors, 0.06));
    /* Window columns follow the frontage: a 1.2-wide tower with four windows
       looks like a diagram, and the same four on a 1.6-wide tower look like
       stretched panes. Rounded so every tier shares the grid across setbacks. */
    var cols = Math.max(6, Math.min(12, Math.round(w * 6.0)));
    /* The silhouette is drawn by how wide each tier stands against the plot,
       not only by where it is cut. A narrow crown on a wide shaft is a spire,
       a wide crown on a narrow shaft is a thirties block, and the two should
       never be told apart by height alone. */
    var podiumW = 1.05 + 0.16 * SK.hashSeed(o.seed * 2.31 + 0.9);
    var shaftW = 0.97 + 0.05 * SK.hashSeed(o.seed * 8.17 + 4.4);
    var stepW = 0.72 + 0.18 * SK.hashSeed(o.seed * 3.19 + 8.8);
    var thin = SK.hashSeed(o.seed * 9.13 + 5.1) < 0.22;
    var crownW = thin ? stepW * 0.62
      : stepW * (0.90 + 0.16 * SK.hashSeed(o.seed * 4.71 + 2.2));
    return {floors: floors, h: h, w: w, d: d, lit: lit, cols: cols,
            tiers: [podium, setback, crown],
            tierw: [podiumW, shaftW, stepW, crownW]};
  };
})(window.SK = window.SK || {});
