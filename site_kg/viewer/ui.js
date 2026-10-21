/* HUD, search, chapters, launcher poster, pointer/keyboard wiring. */
(function (SK) {
  "use strict";

  function byId(id) { return document.getElementById(id); }

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  var SVG_NS = "http://www.w3.org/2000/svg";

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
    this.infoEl = byId("info");
    this.tipEl = byId("tip");
    this.legendEl = byId("legend");
    this.launcherEl = byId("launcher");
    if (SK.launcher) SK.launcher.art = SK.launcher.loadArt();
    if (document.documentElement) document.documentElement.classList.add("sk-anim");
    this._buildBar();
    this._buildLegend();
    this._wireLegend();
    this._buildBuild();
    this._wirePointer();
    this._wireKeys();
    this._buildFinale();
    this._wireBarResize();
    this._wireReveals();
    this.setSceneActive(this._scene);
    this._updateCount();
    return this;
  };

  UI.prototype._buildFinale = function () {
    this.finaleNote = null;
  };

  UI.prototype._makeFinale = function () { return null; };

  /* The bar wraps at narrow widths; the canvas is offset by --bar, so keep the
     custom property in sync with the bar's natural height. #bar's min-height is
     a constant design floor, not var(--bar), or the floor would equal the
     measured height and the property would feed itself. min-height is still
     suspended while measuring so the natural height can fall below the floor. */
  UI.prototype._wireBarResize = function () {
    var hud = byId("hud");
    var bar = byId("bar");
    if (!hud || !bar) return;
    var sync = function () {
      bar.style.minHeight = "0px";
      /* The floating deck is inset by the HUD's padding, so the offset the
         canvas needs is the whole HUD box, not just the bar. */
      var h = Math.ceil(hud.getBoundingClientRect().height);
      bar.style.minHeight = "";
      if (h > 0) {
        document.documentElement.style.setProperty("--bar", Math.max(h, 60) + "px");
      }
    };
    this._barSync = sync;
    if (typeof ResizeObserver !== "undefined") {
      if (this._barRO) { try { this._barRO.disconnect(); } catch (ignored) {} }
      this._barRO = new ResizeObserver(sync);
      this._barRO.observe(hud);
    }
    window.addEventListener("resize", sync);
    sync();
  };

  /* Scroll reveals: hidden only once JS is alive (the sk-anim class), always
     ending visible, with a no-IntersectionObserver fallback. */
  UI.prototype._wireReveals = function () {
    var nodes = document.querySelectorAll(".reveal");
    if (!nodes.length) return;
    var i;
    if (typeof IntersectionObserver === "undefined") {
      for (i = 0; i < nodes.length; i++) nodes[i].classList.add("in");
      return;
    }
    if (!this._io) {
      var self = this;
      this._io = new IntersectionObserver(function (entries) {
        for (var k = 0; k < entries.length; k++) {
          if (!entries[k].isIntersecting) continue;
          entries[k].target.classList.add("in");
          self._io.unobserve(entries[k].target);
        }
      }, {rootMargin: "0px 0px -6% 0%", threshold: 0.05});
    }
    for (i = 0; i < nodes.length; i++) {
      if (!nodes[i].classList.contains("in")) this._io.observe(nodes[i]);
    }
  };

  UI.prototype._buildBar = function () {
    var bar = this.root.querySelector ? this.root.querySelector("#bar") : null;
    if (!bar) bar = this.root;
    bar.innerHTML = "";
    var self = this;

    /* Brand: the sigil and the crawl identity, stacked so the host never
       competes with the product name. */
    var brand = el("div", "hud-brand");
    brand.appendChild(el("span", "hud-mark"));
    var ident = el("div", "hud-id");
    /* A crawler's "title" is often the address itself; the deck already shows
       the host on the second line, so the brand stays the product name. */
    var brandName = this.cfg.title || "site-kg graph";
    if (/^https?:\/\//i.test(brandName)) brandName = "site-kg";
    var title = el("b", null, brandName);
    title.id = "title";
    title.title = this.cfg.title || "";
    ident.appendChild(title);
    this.titleEl = title;
    var target = el("span");
    target.id = "live-target";
    ident.appendChild(target);
    this.targetEl = target;
    brand.appendChild(ident);
    bar.appendChild(brand);

    /* Live telemetry: state chip first, then the page/node/edge readout. */
    var liveBox = el("div", "hud-live");
    var phase = el("span", null, "ingesting");
    phase.id = "phase";
    liveBox.appendChild(phase);
    this.phaseEl = phase;

    var readout = el("span", null, "");
    readout.id = "readout";
    liveBox.appendChild(readout);
    this.readoutEl = readout;

    var cnt = el("span", null, "");
    cnt.id = "cnt";
    liveBox.appendChild(cnt);
    this.cntEl = cnt;
    bar.appendChild(liveBox);

    /* Tools: scene switch, search, help. */
    var tools = el("div", "hud-tools");
    var scenes = el("span", "t-scenes");
    (SK.sceneOrder || ["planets"]).forEach(function (name) {
      var b = el("button", "mb", (SK.scenes[name] && SK.scenes[name].label) || name);
      b.type = "button";
      b.setAttribute("data-scene", name);
      b.title = "scene " + name;
      b.addEventListener("click", function () { self.activateScene(name); });
      scenes.appendChild(b);
    });
    tools.appendChild(scenes);

    var wrap = el("span", "swrap");
    var input = el("input");
    input.id = "q";
    input.type = "search";
    input.autocomplete = "off";
    input.placeholder = "search pages  /";
    input.setAttribute("aria-label", "search pages");
    var res = el("div");
    res.id = "sres";
    res.setAttribute("role", "listbox");
    res.setAttribute("aria-label", "search results");
    wrap.appendChild(input);
    wrap.appendChild(res);
    tools.appendChild(wrap);
    this.input = input;
    this.results = res;

    var chap = el("button", "mb", "chapters");
    chap.type = "button";
    chap.title = "chapters, edge kinds and controls";
    chap.setAttribute("aria-label", "chapters, edge kinds and controls");
    chap.setAttribute("aria-expanded", "false");
    chap.setAttribute("aria-controls", "legend");
    chap.addEventListener("click", function () {
      var on = self.legendEl.classList.toggle("on");
      chap.classList.toggle("on", on);
      chap.setAttribute("aria-expanded", on ? "true" : "false");
      /* The live-build card owns the same corner; it steps aside while the
         legend is open so the two panels never stack on each other. */
      var build = byId("build");
      if (build) build.classList.toggle("legend-open", on);
      if (on) self._buildLegend();
    });
    tools.appendChild(chap);
    bar.appendChild(tools);

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
      legend.appendChild(row);
    });

    legend.appendChild(el("h4", null, "controls"));
    var keys = el("div", "keys");
    [["drag", "rotate"], ["wheel", "zoom"], ["click", "focus"],
     ["1\u20132", "scenes"], ["F", "finale"], ["shift+F", "fit"],
     ["space", "replay"], ["/", "search"]].forEach(function (pair) {
      var row = el("div", "kr");
      row.appendChild(el("span", "kb", pair[0]));
      row.appendChild(el("span", "ka", pair[1]));
      keys.appendChild(row);
    });
    legend.appendChild(keys);
  };

  /* Row clicks are delegated from the panel itself: _buildLegend replaces the
     rows on every live flush, and listeners bound to rows that get replaced
     mid-crawl swallow the click. */
  UI.prototype._wireLegend = function () {
    var legend = this.legendEl, self = this;
    if (!legend) return;
    legend.addEventListener("click", function (e) {
      var node = e.target;
      while (node && node !== legend &&
             !(node.classList && node.classList.contains("ch"))) {
        node = node.parentNode;
      }
      if (!node || node === legend) return;
      var ch = node.getAttribute("data-ch");
      if (ch != null) {
        var active = self.director.setChapter(ch);
        var rows = legend.querySelectorAll(".ch[data-ch]");
        for (var k = 0; k < rows.length; k++) {
          rows[k].classList.toggle("on", rows[k].getAttribute("data-ch") === active);
        }
        self._updateCount();
        return;
      }
      var kind = node.getAttribute("data-kind");
      if (kind != null) {
        var hidden = self.director.toggleKind(kind);
        node.style.opacity = hidden ? "0.4" : "1";
        self._updateCount();
      }
    });
  };

  /* Build overlay: the stage, a progress bar, live counts and the last few
     page titles. It reads director.status() plus director.recent, so no extra
     server event or endpoint is involved. */
  UI.prototype._buildBuild = function () {
    var box = byId("build");
    if (!box) return;
    box.innerHTML = "";
    var panel = el("div", "b-panel");
    panel.appendChild(el("div", "b-kicker", "live build"));
    this.buildPhaseEl = el("div", "b-phase", "finding pages");
    this.buildFillEl = el("span", "b-fill");
    this.buildTrackEl = el("div", "b-track");
    this.buildTrackEl.appendChild(this.buildFillEl);
    this.buildCountsEl = el("div", "b-counts", "");
    this.buildRecentEl = el("ul", "b-recent");
    panel.appendChild(this.buildPhaseEl);
    panel.appendChild(this.buildTrackEl);
    panel.appendChild(this.buildCountsEl);
    panel.appendChild(this.buildRecentEl);
    box.appendChild(panel);
    this.buildEl = box;
    this.buildTitles = "";
    box.classList.add("hide");
  };

  UI.prototype._drawBuild = function (status) {
    var box = this.buildEl;
    if (!box || !SK.launcher) return;
    var launcherUp = this.launcherEl &&
      this.launcherEl.classList.contains("on");
    if (this.cfg.mode !== "live" || launcherUp) {
      box.classList.add("hide");
      return;
    }
    var v = SK.launcher.buildView(status, this.director.recent);
    box.classList.remove("hide");
    box.classList.toggle("bad", v.failed);
    box.classList.toggle("unknown", v.indeterminate);
    this.buildPhaseEl.textContent = v.phase;
    this.buildCountsEl.textContent = v.label;
    if (!v.indeterminate) {
      this.buildFillEl.style.transform = "scaleX(" + v.ratio.toFixed(3) + ")";
    } else {
      this.buildFillEl.style.transform = "";
    }
    var joined = v.titles.join("\n");
    if (joined !== this.buildTitles) {
      this.buildTitles = joined;
      this.buildRecentEl.innerHTML = "";
      for (var i = 0; i < v.titles.length; i++) {
        this.buildRecentEl.appendChild(el("li", null, v.titles[i]));
      }
    }
    var self = this;
    if (v.complete) {
      if (!this._buildOut && !this._buildDoneTimer) {
        this._buildDoneTimer = setTimeout(function () {
          self._buildDoneTimer = 0;
          self._buildOut = true;
          box.classList.add("out");
        }, 1200);
      }
    } else {
      if (this._buildDoneTimer) {
        clearTimeout(this._buildDoneTimer);
        this._buildDoneTimer = 0;
      }
      this._buildOut = false;
      box.classList.remove("out");
    }
  };

  UI.prototype._updateCount = function () {
    if (!this.cntEl) return;
    var live = this.live, visible = 0, i;
    for (i = 0; i < live.n; i++) if (live.dim[i] > 0.5) visible++;
    var d = this.director;
    var filtered = !!(d && ((d.query && String(d.query).trim()) || d.chapter
                            || (d.selection && d.selection.length)));
    this.cntEl.classList.toggle("on", filtered);
    if (!filtered) {
      this.cntEl.textContent = "";
      return;
    }
    var text = visible + "/" + live.n;
    var selection = d && d.selection;
    if (selection && selection.length) text += " \u00b7 ego " + selection.length;
    this.cntEl.textContent = text;
  };

  /* Live build readings: pages/nodes/edges prominent while ingesting, calm at
     rest; the phase chip carries the ingest state machine. */
  UI.prototype.updateStatus = function (s) {
    var L = SK.launcher;
    if (this.readoutEl && L) {
      var opts = null;
      if (this.cfg && this.cfg.mode === "archive") {
        /* archive pages are the crawled document count, not the node count
           (which max_nodes may have trimmed); with no real doc count, omit
           pages rather than printing nodes twice */
        opts = {pages: this.cfg.docs == null ? false : this.cfg.docs};
      }
      var r = L.readout(s, opts);
      this.readoutEl.textContent = r.label;
      this.readoutEl.classList.toggle("calm", !r.building);
    }
    if (this.phaseEl && L) {
      this.phaseEl.textContent = L.phaseLabel(s.state);
      this.phaseEl.classList.toggle("done", s.state === "complete");
    }
    this._drawBuild(s);
    this._updateCount();
  };

  UI.prototype.setLiveTarget = function (url) {
    var value = String(url == null ? "" : url);
    var host = SK.launcher ? SK.launcher.hostLabel(value) : value;
    if (this.targetEl) {
      this.targetEl.textContent = host;
      this.targetEl.title = host;
    }
    /* The brand is the product, the host is the subject: a live launch must
       not replace the masthead with a raw URL. */
    if (this.titleEl) this.titleEl.textContent = "site-kg";
    if (typeof document !== "undefined") {
      document.title = host ? "site-kg \u00b7 " + host : "site-kg";
    }
  };

  UI.finaleParts = function (cfg, status) {
    var parts = [];
    if (cfg && cfg.mode === "live" && status) {
      parts.push((status.pages || 0) + " pages \u00b7 " + (status.nodes || 0)
        + " nodes \u00b7 " + (status.edges || 0) + " edges");
    } else if (cfg && cfg.stats) {
      parts.push(cfg.stats);
    }
    if (status && status.ego) parts.push("ego " + status.ego);
    else if (status && status.visible) parts.push(status.visible + " visible");
    return parts;
  };

  /* The caption is painted by the label canvas (see Labels._finale); keeping
     it out of the DOM avoids a compositor ghost over the WebGL scene. */
  UI.prototype.showFinale = function (sceneName, status) {
    var scene = (SK.scenes && SK.scenes[sceneName]) || {};
    this.finaleNote = {
      title: scene.finale || "Finale",
      detail: UI.finaleParts(this.cfg, status).join(" \u00b7 ")
    };
  };

  UI.prototype.hideFinale = function () {
    this.finaleNote = null;
  };

  UI.prototype.setSceneActive = function (name) {
    this._scene = name;
    var bar = this.root.querySelector ? this.root.querySelector("#bar") : null;
    if (bar) {
      var buttons = bar.querySelectorAll("[data-scene]");
      for (var i = 0; i < buttons.length; i++) {
        buttons[i].classList.toggle("on", buttons[i].getAttribute("data-scene") === name);
      }
    }
    var launch = this.launcherEl ? this.launcherEl.querySelectorAll(".world") : [];
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
      var x = e.clientX - rect.left, y = e.clientY - rect.top;
      if (self._dragDown) {
        self.renderer.moveDrag(x, y);
        if (Math.hypot(e.clientX - self._dragDown.x,
                       e.clientY - self._dragDown.y) > 3) {
          self._dragDown.moved = true;
        }
        return;
      }
      self._mouse = {x: x, y: y, cx: e.clientX, cy: e.clientY};
      if (!self._hoverRaf) {
        self._hoverRaf = requestAnimationFrame(function () {
          self._hoverRaf = 0;
          self._doHover();
        });
      }
    });
    canvas.addEventListener("pointerleave", function () {
      if (self._dragDown) return;
      self._setHover(-1);
    });
    var down = null;
    canvas.addEventListener("pointerdown", function (e) {
      if (e.button !== 0) return;
      down = {x: e.clientX, y: e.clientY};
      var rect = canvas.getBoundingClientRect();
      var idx = self.renderer.pick(e.clientX - rect.left, e.clientY - rect.top);
      if (idx < 0) return;
      var begin = self.renderer.beginDrag(idx, e.clientX - rect.left,
                                          e.clientY - rect.top);
      if (!begin) return;
      /* The cell wins over the orbit rig: press-and-hold moves this node,
         release without travel still opens its page. */
      down.drag = idx;
      down.moved = false;
      self._dragDown = down;
      if (e.pointerId != null && canvas.setPointerCapture) {
        try { canvas.setPointerCapture(e.pointerId); } catch (ignored) {}
      }
      self.camera.controls.enabled = false;
      self.camera.controls.autoRotate = false;
      self._setHover(-1);
      canvas.style.cursor = "grabbing";
    });
    var release = function (e) {
      if (!down) return;
      var moved = Math.hypot(e.clientX - down.x, e.clientY - down.y);
      var dragging = down.drag !== undefined;
      var idx = down.drag;
      down = null;
      self._dragDown = null;
      if (dragging) {
        self.renderer.endDrag();
        self.camera.controls.enabled = true;
        canvas.style.cursor = "";
        if (e.pointerId != null && canvas.releasePointerCapture) {
          try { canvas.releasePointerCapture(e.pointerId); } catch (ignored) {}
        }
        if (moved > 4) return;
      } else if (moved > 6) {
        return;
      }
      var rect = canvas.getBoundingClientRect();
      if (idx === undefined) {
        idx = self.renderer.pick(e.clientX - rect.left, e.clientY - rect.top);
      }
      var focus = self.director.focus(idx);
      self.showInfo(focus);
    };
    canvas.addEventListener("pointerup", release);
    canvas.addEventListener("pointercancel", function (e) {
      if (!down) return;
      var dragging = down.drag !== undefined;
      down = null;
      self._dragDown = null;
      if (dragging) {
        self.renderer.endDrag();
        self.camera.controls.enabled = true;
        canvas.style.cursor = "";
      }
      if (e.pointerId != null && canvas.releasePointerCapture) {
        try { canvas.releasePointerCapture(e.pointerId); } catch (ignored) {}
      }
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
    this.renderer.canvas.style.cursor = "grab";
  };

  UI.prototype._wireKeys = function () {
    var self = this;
    document.addEventListener("keydown", function (e) {
      /* While the poster is up it owns the keyboard. */
      if (self.launcherEl && self.launcherEl.classList.contains("on")) return;
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
      if (e.key >= "1" && e.key <= "2") {
        var n = parseInt(e.key, 10) - 1;
        var names = SK.sceneOrder || [];
        if (names[n]) self.activateScene(names[n]);
      }
    });
  };

  /* ---- launcher poster ------------------------------------------------- */

  /* Cancel a pending hide: its timer and any animationend handler registered
     by an earlier hideLauncher call, so retry loops cannot accumulate them. */
  UI.prototype._dropLauncherHide = function () {
    if (this._launcherHideTimer) {
      clearTimeout(this._launcherHideTimer);
      this._launcherHideTimer = 0;
    }
    if (this.launcherEl && this._launcherAnimEnd) {
      this.launcherEl.removeEventListener("animationend", this._launcherAnimEnd);
      this._launcherAnimEnd = 0;
    }
  };

  UI.prototype.showLauncher = function () {
    if (!this.launcherEl) return;
    this._dropLauncherHide();
    var card = this.launcherEl.querySelector("#launch-card");
    if (!card) return;
    this.launcherEl.classList.remove("leave");
    this.launcherEl.classList.add("on");
    if (!card.firstChild) this._buildLauncher(card);
  };

  UI.prototype._buildLauncher = function (card) {
    var self = this;
    var L = SK.launcher;
    var copy = (L && L.copy) || {};
    var art = (L && L.art) || {};
    var steps = copy.steps || [];
    var sceneNotes = copy.scenes || {};
    card.innerHTML = "";

    function arrow() {
      var svg = document.createElementNS(SVG_NS, "svg");
      svg.setAttribute("viewBox", "0 0 18 18");
      svg.setAttribute("aria-hidden", "true");
      var shaft = document.createElementNS(SVG_NS, "path");
      shaft.setAttribute("d", "M2 9h12M9 3l6 6-6 6");
      shaft.setAttribute("fill", "none");
      shaft.setAttribute("stroke", "currentColor");
      shaft.setAttribute("stroke-width", "1.8");
      shaft.setAttribute("stroke-linecap", "round");
      shaft.setAttribute("stroke-linejoin", "round");
      svg.appendChild(shaft);
      return svg;
    }

    /* hero: a real render fills the frame, the address is its live element */
    var hero = el("section", "p-hero");
    var heroArt = art.hero || art.planets;
    if (heroArt) {
      var bg = document.createElement("img");
      bg.className = "p-hero-art";
      bg.src = heroArt;
      bg.alt = "";
      bg.setAttribute("aria-hidden", "true");
      hero.appendChild(bg);
      hero.appendChild(el("div", "p-hero-scrim"));
    }
    var inner = el("div", "p-hero-in");
    var head = el("header", "p-head");
    head.appendChild(el("span", "p-mark"));
    head.appendChild(el("span", "p-wm", "site-kg live"));
    inner.appendChild(head);

    var body = el("div", "p-hero-body");
    var title = el("h1", "p-title reveal");
    title.style.setProperty("--d", "0ms");
    title.appendChild(el("span", "p-title-a", copy.line1 || "TURN ANY SITE"));
    title.appendChild(el("span", "p-title-b", copy.line2 || "INTO A WORLD."));
    body.appendChild(title);

    var support = el("p", "p-support reveal", copy.support || "");
    support.style.setProperty("--d", "90ms");
    body.appendChild(support);

    var form = el("form", "p-form reveal");
    form.style.setProperty("--d", "180ms");
    form.noValidate = true;
    var input = el("input", "p-url");
    input.type = "text";
    input.placeholder = copy.placeholder || "docs.example.com";
    input.autocomplete = "url";
    input.spellcheck = false;
    input.setAttribute("inputmode", "url");
    input.setAttribute("aria-label", "site address");
    var prefill = "";
    if (/^https?:\/\//i.test(this.cfg.title || "")) prefill = this.cfg.title;
    input.value = prefill;
    var go = el("button", "p-go off", copy.action || "Build the graph");
    go.type = "submit";
    form.appendChild(input);
    form.appendChild(go);
    body.appendChild(form);
    var err = el("p", "p-err");
    err.id = "launch-err";
    body.appendChild(err);
    var note = el("p", "p-note reveal", copy.ctaNote || "");
    note.style.setProperty("--d", "260ms");
    body.appendChild(note);
    inner.appendChild(body);
    hero.appendChild(inner);
    card.appendChild(hero);

    /* live validation lights the action button and feeds back inline */
    function refresh() {
      var res = L.normalizeTarget(input.value);
      if (!res.ok) {
        go.className = "p-go off";
        err.classList.remove("ok");
        err.textContent = input.value.trim() ? res.error : "";
      } else {
        go.className = "p-go on";
        err.classList.add("ok");
        err.textContent = L.hostLabel(res.url);
      }
    }
    input.addEventListener("input", refresh);
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var res = L.normalizeTarget(input.value);
      if (!res.ok) {
        refresh();
        input.focus();
        return;
      }
      self.launchLive(res.url, self._scene);
    });
    this.urlInput = input;
    this.goEl = go;
    this.errEl = err;
    refresh();

    /* two worlds: one real render each, text and image trading sides */
    var worldsSec = el("section", "p-worlds");
    (SK.sceneOrder || []).forEach(function (name, i) {
      var scene = SK.scenes[name] || {};
      var tile = el("button", "world reveal");
      tile.type = "button";
      tile.setAttribute("data-scene", name);
      tile.setAttribute("data-ui", "scene-card");
      tile.style.setProperty("--d", (i * 80) + "ms");
      var shot = el("span", "world-art");
      var url = art[name];
      if (url) {
        var img = document.createElement("img");
        img.src = url;
        img.alt = (scene.label || name) + " view: a real render of the "
          + name + " scene";
        shot.appendChild(img);
      }
      tile.appendChild(shot);
      var text = el("span", "world-copy");
      text.appendChild(el("span", "world-name", scene.label || name));
      text.appendChild(el("span", "world-desc",
        sceneNotes[name] || scene.hint || ""));
      var foot = el("span", "world-foot");
      var idle = el("span", "world-go");
      idle.appendChild(el("span", null, copy.worldIdle || "Switch to this view"));
      idle.appendChild(arrow());
      foot.appendChild(idle);
      foot.appendChild(el("span", "world-now", copy.worldOn || "Showing now"));
      text.appendChild(foot);
      tile.appendChild(text);
      if (name === self._scene) tile.classList.add("on");
      tile.addEventListener("click", function () { self.activateScene(name); });
      worldsSec.appendChild(tile);
    });
    card.appendChild(worldsSec);

    /* how it works: a spine of three steps beside a real build capture */
    var how = el("section", "p-how");
    var list = el("ol", "p-step-list");
    steps.forEach(function (step, i) {
      var li = el("li", "p-step reveal");
      li.style.setProperty("--d", (i * 80) + "ms");
      li.appendChild(el("span", "p-step-title", step[0]));
      li.appendChild(el("p", "p-step-note", step[1]));
      list.appendChild(li);
    });
    how.appendChild(list);
    if (art.build) {
      var fig = el("figure", "p-how-shot reveal");
      fig.style.setProperty("--d", "160ms");
      var bimg = document.createElement("img");
      bimg.src = art.build;
      bimg.alt = "The build panel during a real crawl: phase, progress bar, "
        + "counts and the pages as they arrive";
      bimg.setAttribute("data-ui", "build-overlay");
      fig.appendChild(bimg);
      how.appendChild(fig);
    }
    card.appendChild(how);

    /* closing lockup: the name at full size, nothing to click */
    var close = el("footer", "p-close");
    var word = el("p", "p-close-word");
    word.appendChild(el("span", null, "site-kg"));
    word.appendChild(el("span", "p-close-dot"));
    close.appendChild(word);
    close.appendChild(el("p", "p-close-line", "site-kg live"));
    card.appendChild(close);

    this._wireReveals();
  };

  UI.prototype.showScenes = function () {
    if (this.cfg.mode === "live") this.showLauncher();
  };

  UI.prototype.hideLauncher = function () {
    var launcher = this.launcherEl;
    if (!launcher || !launcher.classList.contains("on")) return;
    var reduce = window.matchMedia
      && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    var self = this;
    var finished = false;
    /* a previous hide may still be pending; drop its timer and listener
       before registering this one (never stack handlers) */
    this._dropLauncherHide();
    var done = function () {
      if (finished) return;
      finished = true;
      self._dropLauncherHide();
      launcher.classList.remove("on", "leave");
    };
    /* only the poster's own leave animation may end the hide; children's
       entrance animations bubble here and would cut it short */
    this._launcherAnimEnd = function (ev) {
      if (ev.target === launcher && ev.animationName === "posterLeave") done();
    };
    if (reduce) {
      done();
      return;
    }
    launcher.addEventListener("animationend", this._launcherAnimEnd);
    this._launcherHideTimer = setTimeout(done, 800); /* failure-safe: never leave the poster up */
    launcher.classList.add("leave");
  };

  UI.prototype.connectLive = function (base, scene) {
    if (scene) this.activateScene(scene);
    this.hideLauncher();
    if (this.cfg.liveUrl) this.setLiveTarget(this.cfg.title || this.cfg.liveUrl);
    this.director.connectEvents("", 0, {base: base || "", token: this.token});
  };

  UI.prototype.launchLive = function (target, scene) {
    var self = this;
    var res = SK.launcher.normalizeTarget(target);
    if (!res.ok) {
      var box = byId("launch-err");
      if (box) {
        box.textContent = res.error;
        box.classList.remove("ok");
      }
      if (this.urlInput) this.urlInput.focus();
      return null;
    }
    var url = res.url;
    if (scene) this.activateScene(scene);
    /* the status line sits under the poster; the launcher's own error line
       is the feedback the user can actually see while the request runs */
    var ebox = this.errEl || byId("launch-err");
    if (ebox) {
      ebox.textContent = "starting live ingest \u2026";
      ebox.classList.add("ok");
    }
    var headers = {"Content-Type": "application/json"};
    if (this.token) headers["X-Site-KG-Token"] = this.token;
    var request = typeof fetch === "function"
      ? fetch("/api/start", {
          method: "POST",
          headers: headers,
          body: JSON.stringify({target: url, scene: scene})
        })
      : Promise.reject(new Error("fetch is not available"));
    return request.then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (body) {
        if (!r.ok || !body.ok) {
          throw new Error(body.error || ("start failed: " + r.status));
        }
        if (ebox) ebox.textContent = "";
        self.setLiveTarget(url);
        self.hideLauncher();
        self.director.start("live", null);
        self.director.connectEvents(body.run_id || "", body.last_seq || 0,
                                    {base: "", token: self.token});
        return body;
      });
    }).catch(function (err) {
      self.showLauncher();
      var failbox = self.errEl || byId("launch-err");
      if (failbox) {
        failbox.textContent = (err && err.message) ? err.message : String(err);
        failbox.classList.remove("ok");
      }
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
      if (SK.GpuRenderer && SK.GpuRenderer.supported()) {
        /* WebGPU first: the canvas has no context yet, so a missing adapter
           falls back to WebGL on the same canvas. */
        renderer = new SK.GpuRenderer();
        try {
          renderer.mount(canvas, live);
          renderer.setScene(cfg.scene || "planets");
        } catch (gpuErr) {
          try { if (renderer.dispose) renderer.dispose(); } catch (ignored) {}
          renderer = null;
        }
      }
      if (!renderer && SK.Fallback2D && SK.hasWebGL2 && !SK.hasWebGL2()) {
        renderer = new SK.Fallback2D(canvas);
        renderer.setScene(cfg.scene || "planets");
      }
      if (!renderer) {
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
      if (typeof window !== "undefined") window.__sk = {renderer: renderer, ui: ui};
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
