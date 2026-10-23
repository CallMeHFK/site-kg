/* Three-act intro film: one mind, many minds, everything.
   Every frame is generated at runtime on one canvas, so the film ships with
   no assets and no dependencies. It plays once per browser session over the
   live launcher, is skippable at any moment, and never runs for a visitor
   who asked for reduced motion. */
(function (SK) {
  "use strict";

  var KEY = "sk-promo";

  function makeRng(seed) {
    return function () {
      seed |= 0; seed = seed + 0x6D2B79F5 | 0;
      var t = Math.imul(seed ^ seed >>> 15, 1 | seed);
      t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
      return ((t ^ t >>> 14) >>> 0) / 4294967296;
    };
  }

  /* ---------- glow sprites ---------- */
  function sprite(r, g, b) {
    var s = 64, c = document.createElement("canvas");
    c.width = c.height = s;
    var x = c.getContext("2d");
    var grad = x.createRadialGradient(s / 2, s / 2, 0, s / 2, s / 2, s / 2);
    grad.addColorStop(0, "rgba(255,255,255,1)");
    grad.addColorStop(.16, "rgba(" + r + "," + g + "," + b + ",.95)");
    grad.addColorStop(.38, "rgba(" + r + "," + g + "," + b + ",.35)");
    grad.addColorStop(.7, "rgba(" + r + "," + g + "," + b + ",.07)");
    grad.addColorStop(1, "rgba(" + r + "," + g + "," + b + ",0)");
    x.fillStyle = grad;
    x.fillRect(0, 0, s, s);
    return c;
  }
  function spriteCore(r, g, b, cr, cg, cb) {
    var s = 64, c = document.createElement("canvas");
    c.width = c.height = s;
    var x = c.getContext("2d");
    var grad = x.createRadialGradient(s / 2, s / 2, 0, s / 2, s / 2, s / 2);
    grad.addColorStop(0, "rgba(" + cr + "," + cg + "," + cb + ",1)");
    grad.addColorStop(.2, "rgba(" + r + "," + g + "," + b + ",.96)");
    grad.addColorStop(.44, "rgba(" + r + "," + g + "," + b + ",.3)");
    grad.addColorStop(.74, "rgba(" + r + "," + g + "," + b + ",.06)");
    grad.addColorStop(1, "rgba(" + r + "," + g + "," + b + ",0)");
    x.fillStyle = grad;
    x.fillRect(0, 0, s, s);
    return c;
  }
  var SP = {
    cyan: sprite(94, 231, 255),
    violet: sprite(143, 134, 255),
    amber: sprite(255, 192, 106),
    white: sprite(226, 248, 255),
    lamp: spriteCore(255, 188, 96, 255, 243, 214),
    cool: spriteCore(126, 206, 255, 224, 246, 255)
  };

  function startCanvas(cv) {
    if (!cv) return null;
    var ctx = cv.getContext("2d");
    var w = 1, h = 1, dpr = 1, cx = 0, sceneY = 0, groundY = 0, R = 120, GALS = 200, narrow = false;
    var N = (window.innerWidth < 640) ? 540 : 820;
    var rnd = makeRng(37);
    var pts = [], towers = [], links = [], pulses = [], stars = [];
    var SX = [], SY = [], SZ = [], SA = [], SC = [];
    var t0 = performance.now();
    var raf = 0, stopped = false;
    var T = { m1: 2.45, city: 3.35, m2: 4.45, gal: 5.15 };

    function ease(x) { return x < .5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2; }

    function build() {
      var r = cv.getBoundingClientRect();
      w = Math.max(1, r.width); h = Math.max(1, r.height);
      dpr = Math.min(window.devicePixelRatio || 1, 1.75);
      cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      narrow = w < 640;
      cx = w * .5;
      sceneY = h * (narrow ? .38 : .35);
      groundY = h * (narrow ? .53 : .5);
      R = Math.max(62, Math.min(w * (narrow ? .3 : .26), h * .205));
      GALS = Math.min(R * 1.68, w * .31);

      /* skyline: towers the windows will be pinned to */
      towers = [];
      var tn = w < 640 ? 9 : 15;
      var span = Math.min(w * .86, 900);
      var left = cx - span * .5;
      for (var ti = 0; ti < tn; ti++) {
        var bw = span / tn * (.6 + rnd() * .26);
        var bh = R * (.55 + rnd() * 1.3);
        if (rnd() < .26) bh *= 1.28;
        var bx = left + span * ((ti + .5) / tn) - bw * .5 + (rnd() - .5) * span / tn * .18;
        towers.push({
          x: bx, w: bw, h: bh, top: groundY - bh
        });
      }

      stars = [];
      var sn = Math.round(w * h / 15000);
      for (var si = 0; si < sn; si++) {
        stars.push({
          x: rnd() * w, y: rnd() * h,
          s: .9 + rnd() * 1.9,
          a: .16 + rnd() * .5,
          ph: rnd() * 6.283
        });
      }

      pts.length = 0;
      for (var i = 0; i < N; i++) {
        /* 1 — one mind: a filled, lumpy neural cloud */
        var u = rnd() * 2 - 1, th = rnd() * 6.283185307;
        var rr = Math.sqrt(Math.max(0, 1 - u * u));
        var shell = .3 + .7 * Math.sqrt(rnd());
        var nx = Math.cos(th) * rr * 1.16 * shell;
        var ny = u * .92 * shell;
        var nz = Math.sin(th) * rr * .84 * shell;
        /* hemispheres: nudge particles off the midline so a fissure reads */
        if (ny > -.22 && Math.abs(nx) < .12) nx += nx < 0 ? -.1 : .1;
        if (rnd() < .04) { nx *= 1.2; ny *= 1.16; nz *= 1.2; }

        /* where the dust flies in from */
        var u0 = rnd() * 2 - 1, a0 = rnd() * 6.283185307;
        var r0 = Math.sqrt(Math.max(0, 1 - u0 * u0));
        var far = 2.3 + rnd() * .7;
        var fx = Math.cos(a0) * r0 * far, fy = u0 * far * .92, fz = Math.sin(a0) * r0 * far;

        /* 2 — many minds: one lit window each */
        var tw = towers[i % towers.length];
        var cols = Math.max(2, Math.round(tw.w / 12));
        var rows = Math.max(3, Math.round(tw.h / 13));
        var slot = Math.floor(i / towers.length);
        var col = slot % cols;
        var row = Math.floor(slot / cols) % rows;
        var wx = tw.w > 14 ? tw.x + 4 + (tw.w - 8) * col / (cols - 1) : tw.x + tw.w * .5;
        var wy = tw.top + 8 + (tw.h - 14) * row / Math.max(1, rows - 1);
        wx += (rnd() - .5) * 1.7;
        wy += (rnd() - .5) * 1.7;

        /* 3 — everything: spiral arms */
        var arm = i % 3;
        var tt = Math.pow(rnd(), .6);
        var ang = tt * 4.4 + arm * 2.0944 + (rnd() - .5) * .46;
        var rad = .13 + tt * .95 + (rnd() - .5) * .1 * (.3 + tt);
        var gx = Math.cos(ang) * rad;
        var gy = (rnd() - .5) * .075 * (1.25 - tt) + (rnd() - .5) * .035;
        var gz = Math.sin(ang) * rad;

        pts.push({
          nx: nx, ny: ny, nz: nz, fx: fx, fy: fy, fz: fz,
          nr: Math.sqrt(nx * nx + ny * ny + nz * nz),
          wx: wx, wy: wy,
          sxr: Math.max(0, Math.min(1, (wx - left) / span)),
          gr: rad, ga: ang, gy: gy,
          c: rnd() < .7 ? 0 : (rnd() < .62 ? 1 : 2),
          warm: rnd() < .62,
          cool: rnd() < .15,
          lit: rnd() < .8 ? 1 : .07,
          s: .55 + rnd() * .85,
          ph: rnd() * 6.283,
          st: rnd()
        });
      }

      /* synapses: neighbour pairs inside the cloud */
      links = [];
      var CELL = .3, grid = {};
      for (var q = 0; q < pts.length; q++) {
        var pp = pts[q];
        var key = Math.floor(pp.nx / CELL) + "|" + Math.floor(pp.ny / CELL) + "|" + Math.floor(pp.nz / CELL);
        (grid[key] || (grid[key] = [])).push(q);
      }
      for (var key2 in grid) {
        var cell = grid[key2];
        for (var ai = 0; ai < cell.length; ai++) {
          for (var bi = ai + 1; bi < cell.length; bi++) {
            var A = pts[cell[ai]], B = pts[cell[bi]];
            var dx = A.nx - B.nx, dy = A.ny - B.ny, dz = A.nz - B.nz;
            if (dx * dx + dy * dy + dz * dz < .075) links.push([cell[ai], cell[bi]]);
            if (links.length > 1100) break;
          }
          if (links.length > 1100) break;
        }
        if (links.length > 1100) break;
      }
      pulses.length = 0;

      SX = new Float32Array(N); SY = new Float32Array(N); SZ = new Float32Array(N);
      SA = new Float32Array(N); SC = new Array(N);
    }

    function frame(now) {
      if (stopped) return;
      var t = (now - t0) / 1000;

      var m = t < T.m1 ? 0
        : t < T.city ? (t - T.m1) / (T.city - T.m1)
        : t < T.m2 ? 1
        : t < T.gal ? 1 + (t - T.m2) / (T.gal - T.m2)
        : 2;

      ctx.clearRect(0, 0, w, h);

      var asm = ease(Math.min(1, Math.max(0, (t - .22) / .78)));
      var neuralA = Math.max(0, 1 - m / .55);
      var cityA = Math.min(1, m / .45) * Math.max(0, 1 - (m - 1.45) / .45);
      var galA = Math.max(0, Math.min(1, (m - 1.55) / .45));
      var cityRise = ease(Math.max(0, Math.min(1, (m - .02) / .72)));

      /* --- skyline --- */
      if (cityA > .02) {
        ctx.globalCompositeOperation = "source-over";
        ctx.globalAlpha = cityA;
        for (var b = 0; b < towers.length; b++) {
          var tw = towers[b];
          var bh = tw.h * cityRise;
          if (bh < 2) continue;
          var top = groundY - bh;
          ctx.fillStyle = "rgba(10,15,26,.97)";
          ctx.fillRect(tw.x, top, tw.w, bh);
          ctx.strokeStyle = "rgba(130,200,255,.2)";
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.moveTo(tw.x + .5, top + .5);
          ctx.lineTo(tw.x + tw.w - .5, top + .5);
          ctx.stroke();
        }
        var gy2 = ctx.createLinearGradient(0, groundY - 46, 0, groundY + 18);
        gy2.addColorStop(0, "rgba(60,130,220,0)");
        gy2.addColorStop(.72, "rgba(70,150,230," + (.14 * cityA) + ")");
        gy2.addColorStop(1, "rgba(70,150,230,0)");
        ctx.globalAlpha = 1;
        ctx.fillStyle = gy2;
        ctx.fillRect(0, groundY - 46, w, 64);
        ctx.globalCompositeOperation = "lighter";
      }

      var nLean = 1 + .09 * ease(Math.max(0, Math.min(1, (t - .5) / 1.9)));
      var nYaw = t * .2, nTilt = -.16 + Math.sin(t * .33) * .05;
      var ncy = Math.cos(nYaw), nsy = Math.sin(nYaw);
      var nct = Math.cos(nTilt), nst = Math.sin(nTilt);
      var gYaw = -t * .16, gTilt = -.52;
      var gcy = Math.cos(gYaw), gsy = Math.sin(gYaw);
      var gct = Math.cos(gTilt), gst = Math.sin(gTilt);
      /* the disc spins up on arrival, then settles as the wordmark lands */
      var gT0 = Math.max(0, Math.min(t, 7.2) - T.gal);
      var spinAmt = 2.2 * (gT0 - .3 * (1 - Math.exp(-gT0 / .3)));
      var galPop = 1 + .17 * Math.max(0, 1 - Math.max(0, t - T.gal) / .8);
      var galY = sceneY - (narrow ? 6 : 17);
      var breathe = 1 + Math.sin(t * .8) * .014;
      /* a thought: one ignition, expanding through the network */
      var cascR = Math.max(0, (t - 1.12) * 1.05);
      var cascOn = t > 1.12 && t < T.m1 + .25;
      /* a crowd: light crosses the skyline, district by district */
      var sweepF = Math.max(0, Math.min(1.2, (t - T.m1 - .02) / .92));

      for (var i = 0; i < pts.length; i++) {
        var p = pts[i];

        var ax = p.fx + (p.nx - p.fx) * asm;
        var ay = p.fy + (p.ny - p.fy) * asm;
        var az = p.fz + (p.nz - p.fz) * asm;
        var x1 = ax * ncy + az * nsy;
        var z1 = -ax * nsy + az * ncy;
        var y1 = ay * nct - z1 * nst;
        var z2 = ay * nst + z1 * nct;
        var per0 = 3.4 / (3.4 + z2);
        var sx0 = cx + x1 * R * per0 * breathe * nLean;
        var sy0 = sceneY + y1 * R * per0 * breathe * nLean;

        var sx1 = p.wx;
        var sy1 = groundY - (groundY - p.wy) * cityRise;

        var gAng = p.ga + spinAmt * (.5 / (p.gr + .5));
        var gxx = Math.cos(gAng) * p.gr, gzz = Math.sin(gAng) * p.gr;
        var gx1 = gxx * gcy + gzz * gsy;
        var gz1 = -gxx * gsy + gzz * gcy;
        var gy1 = p.gy * gct - gz1 * gst;
        var gz2 = p.gy * gst + gz1 * gct;
        var per2 = 3.8 / (3.8 + gz2);
        var sx2 = cx + gx1 * GALS * galPop * per2;
        var sy2 = galY + gy1 * GALS * galPop * per2;

        var X, Y, size, alpha, sprite;
        // hoisted: the else branch (third act) reads flick too, and a
        // branch-local var leaves it undefined there -> NaN alpha
        var flick = p.lit < .5 ? .08 + .28 * Math.abs(Math.sin(t * 1.9 + p.ph * 5.3)) : 1;

        if (m <= 1) {
          var q = ease(Math.max(0, Math.min(1, m * 1.3 - p.st * .3)));
          X = sx0 + (sx1 - sx0) * q;
          Y = sy0 + (sy1 - sy0) * q;
          var sz0 = (5.5 + per0 * 7) * p.s;
          var sz1 = 4.6 + p.s * 3.2;
          var near = 0;
          var al0 = (.16 + .78 * per0) * asm;
          if (cascOn) {
            var cOn = Math.min(Math.max(0, Math.min(1, (t - 1.12) / .25)),
                               Math.max(0, Math.min(1, (2.85 - t) / .4)));
            var litF = Math.max(0, Math.min(1, (cascR + .08 - p.nr) / .14));
            var cdd = Math.abs(p.nr - cascR);
            near = cdd < .09 ? 1 - cdd / .09 : 0;
            al0 *= (1 - cOn) + cOn * (.3 + 1.25 * litF);
          }
          size = (sz0 + (sz1 - sz0) * q) * (1 + .9 * near);
          var al1 = (.42 + .5 * Math.abs(Math.sin(t * .75 + p.ph * 3))) * flick;
          alpha = al0 + (al1 - al0) * q;
          if (q > 0) {
            var wave = Math.max(.04, Math.min(1, (sweepF - p.sxr) * 7));
            alpha *= 1 - q + q * wave;
          }
          sprite = q > .55 ? (p.cool ? SP.cool : p.warm ? SP.lamp : SP.white)
            : (p.c === 0 ? SP.cyan : p.c === 1 ? SP.violet : SP.white);
          if (near > .06) sprite = SP.white;
        } else {
          var q2 = ease(Math.max(0, Math.min(1, (m - 1) * 1.3 - p.st * .3)));
          X = sx1 + (sx2 - sx1) * q2;
          Y = sy1 + (sy2 - sy1) * q2;
          var sz1b = 4.6 + p.s * 3.2;
          var sz2 = (5.2 + per2 * 7) * p.s;
          size = sz1b + (sz2 - sz1b) * q2;
          var al1b = (.42 + .5 * Math.abs(Math.sin(t * .75 + p.ph * 3))) * flick;
          var al2 = (.2 + .88 * per2) * galA;
          alpha = al1b + (al2 - al1b) * q2;
          sprite = q2 > .5 ? (p.c === 0 ? SP.cyan : p.c === 1 ? SP.violet : SP.white)
            : (p.cool ? SP.cool : p.warm ? SP.lamp : SP.white);
        }

        SX[i] = X; SY[i] = Y; SZ[i] = size;
        SA[i] = Math.max(0, Math.min(1, alpha));
        SC[i] = sprite;
      }

      ctx.globalCompositeOperation = "lighter";

      /* --- synapses --- */
      if (neuralA > .02) {
        ctx.strokeStyle = "rgba(110,200,255,1)";
        ctx.lineWidth = .7;
        for (var l = 0; l < links.length; l++) {
          var la = links[l][0], lb = links[l][1];
          ctx.globalAlpha = (.045 + (SZ[la] + SZ[lb]) * .006) * neuralA;
          ctx.beginPath();
          ctx.moveTo(SX[la], SY[la]);
          ctx.lineTo(SX[lb], SY[lb]);
          ctx.stroke();
        }
        if (cascOn && Math.random() < .6) {
          var lk = (Math.random() * links.length) | 0;
          var la2 = links[lk][0], lb2 = links[lk][1];
          if (Math.abs((pts[la2].nr + pts[lb2].nr) * .5 - cascR) < .13) {
            pulses.push({ l: lk, p: 0, sp: .035 + Math.random() * .05 });
          }
        }
        for (var pi = pulses.length - 1; pi >= 0; pi--) {
          var pu = pulses[pi];
          pu.p += pu.sp;
          if (pu.p >= 1) { pulses.splice(pi, 1); continue; }
          var pa = links[pu.l][0], pb = links[pu.l][1];
          var hx = SX[pa] + (SX[pb] - SX[pa]) * pu.p;
          var hy = SY[pa] + (SY[pb] - SY[pa]) * pu.p;
          var fade = Math.sin(Math.PI * pu.p) * neuralA;
          ctx.globalAlpha = fade;
          ctx.drawImage(SP.white, hx - 6, hy - 6, 12, 12);
          ctx.globalAlpha = fade * .45;
          ctx.drawImage(SP.cyan, hx - 13, hy - 13, 26, 26);
        }
      }

      /* --- deep field --- */
      if (galA > .02) {
        for (var s2 = 0; s2 < stars.length; s2++) {
          var st2 = stars[s2];
          ctx.globalAlpha = st2.a * galA * (.5 + .5 * Math.sin(t * 1.3 + st2.ph));
          ctx.fillStyle = "#cfe9ff";
          ctx.fillRect(st2.x, st2.y, st2.s, st2.s);
        }
      }

      /* --- galactic core --- */
      if (galA > .02) {
        var coreS = GALS * (1.16 + Math.sin(t * 1.6) * .04);
        ctx.globalAlpha = .42 * galA;
        ctx.drawImage(SP.cyan, cx - coreS / 2, galY - coreS / 2, coreS, coreS);
        ctx.globalAlpha = .24 * galA;
        ctx.drawImage(SP.white, cx - coreS * .21, galY - coreS * .21, coreS * .42, coreS * .42);
      }

      for (var j = 0; j < pts.length; j++) {
        ctx.globalAlpha = SA[j];
        ctx.drawImage(SC[j], SX[j] - SZ[j] / 2, SY[j] - SZ[j] / 2, SZ[j], SZ[j]);
      }

      /* two beats: the mind lights up, the universe opens */
      var flash = 0;
      if (t > 1.12 && t < 1.6) flash = 1 - (t - 1.12) / .48;
      if (t > 5.15 && t < 5.6) flash = Math.max(flash, 1 - (t - 5.15) / .45);
      if (flash > 0) {
        ctx.globalAlpha = flash * .45;
        var fs = R * .8;
        ctx.drawImage(SP.white, cx - fs / 2, sceneY - fs / 2, fs, fs);
      }

      ctx.globalAlpha = 1;
      raf = requestAnimationFrame(frame);
    }

    var ro = window.ResizeObserver ? new ResizeObserver(build) : null;
    if (ro) ro.observe(cv);
    window.addEventListener("resize", build);
    build();
    raf = requestAnimationFrame(frame);

    return function () {
      stopped = true;
      cancelAnimationFrame(raf);
      if (ro) ro.disconnect();
      window.removeEventListener("resize", build);
      ctx.clearRect(0, 0, w, h);
    };
  }

  /* Build the overlay once. The film owns the viewport while it runs; the
     launcher waits behind it, softened, and is revealed on the last frame. */
  function mount() {
    if (document.getElementById("promo")) return document.getElementById("promo");
    var root = document.createElement("div");
    root.className = "promo";
    root.id = "promo";
    root.innerHTML = [
      '<canvas id="promo-canvas" aria-hidden="true"></canvas>',
      '<div class="promo-core" aria-hidden="true"></div>',
      '<div class="promo-acts" aria-hidden="true">',
      '<div class="act"><span class="act-head"><b>01</b><i>synapses</i></span>',
      '<p class="act-t">one mind</p></div>',
      '<div class="act"><span class="act-head"><b>02</b><i>skyline</i></span>',
      '<p class="act-t">a city of minds</p></div>',
      '<div class="act"><span class="act-head"><b>03</b><i>galaxy</i></span>',
      '<p class="act-t">the whole universe</p></div>',
      '</div>',
      '<div class="promo-copy">',
      '<p class="promo-word" role="img" aria-label="site-kg">',
      '<span style="--i:0">s</span><span style="--i:1">i</span>',
      '<span style="--i:2">t</span><span style="--i:3">e</span>',
      '<em style="--i:4">-</em>',
      '<span style="--i:5">k</span><span style="--i:6">g</span>',
      '</p>',
      '<p class="promo-sub">turn any site into a world</p>',
      '</div>',
      '<div class="promo-foot">',
      '<div class="promo-bar"><i></i></div>',
      '<div class="promo-meta"><span><b>local engine</b></span><s>&middot;</s>',
      '<span><b>3 worlds</b></span><s>&middot;</s>',
      '<span><b>nothing uploaded</b></span></div>',
      '</div>',
      '<button class="promo-skip" type="button">skip intro <i>&rsaquo;</i></button>'
    ].join("");
    document.body.appendChild(root);
    return root;
  }

  function reduced() {
    return !!(window.matchMedia
      && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  }

  function seen() {
    try { return sessionStorage.getItem(KEY) === "1"; } catch (e) { return false; }
  }

  function remember() {
    try { sessionStorage.setItem(KEY, "1"); } catch (e) {}
  }

  /* Play the film. opts.onDone fires the moment the launcher is handed back,
     so callers never wait on the fade-out. Returns true when it played. */
  function play(opts) {
    var o = opts || {};
    var force = o.force === true
      || /[?&]promo=1\b/.test(window.location.search);
    if (!force && (reduced() || seen())) return false;
    var root = mount();
    var cv = root.querySelector("#promo-canvas");
    var skip = root.querySelector(".promo-skip");
    var acts = Array.prototype.slice.call(root.querySelectorAll(".act"));
    var stop = null, done = false;

    function showAct(i) {
      for (var j = 0; j < acts.length; j++) {
        acts[j].classList.toggle("on", j === i);
      }
    }

    function finish() {
      if (done) return;
      done = true;
      remember();
      document.documentElement.classList.remove("promo-live");
      root.classList.add("out");
      if (stop) stop();
      window.removeEventListener("keydown", onKey, true);
      setTimeout(function () {
        if (root.parentNode) root.parentNode.removeChild(root);
      }, 900);
      if (o.onDone) o.onDone();
    }

    function onKey(e) {
      if (e.key === "Escape" || e.key === "Enter" || e.key === " ") finish();
    }

    document.documentElement.classList.add("promo-live");
    skip.addEventListener("click", function (e) { e.stopPropagation(); finish(); });
    root.addEventListener("click", finish);
    window.addEventListener("keydown", onKey, true);

    if (reduced()) {
      showAct(acts.length - 1);
      root.classList.add("ready", "in");
      setTimeout(finish, 1600);
      return true;
    }

    root.classList.add("ready");
    stop = startCanvas(cv);

    /* one mind, many minds, everything, then the wordmark */
    setTimeout(function () { showAct(0); }, 560);
    setTimeout(function () { showAct(-1); }, 2280);
    setTimeout(function () { showAct(1); }, 2620);
    setTimeout(function () { showAct(-1); }, 4220);
    setTimeout(function () { showAct(2); }, 4520);
    setTimeout(function () { showAct(-1); }, 6140);
    setTimeout(function () { root.classList.add("in"); }, 6150);
    setTimeout(finish, 7800);
    return true;
  }

  SK.promo = {play: play, seen: seen};
})(window.SK = window.SK || {});
