/* Camera rig: OrbitControls plus scripted fit/focus/push-in tweens. */
(function (SK) {
  "use strict";

  function Camera(renderer) {
    var THREE = T3.THREE;
    this.renderer = renderer;
    this.camera = renderer.camera;
    this.live = null;
    this.tween = null;
    this.settled = true;
    this.userInteracting = false;
    this.reduced = !!(window.matchMedia &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches);
    this.controls = new T3.OrbitControls(this.camera, renderer.canvas);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.08;
    this.controls.rotateSpeed = 0.7;
    this.controls.zoomSpeed = 0.9;
    /* The orbit is driven from dt in update(), not by OrbitControls, whose
       auto-rotate advances a fixed angle per rendered frame: on a device
       that misses frames the head visibly steps instead of turning. */
    this.controls.autoRotate = false;
    this.controls.target.set(0, 0, 0);
    /* A head has a front: open on a three-quarter view from the left, a little
       above the temporal lobe. The later fits keep this direction, so the
       scene never opens looking straight down at an oval. */
    if (renderer.cfg && renderer.cfg.brain) this._placeBrain(1);
    if (renderer.cfg && renderer.cfg.city) this._placeCity(1);
    var self = this;
    this.controls.addEventListener("start", function () {
      self.userInteracting = true;
      self.tween = null;
      self.settled = true;
    });
    this.controls.addEventListener("end", function () {
      self.userInteracting = false;
    });
    this._tmp = new THREE.Vector3();
  }

  Camera.prototype.attach = function (live) {
    this.live = live;
    return this;
  };

  /* The three-quarter opening shot. Shared by the constructor and the scene
     switch so the head is framed the same way every time it is chosen. */
  Camera.prototype._placeBrain = function (zoom) {
    var THREE = T3.THREE;
    var cfg = this.renderer.cfg || {};
    /* 2.3 radii clears the brainstem: the head is taller than its cortical
       radius, and at the fit zoom the bottom of the stem sat off-screen. */
    var d = (cfg.brainRadius || 17) * 2.3 * (zoom || 1);
    var p = new THREE.Vector3(-0.52, 0.30, 0.80).normalize()
      .multiplyScalar(d);
    p.y += 1.5;
    this.camera.position.copy(p);
    this.controls.target.set(0, 0.4, 0);
    this.camera.lookAt(this.controls.target);
  };

  Camera.prototype.frameBrain = function () {
    if (!this.renderer.cfg || !this.renderer.cfg.brain) return;
    var THREE = T3.THREE;
    var cfg = this.renderer.cfg;
    var d = (cfg.brainRadius || 17) * 2.3;
    var p = new THREE.Vector3(-0.52, 0.30, 0.80).normalize()
      .multiplyScalar(d);
    p.y += 1.5;
    this._to(p, new THREE.Vector3(0, 0.4, 0), 900);
  };

  /* The city is read from the air, not from orbit: a low three-quarter view
     puts the towers against the sky and keeps the street grid visible. The
     shot is shared by the constructor and the scene switch, and it honours
     the scene's radius floor so a small build is not framed inside a tower. */
  Camera.prototype._cityShot = function (zoom) {
    var THREE = T3.THREE;
    var cfg = this.renderer.cfg || {};
    var r = Math.max(this.renderer.sceneRadius || 18,
                    cfg.fitMinRadius != null ? cfg.fitMinRadius : 14);
    /* A skyline is shallow and wide: 2.15 radii of distance left the city a
       patch in the middle of the frame, so the shot sits closer and higher. */
    var d = r * 1.55 * (zoom || 1);
    var p = new THREE.Vector3(0.58, 0.42, 0.70).normalize().multiplyScalar(d);
    p.y += r * 0.40;
    return {p: p, t: new THREE.Vector3(0, r * 0.16, 0)};
  };

  Camera.prototype._placeCity = function (zoom) {
    var s = this._cityShot(zoom);
    this.camera.position.copy(s.p);
    this.controls.target.copy(s.t);
    this.camera.lookAt(s.t);
  };

  Camera.prototype.frameCity = function () {
    if (!this.renderer.cfg || !this.renderer.cfg.city) return;
    var s = this._cityShot(1);
    this._to(s.p, s.t, 900);
  };

  Camera.prototype.setAutoRotate = function (on) {
    this.controls.autoRotate = !!on && !this.userInteracting && !this.reduced;
  };

  Camera.prototype.fit = function (points, opts) {
    opts = opts || {};
    var THREE = T3.THREE;
    var n = Math.floor(points.length / 3);
    if (!n) return;
    /* Bounding-box centre, not the centroid: a lopsided cluster must not drag
       the target away and inflate the radius of the whole scene. */
    var minX = Infinity, minY = Infinity, minZ = Infinity;
    var maxX = -Infinity, maxY = -Infinity, maxZ = -Infinity;
    var cx, cy, cz, i;
    for (i = 0; i < n; i++) {
      var px = points[i * 3], py = points[i * 3 + 1], pz = points[i * 3 + 2];
      if (px < minX) minX = px;
      if (py < minY) minY = py;
      if (pz < minZ) minZ = pz;
      if (px > maxX) maxX = px;
      if (py > maxY) maxY = py;
      if (pz > maxZ) maxZ = pz;
    }
    cx = (minX + maxX) / 2; cy = (minY + maxY) / 2; cz = (minZ + maxZ) / 2;
    var dists = new Float64Array(n);
    for (i = 0; i < n; i++) {
      dists[i] = Math.hypot(points[i * 3] - cx, points[i * 3 + 1] - cy,
                            points[i * 3 + 2] - cz);
    }
    var radius = 1;
    if (opts.robust && n > 60) {
      var sorted = Array.prototype.slice.call(dists).sort(function (a, b) { return a - b; });
      radius = sorted[Math.floor(sorted.length * 0.92)] * 1.25;
    } else {
      for (i = 0; i < n; i++) if (dists[i] > radius) radius = dists[i];
    }
    radius = Math.max(radius, opts.minRadius || 1);
    var fov = this.camera.fov * Math.PI / 180;
    var distance = radius / Math.sin(fov / 2) * (opts.zoom || 1) * 1.2;
    var dir = this._tmp.copy(this.camera.position).sub(this.controls.target);
    if (dir.lengthSq() < 1e-6) dir.set(0, 0, 1);
    dir.normalize();
    this._to(new THREE.Vector3(cx, cy, cz).add(dir.multiplyScalar(distance)),
             new THREE.Vector3(cx, cy, cz), opts.duration || 650);
  };

  Camera.prototype.fitLive = function (opts) {
    if (!this.live) return;
    opts = opts || {robust: true};
    /* While the graph is still tiny the visible bounds are a couple of units
       wide; fitting to those dives the camera into the first planet and the
       rest of the build happens inside it. The floor keeps the opening shot
       wide enough to read as space, and the later fit (once the layout is
       real) pulls back from there. A scene may override the floor: the brain
       is a 17-unit head, and the open-cloud default frames it too small. */
    if (opts.minRadius == null) {
      var cfg = this.renderer && this.renderer.cfg;
      opts.minRadius = (cfg && cfg.fitMinRadius != null) ? cfg.fitMinRadius : 21;
      /* A head is a single compact object and can afford to sit closer to the
         frame than an open graph, whose outliers need the margin. */
      if (opts.zoom == null) opts.zoom = (cfg && cfg.fitZoom != null) ? cfg.fitZoom : 1;
    }
    this.fit(this.renderer.samplePoints(this.live, 6000), opts);
  };

  Camera.prototype.focus = function (index) {
    var live = this.live;
    if (!live || index < 0 || index >= live.n) return;
    var THREE = T3.THREE;
    var target = new THREE.Vector3();
    this.renderer.nodeWorld(index, target);
    var dir = this._tmp.copy(this.camera.position).sub(this.controls.target);
    if (dir.lengthSq() < 1e-6) dir.set(0, 0, 1);
    dir.normalize();
    var distance = Math.max(this.renderer.sceneRadius * 0.22, 12);
    this._to(target.clone().add(dir.multiplyScalar(distance)), target, 620);
  };

  Camera.prototype.pushIn = function (scale) {
    var THREE = T3.THREE;
    var target = this.controls.target.clone();
    var offset = new THREE.Vector3().copy(this.camera.position).sub(target);
    offset.multiplyScalar(scale || 0.82);
    if (offset.length() < 4) offset.setLength(4);
    this._to(target.clone().add(offset), target, 900);
  };

  Camera.prototype._to = function (pos, target, duration) {
    if (this.reduced) {
      this.camera.position.copy(pos);
      this.controls.target.copy(target);
      this.tween = null;
      this.settled = true;
      this.controls.autoRotate = false;
      return;
    }
    this.tween = {
      p0: this.camera.position.clone(), p1: pos,
      t0: this.controls.target.clone(), t1: target,
      t: 0, dur: duration || 600
    };
    this.settled = false;
    this.controls.autoRotate = false;
  };

  Camera.prototype.settleNow = function () {
    var tw = this.tween;
    if (tw) {
      this.camera.position.copy(tw.p1);
      this.controls.target.copy(tw.t1);
      this.tween = null;
    }
    this.settled = true;
  };

  Camera.prototype.setUserInteracting = function (flag) {
    this.userInteracting = !!flag;
    if (flag) {
      this.tween = null;
      this.settled = true;
    }
  };

  Camera.prototype._angle = function (dt) {
    var speed = this.renderer.cfg && this.renderer.cfg.spin;
    if (speed == null) speed = 0.6;
    if (!speed) return 0;
    return dt * speed * 0.10472;
  };

  /* Turns a point around a target on the ground plane. Shared by the camera
     and by the endpoints of a refit tween, so the two can never disagree on
     which way is forward. */
  Camera.prototype._rotate = function (p, t, a) {
    if (!a) return;
    var dx = p.x - t.x, dz = p.z - t.z;
    var c = Math.cos(a), s = Math.sin(a);
    p.x = t.x + dx * c - dz * s;
    p.z = t.z + dx * s + dz * c;
  };

  /* OrbitControls' auto-rotate speed is "degrees per frame at 60fps"; the
     same unit here is a rate per second, so the view turns at the speed the
     scene asks for regardless of how many frames the device manages. */
  Camera.prototype._spin = function (dt) {
    this._rotate(this.camera.position, this.controls.target, this._angle(dt));
  };

  Camera.prototype.update = function (dt) {
    var tw = this.tween;
    var spin = this.userInteracting || this.reduced ? 0 : this._angle(dt);
    if (tw) {
      tw.t += dt * 1000;
      var k = Math.min(1, tw.t / tw.dur);
      var e = 1 - Math.pow(1 - k, 3);
      this.camera.position.lerpVectors(tw.p0, tw.p1, e);
      this.controls.target.lerpVectors(tw.t0, tw.t1, e);
      /* A refit used to freeze the orbit for its whole duration, which read
         as a hitch every time the graph grew. Turning both endpoints by the
         same angle leaves this frame's lerp exactly where it is and starts
         the next one turned, so the camera keeps orbiting while it eases. */
      if (spin) {
        var anchor = this.controls.target;
        this._rotate(tw.p0, anchor, spin);
        this._rotate(tw.p1, anchor, spin);
        this._rotate(this.camera.position, anchor, spin);
      }
      if (k >= 1) {
        this.tween = null;
        this.settled = true;
      }
    } else if (spin) {
      this._rotate(this.camera.position, this.controls.target, spin);
    }
    this.controls.update();
    this.renderer.camDistance = this.camera.position.distanceTo(this.controls.target);
  };

  SK.Camera = Camera;
})(window.SK = window.SK || {});
