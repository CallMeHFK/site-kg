"""Multi-dimensional relation construction. Offline: no network, no LLM."""
from pathlib import Path

import pytest

from site_kg import graph, relations

PROSE = ("The driver probe ordering decides when the interrupt handler is registered "
         "and how the device tree is walked before the interrupt handler runs. "
         "A wrong probe ordering leaves the interrupt handler unbound, so the driver "
         "probe ordering must be documented per bus and per device tree binding.")


def _doc(pid, body, *, title=None, url=""):
    return {"id": pid, "chapter": "_root", "title": title or pid.replace("_", " "),
            "url": url, "sha": "", "body": body}


def _code(pid, path, symbols, source):
    header = f"path: {path}\nlanguage: python\nsymbols: {', '.join(symbols)}\n\n"
    return _doc(pid, header + source)


def _profiles(docs):
    profiles = [relations.profile_of(d) for d in docs]
    relations._index_groups(profiles)
    return profiles


def _dims(p, **kw):
    return relations.select_dimensions(p, enabled=set(relations.KEYLESS_KINDS),
                                       heavy_ok=True, **kw)


# ---- the feature: selection is per node, not per corpus -------------------
def test_dimensions_differ_by_node_content():
    docs = [
        _doc("stub", "todo"),
        _doc("guide_a", PROSE),
        _doc("guide_b", PROSE.replace("driver", "driver") + " Extra wiring notes."),
        _code("svc_auth.py", "svc/auth.py", ["load_token", "verify_token"],
              "def load_token(x):\n    return verify_token(x)\n"),
        _code("svc_user.py", "svc/user.py", ["verify_user"],
              "def verify_user(u):\n    return load_token(u)\n"),
        _doc("release_v1", PROSE, url="https://s.example/docs/release-v1"),
        _doc("release_v2", PROSE, url="https://s.example/docs/release-v2"),
    ]
    dims = {p["id"]: _dims(p) for p in _profiles(docs)}

    assert "similar_to" not in dims["stub"]        # no lexical signature in a stub
    assert "similar_to" in dims["guide_a"]
    assert "mentions_symbol" in dims["svc_user.py"]
    assert "mentions_symbol" not in dims["guide_a"]  # prose is not code
    assert "sibling_of" in dims["svc_auth.py"]     # same source directory
    assert "supersedes" in dims["release_v2"]
    assert "supersedes" not in dims["guide_a"]     # no version in id or url
    # the sets are genuinely different, not one global config replayed per node
    assert len({tuple(v) for v in dims.values()}) >= 4


def test_similar_to_skips_short_bodies():
    docs = [_doc("a", PROSE), _doc("b", PROSE.replace("driver", "driver")),
            _doc("stub", "see a")]
    edges, _ = relations.build_relations(docs, mode="auto", have={("a", "b")})
    assert not any(e["k"] == "similar_to" for e in edges)


def test_symbol_reference_links_code_files():
    docs = [
        _doc("seed_a", PROSE, url="https://s.example/x"),
        _code("svc_auth.py", "svc/auth.py", ["load_token"],
              "def load_token(x):\n    return x\n"),
        _code("svc_user.py", "svc/user.py", ["verify_user"],
              "def verify_user(u):\n    return load_token(u) or load_token(u)\n"),
    ]
    # seed with an evidence edge so derived dimensions are allowed to run
    edges, rep = relations.build_relations(docs, mode="auto", have={("seed_a", "x")})
    syms = [(e["s"], e["t"]) for e in edges if e["k"] == "mentions_symbol"]
    assert ("svc_user.py", "svc_auth.py") in syms   # user code uses auth's symbol
    assert ("svc_auth.py", "svc_user.py") not in syms  # no reverse invention
    assert rep["dimensions"]["mentions_symbol"]["edges"] == len(syms)


def test_version_lineage_is_directional():
    docs = [_doc("release_v1", PROSE, url="https://s.example/docs/release-v1"),
            _doc("release_v2", PROSE, url="https://s.example/docs/release-v2"),
            _doc("release_v3", PROSE, url="https://s.example/docs/release-v3")]
    edges, _ = relations.build_relations(docs, mode="supersedes", have={("a", "b")})
    pairs = {(e["s"], e["t"]) for e in edges if e["k"] == "supersedes"}
    assert pairs == {("release_v3", "release_v2"), ("release_v2", "release_v1")}


# ---- invariants -----------------------------------------------------------
def test_derived_edges_never_replace_evidence():
    docs = [_doc("a", PROSE), _doc("b", PROSE.replace("driver", "driver"))]
    have = {("a", "b")}  # already linked by a real markdown link
    edges, _ = relations.build_relations(docs, mode="all", have=have)
    assert not any(e["k"] == "similar_to" and {e["s"], e["t"]} == {"a", "b"}
                   for e in edges)


def test_off_mode_is_a_noop():
    docs = [_doc("a", PROSE), _doc("b", PROSE)]
    edges, rep = relations.build_relations(docs, mode="off")
    assert edges == []
    assert rep["relationMode"] == "off"


def test_explicit_dimension_list_narrows_the_registry():
    docs = [_code("s_auth.py", "svc/auth.py", ["load_token"], "def load_token(x):\n    return x\n"),
            _code("s_user.py", "svc/user.py", ["verify_user"],
                  "def verify_user(u):\n    return load_token(u)\n")]
    edges, _ = relations.build_relations(docs, mode="mentions_symbol", have={("a", "b")})
    assert {e["k"] for e in edges} == {"mentions_symbol"}


def test_llm_dimensions_fail_open(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("no gateway")

    monkeypatch.setattr("site_kg.semantic.discover_models", boom, raising=False)
    docs = [_doc("a", PROSE), _doc("b", PROSE.replace("driver", "driver"))]
    edges, rep = relations.build_relations(docs, mode="llm", have={("a", "z")})
    assert any(e["k"] == "similar_to" for e in edges)   # keyless still ran
    assert any("skipped" in n for n in rep["relationNotes"])
    assert "shares_entity" not in rep["dimensions"]


# ---- integration through build_graph --------------------------------------
def _write(corpus: Path, name: str, title: str, url: str, body: str):
    corpus.mkdir(parents=True, exist_ok=True)
    fm = f"---\ntitle: {title}\nsource_url: {url}\nsha256: x\n---\n\n"
    (corpus / name).write_text(fm + body, encoding="utf-8")


def test_graph_enriches_a_ready_corpus(tmp_path: Path):
    c = tmp_path / "c"
    _write(c, "guide_a.md", "Guide A", "https://s.example/a",
           PROSE + "\nsee [guide b](guide_b.md)")
    _write(c, "guide_b.md", "Guide B", "https://s.example/b", PROSE)
    _write(c, "stub.md", "Stub", "https://s.example/stub", "todo")
    (c / "svc").mkdir()
    _write(c / "svc", "svc_auth.py.md", "svc/auth.py", "file:///svc/auth.py",
           "path: svc/auth.py\nlanguage: python\nsymbols: load_token\n\n"
           "def load_token(x):\n    return x\n")
    _write(c / "svc", "svc_user.py.md", "svc/user.py", "file:///svc/user.py",
           "path: svc/user.py\nlanguage: python\nsymbols: verify_user\n\n"
           "def verify_user(u):\n    return load_token(u)\n")

    built = graph.build_graph(c, relations="auto")
    kinds = {e["k"] for e in built["graph"]["edges"]}
    assert "ref" in kinds and "mentions_symbol" in kinds
    assert built["report"]["verdict"] == "READY"
    assert built["report"]["relationMode"] == "auto"
    assert built["report"]["dimensions"]["mentions_symbol"]["nodes"] == 2


def test_graph_never_invents_edges_for_a_linkless_corpus(tmp_path: Path):
    c = tmp_path / "c"
    _write(c, "one.md", "One", "https://s.example/one", PROSE)
    _write(c, "two.md", "Two", "https://s.example/two", PROSE)
    built = graph.build_graph(c, relations="all")
    assert built["report"]["verdict"] == "NOT-READY"
    assert built["report"]["edges"] == 0


def test_off_mode_matches_pre_feature_behaviour(tmp_path: Path):
    c = tmp_path / "c"
    _write(c, "one.md", "One", "https://s.example/one", PROSE + "\nsee [two](two.md)")
    _write(c, "two.md", "Two", "https://s.example/two", PROSE)
    off = graph.build_graph(c, relations="off")
    assert {e["k"] for e in off["graph"]["edges"]} <= {"ref", "contains", "hub", "imports"}


@pytest.mark.parametrize("mode,expected", [
    ("auto", "auto"), ("off", "off"), ("all", "all"), ("llm", "llm"),
    ("similar_to", "similar_to"), ("bogus,nonsense", "bogus,nonsense"),
])
def test_resolve_mode(mode, expected):
    resolved, enabled, use_llm = relations.resolve_mode(mode)
    assert resolved == expected
    assert use_llm is (mode == "llm")
    if mode in ("off", "bogus,nonsense"):
        assert enabled == set()
