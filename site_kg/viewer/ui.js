/* HUD, search, chapters, launcher, pointer/keyboard wiring. */
(function (SK) {
  "use strict";

  function byId(id) { return document.getElementById(id); }

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  function UI() {
    this.ctx = null;
    this.hoverIndex = -1;
    this._hoverRaf = 0;
    this._scene = "planets";
    this.es = null;
    this.token = "";
  }

  UI.prototype.mount = function (root, ctx) {
    this.ctx = ctx;
    this.cfg = ctx.cfg;
    this.live = ctx.live;
    this.renderer = ctx.renderer;
    this.camera = ctx.camera;
    this.director = ctx.director;
    this.root = root;
    this._scene = this.cfg.scene || "planets";
    this.statusEl = byId("status");
    this.infoEl = byId("info");
    this.tipEl = byId("tip");
    this.legendEl = byId("legend");
    this.launcherEl = byId("launcher");
    this._buildBar();
    this._buildLegend();
    this._wirePointer();
    this._wireKeys();
    this._buildFinale();
    this.setSceneActive(this._scene);
    this._updateCount();
    return this;
  };

  UI.prototype._buildFinale = function () {
    var box = el("div", "panel");
    box.id = "finale";
    box.style.cssText = "position:fixed;left:50%;bottom:34px;transform:translateX(-50%);"
      + "padding:10px 16px;z-index:7;display:none;pointer-events:none;"
      + "text-align:center;max-width:80vw";
    document.body.appendChild(box);
    this.finaleEl = box;
  };

  UI.prototype._buildBar = function () {
    var bar = this.root.querySelector ? this.root.querySelector("#bar") : null;
    if (!bar) bar = this.root;
    bar.innerHTML = "";
    var title = el("b", null, this.cfg.title || "site-kg graph");
    title.id = "title";
    var stats = el("span", "muted", this.cfg.stats || "");
    stats.id = "stats";
    bar.appendChild(title);
    bar.appendChild(stats);

    var scenes = el("span");
    scenes.style.display = "flex";
    scenes.style.gap = "6px";
    var self = this;
    (SK.sceneOrder || ["planets"]).forEach(function (name) {
      var b = el("button", "mb", (SK.scenes[name] && SK.scenes[name].label) || name);
      b.type = "button";
      b.setAttribute("data-scene", name);
      b.title = "scene " + name;
      b.addEventListener("click", function () { self.activateScene(name); });
      scenes.appendChild(b);
    });
    bar.appendChild(scenes);

    var wrap = el("span", "swrap");
    var input = el("input");
    input.id = "q";
    input.type = "search";
    input.autocomplete = "off";
    input.placeholder = "filter / search title\u2026 (/)";
    input.setAttribute("aria-label", "search pages");
    var res = el("div");
    res.id = "sres";
    res.setAttribute("role", "listbox");
    res.setAttribute("aria-label", "search results");
    wrap.appendChild(input);
    wrap.appendChild(res);
    bar.appendChild(wrap);
    this.input = input;
    this.results = res;

    var chap = el("button", "mb", "chapters");
    chap.type = "button";
    chap.addEventListener("click", function () {
      self.legendEl.classList.toggle("on");
      if (self.legendEl.classList.contains("on")) self._buildLegend();
    });
    bar.appendChild(chap);

    var cnt = el("span", "muted", "");
    cnt.id = "cnt";
    bar.appendChild(cnt);
    this.cntEl = cnt;
    var hint = el("span", "muted",
                  "drag rotate \u00b7 wheel zoom \u00b7 click node = focus \u00b7 1\u20133 scenes \u00b7 "
                  + "F finale \u00b7 shift+F fit \u00b7 space replay");
    hint.id = "hint";
    bar.appendChild(hint);

    input.addEventListener("input", function () {
      self.director.setQuery(input.value);
      self._buildSearch(input.value);
      self._updateCount();
    });
    document.addEventListener("click", function (e) {
      if (!wrap.contains(e.target)) self._closeResults();
    });
    return bar;
  };

  UI.prototype._buildSearch = function (raw) {
    var res = this.results;
    res.innerHTML = "";
    var q = String(raw || "").trim().toLowerCase();
    if (q.length < 2) {
      this._closeResults();
      return;
    }
    var live = this.live, hits = [], i;
    for (i = 0; i < live.n; i++) {
      if (!live.revealed[i]) continue;
      if ((live.titles[i] || "").toLowerCase().indexOf(q) >= 0) hits.push(i);
    }
    hits.sort(function (a, b) { return (live.degree[b] || 0) - (live.degree[a] || 0); });
    hits = hits.slice(0, 8);
    var self = this;
    hits.forEach(function (idx) {
      var row = el("div", "sr", live.titles[idx] || live.ids[idx]);
      row.setAttribute("role", "option");
      var meta = el("span", "m",
                    (live.clusters[live._nodeCluster[idx]] || "(root)") +
                    " \u00b7 deg " + (live.degree[idx] || 0));
      row.appendChild(meta);
      row.addEventListener("click", function () {
        self._closeResults();
        self.input.value = "";
        self.director.setQuery("");
        var focus = self.director.focus(idx);
        self.showInfo(focus);
        self._updateCount();
      });
      res.appendChild(row);
    });
    if (hits.length) res.classList.add("open");
    else res.classList.remove("open");
  };

  UI.prototype._closeResults = function () {
    if (this.results) {
      this.results.classList.remove("open");
      this.results.innerHTML = "";
    }
  };

  UI.prototype._buildLegend = function () {
    var live = this.live, legend = this.legendEl;
    if (!legend) return;
    legend.innerHTML = "";
    var counts = {}, order = [];
    for (var i = 0; i < live.n; i++) {
      var c = live.clusters[live._nodeCluster[i]] || "(root)";
      if (counts[c] === undefined) {
        counts[c] = 0;
        order.push(c);
      }
      counts[c]++;
    }
    order.sort(function (a, b) { return counts[b] - counts[a]; });
    legend.appendChild(el("h4", null, "chapters"));
    var palette = (this.renderer.cfg && this.renderer.cfg.palette) || SK.PALETTE;
    var self = this;
    if (!order.length) {
      legend.appendChild(el("div", "muted", "no chapters yet"));
    } else {
      order.slice(0, 60).forEach(function (name) {
        var row = el("div", "ch");
        row.setAttribute("data-ch", name);
        if (self.director.chapter === name) row.classList.add("on");
        var dot = el("span", "dot");
        var cid = live.clusterIndex[name] === undefined ? 0 : live.clusterIndex[name];
        dot.style.background = "#" + ("000000" + palette[cid % palette.length].toString(16)).slice(-6);
        row.appendChild(dot);
        row.appendChild(el("span", null, name));
        row.appendChild(el("span", "n", String(counts[name])));
        row.addEventListener("click", function () {
          var active = self.director.setChapter(name);
          var rows = legend.querySelectorAll(".ch[data-ch]");
          for (var k = 0; k < rows.length; k++) {
            rows[k].classList.toggle("on", rows[k].getAttribute("data-ch") === active);
          }
          self._updateCount();
        });
        legend.appendChild(row);
      });
    }

    var kindCounts = {}, kindOrder = [];
    for (var e = 0; e < live.m; e++) {
      var kind = live.kinds[live.eKind[e]] || "ref";
      if (kindCounts[kind] === undefined) {
        kindCounts[kind] = 0;
        kindOrder.push(kind);
      }
      kindCounts[kind]++;
    }
    legend.appendChild(el("h4", null, "edge kinds"));
    SK.kindLegend(kindOrder, this.director.hiddenKinds).forEach(function (entry) {
      var row = el("div", "ch");
      row.setAttribute("data-kind", entry.kind);
      row.title = entry.derived ? "derived relation" : "structural link";
      var dot = el("span", "dot");
      dot.style.background = entry.derived ? "#8b949e" : "#58a6ff";
      row.appendChild(dot);
      row.appendChild(el("span", null, entry.kind));
      row.appendChild(el("span", "n", String(kindCounts[entry.kind] || 0)));
      row.style.opacity = entry.on ? "1" : "0.4";
      row.addEventListener("click", function () {
        var hidden = self.director.toggleKind(entry.kind);
        row.style.opacity = hidden ? "0.4" : "1";
        self._updateCount();
      });
      legend.appendChild(row);
    });
  };

  UI.prototype._updateCount = function () {
    if (!this.cntEl) return;
    var live = this.live, visible = 0;
    for (var i = 0; i < live.n; i++) if (live.dim[i] > 0.5) visible++;
    var text = visible + "/" + live.n;
    var selection = this.director && this.director.selection;
    if (selection && selection.length) text += " \u00b7 ego " + selection.length;
    this.cntEl.textContent = text;
  };

  UI.prototype.updateStatus = function (s) {
    if (!this.statusEl) return;
    var parts = [s.nodes + " nodes", s.edges + " edges"];
    if (s.revealed < s.nodes) parts.push("revealed " + s.revealed);
    if (s.pending) parts.push("pending " + s.pending);
    if (s.ego) parts.push("ego " + s.ego);
    parts.push(s.scene, s.state, s.fps + " fps");
    this.statusEl.textContent = parts.join(" \u00b7 ");
    this._updateCount();
  };

  UI.prototype.showFinale = function (sceneName, status) {
    var box = this.finaleEl;
    if (!box) return;
    var scene = (SK.scenes && SK.scenes[sceneName]) || {};
    var parts = [];
    if (this.cfg && this.cfg.stats) parts.push(this.cfg.stats);
    if (status && status.ego) parts.push("ego " + status.ego);
    else if (status && status.visible) parts.push(status.visible + " visible");
    box.innerHTML = "";
    box.appendChild(el("b", null, scene.finale || "Finale"));
    if (parts.length) box.appendChild(el("div", "muted", parts.join(" \u00b7 ")));
    box.style.display = "block";
  };

  UI.prototype.hideFinale = function () {
    if (this.finaleEl) this.finaleEl.style.display = "none";
  };

  UI.prototype.setSceneActive = function (name) {
    this._scene = name;
    var bar = this.root.querySelector ? this.root.querySelector("#bar") : null;
    if (!bar) return;
    var buttons = bar.querySelectorAll("[data-scene]");
    for (var i = 0; i < buttons.length; i++) {
      buttons[i].classList.toggle("on", buttons[i].getAttribute("data-scene") === name);
    }
    var launch = this.launcherEl ? this.launcherEl.querySelectorAll(".scene") : [];
    for (var k = 0; k < launch.length; k++) {
      launch[k].classList.toggle("on", launch[k].getAttribute("data-scene") === name);
    }
  };

  UI.prototype.activateScene = function (name) {
    if (!SK.scenes || !SK.scenes[name]) return;
    this.director.switchScene(name);
    this.setSceneActive(name);
    this._buildLegend();
  };

  UI.prototype.showInfo = function (index) {
    var live = this.live, info = this.infoEl;
    if (!info) return;
    if (index < 0 || index >= live.n) {
      info.classList.remove("on");
      info.innerHTML = "";
      return;
    }
    info.innerHTML = "";
    info.appendChild(el("b", null, live.titles[index] || live.ids[index]));
    info.appendChild(el("div", "muted", live.ids[index]));
    info.appendChild(el("div", "muted",
      "chapter: " + (live.clusters[live._nodeCluster[index]] || "(root)") +
      " \u00b7 in-degree: " + (live.degree[index] || 0)));
    var url = live.urls[index] || "";
    if (/^https?:\/\//i.test(url)) {
      var a = el("a", null, "source \u2197");
      a.href = url;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      info.appendChild(a);
    }
    info.classList.add("on");
  };

  UI.prototype._wirePointer = function () {
    var canvas = this.renderer.canvas, self = this;
    canvas.addEventListener("pointermove", function (e) {
      var rect = canvas.getBoundingClientRect();
      self._mouse = {x: e.clientX - rect.left, y: e.clientY - rect.top,
                     cx: e.clientX, cy: e.clientY};
      if (!self._hoverRaf) {
        self._hoverRaf = requestAnimationFrame(function () {
          self._hoverRaf = 0;
          self._doHover();
        });
      }
    });
    canvas.addEventListener("pointerleave", function () {
      self._setHover(-1);
    });
    var down = null;
    canvas.addEventListener("pointerdown", function (e) {
      down = {x: e.clientX, y: e.clientY};
    });
    canvas.addEventListener("pointerup", function (e) {
      if (!down) return;
      var moved = Math.hypot(e.clientX - down.x, e.clientY - down.y);
      down = null;
      if (moved > 6) return;
      var rect = canvas.getBoundingClientRect();
      var idx = self.renderer.pick(e.clientX - rect.left, e.clientY - rect.top);
      var focus = self.director.focus(idx);
      self.showInfo(focus);
    });
  };

  UI.prototype._doHover = function () {
    var m = this._mouse;
    if (!m) return;
    var idx = this.renderer.pick(m.x, m.y);
    this._setHover(idx);
  };

  UI.prototype._setHover = function (idx) {
    this.hoverIndex = idx;
    var tip = this.tipEl, live = this.live;
    if (!tip) return;
    if (idx < 0 || idx >= live.n) {
      tip.classList.remove("on");
      this.renderer.canvas.style.cursor = "";
      return;
    }
    tip.textContent = (live.titles[idx] || live.ids[idx]) + " \u00b7 " +
      (live.clusters[live._nodeCluster[idx]] || "(root)") +
      " \u00b7 deg " + (live.degree[idx] || 0);
    tip.classList.add("on");
    var m = this._mouse || {cx: 0, cy: 0};
    var x = Math.min(m.cx + 14, window.innerWidth - 380);
    var y = Math.min(m.cy + 12, window.innerHeight - 40);
    tip.style.left = Math.max(8, x) + "px";
    tip.style.top = Math.max(8, y) + "px";
    this.renderer.canvas.style.cursor = "pointer";
  };

  UI.prototype._wireKeys = function () {
    var self = this;
    document.addEventListener("keydown", function (e) {
      var tag = (e.target && e.target.tagName) || "";
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") {
        if (e.key === "Escape" && e.target === self.input) {
          self.director.clear();
          self.input.value = "";
          self._closeResults();
          self.showInfo(-1);
          self._updateCount();
          e.target.blur();
        }
        return;
      }
      if (e.key === "/") {
        e.preventDefault();
        self.input.focus();
        return;
      }
      if (e.key === "Escape") {
        self.director.clear();
        self.input.value = "";
        self._closeResults();
        self.showInfo(-1);
        self._updateCount();
        return;
      }
      if (e.key === "f" || e.key === "F") {
        if (e.shiftKey) self.director.fit();
        else if (self.director.skipToFinale) self.director.skipToFinale();
        return;
      }
      if (e.key === " ") {
        e.preventDefault();
        if (!self.director.replayArchive()) {
          self.camera.setAutoRotate(!self.camera.controls.autoRotate);
        }
        return;
      }
      if (e.key >= "1" && e.key <= "3") {
        var n = parseInt(e.key, 10) - 1;
        var names = SK.sceneOrder || [];
        if (names[n]) self.activateScene(names[n]);
      }
    });
  };

  UI.prototype.showLauncher = function () {
    if (!this.launcherEl) return;
    var card = this.launcherEl.querySelector("#launch-card");
    if (!card) return;
    if (!card.firstChild) this._buildLauncher(card);
    this.launcherEl.classList.add("on");
  };

  UI.prototype._buildLauncher = function (card) {
    var self = this;
    card.innerHTML = "";
    card.appendChild(el("h1", null, "site-kg live"));
    card.appendChild(el("p", null,
      "Enter a site address, pick a scene, then watch the graph grow as pages arrive."));
    var row = el("div");
    row.id = "launch-row";
    var input = el("input");
    input.id = "launch-url";
    input.type = "url";
    input.placeholder = "https://docs.example.com";
    var prefill = this.cfg.title || "";
    if (prefill === "site-kg graph" || prefill === "site-kg live") prefill = "";
    input.value = prefill;
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") self.launchLive(input.value.trim(), self._scene);
    });
    row.appendChild(input);
    card.appendChild(row);
    var grid = el("div", "scenes");
    (SK.sceneOrder || []).forEach(function (name) {
      var scene = SK.scenes[name];
      var b = el("button", "scene");
      b.type = "button";
      b.setAttribute("data-scene", name);
      b.appendChild(el("b", null, scene.label));
      b.appendChild(el("span", null, scene.hint));
      if (name === self._scene) b.classList.add("on");
      b.addEventListener("click", function () {
        self.activateScene(name);
        self.setSceneActive(name);
      });
      grid.appendChild(b);
    });
    card.appendChild(grid);
    var go = el("button", null, "Start live ingest");
    go.id = "launch-go";
    go.type = "button";
    go.addEventListener("click", function () {
      self.launchLive(input.value.trim(), self._scene);
    });
    card.appendChild(go);
    var err = el("p", "muted", "");
    err.id = "launch-err";
    card.appendChild(err);
  };

  UI.prototype.showScenes = function () {
    if (this.cfg.mode === "live") this.showLauncher();
  };

  UI.prototype.hideLauncher = function () {
    if (this.launcherEl) this.launcherEl.classList.remove("on");
  };

  UI.prototype.connectLive = function (base, scene) {
    if (scene) this.activateScene(scene);
    this.hideLauncher();
    this.director.connectEvents("", 0, {base: base || "", token: this.token});
  };

  UI.prototype.launchLive = function (target, scene) {
    var self = this;
    if (!target) {
      var err = byId("launch-err");
      if (err) err.textContent = "A site address is required.";
      return null;
    }
    if (scene) this.activateScene(scene);
    this.statusEl.textContent = "starting live ingest \u2026";
    var headers = {"Content-Type": "application/json"};
    if (this.token) headers["X-Site-KG-Token"] = this.token;
    var request = typeof fetch === "function"
      ? fetch("/api/start", {
          method: "POST",
          headers: headers,
          body: JSON.stringify({target: target, scene: scene})
        })
      : Promise.reject(new Error("fetch is not available"));
    return request.then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (body) {
        if (!res.ok || !body.ok) {
          throw new Error(body.error || ("start failed: " + res.status));
        }
        self.hideLauncher();
        self.director.start("live", null);
        self.director.connectEvents(body.run_id || "", body.last_seq || 0,
                                    {base: "", token: self.token});
        return body;
      });
    }).catch(function (err) {
      self.statusEl.textContent = "live start failed";
      self.showLauncher();
      var box = byId("launch-err");
      if (box) box.textContent = (err && err.message) ? err.message : String(err);
      return null;
    });
  };

  UI.prototype.fatal = function (err) {
    var box = byId("fatal");
    if (!box) return;
    box.textContent = "viewer failed: " + (err && err.message ? err.message : err);
    box.classList.add("on");
  };

  SK.UI = UI;

  function loadArchive(cfg) {
    if (cfg.external) {
      var names = ["sk-graph.bin", "sk-layout.bin", "sk-timeline.bin"];
      return Promise.all(names.map(function (name) {
        return fetch(name).then(function (res) {
          if (!res.ok) throw new Error("cannot fetch " + name + " (" + res.status + ")");
          return res.arrayBuffer();
        });
      })).then(function (parts) {
        return SK.decodeArchiveParts(new Uint8Array(parts[0]), new Uint8Array(parts[1]),
                                     new Uint8Array(parts[2]));
      });
    }
    var text = byId("sk-payload");
    return SK.inflateBase64(text ? text.textContent : "").then(SK.decodeArchiveBuffer);
  }

  SK.boot = function () {
    var cfg = {};
    var token = "";
    try {
      token = new URLSearchParams(window.location.search).get("token") || "";
    } catch (queryErr) {
      token = "";
    }
    try {
      cfg = JSON.parse(byId("sk-config").textContent);
    } catch (err) {
      var box = byId("fatal");
      if (box) {
        box.textContent = "viewer config is not valid JSON";
        box.classList.add("on");
      }
      return;
    }
    try {
      var live = new SK.LiveGraph(cfg.mode === "archive" ? 4096 : 1024);
      var canvas = byId("sk-canvas");
      var renderer;
      if (SK.Fallback2D && SK.hasWebGL2 && !SK.hasWebGL2()) {
        renderer = new SK.Fallback2D(canvas);
        renderer.setScene(cfg.scene || "planets");
      } else {
        renderer = new SK.Renderer();
        try {
          renderer.mount(canvas, live);
          renderer.setScene(cfg.scene || "planets");
        } catch (mountErr) {
          try { if (renderer.dispose) renderer.dispose(); } catch (ignored) {}
          if (!SK.Fallback2D) throw mountErr;
          renderer = new SK.Fallback2D(canvas);
          renderer.setScene(cfg.scene || "planets");
        }
      }
      var camera = new SK.Camera(renderer);
      camera.attach(live);
      var labels = new SK.Labels(byId("sk-labels"), renderer);
      var director = new SK.Director(live, renderer, camera, labels);
      var ui = new SK.UI();
      ui.token = token;
      ui.mount(byId("hud"), {cfg: cfg, live: live, renderer: renderer,
                             camera: camera, director: director});
      director.attach(ui);
      if (cfg.title) document.title = cfg.title;

      if (cfg.mode === "archive") {
        loadArchive(cfg).then(function (payload) {
          director.start("archive", payload);
          ui.hideLauncher();
        }).catch(function (err) {
          ui.fatal(err);
        });
      } else {
        director.start("live", null);
        if (cfg.liveUrl) ui.connectLive(cfg.liveUrl, cfg.scene);
        else ui.showLauncher();
      }

      window.addEventListener("resize", function () {
        renderer.resize();
        labels.resize();
      });
      var last = performance.now();
      function frame(now) {
        requestAnimationFrame(frame);
        var dt = Math.min(0.1, Math.max(0, (now - last) / 1000));
        last = now;
        director.tick(dt);
      }
      requestAnimationFrame(frame);
    } catch (err) {
      var fatal = byId("fatal");
      if (fatal) {
        fatal.textContent = "viewer failed: " + (err && err.message ? err.message : err);
        fatal.classList.add("on");
      }
      throw err;
    }
  };
})(window.SK = window.SK || {});
