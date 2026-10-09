"""Offline tests for local-directory ingest. Run: pytest -q"""
import json
from pathlib import Path

import pytest

from site_kg import graph, local


def make_tree(root: Path) -> None:
    (root / "docs").mkdir(parents=True)
    (root / "pkg").mkdir()
    (root / "index.md").write_text(
        "# Home\n\nSee [the guide](docs/guide.md) and [[Design Notes]].\n")
    (root / "docs" / "guide.md").write_text(
        "# Guide\n\nBack to [home](../index.md). Missing [gone](nope.md).\n")
    (root / "docs" / "design_notes.md").write_text("# Design Notes\n\nprose\n")
    (root / "pkg" / "__init__.py").write_text("")
    (root / "pkg" / "a.py").write_text(
        "from pkg import b\nimport json\nfrom . import helper\n"
        "from pkg import (\n    helper,\n)\n\n\ndef alpha():\n    return 1\n")
    (root / "pkg" / "b.py").write_text("class Beta:\n    pass\n")
    (root / "pkg" / "helper.py").write_text("def gamma():\n    return 2\n")
    (root / "app.ts").write_text(
        "import { util } from './src/util';\nimport react from 'react';\n")
    (root / "src").mkdir()
    (root / "src" / "util.ts").write_text("export function util() {\n  return 3;\n}\n")


def build(root: Path, corpus: Path, **kw) -> dict:
    stats = local.build_local_corpus(root, corpus, **kw)
    built = graph.build_graph(corpus)
    return {"stats": stats, **built}


def test_local_docs_links_and_wikilinks(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    make_tree(src)
    r = build(src, tmp_path / "corpus")
    etypes = r["report"]["edgeTypes"]
    assert r["report"]["verdict"] == "READY"
    assert etypes.get("ref", 0) >= 3  # md link x2 + wikilink x1 (back-link makes 4th)
    pairs = {(e["s"], e["t"], e["k"]) for e in r["graph"]["edges"]}
    assert ("index", "docs_guide", "ref") in pairs
    assert ("index", "docs_design_notes", "ref") in pairs  # [[Design Notes]] -> design_notes.md
    assert ("docs_guide", "index", "ref") in pairs
    assert r["stats"]["dropped_refs"] == 1  # nope.md counted, no edge invented
    assert not any(e["t"] == "nope" for e in r["graph"]["edges"])


def test_local_code_import_edges(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    make_tree(src)
    r = build(src, tmp_path / "corpus")
    pairs = {(e["s"], e["t"], e["k"]) for e in r["graph"]["edges"]}
    assert ("pkg_a.py", "pkg_b.py", "imports") in pairs
    assert ("pkg_a.py", "pkg_helper.py", "imports") in pairs
    assert ("pkg_a.py", "pkg___init__.py", "imports") in pairs
    assert ("app.ts", "src_util.ts", "imports") in pairs
    # stdlib/3rd-party imports must not invent edges
    assert not any(e["k"] == "imports" and e["t"] in ("json", "react")
                   for e in r["graph"]["edges"])


def test_local_code_symbols_searchable(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    make_tree(src)
    r = build(src, tmp_path / "corpus")
    hits = graph.search_index(r["search"], "gamma", 5)
    assert hits and hits[0]["id"] == "pkg_helper.py"


def test_local_readme_hub_contains(tmp_path: Path):
    src = tmp_path / "src"
    (src / "docs").mkdir(parents=True)
    (src / "docs" / "README.md").write_text("# Docs\n")
    (src / "docs" / "a.md").write_text("a\n")
    (src / "docs" / "b.md").write_text("b\n")
    r = build(src, tmp_path / "corpus")
    pairs = {(e["s"], e["t"], e["k"]) for e in r["graph"]["edges"]}
    assert ("docs_README", "docs_a", "contains") in pairs
    assert ("docs_README", "docs_b", "contains") in pairs
    assert r["report"]["verdict"] == "NOT-READY"  # fan-out alone is a ToC


def test_local_imports_only_verdict_is_ready(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text("import b\n")
    (src / "b.py").write_text("x = 1\n")
    r = build(src, tmp_path / "corpus")
    assert r["report"]["verdict"] == "READY"
    assert set(r["report"]["edgeTypes"]) == {"imports"}


def test_local_not_ready_when_no_structure(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "one.md").write_text("prose only\n")
    (src / "two.py").write_text("x = 1\n")
    r = build(src, tmp_path / "corpus")
    assert r["report"]["verdict"] == "NOT-READY"
    assert r["report"]["edges"] == 0


def test_local_gitignore_and_noise_dirs(tmp_path: Path):
    pytest.importorskip("pathspec")
    src = tmp_path / "src"
    (src / "node_modules" / "junk").mkdir(parents=True)
    (src / "node_modules" / "junk" / "dep.js").write_text("export const d = 1;\n")
    (src / "secret.py").write_text("TOKEN = 'x'\n")
    (src / ".gitignore").write_text("secret.py\n")
    (src / "main.py").write_text("import os\n")
    stats = local.build_local_corpus(src, tmp_path / "corpus")
    ids = {n["i"] for n in graph.build_graph(tmp_path / "corpus")["graph"]["nodes"]}
    assert "main.py" in ids
    assert "secret.py" not in ids  # via .gitignore when pathspec is installed
    assert not any("node_modules" in i or "dep.js" in i for i in ids)
    assert stats["skipped_ignored"] >= 1


def test_local_edges_sidecar_shape(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    make_tree(src)
    corpus = tmp_path / "corpus"
    local.build_local_corpus(src, corpus)
    edges = json.loads((corpus / "_edges.json").read_text())
    assert edges and all(set(e) == {"s", "t", "k"} for e in edges)


def test_pagerank_rewards_linked_pages(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    make_tree(src)
    r = build(src, tmp_path / "corpus")
    ranks = {n["i"]: n["r"] for n in r["graph"]["nodes"]}
    assert ranks["index"] > 0
    # the linked-to home page outranks an unreferenced leaf
    assert ranks["index"] > ranks["docs_design_notes"]


def test_symlinks_are_never_followed(tmp_path: Path):
    outside = tmp_path / "outside.md"
    outside.write_text("secret content\n")
    src = tmp_path / "src"
    src.mkdir()
    (src / "real.md").write_text("hello [x](real.md)\n")
    (src / "link.md").symlink_to(outside)          # file symlink -> outside root
    (src / "loop").symlink_to(src, target_is_directory=True)  # dir symlink cycle
    stats = local.build_local_corpus(src, tmp_path / "corpus")
    ids = {n["i"] for n in graph.build_graph(tmp_path / "corpus")["graph"]["nodes"]}
    assert ids == {"real"}
    assert stats["skipped_symlink"] == 1  # link.md; loop dir pruned before descend


def test_reingest_leaves_no_ghost_docs(tmp_path: Path):
    from site_kg import store
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.md").write_text("[b](b.md)\n")
    (src / "b.md").write_text("b\n")
    corpus = tmp_path / "corpus"
    local.build_local_corpus(src, corpus)
    (src / "b.md").unlink()
    store.reset_corpus(corpus)
    local.build_local_corpus(src, corpus)
    built = graph.build_graph(corpus)
    assert {n["i"] for n in built["graph"]["nodes"]} == {"a"}
    assert built["report"]["verdict"] == "NOT-READY"


def test_fenced_code_and_images_make_no_edges(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.md").write_text(
        "# A\n\n```\nexample: [x](b.md)\n```\n\n![pic](b.md)\n\ninline `[y](b.md)`\n")
    (src / "b.md").write_text("b\n")
    r = build(src, tmp_path / "corpus")
    assert r["graph"]["edges"] == []
    assert r["stats"]["dropped_refs"] == 0  # image never counted as a doc ref


def test_hub_only_verdict_is_not_ready(tmp_path: Path):
    # index fan-out alone is a table of contents, not a traversable graph
    d = tmp_path / "c"
    d.mkdir()
    (d / "index.md").write_text("---\ntitle: I\n---\n\n[a](a.md) [b](b.md)")
    (d / "a.md").write_text("---\ntitle: A\n---\n\nprose")
    (d / "b.md").write_text("---\ntitle: B\n---\n\nprose")
    built = graph.build_graph(d)
    assert built["report"]["verdict"] == "NOT-READY"
    assert set(built["report"]["edgeTypes"]) == {"hub"}


def test_ingest_local_tool_guards_and_clamp(tmp_path: Path):
    import asyncio

    from site_kg import mcp_server
    r = asyncio.run(mcp_server.ingest_local(str(tmp_path / "nope")))
    assert r["ok"] is False and "not a directory" in r["error"]
    f = tmp_path / "one.md"
    f.write_text("x\n")
    r = asyncio.run(mcp_server.ingest_local(str(f)))
    assert r["ok"] is False and "parent directory" in r["error"]
