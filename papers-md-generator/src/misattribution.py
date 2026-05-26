"""Misattribution-trap engine.

Catalog: misattribution-catalog.yaml (15 entries seeded 2026-05-25).

Algorithm: For each extracted estimator's `estimator_canonical_citation`,
match against any `wrong_credit` entry in the catalog. On match: flag
MISATTRIBUTION_CANDIDATE with the catalog's `correct_credit` reference.

Self-consistency gate (REQUIRED) is enforced upstream in extractor.py;
this module only does rule-based matching. Flags are queued to
misattribution-flags-pending-review.md and do NOT auto-promote into
the block's do_NOT_attribute list.
"""
from __future__ import annotations
import re
from pathlib import Path

import yaml


def load_catalog(yaml_path):
    """Load misattribution catalog YAML. Returns list of entry dicts."""
    p = Path(yaml_path)
    if not p.exists():
        return []
    data = yaml.safe_load(p.read_text()) or {}
    return data.get("entries", [])


def _normalize_citation_string(s):
    """Lowercase, replace separators with spaces, collapse whitespace.

    Treats hyphen, en-dash, comma, "&", and "and" as equivalent token
    separators. This way 'Sun-Abraham 2021' and 'Sun & Abraham, 2021'
    and 'Sun and Abraham (2021)' all normalize to a comparable token list.
    """
    if not s:
        return ""
    out = s.lower()
    out = re.sub(r"[‘’“”]", "'", out)  # smart quotes
    # Replace common separators with spaces (preserve word boundaries)
    out = out.replace("&", " and ")
    out = re.sub(r"[\-–—,/()]", " ", out)
    out = re.sub(r"\s+", " ", out)
    return out.strip()


def _tokens(s):
    return [t for t in s.split() if t]


def _tokens_appear_in_order(needle_tokens, hay_tokens):
    """True if every needle token appears in hay_tokens in order (gaps ok).

    Skips the connective 'and' in the needle, since whether 'X and Y' or
    'X Y' is reported varies by citation form.
    """
    skipset = {"and", "the", "of"}
    needle = [t for t in needle_tokens if t not in skipset]
    if not needle:
        return False
    i = 0
    for h in hay_tokens:
        if h == needle[i]:
            i += 1
            if i >= len(needle):
                return True
    return False


def _match_authors_year(citation, wrong):
    """True if citation contains all author tokens (in order) AND the year.

    Authors field may use 'X & Y', 'X and Y', 'X, Y & Z', or single name.
    We tokenize both sides and check ordered subsequence.
    """
    if not wrong.get("authors"):
        return False
    citation_n = _normalize_citation_string(citation)
    cite_tokens = _tokens(citation_n)
    year = str(wrong.get("year", "")) if wrong.get("year") else ""
    authors_n = _normalize_citation_string(wrong.get("authors", ""))
    author_tokens = _tokens(authors_n)
    if not _tokens_appear_in_order(author_tokens, cite_tokens):
        # Try DOI fallback
        wrong_doi = (wrong.get("doi") or "").lower()
        if wrong_doi and wrong_doi in citation.lower():
            if not year:
                return True
            return year in citation_n
        return False
    if not year:
        return True
    return year in citation_n


def _match_surface_form(citation, wrong):
    """True if any surface_form's tokens appear in order in citation."""
    citation_n = _normalize_citation_string(citation)
    cite_tokens = _tokens(citation_n)
    for sf in wrong.get("surface_forms", []) or []:
        sf_n = _normalize_citation_string(sf)
        sf_tokens = _tokens(sf_n)
        if not sf_tokens:
            continue
        # Require either exact substring OR ordered-token-subsequence match
        if sf_n in citation_n:
            return True
        if _tokens_appear_in_order(sf_tokens, cite_tokens):
            return True
    return False


def find_misattribution(estimator_citation, catalog):
    """Match a single estimator_canonical_citation against the catalog.

    Returns list of matching catalog entries (zero or more).
    """
    if not estimator_citation:
        return []
    matches = []
    for entry in catalog:
        wrong = entry.get("wrong_credit", {})
        if _match_authors_year(estimator_citation, wrong) or \
           _match_surface_form(estimator_citation, wrong):
            matches.append(entry)
    return matches


def format_do_not_attribute(entry):
    """Produce a single string for the do_NOT_attribute list."""
    wrong = entry.get("wrong_credit", {})
    correct = entry.get("correct_credit", {})
    method = entry.get("method", "")
    wrong_str = f"{wrong.get('authors', '?')} {wrong.get('year', '')}"
    correct_str = f"{correct.get('authors', '?')} {correct.get('year', '')}"
    return (f"{method.capitalize()} is NOT {wrong_str.strip()}; "
            f"it is {correct_str.strip()} ({correct.get('section', '')}).")


def format_pending_review_entry(paper_doi, estimator_name,
                                estimator_citation, catalog_entries,
                                self_consistency_passed):
    """Render a markdown entry for misattribution-flags-pending-review.md."""
    lines = []
    lines.append(f"## Paper DOI: {paper_doi}")
    lines.append(f"- **Estimator:** {estimator_name}")
    lines.append(f"- **Extracted citation:** {estimator_citation}")
    lines.append(
        f"- **Self-consistency gate passed: {self_consistency_passed}**"
    )
    for e in catalog_entries:
        wrong = e.get("wrong_credit", {})
        correct = e.get("correct_credit", {})
        lines.append(f"  - **Catalog entry id:** {e.get('id', '?')}")
        lines.append(
            f"    - **Wrong credit:** {wrong.get('authors', '?')} "
            f"{wrong.get('year', '')} (DOI: {wrong.get('doi') or 'n/a'})"
        )
        lines.append(
            f"    - **Correct credit:** {correct.get('authors', '?')} "
            f"{correct.get('year', '')} (DOI: {correct.get('doi') or 'n/a'})"
        )
        lines.append(f"    - **Method:** {e.get('method', '?')}")
        lines.append(f"    - **Evidence:** {e.get('evidence', '?')}")
        lines.append(f"    - **Confidence:** {e.get('confidence_level', '?')}")
    lines.append("")
    return "\n".join(lines)
