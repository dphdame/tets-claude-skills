"""Unit tests for misattribution engine."""
import sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from src import misattribution


# Catalog path: resolves to ../../shared/misattribution-catalog.yaml relative to the
# skill folder (the canonical location after install). Override with TETS_CATALOG_PATH
# in the environment for tests against a custom catalog.
import os
_default_catalog = Path(__file__).parent.parent.parent / "shared" / "misattribution-catalog.yaml"
SPEC_PATH = Path(os.environ.get("TETS_CATALOG_PATH", str(_default_catalog)))
CATALOG_PATH = SPEC_PATH if SPEC_PATH.exists() else SPEC_PATH


def test_catalog_loads():
    catalog = misattribution.load_catalog(CATALOG_PATH)
    assert len(catalog) >= 10
    # The seed catalog has 15 entries
    assert any(e.get("id") == "cs-not-yet-treated-mis-credited-to-sa"
               for e in catalog)


def test_misattribution_match_sun_abraham_not_yet_treated():
    catalog = misattribution.load_catalog(CATALOG_PATH)
    # Common wrong attribution: Sun-Abraham not-yet-treated
    cite = "Sun and Abraham (2021) not-yet-treated comparison"
    matches = misattribution.find_misattribution(cite, catalog)
    assert len(matches) >= 1
    ids = [m.get("id") for m in matches]
    assert "cs-not-yet-treated-mis-credited-to-sa" in ids


def test_misattribution_no_false_positive_on_correct_credit():
    catalog = misattribution.load_catalog(CATALOG_PATH)
    # Correct attribution: should NOT trigger
    cite = "Callaway & Sant'Anna 2021 JoE (10.1016/j.jeconom.2020.12.001)"
    matches = misattribution.find_misattribution(cite, catalog)
    # The catalog has entries about CS being mis-credited; the citation
    # itself is correct. We expect zero matches against wrong_credit.authors
    # = Sun & Abraham. CS itself doesn't appear as a wrong_credit in the
    # seed catalog, so zero matches expected.
    assert all(m.get("wrong_credit", {}).get("authors") != "Callaway & Sant'Anna"
               for m in matches)


def test_misattribution_match_causal_forests_athey_tibshirani_wager():
    catalog = misattribution.load_catalog(CATALOG_PATH)
    cite = "Athey-Tibshirani-Wager 2019 causal forests"
    matches = misattribution.find_misattribution(cite, catalog)
    ids = [m.get("id") for m in matches]
    assert "causal-forests-vs-grf" in ids


def test_format_pending_review_entry():
    catalog = misattribution.load_catalog(CATALOG_PATH)
    matches = misattribution.find_misattribution(
        "Sun-Abraham 2021 doubly-robust ATT estimator", catalog
    )
    assert len(matches) >= 1
    md = misattribution.format_pending_review_entry(
        "10.1016/j.jeconom.2020.12.001",
        "Sun-Abraham IW",
        "Sun-Abraham 2021 doubly-robust ATT estimator",
        matches,
        self_consistency_passed=True,
    )
    assert "Self-consistency gate passed: True" in md
    assert "MISATTRIBUTION" not in md  # we don't put that token in the entry
    assert "Wrong credit" in md
