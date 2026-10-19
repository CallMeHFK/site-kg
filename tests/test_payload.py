"""Binary payload codecs (site_kg/payload.py): roundtrip, magic, version, pack."""
import base64

import pytest

from site_kg import payload


def _graph() -> dict:
    return {
        "nodes": [
            {"i": "a", "c": "ch1", "t": "A", "u": "http://x/a", "g": 3, "r": 0.5},
            {"i": "b", "c": "ch2", "t": "B", "u": "http://x/b", "g": 1, "r": 0.25},
        ],
        "edges": [{"s": "a", "t": "b", "k": "ref"}],
    }


def test_graph_roundtrip():
    g = _graph()
    out = payload.decode_graph(payload.encode_graph(g))
    assert out["nodes"] == g["nodes"]
    assert out["edges"] == g["edges"]


def test_graph_roundtrip_preserves_duplicate_strings_and_empty_fields():
    g = {
        "nodes": [
            {"i": "same", "c": "ch", "t": "same", "u": "", "g": 0, "r": 0.0},
            {"i": "same2", "c": "ch", "t": "same", "u": "", "g": 0, "r": 0.0},
        ],
        "edges": [],
    }
    out = payload.decode_graph(payload.encode_graph(g))
    assert out["nodes"] == g["nodes"]


def test_layout_quantization_roundtrip():
    nodes = [{"i": "a", "x": 0.25, "y": -0.5, "z": 1.0, "c": 2, "d": 1}]
    out = payload.decode_layout(payload.encode_layout(nodes, []))
    assert abs(out["nodes"][0]["x"] - 0.25) < 1e-4
    assert abs(out["nodes"][0]["y"] + 0.5) < 1e-4
    assert abs(out["nodes"][0]["z"] - 1.0) < 1e-4
    assert out["nodes"][0]["c"] == 2 and out["nodes"][0]["d"] == 1


def test_layout_patches_and_final_block_roundtrip():
    nodes = [{"x": 0.0, "y": 0.0, "z": 0.0} for _ in range(3)]
    patches = [
        {"seq": 7, "ms": 120, "rows": [{"i": 1, "x": 0.5, "y": 0.0, "z": -0.25}]},
        {"seq": 9, "ms": 240, "rows": [
            {"i": 0, "x": -0.125, "y": 0.75, "z": 0.0},
            {"i": 2, "x": 0.0, "y": 0.0, "z": 0.875},
        ]},
    ]
    out = payload.decode_layout(payload.encode_layout(nodes, patches, final=9))
    assert [p["seq"] for p in out["patches"]] == [7, 9]
    assert out["patches"][1]["rows"][1]["i"] == 2
    assert abs(out["patches"][1]["rows"][0]["y"] - 0.75) < 1e-4
    assert out["final"] == {"seq": 9, "nodes": 3}


def test_timeline_roundtrip():
    events = [
        {"seq": 1, "t": "node_added", "id": "a", "ms": 10},
        {"seq": 2, "t": "node_added", "id": "b", "ms": 20},
        {"seq": 3, "t": "edge_added", "s": "a", "d": "b", "kind": "ref", "ms": 30},
        {"seq": 4, "t": "node_added", "id": "a"},  # duplicate update: no new ordinal
        {"seq": 5, "t": "ingest_done", "ms": 40},
    ]
    out = payload.decode_timeline(payload.encode_timeline("run-1", events))
    assert out["run_id"] == "run-1"
    assert out["nodes"] == [0, 1]
    assert out["edges"] == [0]
    assert sum(b["node_n"] for b in out["batches"]) == 2
    assert sum(b["edge_n"] for b in out["batches"]) == 1
    assert out["batches"][0]["ms"] == 10


@pytest.mark.parametrize("bad_magic", [b"XXXX"])
def test_bad_magic_rejected(bad_magic):
    for decode, size in (
        (payload.decode_graph, 64),
        (payload.decode_layout, 64),
        (payload.decode_timeline, 64),
    ):
        with pytest.raises(ValueError, match="magic"):
            decode(bad_magic + b"\x00" * (size - len(bad_magic)))


def test_version_mismatch_rejected():
    good = payload.encode_graph(_graph())
    bad = good[:4] + (99).to_bytes(2, "little") + good[6:]
    with pytest.raises(ValueError, match="version"):
        payload.decode_graph(bad)


def test_truncated_payload_rejected():
    with pytest.raises(ValueError):
        payload.decode_graph(payload.encode_graph(_graph())[:-3])


def test_unknown_edge_endpoint_rejected():
    g = {"nodes": [{"i": "a", "c": "ch", "t": "A", "u": "", "g": 0, "r": 0.0}],
         "edges": [{"s": "a", "t": "ghost", "k": "ref"}]}
    with pytest.raises(ValueError, match="unknown node"):
        payload.encode_graph(g)


def test_pack_is_gzip_base64():
    packed = payload.pack_html_payload(b"hello" * 100)
    assert base64.b64decode(packed)[:2] == b"\x1f\x8b"
    assert payload.unpack_html_payload(packed) == b"hello" * 100


def test_pack_is_deterministic():
    assert payload.pack_html_payload(b"hello" * 100) == \
           payload.pack_html_payload(b"hello" * 100)
