"""Deterministic incremental placement and anchored settling for the live scene.

Coordinates are normalized to roughly [-1, 1] so the archive payload can
quantize them to int16. New nodes land next to already-placed structural
neighbors; ``settle`` runs a fixed-order, fixed-round anchored relaxation:
springs on structural evidence only (ref/imports strong, contains/hub weak),
uniform-grid repulsion, cluster-centroid attraction, and an isolated-node
shell. Derived relation edges are deliberately ignored, so toggling a
dimension can never move the graph.

Determinism: all iteration order derives from node/edge order and a seeded
blake2b hash of ids; no wall-clock cutoffs, so the same graph + seed yields the
same coordinates on every run.
"""
from __future__ import annotations

import hashlib
import math

Q = 30000
R_MAX = 0.999
STAGE_R = (0.55, 0.92)
JITTER = 0.10
SPRING = 0.35
REST = 0.10
REP_CUT = 0.12
CELL_CUT = 0.35
MAX_STEP = 0.06
STRUCT_WEIGHT = {"ref": 1.0, "imports": 1.0, "contains": 0.30, "hub": 0.30}
DERIVED = frozenset({"mentions_symbol", "similar_to", "sibling_of", "supersedes",
                     "shares_entity", "semantic_similar",
                     "shares_heading", "shares_term", "duplicate_of",
                     "coupled_with", "co_cited_with"})


def _rand(seed: int, *parts: object) -> float:
    raw = f"{seed}|{'|'.join(map(str, parts))}".encode()
    digest = hashlib.blake2b(raw, digest_size=8).digest()
    return int.from_bytes(digest, "little") / 2 ** 64


def _unit(seed: int, *parts: object) -> tuple[float, float, float]:
    u = _rand(seed, *parts)
    phi = 2.0 * math.pi * _rand(seed, "phi", *parts)
    z = 2.0 * u - 1.0
    r = math.sqrt(max(0.0, 1.0 - z * z))
    return r * math.cos(phi), z, r * math.sin(phi)


def _norm(p: tuple[float, float, float]) -> float:
    return math.sqrt(p[0] * p[0] + p[1] * p[1] + p[2] * p[2])


def _clamp(p: tuple[float, float, float], limit: float = R_MAX) -> tuple[float, float, float]:
    n = _norm(p)
    if n > limit > 0:
        k = limit / n
        return p[0] * k, p[1] * k, p[2] * k
    return p


def _stage(seed: int, node_id: str) -> tuple[float, float, float]:
    u = _unit(seed, "stage", node_id)
    r = STAGE_R[0] + (STAGE_R[1] - STAGE_R[0]) * _rand(seed, "radius", node_id)
    return _clamp((u[0] * r, u[1] * r, u[2] * r))


def _rounds(n: int) -> int:
    if n <= 2000:
        return 40
    if n <= 20000:
        return 24
    return 14


def quantize(x: float) -> int:
    return max(-32767, min(32767, round(float(x) * Q)))


def dequantize(q: int) -> float:
    return q / Q


class LiveLayout:
    def __init__(self, seed: int = 7):
        self.seed = seed
        self.positions: dict[str, tuple[float, float, float]] = {}
        self.index: dict[str, int] = {}
        self.neighbors: dict[str, list[tuple[str, str]]] = {}
        self._edges: list[tuple[str, str, str]] = []
        self._seen_edges: set[tuple[str, str, str]] = set()

    def place(self, node: dict, neighbors: list[tuple[str, str]]) -> tuple[float, float, float]:
        nid = str(node["i"])
        if nid in self.positions:
            return self.positions[nid]
        located = []
        for other, kind in neighbors:
            pos = self.positions.get(str(other))
            if pos is not None and len(located) < 8:
                located.append((pos, STRUCT_WEIGHT.get(str(kind), 0.10)))
        if located:
            wsum = sum(w for _, w in located) or 1.0
            cx = sum(p[0] * w for p, w in located) / wsum
            cy = sum(p[1] * w for p, w in located) / wsum
            cz = sum(p[2] * w for p, w in located) / wsum
            j = _unit(self.seed, "jitter", nid)
            jr = JITTER * (0.35 + 0.65 * _rand(self.seed, "jitter-r", nid))
            pos = (cx + j[0] * jr, cy + j[1] * jr, cz + j[2] * jr)
        else:
            pos = _stage(self.seed, nid)
        pos = _clamp(pos)
        self.positions[nid] = pos
        self.index.setdefault(nid, len(self.index))
        return pos

    def apply(self, events: list[dict]) -> list[dict]:
        # Edges may trail their endpoints in the same batch; register them
        # first so a newly arriving node can land near a neighbor that is
        # already placed even though its edge event came later.
        for ev in events:
            if ev.get("t") != "edge_added":
                continue
            s, d = str(ev.get("s")), str(ev.get("d"))
            k = str(ev.get("kind") or "ref")
            key = (s, d, k)
            if key in self._seen_edges:
                continue
            self._seen_edges.add(key)
            self.neighbors.setdefault(s, []).append((d, k))
            self.neighbors.setdefault(d, []).append((s, k))
            self._edges.append((s, d, k))
        rows: list[dict] = []
        seq = ms = 0
        for ev in events:
            kind = ev.get("t")
            seq = int(ev.get("seq", seq))
            ms = int(ev.get("ms", ms))
            if kind == "node_added":
                nid = str(ev.get("id"))
                if nid in self.positions:
                    continue
                node = {"i": nid, "c": ev.get("cluster", ev.get("c", ""))}
                pos = self.place(node, self.neighbors.get(nid, []))
                rows.append({"i": self.index[nid], "x": pos[0], "y": pos[1], "z": pos[2]})
        if not rows:
            return []
        return [{"seq": seq, "ms": ms, "rows": rows}]

    def settle(self, graph: dict, on_progress=None) -> dict[str, tuple[float, float, float]]:
        nodes = graph["nodes"]
        anchors = dict(self.positions)
        init: dict[str, tuple[float, float, float]] = {}
        for nd in nodes:
            nid = str(nd["i"])
            if nid in self.positions:
                init[nid] = self.positions[nid]
            else:
                init[nid] = self.place(nd, self.neighbors.get(nid, []))
        edges = list(graph.get("edges", [])) + [
            {"s": s, "t": d, "k": k} for s, d, k in self._edges]
        final = _relax(nodes, edges, init, self.seed, anchors=anchors,
                       anchor_w=0.12, on_progress=on_progress)
        self.positions.update(final)
        return final


def settle_graph(graph: dict, seed: int = 7) -> dict[str, tuple[float, float, float]]:
    nodes = graph["nodes"]
    init = {str(nd["i"]): _stage(seed, str(nd["i"])) for nd in nodes}
    return _relax(nodes, graph.get("edges", []), init, seed)


def _relax(nodes: list[dict], edges: list[dict], init: dict[str, tuple[float, float, float]],
           seed: int, *, anchors: dict[str, tuple[float, float, float]] | None = None,
           anchor_w: float = 0.0, on_progress=None) -> dict[str, tuple[float, float, float]]:
    ids = [str(nd["i"]) for nd in nodes]
    n = len(ids)
    if n == 0:
        return {}
    index = {nid: i for i, nid in enumerate(ids)}
    xs = [float(init[nid][0]) for nid in ids]
    ys = [float(init[nid][1]) for nid in ids]
    zs = [float(init[nid][2]) for nid in ids]
    clusters = [str(nd.get("c", "")) for nd in nodes]

    springs: list[tuple[int, int, float]] = []
    degree = [0] * n
    for e in edges:
        kind = str(e.get("k", "ref"))
        if kind in DERIVED:
            continue
        a, b = index.get(str(e["s"])), index.get(str(e["t"]))
        if a is None or b is None or a == b:
            continue
        springs.append((a, b, STRUCT_WEIGHT.get(kind, 0.10)))
        degree[a] += 1
        degree[b] += 1

    csum: dict[str, list[float]] = {}
    for i, c in enumerate(clusters):
        acc = csum.setdefault(c, [0.0, 0.0, 0.0, 0.0])
        acc[0] += xs[i]
        acc[1] += ys[i]
        acc[2] += zs[i]
        acc[3] += 1.0
    centroids = {
        c: (acc[0] / acc[3], acc[1] / acc[3], acc[2] / acc[3])
        for c, acc in csum.items()
    }

    # uniform-grid repulsion: same-cell pairs plus a coarse inter-cell field
    spacing = (4.19 / n) ** (1.0 / 3.0)
    cell = max(REP_CUT, spacing * 1.5)
    within_all = n <= 5000
    rounds = _rounds(n)
    ax = [0.0] * n
    ay = [0.0] * n
    az = [0.0] * n
    if anchors:
        for i, nid in enumerate(ids):
            p = anchors.get(nid)
            if p is not None:
                ax[i], ay[i], az[i] = p

    for rnd in range(rounds):
        fx = [0.0] * n
        fy = [0.0] * n
        fz = [0.0] * n

        for a, b, w in springs:
            dx, dy, dz = xs[b] - xs[a], ys[b] - ys[a], zs[b] - zs[a]
            d = math.sqrt(dx * dx + dy * dy + dz * dz) + 1e-9
            rest = REST if w >= 1.0 else REST * 1.6
            corr = SPRING * w * (d - rest)
            ux, uy, uz = dx / d, dy / d, dz / d
            fx[a] += ux * corr
            fy[a] += uy * corr
            fz[a] += uz * corr
            fx[b] -= ux * corr
            fy[b] -= uy * corr
            fz[b] -= uz * corr

        cells: dict[tuple[int, int, int], list[int]] = {}
        for i in range(n):
            key = (math.floor(xs[i] / cell), math.floor(ys[i] / cell),
                   math.floor(zs[i] / cell))
            cells.setdefault(key, []).append(i)
        cell_sum: dict[tuple[int, int, int], list[float]] = {}
        for key in sorted(cells):
            members = cells[key]
            acc = [0.0, 0.0, 0.0, 0.0]
            for i in members:
                acc[0] += xs[i]
                acc[1] += ys[i]
                acc[2] += zs[i]
                acc[3] += 1.0
            cell_sum[key] = acc
            m = len(members)
            if within_all or m <= 48:
                pairs = ((members[a], members[b])
                         for a in range(m) for b in range(a + 1, m))
            else:
                pairs = ((members[a], members[a + d])
                         for a in range(m) for d in (1, 2, 3) if a + d < m)
            for a, b in pairs:
                dx, dy, dz = xs[b] - xs[a], ys[b] - ys[a], zs[b] - zs[a]
                d2 = dx * dx + dy * dy + dz * dz
                if d2 >= REP_CUT * REP_CUT or d2 < 1e-12:
                    continue
                d = math.sqrt(d2)
                corr = 0.022 * (REP_CUT - d) / d
                fx[a] -= dx * corr
                fy[a] -= dy * corr
                fz[a] -= dz * corr
                fx[b] += dx * corr
                fy[b] += dy * corr
                fz[b] += dz * corr
        # coarse field: a cell repels its forward neighbours, applied per node
        cfx: dict[tuple[int, int, int], float] = {}
        cfy: dict[tuple[int, int, int], float] = {}
        cfz: dict[tuple[int, int, int], float] = {}
        for key in sorted(cells):
            acc = cell_sum[key]
            cx, cy, cz = acc[0] / acc[3], acc[1] / acc[3], acc[2] / acc[3]
            for dx in (0, 1):
                for dy in (-1, 0, 1):
                    for dz in (-1, 0, 1):
                        if dx == 0 and (dy < 0 or (dy == 0 and dz <= 0)):
                            continue
                        other = (key[0] + dx, key[1] + dy, key[2] + dz)
                        oacc = cell_sum.get(other)
                        if oacc is None:
                            continue
                        ox, oy, oz = oacc[0] / oacc[3], oacc[1] / oacc[3], oacc[2] / oacc[3]
                        ddx, ddy, ddz = ox - cx, oy - cy, oz - cz
                        d = math.sqrt(ddx * ddx + ddy * ddy + ddz * ddz) + 1e-9
                        if d >= CELL_CUT:
                            continue
                        push = 0.010 * (CELL_CUT - d) / d
                        cfx[key] = cfx.get(key, 0.0) - ddx * push
                        cfy[key] = cfy.get(key, 0.0) - ddy * push
                        cfz[key] = cfz.get(key, 0.0) - ddz * push
                        cfx[other] = cfx.get(other, 0.0) + ddx * push
                        cfy[other] = cfy.get(other, 0.0) + ddy * push
                        cfz[other] = cfz.get(other, 0.0) + ddz * push
        for key, members in cells.items():
            gx, gy, gz = cfx.get(key, 0.0), cfy.get(key, 0.0), cfz.get(key, 0.0)
            for i in members:
                fx[i] += gx
                fy[i] += gy
                fz[i] += gz

        for i in range(n):
            cent = centroids[clusters[i]]
            fx[i] += 0.012 * (cent[0] - xs[i])
            fy[i] += 0.012 * (cent[1] - ys[i])
            fz[i] += 0.012 * (cent[2] - zs[i])
            r = math.sqrt(xs[i] * xs[i] + ys[i] * ys[i] + zs[i] * zs[i]) + 1e-9
            if degree[i] == 0:
                fx[i] += 0.008 * (0.9 - r) * xs[i] / r
                fy[i] += 0.008 * (0.9 - r) * ys[i] / r
                fz[i] += 0.008 * (0.9 - r) * zs[i] / r
            if anchors:
                p = anchors.get(ids[i])
                if p is not None and anchor_w:
                    fx[i] += anchor_w * (p[0] - xs[i])
                    fy[i] += anchor_w * (p[1] - ys[i])
                    fz[i] += anchor_w * (p[2] - zs[i])

            dx, dy, dz = fx[i], fy[i], fz[i]
            step = math.sqrt(dx * dx + dy * dy + dz * dz)
            if step > MAX_STEP:
                k = MAX_STEP / step
                dx, dy, dz = dx * k, dy * k, dz * k
            xs[i] += dx
            ys[i] += dy
            zs[i] += dz

        if on_progress is not None and (rnd + 1) % max(1, rounds // 4) == 0:
            on_progress({
                ids[i]: (xs[i], ys[i], zs[i]) for i in range(n)
            })

    top = max(max(abs(xs[i]), abs(ys[i]), abs(zs[i])) for i in range(n))
    scale = (R_MAX / top) if top > R_MAX else 1.0
    return {
        ids[i]: (xs[i] * scale, ys[i] * scale, zs[i] * scale)
        for i in range(n)
    }
