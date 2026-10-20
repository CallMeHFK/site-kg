"""Multi-dimensional relation construction. Offline: no network, no LLM."""
import hashlib
import inspect
import math
import sys
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
    # have only dedups against evidence; derived dimensions run regardless
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
    # real sha like the corpus writer: duplicate_of chains identical content,
    # so a constant fake hash here would chain every doc in the test corpus
    raw = (title + "\n" + body).encode()
    fm = (f"---\ntitle: {title}\nsource_url: {url}\n"
          f"sha256: {hashlib.sha256(raw).hexdigest()}\n---\n\n")
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


def test_graph_never_invents_edges_for_a_stub_corpus(tmp_path: Path):
    # a corpus with no prose, no headings, no dirs, no versions gates to zero
    # edges even with relations="all" -- the anti-hairball guard
    c = tmp_path / "c"
    _write(c, "one.md", "One", "", "todo")
    _write(c, "two.md", "Two", "", "more todo")
    built = graph.build_graph(c, relations="all")
    assert built["report"]["verdict"] == "NOT-READY"
    assert built["report"]["edges"] == 0


def test_linkless_corpus_gains_content_edges_but_stays_not_ready(tmp_path: Path):
    # content is the only signal a link-less corpus has: keyless content
    # dimensions run, but the verdict keeps saying "no link structure"
    c = tmp_path / "c"
    _write(c, "one.md", "One", "https://s.example/one", PROSE)
    _write(c, "two.md", "Two", "https://s.example/two", PROSE)
    built = graph.build_graph(c, relations="all")
    rep = built["report"]
    assert rep["verdict"] == "NOT-READY"
    kinds = {e["k"] for e in built["graph"]["edges"]}
    assert kinds and kinds <= set(relations.KEYLESS_KINDS) | set(relations.MODEL_KINDS)
    assert "content-derived" in rep["verdictNote"]


# ---- shares_heading: document-skeleton overlap ------------------------------
HEADINGS = ("# Installation\n\nbody text here\n\n## Configuration\n\nmore text\n\n"
            "## Troubleshooting\n\nlast text\n")


def test_shares_heading_links_pages_with_the_same_skeleton():
    docs = [_doc("api_cats", HEADINGS + "Cats are small felines."),
            _doc("api_dogs", HEADINGS + "Dogs are loyal animals."),
            _doc("note", "# Only one heading\n\ntiny")]
    edges, rep = relations.build_relations(docs, mode="auto")
    pairs = {(e["s"], e["t"]) for e in edges if e["k"] == "shares_heading"}
    assert ("api_cats", "api_dogs") in pairs
    assert not any("note" in p for p in pairs)          # 1 heading: not a skeleton
    assert rep["dimensions"]["shares_heading"]["nodes"] == 2


def test_heading_dimension_gated_by_skeleton_size():
    p = relations.profile_of(_doc("a", "# one\n\ntext"))
    assert "shares_heading" not in _dims(p)
    p2 = relations.profile_of(_doc("b", HEADINGS))
    assert "shares_heading" in _dims(p2)


def test_heading_edges_are_undirected_and_capped():
    # 6 pages sharing one skeleton -> 15 pairs before the cap; every node
    # nominates at most HEAD_CAP neighbours and pairs canonicalize a < b
    docs = [_doc(f"page_{i}", HEADINGS + f"variant {i}") for i in range(6)]
    edges, _ = relations.build_relations(docs, mode="shares_heading")
    hs = [e for e in edges if e["k"] == "shares_heading"]
    assert hs
    assert all(e["s"] < e["t"] for e in hs)
    for pid in {e["s"] for e in hs} | {e["t"] for e in hs}:
        n = sum(1 for e in hs if pid in (e["s"], e["t"]))
        assert n <= relations.HEAD_CAP


def test_heading_edges_respect_the_have_set():
    docs = [_doc("api_cats", HEADINGS), _doc("api_dogs", HEADINGS)]
    edges, _ = relations.build_relations(docs, mode="shares_heading",
                                         have={("api_cats", "api_dogs")})
    assert not any(e["k"] == "shares_heading" for e in edges)


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


# ---- the three mode channels: --relations / relations= / SITE_KG_RELATIONS -
def test_env_var_selects_mode_when_mode_is_absent(monkeypatch):
    monkeypatch.setenv("SITE_KG_RELATIONS", "off")
    resolved, enabled, _ = relations.resolve_mode(None)
    assert resolved == "off"
    assert enabled == set()
    # an explicit mode beats the environment
    resolved, enabled, _ = relations.resolve_mode("auto")
    assert resolved == "auto"
    assert enabled == set(relations.KEYLESS_KINDS)


def test_cli_relations_default_defers_to_env(monkeypatch):
    from site_kg import cli, mcp_server
    captured = {}

    async def fake_ingest_local(path, **kw):
        captured.update(kw)
        return {}

    monkeypatch.setattr(mcp_server, "ingest_local", fake_ingest_local)
    monkeypatch.setattr(sys, "argv", ["site-kg", "ingest-local", "/tmp"])
    cli.main()
    # a concrete default here would shadow SITE_KG_RELATIONS before resolve_mode
    assert captured["relations"] is None


def test_mcp_tools_relations_default_defers_to_env():
    from site_kg import mcp_server
    for tool in (mcp_server.ingest_url, mcp_server.ingest_local):
        assert inspect.signature(tool).parameters["relations"].default is None


# ---- llm router semantics ---------------------------------------------------
def _stub_router(monkeypatch, picks):
    monkeypatch.setattr("site_kg.semantic.discover_models",
                        lambda: {"chat": None, "embed": None}, raising=False)
    monkeypatch.setattr(relations, "route_with_llm",
                        lambda profiles, enabled, notes: picks)


def test_llm_router_may_narrow_a_node_to_zero_dimensions(monkeypatch):
    _stub_router(monkeypatch, {"a": [], "b": []})
    docs = [_doc("a", PROSE), _doc("b", PROSE.replace("driver", "driver"))]
    edges, _ = relations.build_relations(docs, mode="llm", have={("a", "z")})
    assert edges == []


def test_llm_router_falls_back_only_for_nodes_it_did_not_pick(monkeypatch):
    _stub_router(monkeypatch, {"a": []})  # b not picked -> heuristic set
    docs = [_doc("a", PROSE), _doc("b", PROSE.replace("driver", "driver"))]
    edges, _ = relations.build_relations(docs, mode="llm", have={("a", "z")})
    # b kept similar_to, and one endpoint admitting a dimension admits the edge
    assert any(e["k"] == "similar_to" for e in edges)


# ---- duplicate_of: sha256 tier claims pairs before fuzzy dimensions --------
def test_duplicate_of_chains_identical_docs_and_blocks_similar_to():
    body = "alpha " * 60  # long enough for similar_to to fire if unclaimed
    docs = [_doc("d1", body, url="https://s.example/d1"),
            _doc("d2", body, url="https://s.example/d2")]
    for d in docs:
        d["sha"] = "deadbeef"
    edges, rep = relations.build_relations(docs, mode="all")
    pair_kinds = {e["k"] for e in edges if {e["s"], e["t"]} == {"d1", "d2"}}
    assert pair_kinds == {"duplicate_of"}
    assert rep["dimensions"]["duplicate_of"]["edges"] == 1


def test_distinct_docs_get_no_duplicate_of():
    docs = [_doc("d1", "alpha " * 60), _doc("d2", "beta " * 60)]
    for d, sha in zip(docs, ("beef01", "beef02")):
        d["sha"] = sha
    edges, _ = relations.build_relations(docs, mode="all")
    assert not any(e["k"] == "duplicate_of" for e in edges)


# ---- coupled_with / co_cited_with: evidence-graph neighbourhood overlap ----
def _link_docs(*ids):
    return [_doc(i, "todo") for i in ids]


def test_coupled_with_from_shared_citation_targets():
    # p1 and p2 both cite t1 and t2: bibliographic coupling fires
    link_pairs = {("p1", "t1"), ("p1", "t2"), ("p2", "t1"), ("p2", "t2")}
    edges, _ = relations.build_relations(
        _link_docs("p1", "p2", "t1", "t2"), mode="all",
        have=link_pairs, link_pairs=link_pairs)
    kinds = {(e["s"], e["t"], e["k"]) for e in edges}
    assert ("p1", "p2", "coupled_with") in kinds


def test_co_cited_with_relates_two_stubs_via_shared_sources():
    # s1 and s2 both cite x and y: x and y are related without any outgoing link
    link_pairs = {("s1", "x"), ("s1", "y"), ("s2", "x"), ("s2", "y")}
    edges, _ = relations.build_relations(
        _link_docs("s1", "s2", "x", "y"), mode="all",
        have=link_pairs, link_pairs=link_pairs)
    kinds = {(e["s"], e["t"], e["k"]) for e in edges}
    assert ("x", "y", "co_cited_with") in kinds


def test_hub_targets_carry_no_coupling_signal():
    # one shared target with total degree 40: RA weight 1/40 is far under the bar
    link_pairs = {("p1", "hub"), ("p2", "hub")}
    for i in range(38):
        link_pairs.add((f"w{i}", "hub"))
    docs = _link_docs("p1", "p2", "hub", *[f"w{i}" for i in range(38)])
    edges, _ = relations.build_relations(docs, mode="all",
                                         have=link_pairs, link_pairs=link_pairs)
    assert not any(e["k"] in ("coupled_with", "co_cited_with") for e in edges)


def test_toc_sources_generate_no_co_citation():
    # a source with > HUB_OUT_MAX out-links is a table of contents: excluded
    # from generating neighbourhood candidates (and its RA weight 1/30 alone
    # is under the bar regardless)
    link_pairs = {("toc", f"p{i}") for i in range(30)}
    docs = _link_docs("toc", *[f"p{i}" for i in range(30)])
    edges, _ = relations.build_relations(docs, mode="all",
                                         have=link_pairs, link_pairs=link_pairs)
    assert not any(e["k"] in ("coupled_with", "co_cited_with") for e in edges)


# ---- shares_term: curated inline-code / heading / symbol vocabulary ---------
def test_shares_term_fires_where_prose_cosine_cannot():
    # short pages under MIN_PROSE: similar_to is not even selected, but the
    # backticked API vocabulary they share is real evidence
    d1 = _doc("api_a", "Use `alpha_api` with `beta_tool` to proceed.")
    d2 = _doc("api_b", "Reference `alpha_api` plus `beta_tool` for setup.")
    edges, _ = relations.build_relations([d1, d2], mode="all")
    kinds = {(e["s"], e["t"], e["k"]) for e in edges}
    assert ("api_a", "api_b", "shares_term") in kinds
    assert not any(k == "similar_to" for _, _, k in kinds)


def test_single_shared_term_is_not_enough():
    d1 = _doc("a1", "Use `alpha_api` for everything.")
    d2 = _doc("a2", "Reference `alpha_api` only in passing.")
    edges, _ = relations.build_relations([d1, d2], mode="all")
    assert not any(e["k"] == "shares_term" for e in edges)


# ---- scale robustness: the df window is a fraction, not an absolute ---------
def test_df_window_survives_corpus_growth():
    # a term in 30 of 100 docs: the old absolute DF_MAX=25 dropped it and
    # similar_to silently collapsed; the relative window keeps it in play
    topic = "quantumflux"
    docs = []
    for i in range(100):
        if i < 30:
            body = f"About {topic} alignment notes " + "x" * 240
        else:
            body = "Unrelated filler prose about something else " + "y" * 240
        docs.append(_doc(f"p{i:03d}", body, url=f"https://s.example/p{i}"))
    edges, _rep = relations.build_relations(docs, mode="auto")
    sim = [e for e in edges if e["k"] == "similar_to"]
    assert sim, "similar_to collapsed despite in-window topical term"
    tops = {e["s"] for e in sim} | {e["t"] for e in sim}
    assert any(i.startswith("p0") for i in tops)


# ---- mentions_symbol: salience ordering + permutation-robust tie-break ------
def test_symbol_ranking_is_score_ordered_and_deterministic():
    p_def1 = _code("lib_a.py", "lib/a.py", ["gamma_helper"], "pass")
    p_def2 = _code("lib_b.py", "lib/b.py", ["alpha_fn"], "pass")
    p_user = _code("lib_c.py", "lib/c.py", [],
                   "use alpha_fn here\nuse gamma_helper there\n"
                   "and gamma_helper again\nplus alpha_fn twice\nalpha_fn\n"
                   "the zeta_helper is defined elsewhere\nzeta_helper\n")
    p_user["title"] = "about alpha_fn usage"  # title bonus applies to alpha_fn
    p_def3 = _code("lib_d.py", "lib/d.py", ["zeta_helper"], "pass")
    edges = relations._symbol_edges(_profiles([p_def1, p_def2, p_user, p_def3]))
    from_user = [e[1] for e in edges if e[0] == "lib_c.py"]
    # alpha_fn: 3 mentions + 2 title bonus; gamma_helper: 3 mentions; both
    # beat zeta_helper's 2, and equal mentions fall back to (-score, symbol)
    assert from_user == ["lib_b.py", "lib_a.py", "lib_d.py"]


# ---- _dir_key: local (file://) corpora get sibling cohorts back -------------
def test_local_file_urls_get_sibling_cohorts():
    docs = [_doc("local_a", "prose body here", url="file:///docs/guide/a.md"),
            _doc("local_b", "other prose body", url="file:///docs/guide/b.md"),
            _doc("elsewhere", "third body", url="file:///docs/other/c.md")]
    assert relations._dir_key(docs[0]) == "file:///docs/guide"
    edges, _ = relations.build_relations(docs, mode="auto")
    kinds = {(e["s"], e["t"], e["k"]) for e in edges}
    assert ("local_a", "local_b", "sibling_of") in kinds
    assert not any("elsewhere" in (s, t) and k == "sibling_of"
                   for s, t, k in kinds)


def test_identical_stubs_chain_but_stay_not_ready(tmp_path: Path):
    # the one deliberate exception to the stub gate: identical content is its
    # own corpus-level evidence, and the verdict still says "no link structure"
    c = tmp_path / "c"
    for name in ("p1", "p2", "p3"):
        _write(c, f"{name}.md", "Placeholder", f"https://s.example/{name}", "todo")
    built = graph.build_graph(c, relations="all")
    kinds = [e["k"] for e in built["graph"]["edges"]]
    # chain of identical-content edges, not a clique; the three stubs also sit
    # in the same directory, so one sibling_of cohort edge co-fires
    assert sorted(kinds) == ["duplicate_of", "duplicate_of", "sibling_of"]
    dup_pairs = sorted(tuple(sorted((e["s"], e["t"])))
                       for e in built["graph"]["edges"] if e["k"] == "duplicate_of")
    assert dup_pairs == [("p1", "p2"), ("p2", "p3")]
    assert built["report"]["verdict"] == "NOT-READY"


def test_similar_to_needs_a_shared_body_term():
    # a shared title convention alone must not mint a "same topic" edge: both
    # docs clear MIN_PROSE and share the title, but their body vocabularies are
    # disjoint, so the body_sig gate blocks them -- while a genuine pair with
    # shared body terms still connects (positive control keeps this test honest)
    d1 = _doc("ref_a",
              "Widget fabrication demands calibrated torque wrenches, anodised "
              "aluminium fixtures, nickel plating baths, burr free deburring, plus "
              "final inspection checkpoints covering the full assembled unit before "
              "shipment paperwork closes. Fixture torque values are re-checked "
              "whenever the aluminium fixtures move between plating baths, because "
              "a drifting wrench reading silently skews the deburring checkpoints.",
              title="Reference Manual", url="https://s.example/a")
    d2 = _doc("ref_b",
              "Gadget refurbishment involves desoldering corroded connectors, "
              "winding replacement coils, lathe turned spacers, adhesive curing "
              "ovens, until the functional rigs verify each repaired device against "
              "factory tolerances. Corroded connectors that survive desoldering get "
              "replacement coils and lathe turned spacers, so the curing ovens sign "
              "off every repaired device.",
              title="Reference Manual", url="https://s.example/b")
    d3 = _doc("sim_a",
              "Fluxcore probe alignment: mount the fluxcore probe, run gain staging, "
              "bias trim, span sweep, confirm fluxcore readings stay nominal across "
              "the sweep range, then log the fluxcore span results in the alignment "
              "journal. A wandering bias trim or a clipped span sweep invalidates "
              "the fluxcore readings and the alignment journal entry.",
              title="Calibration Handbook", url="https://s.example/c")
    d4 = _doc("sim_b", d3["body"],
              title="Calibration Handbook", url="https://s.example/d")
    edges, _ = relations.build_relations([d1, d2, d3, d4], mode="all")
    sims = {tuple(sorted((e["s"], e["t"]))) for e in edges if e["k"] == "similar_to"}
    assert ("ref_a", "ref_b") not in sims  # title-only match: gated out
    assert ("sim_a", "sim_b") in sims      # shared body terms: fires


def test_df_ceiling_has_no_absolute_cap():
    # P1 regression: a topical term at 20% of a 3000-doc corpus (df=600, above
    # the old DF_ABS_MAX=200) must stay inside the window like the search index
    topic = "fluxcore"
    docs = []
    for i in range(600):
        body = f"{topic} aligned notes " + "x" * 240
        docs.append(_doc(f"c{i:04d}", body, url=f"https://s.example/c{i}"))
    for i in range(2400):
        body = "unrelated filler prose " + "y" * 240
        docs.append(_doc(f"f{i:04d}", body, url=f"https://s.example/f{i}"))
    edges, _ = relations.build_relations(docs, mode="auto")
    assert any(e["k"] == "similar_to" for e in edges), \
        "similar_to collapsed above the old absolute df cap"


def test_heading_edges_stay_bounded_under_pure_ratio_window(monkeypatch):
    # 100 of 400 pages share two headings (df=100): inside the pure-ratio
    # ceiling (0.35*400=140) an old-style absolute cap would have dropped them,
    # and the 100-doc posting list exceeds HEAD_POST_K=64. Emitted edges cannot
    # observe the truncation (HEAD_CAP nominations always prefer low ids, which
    # truncation keeps), so assert on the pair pass itself: truncated owners
    # yield C(64,2) pairs, untruncated would yield C(100,2).
    SHARED = ["Setup Guide", "Change Log"]
    docs = []
    for i in range(400):
        if i < 100:
            hs = [*SHARED, f"page {i} overview"]
        else:
            hs = [f"page {i} overview", f"page {i} details", f"page {i} notes"]
        body = "\n".join(f"## {h}" for h in hs) + "\n\n" + "body text " * 30
        docs.append(_doc(f"h{i:04d}", body, url=f"https://s.example/h{i}"))
    profiles = [relations.profile_of(d) for d in docs]
    cands = [p for p in profiles if len(p["headings"]) >= relations.HEAD_MIN]
    head_hi = relations._df_ceiling(len(cands), relations.HEAD_DF_FLOOR)
    assert head_hi == 140  # pure-ratio window admits df=100

    shared = relations._heading_shared_pairs(cands, head_hi)
    assert len(shared) == math.comb(64, 2)  # truncated to HEAD_POST_K owners
    assert all(n == 2 for n in shared.values())  # both shared headings agree

    monkeypatch.setattr(relations, "HEAD_POST_K", 10 ** 9)
    untruncated = relations._heading_shared_pairs(cands, head_hi)
    assert len(untruncated) == math.comb(100, 2)  # truncation was load-bearing

    # end-to-end: the pass runs through build-relation's gating and stays bounded
    monkeypatch.setattr(relations, "HEAD_POST_K", 64)
    edges = relations._heading_edges(profiles)
    assert 0 < len(edges) <= 400 * relations.HEAD_CAP
