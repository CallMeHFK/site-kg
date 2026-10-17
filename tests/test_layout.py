"""Server-side incremental layout (site_kg/layout.py) -- deterministic, no browser."""
import math

from site_kg.layout import LiveLayout, dequantize, quantize, settle_graph


def _dist(a, b):
    return math.sqrt(sum((a[k] - b[k]) ** 2 for k in range(3)))


def test_new_node_lands_near_its_parent():
    L = LiveLayout()
    L.place({"i": "a", "c": "ch"}, [])
    pos = L.place({"i": "b", "c": "ch"}, [("a", "ref")])
    assert _dist(pos, L.positions["a"]) ** 2 < 0.2


def test_unconnected_nodes_spread_over_a_3d_staging_shell():
    L = LiveLayout()
    pts = [L.place({"i": f"n{i}", "c": "ch"}, []) for i in range(80)]
    radii = [math.sqrt(sum(v * v for v in p)) for p in pts]
    assert min(radii) > 0.2
    assert max(radii) <= 1.0
    assert len({tuple(round(v, 3) for v in p) for p in pts}) == len(pts)
    ys = [p[1] for p in pts]
    assert max(ys) - min(ys) > 0.5   # a sphere, not a disc


def test_settle_is_deterministic_and_finite():
    g = {"nodes": [{"i": f"n{i}", "c": f"c{i % 3}"} for i in range(60)],
         "edges": [{"s": f"n{i}", "t": f"n{i + 1}", "k": "ref"} for i in range(59)]}
    a = settle_graph(g, seed=7)
    b = settle_graph(g, seed=7)
    assert a == b
    assert len(a) == 60
    assert all(abs(v) < 1.0 for p in a.values() for v in p)


def test_settle_shortens_edges_and_respects_structure():
    n = 40
    g = {"nodes": [{"i": f"n{i}", "c": "c"} for i in range(n)],
         "edges": [{"s": f"n{i}", "t": f"n{(i + 1) % n}", "k": "ref"} for i in range(n)]}
    L = LiveLayout()
    for nd in g["nodes"]:
        L.place(nd, [])
    before = [_dist(L.positions[e["s"]], L.positions[e["t"]]) for e in g["edges"]]
    out = settle_graph(g, seed=7)
    after = [_dist(out[e["s"]], out[e["t"]]) for e in g["edges"]]
    assert max(after) < max(before) * 1.2
    assert sum(after) / len(after) < sum(before) / len(before) * 0.8


def test_derived_edges_never_pull_the_layout():
    base = {
        "nodes": [{"i": f"n{i}", "c": "c"} for i in range(10)] +
                 [{"i": "far", "c": "other"}],
        "edges": [{"s": f"n{i}", "t": f"n{i + 1}", "k": "ref"} for i in range(9)],
    }
    derived = {"nodes": [dict(n) for n in base["nodes"]],
               "edges": [dict(e) for e in base["edges"]] +
                        [{"s": "far", "t": "n0", "k": "similar_to"}]}
    assert settle_graph(base) == settle_graph(derived)


def test_live_settle_keeps_live_positions_as_anchors():
    L = LiveLayout()
    for i in range(12):
        L.place({"i": f"n{i}", "c": "c"}, [("n0", "ref")] if i else [])
    g = {"nodes": [{"i": f"n{i}", "c": "c"} for i in range(12)],
         "edges": [{"s": "n0", "t": f"n{i}", "k": "ref"} for i in range(1, 12)]}
    live = dict(L.positions)
    final = L.settle(g)
    assert set(final) == set(live)
    moved = sum(_dist(live[i], final[i]) for i in live) / len(live)
    assert moved < 0.5   # anchored: no wholesale rearrangement


def test_apply_emits_indexed_patches_in_node_order():
    L = LiveLayout()
    patches = L.apply([
        {"seq": 1, "t": "node_added", "id": "a", "cluster": "ch", "ms": 10},
        {"seq": 2, "t": "node_added", "id": "b", "cluster": "ch", "ms": 20},
        {"seq": 3, "t": "edge_added", "s": "a", "d": "b", "kind": "ref"},
    ])
    rows = [r for p in patches for r in p["rows"]]
    assert [r["i"] for r in rows] == [0, 1]
    assert patches[-1]["seq"] == 3
    assert _dist((rows[0]["x"], rows[0]["y"], rows[0]["z"]),
                 (rows[1]["x"], rows[1]["y"], rows[1]["z"])) < 0.5


def test_apply_ignores_duplicate_nodes_and_emits_nothing_for_edges():
    L = LiveLayout()
    L.apply([{"seq": 1, "t": "node_added", "id": "a"}])
    assert L.apply([{"seq": 2, "t": "node_added", "id": "a"}]) == []
    assert L.apply([{"seq": 3, "t": "edge_added", "s": "a", "d": "b"}]) == []


def test_quantize_roundtrip_and_clamp():
    assert quantize(0.0) == 0
    assert abs(dequantize(quantize(0.5)) - 0.5) < 1e-4
    assert abs(dequantize(quantize(-1.0)) + 1.0) < 1e-4
    assert quantize(9.0) == 32767
    assert quantize(-9.0) == -32767
