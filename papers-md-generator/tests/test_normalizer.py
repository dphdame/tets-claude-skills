"""Unit tests for normalizer pipeline."""
import sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from src import normalizer


def test_ligature_decomposition():
    s = "the ﬁnal proof of the eﬃcacy"
    out = normalizer.normalize(s)
    assert "final" in out
    assert "efficacy" in out


def test_soft_hyphen_removal():
    s = "iden­tifica­tion"
    out = normalizer.normalize(s)
    assert "identification" in out
    assert "­" not in out


def test_linebreak_hyphen_collapse_default():
    s2 = "panel-\ndata"
    out2 = normalizer.normalize(s2)
    assert out2 == "paneldata"


def test_linebreak_hyphen_exception_restored():
    excs = {"difference-in-differences", "state-specific"}
    s = "state-\nspecific"
    out = normalizer.normalize(s, hyphen_exceptions=excs)
    assert "state-specific" in out


def test_whitespace_collapse():
    s = "this  has\t\tmany   \n\nspaces"
    out = normalizer.normalize(s)
    assert "  " not in out
    assert "\t" not in out


def test_exact_match():
    haystack = "the quick brown fox jumps"
    assert normalizer.exact_match("brown fox", haystack)
    assert not normalizer.exact_match("brown cat", haystack)


def test_fuzzy_match_within_threshold():
    # 100-char haystack, 100-char quote, 1 edit within threshold (2 per 100).
    haystack = ("Identification of ATT(g,t) under conditional parallel trends "
                "and limited anticipation. Our framework allows for staggered "
                "adoption.")
    # Identical except one typo
    quote = ("Identification of ATT(g,t) under conditional paralel trends "
             "and limited anticipation. Our framework allows for staggered "
             "adoption.")
    matched, dist = normalizer.fuzzy_match(quote, haystack)
    assert matched, f"expected match, got dist={dist}"
    assert dist is not None and dist <= 2


def test_fuzzy_match_outside_threshold():
    haystack = "the quick brown fox jumps over the lazy dog and runs"
    quote = "completely different content not in source whatsoever"
    matched, _ = normalizer.fuzzy_match(quote, haystack)
    assert not matched


def test_verify_quote_exact():
    haystack = normalizer.normalize("ATT(g,t) is the average treatment effect")
    method, norm = normalizer.verify_quote(
        "ATT(g,t) is the average treatment effect",
        haystack, set(),
    )
    assert method == "exact"


def test_verify_quote_fail():
    haystack = normalizer.normalize("Unrelated text about lemons and tea")
    method, norm = normalizer.verify_quote(
        "Synthetic difference-in-differences procedure",
        haystack, set(),
    )
    assert method == "FAIL"
    assert norm is None
