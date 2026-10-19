/* Launcher poster: copy deck, scene art wiring and pure helpers.
   The scene art (real canvas renders of each scene) ships as a JSON data-URI
   map in the live viewer shell; archive viewers never show the launcher. */
(function (SK) {
  "use strict";

  var COPY = {
    line1: "ANY SITE IS A GRAPH.",
    line2: "WATCH IT BUILD.",
    support: "site-kg live crawls any address and grows its knowledge graph in "
      + "front of you — every page a node, every link an edge.",
    action: "Build the graph",
    placeholder: "docs.example.com",
    steps: [
      ["Give it an address", "Paste any URL. The crawl starts the moment you launch."],
      ["Pick a scene", "Two ways to see the same graph: planets or synapses."],
      ["Watch it build", "Pages arrive as nodes, links as edges, in real time."]
    ],
    scenes: {
      neural: "Synapses — links light up as signals between pages.",
      planets: "Orbits — clusters settle into shells around the core."
    },
    ctaWord: "Build",
    ctaNote: "The crawl starts the moment you launch."
  };

  function loadArt() {
    var node = document.getElementById("sk-launch-art");
    if (!node) return {};
    try {
      var parsed = JSON.parse(node.textContent || "{}");
      return (parsed && typeof parsed === "object") ? parsed : {};
    } catch (err) {
      return {};
    }
  }

  /* Loopback hosts get plain http when the user omits the scheme; the
     server itself (live.py LOOPBACK_HOSTS) is the authority on targets. */
  var LOOPBACK = {"localhost": true, "127.0.0.1": true, "::1": true, "[::1]": true};

  /* Hostname a scheme-less input would produce (port included in input). */
  function bareHost(candidate) {
    try {
      return new URL("http://" + candidate).hostname;
    } catch (err) {
      return "";
    }
  }

  /* Normalise a typed address into a launchable URL.
     Returns {ok: true, url} or {ok: false, error}.
     Explicit http(s):// targets only need a non-empty hostname (intranet
     names, container hosts and IPv6 literals are valid); the host-shape
     heuristic only guards scheme-less input, where we must guess. */
  function normalizeTarget(raw) {
    var value = String(raw == null ? "" : raw).trim();
    if (!value) return {ok: false, error: "A site address is required."};
    var candidate = value;
    var explicit = /^[a-z][a-z0-9+.-]*:\/\//i.test(candidate);
    if (!explicit) {
      if (/\s/.test(candidate)) {
        return {ok: false, error: "That doesn't look like a web address."};
      }
      var scheme = LOOPBACK[bareHost(candidate)] ? "http://" : "https://";
      candidate = scheme + candidate;
    }
    var parsed;
    try {
      parsed = new URL(candidate);
    } catch (err) {
      return {ok: false, error: "That doesn't look like a web address."};
    }
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
      return {ok: false, error: "Use an http or https address."};
    }
    if (!parsed.hostname) {
      return {ok: false, error: "That doesn't look like a web address."};
    }
    if (!explicit) {
      var host = parsed.hostname;
      var hostOk = LOOPBACK[host]
        || /^\d{1,3}(\.\d{1,3}){3}$/.test(host)
        || (host.indexOf(".") > 0 && /[a-z0-9-]/i.test(host));
      if (!hostOk) return {ok: false, error: "That doesn't look like a web address."};
    }
    /* credentials never belong in a crawl target or the run record */
    if (parsed.username || parsed.password) {
      parsed.username = "";
      parsed.password = "";
    }
    return {ok: true, url: parsed.href};
  }

  /* Host label for the HUD target readout (never a mid-word truncation). */
  function hostLabel(url) {
    try {
      return new URL(url).host || String(url || "");
    } catch (err) {
      return String(url || "");
    }
  }

  var PHASES = {ingesting: "ingesting", settling: "settling",
                complete: "complete", failed: "failed"};

  function phaseLabel(state) {
    return PHASES[state] || "ingesting";
  }

  var ERROR_LIMIT = 120;

  function errorSummary(error) {
    var text = String(error == null ? "" : error).replace(/\s+/g, " ").trim();
    if (!text) text = "live run failed";
    if (text.length > ERROR_LIMIT) text = text.slice(0, ERROR_LIMIT - 3) + "...";
    return text;
  }

  /* Build readout from a director status: live pages/nodes/edges + phase.
     opts.pages === false omits the pages field (archive without a real doc
     count), so the HUD never prints the node count twice; a numeric
     opts.pages overrides the status pages.

     While discovery is still running there are no pages to report yet, so a
     non-zero discovered count is shown as "N pages found" instead of a dead
     zero. A failed run puts the error in the same readout: the HUD has no
     separate error line and colour alone would not communicate it. */
  function readout(status, opts) {
    var s = status || {};
    var o = opts || {};
    var pages = o.pages === false ? null
      : (o.pages != null ? Math.max(0, o.pages | 0) : Math.max(0, s.pages | 0));
    var nodes = Math.max(0, s.nodes | 0);
    var edges = Math.max(0, s.edges | 0);
    var discovered = Math.max(0, s.discovered | 0);
    var failed = s.state === "failed";
    var error = failed ? errorSummary(s.error) : "";
    var found = pages === 0 && discovered > 0;
    var label;
    if (failed) {
      label = "failed: " + error;
    } else if (found) {
      label = discovered + " pages found · " + nodes + " nodes · "
        + edges + " edges";
    } else {
      label = pages == null
        ? nodes + " nodes · " + edges + " edges"
        : pages + " pages · " + nodes + " nodes · " + edges + " edges";
    }
    return {
      pages: pages, nodes: nodes, edges: edges,
      discovered: discovered, found: found,
      failed: failed, error: error,
      phase: phaseLabel(s.state),
      building: !failed && s.state !== "complete",
      label: label
    };
  }

  SK.launcher = {
    copy: COPY,
    art: {},
    loadArt: loadArt,
    normalizeTarget: normalizeTarget,
    hostLabel: hostLabel,
    phaseLabel: phaseLabel,
    readout: readout
  };
})(window.SK = window.SK || {});
