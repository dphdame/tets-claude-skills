"""
network_expander.py

Thin wrapper around ~/.claude/scripts/research/phase_02_literature/citation_network_expansion.py
specialized for the attribution-audit-network use case.

Per CAPABILITY-07 spec, the data layer is 60-80% built via citation_network_expansion.py.
This module:
  1. Loads the existing CitationNetworkExpander class
  2. Exposes a convenience function for "expand a (correct, wrong) DOI pair"
  3. Caches results to disk so smoke tests do not re-hit OpenAlex
  4. v0.1: OpenAlex only. Semantic Scholar is currently 403'ing per session log.

v0.2 extension point: the merge_with_semantic_scholar() function is a stub
that activates once Victoria refreshes the SS key.
"""

from __future__ import annotations

import json
import os
import sys
import hashlib
from dataclasses import asdict
from pathlib import Path
from typing import Optional

# Add the existing script directory to sys.path so we can import the existing class.
EXISTING_SCRIPT_DIR = Path.home() / ".claude" / "scripts" / "research" / "phase_02_literature"
if str(EXISTING_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(EXISTING_SCRIPT_DIR))

from citation_network_expansion import CitationNetworkExpander, Paper  # noqa: E402


CACHE_DIR = Path.home() / ".cache" / "tets-attribution-network"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _cache_key(doi: str, kind: str) -> Path:
    """Cache key: hash of DOI + query kind."""
    h = hashlib.sha1(f"{doi}|{kind}".encode()).hexdigest()[:16]
    return CACHE_DIR / f"{kind}_{h}.json"


def _paper_to_dict(p: Paper) -> dict:
    """Serialize Paper dataclass to JSON-safe dict."""
    return {
        "openalex_id": p.openalex_id,
        "doi": p.doi,
        "title": p.title,
        "year": p.year,
        "authors": p.authors,
        "journal": p.journal,
        "cited_by_count": p.cited_by_count,
        "references": p.references,
    }


def _dict_to_paper(d: dict) -> Paper:
    return Paper(
        openalex_id=d["openalex_id"],
        doi=d.get("doi"),
        title=d.get("title", "Unknown"),
        year=d.get("year"),
        authors=d.get("authors", []),
        journal=d.get("journal"),
        cited_by_count=d.get("cited_by_count", 0),
        references=d.get("references", []),
    )


class AttributionExpander:
    """
    Wraps CitationNetworkExpander for the attribution-audit use case.

    Key extensions over the base class:
      - per-DOI disk cache so smoke tests are repeatable
      - resolve_doi() that returns full Paper including referenced_works
      - get_citing_papers_with_refs() that hydrates each citer's
        bibliography (needed for misattribution detection)
    """

    def __init__(self, email: Optional[str] = None, use_cache: bool = True):
        self.email = email or os.environ.get("OPENALEX_EMAIL") or "hello@tooearlytosay.com"
        self.expander = CitationNetworkExpander(email=self.email)
        self.use_cache = use_cache

    # ----------------------------- resolve -----------------------------

    def resolve_doi(self, doi: str) -> Optional[Paper]:
        """Resolve a DOI to a Paper (OpenAlex)."""
        cache_path = _cache_key(doi, "resolve")
        if self.use_cache and cache_path.exists():
            with open(cache_path) as f:
                d = json.load(f)
            if d is None:
                return None
            return _dict_to_paper(d)

        paper = self.expander.get_paper_by_doi(doi)
        with open(cache_path, "w") as f:
            json.dump(_paper_to_dict(paper) if paper else None, f)
        return paper

    # ----------------------------- citing -----------------------------

    def get_citing_papers(self, paper: Paper, limit: int = 50) -> list[Paper]:
        """Forward citations, cached on disk."""
        cache_path = _cache_key(f"{paper.openalex_id}|{limit}", "citing")
        if self.use_cache and cache_path.exists():
            with open(cache_path) as f:
                ds = json.load(f)
            return [_dict_to_paper(d) for d in ds]

        citing = self.expander.get_citing_papers(paper, limit=limit)
        with open(cache_path, "w") as f:
            json.dump([_paper_to_dict(p) for p in citing], f)
        return citing

    # ----------------------------- hydrate -----------------------------

    def hydrate_references(self, paper: Paper, max_refs: int = 120) -> Paper:
        """
        Ensure paper.references is populated. The forward-citation endpoint
        returns the citers' referenced_works directly, so usually no extra
        fetch is needed. But if for some reason references is empty AND we
        need them for misattribution detection, this re-fetches the work.

        We do NOT hydrate each reference into a Paper here - just the IDs.
        """
        if paper.references or not paper.openalex_id:
            return paper

        # Re-fetch the work to get referenced_works
        fresh = self.expander.get_paper_by_id(paper.openalex_id)
        if fresh:
            paper.references = fresh.references[:max_refs]
        return paper

    # --------------------- v0.2 extension point ---------------------

    def merge_with_semantic_scholar(self, paper: Paper) -> Paper:
        """
        v0.2 hook. NOT IMPLEMENTED in v0.1 because the Semantic Scholar key
        at ~/.config/tets/secrets.env is currently 403'ing.

        Once the key is refreshed, this will:
          1. Resolve paper to a Semantic Scholar id by DOI
          2. Pull S2 references and citation intents (introduction/method/result)
          3. Merge into Paper.references (deduplicated)
          4. Record intent metadata on edges
        """
        return paper


def expand_catalog_entry_pair(
    correct_doi: str,
    wrong_doi: str,
    max_citing: int = 50,
    use_cache: bool = True,
) -> dict:
    """
    For a single (correct, wrong) DOI pair from the misattribution catalog,
    pull both papers and the forward-citation set of each.

    Returns a dict ready for downstream misattribution detection:
        {
            "correct": Paper-as-dict,
            "wrong": Paper-as-dict,
            "citers_of_correct": [Paper-as-dict, ...],
            "citers_of_wrong":   [Paper-as-dict, ...],
        }
    """
    exp = AttributionExpander(use_cache=use_cache)

    correct = exp.resolve_doi(correct_doi)
    wrong = exp.resolve_doi(wrong_doi)

    out = {
        "correct_doi": correct_doi,
        "wrong_doi": wrong_doi,
        "correct": _paper_to_dict(correct) if correct else None,
        "wrong": _paper_to_dict(wrong) if wrong else None,
        "citers_of_correct": [],
        "citers_of_wrong": [],
    }

    if correct:
        citers_correct = exp.get_citing_papers(correct, limit=max_citing)
        out["citers_of_correct"] = [_paper_to_dict(p) for p in citers_correct]
    if wrong:
        citers_wrong = exp.get_citing_papers(wrong, limit=max_citing)
        out["citers_of_wrong"] = [_paper_to_dict(p) for p in citers_wrong]

    return out
