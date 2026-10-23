"""Build-replay viewer tests (site_kg/buildview.py) -- offline, no browser."""
import base64
import json
import math
from array import array
from pathlib import Path

import pytest

from site_kg.buildview import (
    DERIVED_ORDER,
    EVIDENCE_ORDER,
    R_MAX,
    _b64,
    build_view_data,
    layout_shells,
    render_build_viewer,
)


def _f32(b64: str) -> array:
    a = array("f")
    a.frombytes(base64.b64decode(b64))
    return a


def _u32(b64: str) -> array:
    a = array("I")
    a.frombytes(base64.b64decode(b64))
    return a


def _data_of(html: str) -> dict:
    """The viewer's embedded DATA literal, parsed back."""
    line = html.split("const DATA = ", 1)[1].split("\n", 1)[0].rstrip(";")
    return json.loads(line)


def _graph(n_nodes: int = 12, n_edges: int = 20, kinds=("ref", "similar_to")):
    nodes = [{"i": f"n{i:02d}", "c": f"ch{i % 3}", "t": f"Node {i}", "u": ""}
             for i in range(n_nodes)]
    edges = [{"s": f"n{i % n_nodes:02d}", "t": f"n{(i * 7 + 3) % n_nodes:02d}",
              "k": kinds[i % len(kinds)]} for i in range(n_edges)]
    edges = [e for e in edges if e["s"] != e["t"]]
    return {"nodes": nodes, "edges": edges}


def test_timeline_orders_corpus_then_evidence_then_derived():
    g = _graph(kinds=("similar_to", "imports", "ref"))
    data = build_view_data(g, {})
    names = [p["name"] for p in data["phases"]]
    assert names[0] == "CORPUS"
    edge_names = names[1:]
    # every present kind appears exactly once, evidence before derived
    assert edge_names == sorted(
        set(edge_names),
        key=lambda k: (0, EVIDENCE_ORDER.index(k)) if k in EVIDENCE_ORDER
        else (1, DERIVED_ORDER.index(k)))
    # phase counts partition the graph exactly
    assert data["phases"][0]["count"] == len(g["nodes"])
    assert sum(p["count"] for p in data["phases"][1:]) == len(g["edges"])
    assert data["total"] == pytest.approx(sum(p["dur"] for p in data["phases"]))


def test_births_are_monotonic_within_phase_and_packed_in_order():
    g = _graph(n_edges=30, kinds=("imports", "similar_to"))
    data = build_view_data(g, {})
    births = _f32(data["birthsB"])
    edge_idx = _u32(data["edgesB"])
    assert len(births) == len(edge_idx) // 4 == len(g["edges"])
    # edges are stored in phase order: the first block belongs to phase 1
    p = data["phases"][1]
    block = births[:p["count"]]
    assert list(block) == sorted(block)   # later in phase => born later
    assert block[0] > p["t0"]
    assert block[-1] == pytest.approx(p["t0"] + p["dur"], abs=1e-5)
    # every edge endpoint is a valid node index
    n = len(g["nodes"])
    for i in range(0, len(edge_idx), 4):
        assert 0 <= edge_idx[i] < n and 0 <= edge_idx[i + 1] < n


def _radius(nd: dict) -> float:
    return (nd["x"] ** 2 + nd["y"] ** 2 + nd["z"] ** 2) ** 0.5


def test_shell_layout_is_deterministic_and_bounded():
    g = _graph(n_nodes=40)
    a = {"nodes": [dict(nd) for nd in g["nodes"]], "edges": g["edges"]}
    b = {"nodes": [dict(nd) for nd in g["nodes"]], "edges": g["edges"]}
    layout_shells(a)
    layout_shells(b)
    assert [(n["x"], n["y"], n["z"]) for n in a["nodes"]] == \
           [(n["x"], n["y"], n["z"]) for n in b["nodes"]]
    for n in a["nodes"]:
        # base_r <= R_MAX, pull <= 1, jitter <= 1.03, rounding to 2 decimals
        assert _radius(n) <= 104.0


def test_hub_nodes_migrate_toward_the_core():
    nodes = [{"i": f"n{i}", "c": "ch", "t": "", "u": ""} for i in range(8)]
    edges = [{"s": "n0", "t": f"n{i}", "k": "ref"} for i in range(1, 8)]
    g = {"nodes": nodes, "edges": edges}
    layout_shells(g)
    r0 = _radius(g["nodes"][0])
    others = [_radius(n) for n in g["nodes"][1:]]
    assert r0 < min(others)   # hub pulled ~30% inward; others keep their shell


def test_shell_layout_fills_the_volume_not_a_pie():
    """The whole point of 3D: a large corpus must not flatten onto y ~ 0."""
    g = _graph(n_nodes=300)
    layout_shells(g)
    mean_abs_y = sum(abs(n["y"]) for n in g["nodes"]) / len(g["nodes"])
    assert mean_abs_y > 0.25 * R_MAX
    # a majority of nodes must sit clearly off the equatorial band
    off_band = sum(1 for n in g["nodes"] if abs(n["y"]) > 0.15 * R_MAX)
    assert off_band > len(g["nodes"]) // 2


def test_chapter_shells_are_ordered_by_chapter_size():
    """Edge-free fixture: pull == 1, so mean radius == shell radius, cleanly."""
    nodes = [{"i": f"a{i}", "c": "big", "t": "", "u": ""} for i in range(30)]
    nodes += [{"i": f"b{i}", "c": "mid", "t": "", "u": ""} for i in range(10)]
    nodes += [{"i": f"c{i}", "c": "small", "t": "", "u": ""} for i in range(5)]
    g = {"nodes": nodes, "edges": []}
    layout_shells(g)

    def mean_r(ch: str) -> float:
        members = [n for n in nodes if n["c"] == ch]
        return sum(_radius(n) for n in members) / len(members)

    big, mid, small = mean_r("big"), mean_r("mid"), mean_r("small")
    assert big > mid > small
    assert big > 0.9 * R_MAX          # biggest chapter owns the outer shell
    assert mid < 0.75 * R_MAX < big   # mid shell sits clearly inside big
    assert small < 0.65 * R_MAX


def test_viewer_html_is_self_contained(tmp_path: Path):
    g = _graph()
    out = render_build_viewer(g, {"url": "http://x/", "verdict": "READY",
                                  "docs": len(g["nodes"]), "relationMode": "auto"},
                              tmp_path / "build.html")
    s = out.read_text(encoding="utf-8")
    assert "navigator.gpu" in s and "WebGPU" in s
    assert "<script src" not in s and "<link" not in s
    data = _data_of(s)
    assert "<" not in json.dumps(data)   # payload cannot break the script block
    assert len(_f32(data["nodesB"])) == len(g["nodes"]) * 4
    assert len(_f32(data["degsB"])) == len(g["nodes"])
    assert len(_u32(data["edgesB"])) == len(g["edges"]) * 4
    assert len(_f32(data["birthsB"])) == len(g["edges"])
    assert data["verdict"] == "READY" and data["edgesTotal"] == len(g["edges"])


def test_payload_carries_framing_radius_that_fits_every_viewport(tmp_path):
    """boundR drives the fit-to-viewport camera: the fitted distance must keep
    every node on-screen at desktop, tablet and phone aspects.

    Invariant (no matrix mirror needed): a node at radius r <= boundR, viewed
    from distance D, has sin(view angle) <= r/D <= boundR/D < sin(half-angle),
    so its NDC = tan(angle)/tan(half) < 1 at any yaw/pitch. Asserted against
    the viewer's default camera and its exact fit formula.
    """
    g = _graph(n_nodes=40)
    out = render_build_viewer(g, {"url": "http://x/"}, tmp_path / "b.html")
    data = _data_of(out.read_text(encoding="utf-8"))
    pos = _f32(data["nodesB"])
    radii = [(pos[i] ** 2 + pos[i + 1] ** 2 + pos[i + 2] ** 2) ** 0.5
             for i in range(0, len(pos), 4)]
    assert data["boundR"] == pytest.approx(max(radii), abs=0.02)
    yaw, pitch = 0.7, 0.42   # the viewer's default camera
    cp, sp = math.cos(pitch), math.sin(pitch)
    ux, uy, uz = math.sin(yaw) * cp, sp, math.cos(yaw) * cp   # eye direction
    for aspect in (16 / 10, 3 / 4, 390 / 844):
        half = min(0.45, math.atan(math.tan(0.45) * aspect))
        dist = max(40, min(900, data["boundR"] / math.sin(half) * 1.08))
        # camera sits at dist*u looking at the origin: forward is -u
        for x, y, z in ((pos[i], pos[i + 1], pos[i + 2])
                        for i in range(0, len(pos), 4)):
            vx, vy, vz = x - dist * ux, y - dist * uy, z - dist * uz
            cos_a = -(ux * vx + uy * vy + uz * vz) / math.sqrt(
                vx * vx + vy * vy + vz * vz)
            assert cos_a > math.cos(half)   # view angle < half-angle => on-screen


def test_titles_with_script_tags_cannot_break_out(tmp_path: Path):
    g = _graph(n_nodes=3, n_edges=1)
    g["nodes"][0]["t"] = "</script><img src=x onerror=alert(1)>"
    out = render_build_viewer(g, {"url": "http://x/"}, tmp_path / "build.html")
    payload_line = out.read_text(encoding="utf-8").split("const DATA = ", 1)[1] \
        .split("\n", 1)[0]
    assert "<img" not in payload_line and "\\u003c" in payload_line


def test_meta_url_injection_is_escaped(tmp_path: Path):
    """meta.url reaches <title> and the HUD panel -- both must be inert."""
    hostile = "http://x/</script><script>alert(1)</script>"
    g = _graph(n_nodes=3, n_edges=1)
    out = render_build_viewer(g, {"url": hostile, "verdict": "READY",
                                  "relationMode": "auto"},
                              tmp_path / "build.html")
    s = out.read_text(encoding="utf-8")
    assert hostile not in s                       # never interpolated raw
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in s   # escaped instead
    assert s.count("<script") == 1                # only the viewer's own block
    assert s.count("</script>") == 1


def test_empty_graph_raises(tmp_path: Path):
    with pytest.raises(ValueError):
        render_build_viewer({"nodes": [], "edges": []}, {}, tmp_path / "b.html")


def test_zero_edge_graph_replays_corpus_only(tmp_path: Path):
    """A NOT-READY corpus still replays its nodes -- no fabricated edges."""
    g = {"nodes": [{"i": "a", "c": "c", "t": "A", "u": ""},
                   {"i": "b", "c": "c", "t": "B", "u": ""}], "edges": []}
    out = render_build_viewer(g, {"url": "http://x/", "verdict": "NOT-READY"},
                              tmp_path / "build.html")
    data = _data_of(out.read_text(encoding="utf-8"))
    assert [p["name"] for p in data["phases"]] == ["CORPUS"]
    assert data["edgesTotal"] == 0 and data["kinds"] == []


def test_end_to_end_from_a_real_corpus(tmp_path: Path):
    """Corpus -> build_graph (with derived dimensions) -> viewer payload."""
    from site_kg import graph

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    prose = ("kernel driver flux capacitance regulator board signal chain "
             "amplifier stage tuning " * 8)
    (corpus / "one.md").write_text(f"---\ntitle: One\n---\n\n{prose}\n\nsee [two](two.md)")
    (corpus / "two.md").write_text(f"---\ntitle: Two\n---\n\n{prose}\n\nback to [one](one.md)")
    # no link to the others: the pair is similar-but-unlinked, exactly the case
    # the derived layer exists for (an already-linked pair keeps its ref kind)
    (corpus / "three.md").write_text(f"---\ntitle: Three\n---\n\n{prose}")
    built = graph.build_graph(corpus, relations="auto")
    out = render_build_viewer(built["graph"], built["report"], tmp_path / "build.html")
    data = _data_of(out.read_text(encoding="utf-8"))
    kinds = {k["name"] for k in data["kinds"]}
    assert "ref" in kinds and "similar_to" in kinds   # evidence + derived
    assert data["edgesTotal"] == len(built["graph"]["edges"])
    assert all(p["count"] > 0 for p in data["phases"])
    # corpus order is the node birth order (rglob order): titles at this scale
    assert data["titles"] == ["One", "Three", "Two"]


def test_b64_roundtrip():
    a = array("f", (1.5, -2.25, 0.0))
    assert list(_f32(_b64(a))) == [1.5, -2.25, 0.0]


def test_hud_fps_formula_matches_seconds_dt():
    # dt is seconds capped at 0.1 (see loop()): a milliseconds-style guard
    # pins the readout at 1000. Source invariant -- the HUD runs in rAF.
    src = Path(__file__).resolve().parent.parent.joinpath(
        "site_kg", "buildview.py").read_text(encoding="utf-8")
    assert "1 / Math.max(dt, 1e-3)" in src
    assert "1000 / Math.max(dt, 1)" not in src
