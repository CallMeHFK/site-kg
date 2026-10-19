"""Assemble the single-file viewer from template, modules and binary payload.

The archive path is fully offline: the Three.js vendor bundle, every SK module
and a gzip+base64 graph/layout/timeline payload are inlined into one HTML file.
The live path ships the same shell with an empty payload and an EventSource URL;
the server feeds the same decoders a snapshot on connect.
"""
from __future__ import annotations

import base64
import hashlib
import html
import json
import math
import re
from pathlib import Path

from . import layout as layout_mod
from . import payload

ROOT = Path(__file__).resolve().parent
VENDOR = ROOT / "vendor" / "three-viewer.min.js"
TEMPLATE = ROOT / "templates" / "viewer.html"
VIEWER_DIR = ROOT / "viewer"
SCENES = ("planets", "people", "neural")
PRE_SCENE_MODULES = ("core.js", "render.js", "labels.js", "camera.js")
POST_SCENE_MODULES = ("director.js", "launcher.js", "fallback2d.js", "ui.js")
MODES = ("archive", "live")
EXTERNAL_FILES = {"graph": "sk-graph.bin", "layout": "sk-layout.bin",
                  "timeline": "sk-timeline.bin"}

# Real render captures shown on the launcher poster (live mode only; archive
# viewers never show the launcher and stay lean).
LAUNCH_ART_FILES = {"people": "asset-people.jpg", "neural": "asset-neural.jpg",
                    "planets": "asset-planets.jpg", "hud": "asset-hud.jpg"}

_PLACEHOLDER = re.compile(
    r"__(TITLE_JSON|STATS_JSON|DOCS_JSON|RUN_ID_JSON|LIVE_URL_JSON|TITLE|MODE|SCENE|EXTERNAL|PAYLOAD|BUNDLE|LAUNCH_ART)__")


def assemble_bundle() -> str:
    files = [VENDOR]
    files += [VIEWER_DIR / name for name in PRE_SCENE_MODULES]
    files += [VIEWER_DIR / "scenes" / f"{name}.js" for name in SCENES]
    files += [VIEWER_DIR / name for name in POST_SCENE_MODULES]
    bundle = "\n".join(path.read_text(encoding="utf-8") for path in files)
    if "</script" in bundle.lower():
        raise ValueError("viewer bundle contains a script close tag")
    return bundle


def _finite(value: object) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(float(value)))


def _has_coords(nodes: list[dict]) -> bool:
    return all(_finite(n.get("x")) and _finite(n.get("y")) and _finite(n.get("z"))
               for n in nodes)


def trim_graph(graph: dict, max_nodes: int | None) -> dict:
    nodes = [dict(n) for n in graph["nodes"]]
    edges = [dict(e) for e in graph["edges"]]
    if max_nodes is None or max_nodes <= 0 or max_nodes >= len(nodes):
        return {"nodes": nodes, "edges": edges}
    ranked = sorted(range(len(nodes)),
                    key=lambda i: (-(nodes[i].get("g") or 0),
                                   -(nodes[i].get("r") or 0.0), str(nodes[i]["i"])))
    keep = set(ranked[:max_nodes])
    kept_nodes = [n for i, n in enumerate(nodes) if i in keep]
    ids = {n["i"] for n in kept_nodes}
    kept_edges = [e for e in edges if e["s"] in ids and e["t"] in ids]
    return {"nodes": kept_nodes, "edges": kept_edges}


def _positions(graph: dict) -> dict[str, tuple[float, float, float]]:
    nodes = graph["nodes"]
    if _has_coords(nodes):
        pos = {str(n["i"]): (float(n["x"]), float(n["y"]), float(n["z"]))
               for n in nodes}
        top = max((abs(v) for p in pos.values() for v in p), default=0.0) or 1.0
        if top > 0.999:
            pos = {k: (p[0] * 0.999 / top, p[1] * 0.999 / top, p[2] * 0.999 / top)
                   for k, p in pos.items()}
        return pos
    return layout_mod.settle_graph(graph)


def _layout_rows(graph: dict, pos: dict[str, tuple[float, float, float]]) -> list[dict]:
    cluster_id: dict[str, int] = {}
    for node in graph["nodes"]:
        cluster_id.setdefault(str(node.get("c", "")), len(cluster_id))
    rows = []
    for node in graph["nodes"]:
        x, y, z = pos[str(node["i"])]
        rows.append({"x": x, "y": y, "z": z,
                     "c": cluster_id[str(node.get("c", ""))], "d": 0})
    return rows


def _synthetic_events(graph: dict) -> list[dict]:
    events, seq, ms = [], 1, 0
    for node in graph["nodes"]:
        events.append({"t": "node_added", "id": node["i"], "title": node.get("t", ""),
                       "seq": seq, "ms": ms})
        seq += 1
        ms += 16
    for edge in graph["edges"]:
        events.append({"t": "edge_added", "s": edge["s"], "d": edge["t"],
                       "kind": edge.get("k", "ref"), "seq": seq, "ms": ms})
        seq += 1
        ms += 16
    events.append({"t": "ingest_done", "nodes": len(graph["nodes"]),
                   "seq": seq, "ms": ms})
    return events


def _run_id(graph: dict, meta: dict) -> str:
    supplied = meta.get("run_id")
    if supplied:
        return str(supplied)
    canon = json.dumps(graph, sort_keys=True, ensure_ascii=False,
                       separators=(",", ":")).encode("utf-8")
    return "archive-" + hashlib.blake2b(canon, digest_size=6).hexdigest()


def _json_string(value: str) -> str:
    return json.dumps(str(value), ensure_ascii=False).replace("<", "\\u003c")


def _launch_art() -> str:
    """Inline the launcher art (real scene renders + HUD fragment) as data URIs."""
    art = {}
    for key, name in LAUNCH_ART_FILES.items():
        path = VIEWER_DIR / "assets" / name
        if not path.is_file():
            continue
        blob = base64.b64encode(path.read_bytes()).decode("ascii")
        art[key] = "data:image/jpeg;base64," + blob
    return json.dumps(art, ensure_ascii=False)


def render_viewer(graph: dict, meta: dict, out_path: Path, *, scene: str = "planets",
                  max_nodes: int | None = None, mode: str = "archive",
                  external_data: bool = False) -> Path:
    if scene not in SCENES:
        raise ValueError(f"unknown scene {scene!r}; choose from {', '.join(SCENES)}")
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}; choose from {', '.join(MODES)}")

    g = trim_graph(graph, max_nodes)
    title = str(meta.get("url") or "site-kg graph")
    if mode == "live":
        # Live graphs are empty at page load; the finale must show the real
        # counts collected by the director, not zeros baked in here.
        stats = ""
    else:
        stats = (f'{meta.get("docs", len(g["nodes"]))} pages'
                 f' \u00b7 {len(g["edges"])} edges'
                 f' \u00b7 verdict {meta.get("verdict", "?")}')
    external = bool(external_data) and mode == "archive"
    payload_text = ""
    run_id = ""
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if mode == "archive":
        run_id = _run_id(g, meta)
        positions = _positions(g)
        graph_bytes = payload.encode_graph(g)
        layout_bytes = payload.encode_layout(_layout_rows(g, positions), [], final=0)
        timeline_bytes = payload.encode_timeline(run_id, _synthetic_events(g))
        if external:
            for key, blob in (("graph", graph_bytes), ("layout", layout_bytes),
                              ("timeline", timeline_bytes)):
                (out_path.parent / EXTERNAL_FILES[key]).write_bytes(blob)
        else:
            payload_text = payload.pack_archive_payload(
                graph_bytes, layout_bytes, timeline_bytes)

    live_url = str(meta.get("live_url") or "") if mode == "live" else ""
    # Archive HUD pages: the real crawled document count, not the (possibly
    # max_nodes-trimmed) node count. Null when meta has no count, so the JS
    # omits pages instead of repeating nodes.
    docs_json = json.dumps(meta.get("docs")) if mode == "archive" else "null"
    template = TEMPLATE.read_text(encoding="utf-8")
    values = {
        "TITLE_JSON": _json_string(title),
        "STATS_JSON": _json_string(stats),
        "DOCS_JSON": docs_json,
        "RUN_ID_JSON": _json_string(run_id),
        "LIVE_URL_JSON": _json_string(live_url),
        "TITLE": html.escape(title),
        "MODE": mode,
        "SCENE": scene,
        "EXTERNAL": "true" if external else "false",
        "PAYLOAD": payload_text,
        "BUNDLE": assemble_bundle(),
        "LAUNCH_ART": _launch_art() if mode == "live" else "{}",
    }
    doc = _PLACEHOLDER.sub(lambda m: values[m.group(1)], template)
    out_path.write_text(doc, encoding="utf-8")
    return out_path
