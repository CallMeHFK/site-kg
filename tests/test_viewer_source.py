"""Source invariants for viewer JS whose full render loops cannot run under
the node harness (promo draws to canvas inside rAF). They pin the exact
scope regressions fixed for #24."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "site_kg" / "viewer"


def test_promo_flick_is_hoisted_above_the_branch_split():
    src = (ROOT / "promo.js").read_text(encoding="utf-8")
    branch = src.index("if (m <= 1)")
    decl = src.index("var flick =")
    assert decl < branch, "flick must be declared before both branches read it"
    assert src.count("var flick =") == 1
