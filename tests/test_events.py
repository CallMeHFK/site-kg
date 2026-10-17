from site_kg.events import EventLog, LiveState


def test_seq_monotonic_and_dedupe(tmp_path):
    log = EventLog(tmp_path / "events.ndjson")
    assert log.append({"t": "node_added", "id": "a", "title": "A"})["seq"] == 1
    assert log.append({"t": "node_added", "id": "a", "title": "A2"})["t"] == "node_updated"
    assert log.append({"t": "edge_added", "s": "a", "d": "b", "kind": "ref"})["seq"] == 3
    log.append({"t": "edge_added", "s": "a", "d": "b", "kind": "ref"})
    assert log.last_seq == 3  # duplicate edge not appended


def test_live_state_bridges_pending_edges():
    st = LiveState()
    st.apply({"t": "edge_added", "s": "a", "d": "b", "kind": "ref"})
    assert st.pending["b"] == 1
    evs = st.apply({"t": "node_added", "id": "b"})
    assert evs is None and ("a", "b", "ref") in st.edges and st.pending["b"] == 0


def test_snapshot_and_resume(tmp_path):
    log = EventLog(tmp_path / "events.ndjson")
    for i in range(5):
        log.append({"t": "node_added", "id": f"n{i}"})
    q, backlog = log.subscribe(2)
    assert [e["seq"] for e in backlog] == [3, 4, 5]
    log.append({"t": "ingest_done", "nodes": 5})
    assert q.get(timeout=1)["t"] == "ingest_done"
