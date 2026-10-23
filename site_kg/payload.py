"""Binary payload codecs for the archive viewer.

Three little-endian formats, each with a 4-byte magic and a u16 version:

  graph.bin    SKGG: 32B header, 12B cluster rows, 8B kind rows, UTF-8 string
               pool, 40B node rows, 16B edge rows.
  layout.bin   SKGL: 24B header, 10B quantized node rows, 12B patch headers
               with 12B patch rows, 16B final block. Coordinates are quantized
               to int16 with ``Q`` per unit; live layout keeps them in [-1, 1].
  timeline.bin SKGT: 32B header, UTF-8 run id, u32 node/edge orders, 20B batch
               rows. Node/edge ordinals follow first appearance in the log, so
               they line up with corpus order in graph.bin.

The whole payload is gzip-compressed and base64-encoded for inline embedding in
a single-file HTML viewer; ``unpack_html_payload`` reverses that.
"""
from __future__ import annotations

import base64
import gzip
import json
import struct

GRAPH_MAGIC = b"SKGG"
LAYOUT_MAGIC = b"SKGL"
TIMELINE_MAGIC = b"SKGT"
VERSION = 1
Q = 30000          # layout quantization: [-1, 1] -> [-30000, 30000] int16

_GRAPH_HEADER = struct.Struct("<4sHHIIIIII")     # magic, ver, flags, n, e, c, k, pool, hash
_CLUSTER_ROW = struct.Struct("<IHHI")            # off, len, reserved, node_count
_KIND_ROW = struct.Struct("<IHH")                # off, len, reserved
_NODE_ROW = struct.Struct("<IIIIIIHHIfI")        # 3 refs, cluster, flags, degree, rank, words
_EDGE_ROW = struct.Struct("<IIHHI")              # src, dst, kind, flags, reserved

_LAYOUT_HEADER = struct.Struct("<4sHHIIfI")      # magic, ver, flags, n, patches, scale
_LAYOUT_NODE = struct.Struct("<hhhHBB")          # x, y, z, cluster, depth, flags
_PATCH_HEADER = struct.Struct("<IIHH")           # seq, ms, rows, reserved
_PATCH_ROW = struct.Struct("<IhhhH")             # node, x, y, z, flags
_LAYOUT_FINAL = struct.Struct("<IIII")           # seq, ms, n_nodes, flags

_TIMELINE_HEADER = struct.Struct("<4sHHIIIIQ")   # magic, ver, flags, n, e, batches, idlen
_BATCH_ROW = struct.Struct("<IIIIHH")            # seq, ms, node_start, edge_start, n_node, n_edge

ARCHIVE_MAGIC = b"SKGA"
_ARCHIVE_HEADER = struct.Struct("<4sIII")        # magic, graph len, layout len, timeline len


def _need(buf: bytes, pos: int, size: int) -> None:
    if pos + size > len(buf):
        raise ValueError(f"truncated payload: need {size} bytes at {pos}, have {len(buf) - pos}")


def _header(buf: bytes, st: struct.Struct, magic: bytes) -> tuple:
    _need(buf, 0, st.size)
    fields = st.unpack_from(buf, 0)
    if fields[0] != magic:
        raise ValueError(f"bad magic: expected {magic!r}, got {fields[0]!r}")
    if fields[1] != VERSION:
        raise ValueError(f"unsupported payload version {fields[1]} (expected {VERSION})")
    return fields


def _finish(buf: bytes, pos: int) -> None:
    if pos != len(buf):
        raise ValueError(f"trailing data: {len(buf) - pos} unconsumed bytes")


class _Pool:
    def __init__(self) -> None:
        self.data = bytearray()
        self._seen: dict[str, tuple[int, int]] = {}

    def ref(self, s: str) -> tuple[int, int]:
        if s in self._seen:
            return self._seen[s]
        raw = s.encode("utf-8")
        ref = (len(self.data), len(raw))
        self.data += raw
        self._seen[s] = ref
        return ref


def _string(pool: bytes, off: int, length: int) -> str:
    if off + length > len(pool):
        raise ValueError("string reference outside pool")
    return pool[off:off + length].decode("utf-8")


def _fnv32(data: bytes) -> int:
    h = 0x811C9DC5
    for b in data:
        h ^= b
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h


def encode_graph(graph: dict) -> bytes:
    nodes, edges = graph["nodes"], graph["edges"]
    clusters: dict[str, int] = {}
    kinds: dict[str, int] = {}
    for nd in nodes:
        clusters.setdefault(str(nd.get("c", "")), len(clusters))
    for ed in edges:
        kinds.setdefault(str(ed.get("k", "ref")), len(kinds))

    index = {nd["i"]: i for i, nd in enumerate(nodes)}
    if len(index) != len(nodes):
        raise ValueError("duplicate node id in graph")
    for ed in edges:
        if ed["s"] not in index or ed["t"] not in index:
            raise ValueError(f"edge {ed['s']}->{ed['t']} references unknown node")

    pool = _Pool()
    counts = [0] * len(clusters)
    for nd in nodes:
        counts[clusters[str(nd.get("c", ""))]] += 1
    cluster_refs = []
    for name, _ in sorted(clusters.items(), key=lambda kv: kv[1]):
        cluster_refs.append(pool.ref(name))
    cluster_rows = [
        _CLUSTER_ROW.pack(off, length, 0, count)
        for (off, length), count in zip(cluster_refs, counts)
    ]

    kind_refs = []
    for name, _ in sorted(kinds.items(), key=lambda kv: kv[1]):
        kind_refs.append(pool.ref(name))

    node_rows = []
    for nd in nodes:
        id_ref = pool.ref(str(nd["i"]))
        t_ref = pool.ref(str(nd.get("t", "")))
        u_ref = pool.ref(str(nd.get("u", "")))
        node_rows.append(_NODE_ROW.pack(
            *id_ref, *t_ref, *u_ref,
            clusters[str(nd.get("c", ""))], 0,
            int(nd.get("g", 0)), float(nd.get("r", 0.0)),
            # The trailing word was reserved; it now carries the page's prose
            # volume, so the row size and the version stay put.
            int(nd.get("w", 0))))

    edge_kind = [kinds[str(ed.get("k", "ref"))] for ed in edges]
    edge_rows = [
        _EDGE_ROW.pack(index[ed["s"]], index[ed["t"]], kind, 0, 0)
        for ed, kind in zip(edges, edge_kind)
    ]

    kind_rows = [_KIND_ROW.pack(off, length, 0) for off, length in kind_refs]

    canon = json.dumps({"nodes": nodes, "edges": edges}, sort_keys=True,
                       separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    header = _GRAPH_HEADER.pack(
        GRAPH_MAGIC, VERSION, 0, len(nodes), len(edges),
        len(clusters), len(kinds), len(pool.data), _fnv32(canon))
    return b"".join([header, *cluster_rows, *kind_rows, bytes(pool.data),
                     *node_rows, *edge_rows])


def decode_graph(data: bytes) -> dict:
    (_, _, _, n_nodes, n_edges, n_clusters, n_kinds, pool_len, graph_hash) = \
        _header(data, _GRAPH_HEADER, GRAPH_MAGIC)
    pos = _GRAPH_HEADER.size
    _need(data, pos, n_clusters * _CLUSTER_ROW.size + n_kinds * _KIND_ROW.size + pool_len)
    cluster_names = []
    cluster_counts = []
    for _ in range(n_clusters):
        off, length, _, count = _CLUSTER_ROW.unpack_from(data, pos)
        pos += _CLUSTER_ROW.size
        cluster_names.append((off, length))
        cluster_counts.append(count)
    kind_names = []
    for _ in range(n_kinds):
        off, length, _ = _KIND_ROW.unpack_from(data, pos)
        pos += _KIND_ROW.size
        kind_names.append((off, length))
    pool = data[pos:pos + pool_len]
    pos += pool_len
    clusters = [_string(pool, off, length) for off, length in cluster_names]
    kinds = [_string(pool, off, length) for off, length in kind_names]

    _need(data, pos, n_nodes * _NODE_ROW.size + n_edges * _EDGE_ROW.size)
    nodes = []
    for _ in range(n_nodes):
        (io, il, to, tl, uo, ul, cluster, _, degree, rank, words) = \
            _NODE_ROW.unpack_from(data, pos)
        pos += _NODE_ROW.size
        if cluster >= len(clusters):
            raise ValueError("node references unknown cluster")
        nodes.append({
            "i": _string(pool, io, il), "c": clusters[cluster],
            "t": _string(pool, to, tl), "u": _string(pool, uo, ul),
            "g": degree, "r": rank, "w": words,
        })
    edges = []
    for _ in range(n_edges):
        src, dst, kind, _, _ = _EDGE_ROW.unpack_from(data, pos)
        pos += _EDGE_ROW.size
        if src >= n_nodes or dst >= n_nodes or kind >= len(kinds):
            raise ValueError("edge reference outside graph")
        edges.append({"s": nodes[src]["i"], "t": nodes[dst]["i"], "k": kinds[kind]})
    _finish(data, pos)
    return {"nodes": nodes, "edges": edges, "hash": graph_hash}


def _q(v: float, scale: float = Q) -> int:
    return max(-32767, min(32767, round(float(v) * scale)))


def _dq(v: int, scale: float) -> float:
    return v / scale


def encode_layout(nodes: list[dict], patches: list[dict], *, final: int | None = None) -> bytes:
    best = 0.0
    for nd in nodes:
        best = max(best, abs(float(nd.get("x", 0.0))), abs(float(nd.get("y", 0.0))),
                   abs(float(nd.get("z", 0.0))))
    # keep the encoding lossless-in-range even if a caller leaks coords past 1:
    # quantize WITH the recorded scale -- decoding divides by it, so a fixed-Q
    # quantization here would scale every coordinate back up by ~best
    scale = float(Q) if best <= 1.0 else Q / best
    header = _LAYOUT_HEADER.pack(
        LAYOUT_MAGIC, VERSION, 0, len(nodes), len(patches), scale, 0)
    out = [header]
    for nd in nodes:
        cluster = nd.get("c", 0)
        depth = nd.get("d", 0)
        out.append(_LAYOUT_NODE.pack(
            _q(nd.get("x", 0.0), scale), _q(nd.get("y", 0.0), scale),
            _q(nd.get("z", 0.0), scale),
            int(cluster) if isinstance(cluster, int) else 0,
            int(depth) if isinstance(depth, int) else 0, 0))
    for patch in patches:
        rows = patch.get("rows", [])
        if len(rows) > 0xFFFF:
            raise ValueError("layout patch has more than 65535 rows")
        seq = int(patch.get("seq", 0))
        ms = int(patch.get("ms", 0))
        out.append(_PATCH_HEADER.pack(seq, ms, len(rows), 0))
        for row in rows:
            i = int(row["i"])
            if not 0 <= i < len(nodes):
                raise ValueError(f"layout patch references unknown node index {i}")
            out.append(_PATCH_ROW.pack(
                i, _q(row.get("x", 0.0), scale), _q(row.get("y", 0.0), scale),
                _q(row.get("z", 0.0), scale), 0))
    last = patches[-1] if patches else {}
    final_seq = int(final) if final is not None else int(last.get("seq", 0))
    out.append(_LAYOUT_FINAL.pack(final_seq, int(last.get("ms", 0)), len(nodes), 0))
    return b"".join(out)


def decode_layout(data: bytes) -> dict:
    (_, _, _, n_nodes, n_patches, scale, _) = _header(data, _LAYOUT_HEADER, LAYOUT_MAGIC)
    if scale <= 0:
        raise ValueError("layout scale must be positive")
    pos = _LAYOUT_HEADER.size
    _need(data, pos, n_nodes * _LAYOUT_NODE.size)
    nodes = []
    for _ in range(n_nodes):
        x, y, z, cluster, depth, _ = _LAYOUT_NODE.unpack_from(data, pos)
        pos += _LAYOUT_NODE.size
        nodes.append({"x": _dq(x, scale), "y": _dq(y, scale), "z": _dq(z, scale),
                      "c": cluster, "d": depth})
    patches = []
    for _ in range(n_patches):
        _need(data, pos, _PATCH_HEADER.size)
        seq, ms, rows_n, _ = _PATCH_HEADER.unpack_from(data, pos)
        pos += _PATCH_HEADER.size
        _need(data, pos, rows_n * _PATCH_ROW.size)
        rows = []
        for _ in range(rows_n):
            i, x, y, z, _ = _PATCH_ROW.unpack_from(data, pos)
            pos += _PATCH_ROW.size
            if i >= n_nodes:
                raise ValueError("layout patch references unknown node index")
            rows.append({"i": i, "x": _dq(x, scale), "y": _dq(y, scale), "z": _dq(z, scale)})
        patches.append({"seq": seq, "ms": ms, "rows": rows})
    _need(data, pos, _LAYOUT_FINAL.size)
    fseq, _fms, fn, _ = _LAYOUT_FINAL.unpack_from(data, pos)
    pos += _LAYOUT_FINAL.size
    _finish(data, pos)
    return {"nodes": nodes, "patches": patches, "final": {"seq": fseq, "nodes": fn}}


def encode_timeline(run_id: str, events: list[dict], *, batch_size: int = 64) -> bytes:
    node_index: dict[str, int] = {}
    edge_index: dict[tuple[str, str, str], int] = {}
    node_order: list[int] = []
    edge_order: list[int] = []
    batches: list[dict] = []
    n_seen = e_seen = 0
    batch: dict | None = None

    for ev in events:
        kind = ev.get("t")
        if kind not in ("node_added", "node_updated", "edge_added"):
            continue
        if kind == "node_added":
            nid = ev.get("id")
            if nid in node_index:
                continue  # repeats are node updates, not births
            node_index[nid] = len(node_index)
        elif kind == "edge_added":
            key = (ev.get("s"), ev.get("d"), ev.get("kind") or "ref")
            if key in edge_index:
                continue
            edge_index[key] = len(edge_index)

        if batch is None or batch["count"] >= batch_size:
            if batch is not None:
                batches.append(batch)
            batch = {"seq": int(ev.get("seq", 0)), "ms": int(ev.get("ms", 0)),
                     "node_start": n_seen, "edge_start": e_seen,
                     "n_node": 0, "n_edge": 0, "count": 0}
        if kind == "node_added":
            node_order.append(node_index[nid])
            n_seen += 1
            batch["n_node"] += 1
        elif kind == "edge_added":
            edge_order.append(edge_index[key])
            e_seen += 1
            batch["n_edge"] += 1
        batch["count"] += 1
    if batch is not None:
        batches.append(batch)

    rid = run_id.encode("utf-8")
    header = _TIMELINE_HEADER.pack(
        TIMELINE_MAGIC, VERSION, 0, len(node_order), len(edge_order), len(batches), len(rid), 0)
    out = [header, rid]
    out.extend(struct.pack("<I", i) for i in node_order)
    out.extend(struct.pack("<I", i) for i in edge_order)
    for b in batches:
        out.append(_BATCH_ROW.pack(b["seq"], b["ms"], b["node_start"], b["edge_start"],
                                   b["n_node"], b["n_edge"]))
    return b"".join(out)


def decode_timeline(data: bytes) -> dict:
    (_, _, _, n_nodes, n_edges, n_batches, id_len, _) = \
        _header(data, _TIMELINE_HEADER, TIMELINE_MAGIC)
    pos = _TIMELINE_HEADER.size
    _need(data, pos, id_len)
    run_id = data[pos:pos + id_len].decode("utf-8")
    pos += id_len
    _need(data, pos, (n_nodes + n_edges) * 4 + n_batches * _BATCH_ROW.size)
    nodes = list(struct.unpack_from(f"<{n_nodes}I", data, pos)) if n_nodes else []
    pos += n_nodes * 4
    edges = list(struct.unpack_from(f"<{n_edges}I", data, pos)) if n_edges else []
    pos += n_edges * 4
    batches = []
    for _ in range(n_batches):
        seq, ms, ns, es, nn, en = _BATCH_ROW.unpack_from(data, pos)
        pos += _BATCH_ROW.size
        batches.append({"seq": seq, "ms": ms, "node_start": ns, "edge_start": es,
                        "node_n": nn, "edge_n": en})
    _finish(data, pos)
    return {"run_id": run_id, "nodes": nodes, "edges": edges, "batches": batches}


def pack_html_payload(data: bytes) -> str:
    return base64.b64encode(gzip.compress(data, compresslevel=9, mtime=0)).decode("ascii")


def unpack_html_payload(s: str) -> bytes:
    return gzip.decompress(base64.b64decode(s.encode("ascii"), validate=True))


def pack_archive_payload(graph: bytes, layout: bytes, timeline: bytes) -> str:
    """One gzip+base64 blob holding the three sub-payloads, ready to inline."""
    body = b"".join([_ARCHIVE_HEADER.pack(ARCHIVE_MAGIC, len(graph), len(layout), len(timeline)),
                     graph, layout, timeline])
    return pack_html_payload(body)


def unpack_archive_payload(text: str) -> dict[str, bytes]:
    raw = unpack_html_payload(text)
    _need(raw, 0, _ARCHIVE_HEADER.size)
    magic, graph_len, layout_len, timeline_len = _ARCHIVE_HEADER.unpack_from(raw, 0)
    if magic != ARCHIVE_MAGIC:
        raise ValueError(f"bad archive magic: expected {ARCHIVE_MAGIC!r}, got {magic!r}")
    pos = _ARCHIVE_HEADER.size
    _need(raw, pos, graph_len + layout_len + timeline_len)
    out = {
        "graph": raw[pos:pos + graph_len],
        "layout": raw[pos + graph_len:pos + graph_len + layout_len],
        "timeline": raw[pos + graph_len + layout_len:pos + graph_len + layout_len + timeline_len],
    }
    _finish(raw, pos + graph_len + layout_len + timeline_len)
    return out
