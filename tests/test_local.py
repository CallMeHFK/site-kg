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
    # no evidence edges: fenced and image links never become ref/imports.
    # A same-directory sibling_of edge is correct derived behaviour (local
    # corpora get directory cohorts since the file:// dir-key fix), so only
    # the evidence kinds are forbidden here.
    kinds = {e["k"] for e in r["graph"]["edges"]}
    assert not kinds & {"ref", "imports", "contains"}
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
    # the note must agree with the verdict: hub fan-out is not link structure
    assert "link structure present" not in built["report"]["verdictNote"]


def test_ingest_local_tool_guards_and_clamp(tmp_path: Path):
    import asyncio

    from site_kg import mcp_server
    r = asyncio.run(mcp_server.ingest_local(str(tmp_path / "nope")))
    assert r["ok"] is False and "not a directory" in r["error"]
    f = tmp_path / "one.md"
    f.write_text("x\n")
    r = asyncio.run(mcp_server.ingest_local(str(f)))
    assert r["ok"] is False and "parent directory" in r["error"]


def make_noise_collision_tree(root: Path) -> None:
    """Real-source dirs colliding with NOISE_DIRS names (kernel: drivers/target)."""
    (root / "drivers" / "target").mkdir(parents=True)
    (root / "drivers" / "target" / "a.c").write_text("int a(void) { return 0; }\n")
    (root / "tools" / "target").mkdir(parents=True)
    (root / "tools" / "target" / "b.c").write_text("int b(void) { return 0; }\n")
    (root / "main.c").write_text("int main(void) { return 0; }\n")


def _node_ids(corpus: Path) -> set:
    return {n["i"] for n in graph.build_graph(corpus)["graph"]["nodes"]}


def test_noise_dirs_pruned_by_default(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("SITE_KG_NOISE_EXEMPT", raising=False)
    src = tmp_path / "src"
    src.mkdir()
    make_noise_collision_tree(src)
    local.build_local_corpus(src, tmp_path / "corpus")
    ids = _node_ids(tmp_path / "corpus")
    assert "main.c" in ids
    assert "drivers_target_a.c" not in ids  # "target" is a NOISE_DIRS name
    assert "tools_target_b.c" not in ids


def test_noise_exempt_env_prefix(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SITE_KG_NOISE_EXEMPT", "drivers/target")
    src = tmp_path / "src"
    src.mkdir()
    make_noise_collision_tree(src)
    local.build_local_corpus(src, tmp_path / "corpus")
    ids = _node_ids(tmp_path / "corpus")
    assert "drivers_target_a.c" in ids
    # the exemption is a path prefix, not a name-wide amnesty
    assert "tools_target_b.c" not in ids


def test_noise_exempt_read_per_call(tmp_path: Path, monkeypatch):
    # env is read inside _walk at call time: same process, no module reload
    src = tmp_path / "src"
    src.mkdir()
    make_noise_collision_tree(src)
    monkeypatch.delenv("SITE_KG_NOISE_EXEMPT", raising=False)
    local.build_local_corpus(src, tmp_path / "c1")
    assert "drivers_target_a.c" not in _node_ids(tmp_path / "c1")
    monkeypatch.setenv("SITE_KG_NOISE_EXEMPT", "drivers/target")
    local.build_local_corpus(src, tmp_path / "c2")
    assert "drivers_target_a.c" in _node_ids(tmp_path / "c2")


def make_kernel_flavored_tree(root: Path) -> None:
    """Files the code whitelist used to miss on a kernel/DT/build tree."""
    (root / "boot").mkdir()
    (root / "dt-bindings").mkdir()
    (root / "scripts").mkdir()
    (root / "boot" / "start.S").write_text(".global _start\n_start:\n")
    (root / "boot" / "link.lds").write_text("SECTIONS { .text : { *(.text) } }\n")
    (root / "dt-bindings" / "nvidia,tegra-soc.yaml").write_text(
        "# SPDX-License-Identifier: GPL-2.0\ndescription: Tegra SoC binding\n")
    (root / "dt-bindings" / "board.dts").write_text("/dts-v1/;\n/ {\n};\n")
    (root / "scripts" / "insert.awk").write_text("BEGIN { print \"awk\" }\n")
    (root / "scripts" / "config.pl").write_text("print \"perl\\n\";\n")
    (root / "frag.mk").write_text("obj-y += driver.o\n")
    (root / "Makefile").write_text("obj-y += boot/\n")
    (root / "Kconfig").write_text("config FOO\n\tbool \"foo\"\n")
    (root / "COPYING").write_text("GNU GENERAL PUBLIC LICENSE\n")
    (root / "README").write_text("# readme\n")


def test_kernel_build_ecosystem_extensions(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    make_kernel_flavored_tree(src)
    corpus = tmp_path / "corpus"
    stats = local.build_local_corpus(src, corpus)
    docs = graph.build_graph(corpus)["docs"]  # id -> {title, url, body}
    # asm .S (case), linker script, DT binding yaml, devicetree dts, perl/awk,
    # makefile fragments and extension-less build files all become docs
    for pid in ("boot_start.S", "boot_link.lds", "dt-bindings_nvidia_tegra-soc.yaml",
                "dt-bindings_board.dts", "scripts_insert.awk", "scripts_config.pl",
                "frag.mk", "Makefile", "Kconfig", "COPYING", "README"):
        assert pid in docs, pid
    # code branch: title is the relpath (not a body line) and the language tag
    # is emitted, so lang "license" is not confused with a .md title heading
    assert docs["COPYING"]["title"] == "COPYING"
    assert docs["COPYING"]["body"].lstrip("\n").startswith(
        "path: COPYING\nlanguage: license")
    assert docs["Makefile"]["body"].lstrip("\n").startswith(
        "path: Makefile\nlanguage: makefile")
    assert "language: asm" in docs["boot_start.S"]["body"]
    assert "language: devicetree" in docs["dt-bindings_board.dts"]["body"]
    assert stats["code"] >= 11 and stats["docs"] == 0


def test_extensionless_ids_do_not_collapse(tmp_path: Path):
    # rel[:-len("")] is "": without the guard every extension-less file
    # collapsed onto the fallback id "index"
    src = tmp_path / "src"
    src.mkdir()
    (src / "Makefile").write_text("all:\n")
    (src / "Kconfig").write_text("config X\n")
    (src / "index.md").write_text("# real index\n")
    local.build_local_corpus(src, tmp_path / "corpus")
    ids = _node_ids(tmp_path / "corpus")
    assert {"Makefile", "Kconfig", "index"} <= ids


def test_extensionless_readme_is_still_a_hub(tmp_path: Path):
    src = tmp_path / "src"
    (src / "docs").mkdir(parents=True)
    (src / "docs" / "README").write_text("# docs\n")
    (src / "docs" / "a.rst").write_text("a\n")
    r = build(src, tmp_path / "corpus")
    pairs = {(e["s"], e["t"], e["k"]) for e in r["graph"]["edges"]}
    assert ("docs_README", "docs_a", "contains") in pairs


def test_scan_stats_attribute_every_skip(tmp_path: Path):
    src = tmp_path / "src"
    (src / "node_modules" / "pkg").mkdir(parents=True)
    (src / "node_modules" / "pkg" / "dep.js").write_text("export const d = 1;\n")
    (src / "package-lock.json").write_text("{\"lockfileVersion\": 3}\n")
    (src / "blob.xyz").write_text("unknown extension\n")
    (src / "keep.py").write_text("import os\n")
    stats = local.build_local_corpus(src, tmp_path / "corpus")
    assert stats["skipped_noise_file"] == 1  # lockfile, not "unsupported ext"
    assert stats["skipped_ext"] == 1
    assert stats["pruned_noise_dirs"] >= 1
    assert "node_modules" in stats["pruned_noise_dirs_sample"]
    # skipped_kind stays the documented lump so old consumers keep working
    assert stats["skipped_kind"] == (stats["skipped_noise_file"]
                                     + stats["skipped_ext"]
                                     + stats.get("skipped_oserror", 0))


def test_max_bytes_env_is_read_per_call(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("SITE_KG_MAX_BYTES", raising=False)
    src = tmp_path / "src"
    src.mkdir()
    (src / "big.py").write_text("x = '" + "a" * (600 * 1024) + "'\n")
    (src / "small.py").write_text("y = 1\n")
    local.build_local_corpus(src, tmp_path / "c1")
    assert "big.py" not in _node_ids(tmp_path / "c1")  # 600K > 512K default
    monkeypatch.setenv("SITE_KG_MAX_BYTES", str(2 * 1024 * 1024))
    local.build_local_corpus(src, tmp_path / "c2")
    assert "big.py" in _node_ids(tmp_path / "c2")  # same process, no reload


def test_dotfiles_never_match_plain_files(tmp_path: Path):
    # pathlib gives .env an empty suffix: without the dotfile guard a .notes or
    # .license file would suddenly be ingested by basename
    src = tmp_path / "src"
    src.mkdir()
    (src / ".notes").write_text("secret scratch\n")
    (src / ".license").write_text("proprietary\n")
    (src / "notes").write_text("real notes\n")
    stats = local.build_local_corpus(src, tmp_path / "corpus")
    ids = _node_ids(tmp_path / "corpus")
    assert "notes" in ids
    assert ".notes" not in ids and ".license" not in ids
    assert stats["skipped_ext"] == 2
