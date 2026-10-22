"""Sync gates for the derived edge-kind registries.

relations.py is the authority for derived edge kinds (KEYLESS_KINDS +
MODEL_KINDS), but core.js (default hidden kinds and legend), layout.py
(spring gating) and buildview.py (phase order) each keep their own copy.
When the five content-derived dimensions were added to relations.py, all
three copies were missed: the new kinds showed by default, pulled on the
layout at the 0.10 structural fallback weight, and fell to the build
replay's alphabetical tail. This gate turns that drift into a failure.
"""
from __future__ import annotations

import re
from pathlib import Path

from site_kg import buildview, layout, relations

ROOT = Path(__file__).resolve().parents[1]
VIEWER = ROOT / "site_kg" / "viewer"


def _core_derived_kinds() -> set[str]:
    """Keys of core.js's DERIVED object literal, scraped with a regex (the
    repo's source-scraping style) so this gate runs under plain pytest with
    no node dependency."""
    src = (VIEWER / "core.js").read_text(encoding="utf-8")
    m = re.search(r"var DERIVED\s*=\s*\{([^}]*)\}", src)
    assert m, "core.js: no `var DERIVED = {...}` literal found"
    kinds = set(re.findall(r"(\w+)\s*:", m.group(1)))
    assert kinds, "core.js: DERIVED literal parsed to nothing"
    return kinds


def test_derived_registries_match_relations():
    authoritative = set(relations.KEYLESS_KINDS) | set(relations.MODEL_KINDS)
    core = _core_derived_kinds()
    assert set(layout.DERIVED) == authoritative, \
        f"layout.py DERIVED drift: {set(layout.DERIVED) ^ authoritative}"
    assert set(buildview.DERIVED_ORDER) == authoritative, \
        f"buildview.py DERIVED_ORDER drift: {set(buildview.DERIVED_ORDER) ^ authoritative}"
    assert core == authoritative, \
        f"core.js DERIVED drift: {core ^ authoritative}"
    # The phase order drives the build replay's timeline; a duplicate entry
    # would replay one dimension twice.
    assert len(buildview.DERIVED_ORDER) == len(set(buildview.DERIVED_ORDER))


def test_fallback2d_exposes_the_renderer_surface_ui_calls():
    """ui.js's pointer handlers call beginDrag/moveDrag/endDrag/poke on
    whichever renderer they own, and Fallback2D is a real peer (picked when
    WebGL2 is missing or the GL mount fails); a missing method turns the
    first node click into an uncaught TypeError. Source-level check:
    instantiating the fallback needs a canvas."""
    src = (VIEWER / "fallback2d.js").read_text(encoding="utf-8")
    for name in ("beginDrag", "moveDrag", "endDrag", "poke"):
        assert re.search(rf"Fallback2D\.prototype\.{name}\s*=", src), \
            f"fallback2d.js does not define {name}"
