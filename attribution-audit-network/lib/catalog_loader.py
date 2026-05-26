"""
catalog_loader.py

Loads and validates the misattribution catalog and method taxonomy.

Catalog lookup order:
  1. TETS_CATALOG_PATH / TETS_TAXONOMY_PATH env vars (explicit override)
  2. ../shared/ relative to the skill folder (canonical post-install location)
  3. TETS_SPEC_DIR env var (legacy)

This module is read-only over those files. Do NOT regenerate them.
"""

from __future__ import annotations

import os
import yaml
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


SKILL_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SHARED = SKILL_ROOT.parent / "shared"

CATALOG_PATH = Path(os.environ.get(
    "TETS_CATALOG_PATH",
    str(DEFAULT_SHARED / "misattribution-catalog.yaml"),
))
TAXONOMY_PATH = Path(os.environ.get(
    "TETS_TAXONOMY_PATH",
    str(DEFAULT_SHARED / "method-taxonomy-v1.yaml"),
))


@dataclass
class CatalogEntry:
    """One row from misattribution-catalog.yaml."""
    id: str
    method: str
    wrong_credit_authors: str
    wrong_credit_year: Optional[int]
    wrong_credit_doi: Optional[str]
    wrong_credit_arxiv_id: Optional[str]
    wrong_credit_surface_forms: list[str]
    correct_credit_authors: str
    correct_credit_year: Optional[int]
    correct_credit_doi: Optional[str]
    correct_credit_arxiv_id: Optional[str]
    correct_credit_journal_doi: Optional[str]
    correct_credit_section: Optional[str]
    evidence: str
    confidence_level: str  # consensus | strong | provisional
    sources_of_claim: list[str]
    verification_notes: Optional[str] = None
    raw: dict = field(default_factory=dict)

    @property
    def correct_origin_resolution_dois(self) -> list[str]:
        """All DOIs that count as 'correctly cited the origin' for this entry."""
        out: list[str] = []
        for d in (self.correct_credit_doi, self.correct_credit_journal_doi):
            if d:
                out.append(d.lower().strip())
        return out

    @property
    def wrong_credit_resolution_dois(self) -> list[str]:
        """All DOIs that count as 'cited the wrong origin' for this entry."""
        return [self.wrong_credit_doi.lower().strip()] if self.wrong_credit_doi else []


@dataclass
class TaxonomyFamily:
    family_id: str
    canonical_name: str
    synonyms: list[str]
    canonical_origin_doi: Optional[str]
    canonical_origin_authors: Optional[str]
    canonical_origin_year: Optional[int]
    related_families: list[str]
    raw: dict = field(default_factory=dict)


def load_catalog(path: Path = CATALOG_PATH) -> list[CatalogEntry]:
    """Load the authored misattribution catalog. Read-only."""
    with open(path) as f:
        doc = yaml.safe_load(f)

    entries = []
    for raw in doc.get("entries", []):
        wc = raw.get("wrong_credit", {}) or {}
        cc = raw.get("correct_credit", {}) or {}
        entries.append(
            CatalogEntry(
                id=raw["id"],
                method=raw.get("method", ""),
                wrong_credit_authors=wc.get("authors", ""),
                wrong_credit_year=wc.get("year"),
                wrong_credit_doi=wc.get("doi"),
                wrong_credit_arxiv_id=wc.get("arxiv_id"),
                wrong_credit_surface_forms=wc.get("surface_forms", []) or [],
                correct_credit_authors=cc.get("authors", ""),
                correct_credit_year=cc.get("year"),
                correct_credit_doi=cc.get("doi"),
                correct_credit_arxiv_id=cc.get("arxiv_id"),
                correct_credit_journal_doi=cc.get("journal_doi"),
                correct_credit_section=cc.get("section"),
                evidence=raw.get("evidence", ""),
                confidence_level=raw.get("confidence_level", "provisional"),
                sources_of_claim=raw.get("sources_of_claim", []) or [],
                verification_notes=raw.get("verification_notes"),
                raw=raw,
            )
        )
    return entries


def load_taxonomy(path: Path = TAXONOMY_PATH) -> list[TaxonomyFamily]:
    """Load the method taxonomy. Read-only."""
    with open(path) as f:
        doc = yaml.safe_load(f)

    families = []
    for raw in doc.get("families", []):
        co = raw.get("canonical_origin", {}) or {}
        families.append(
            TaxonomyFamily(
                family_id=raw["family_id"],
                canonical_name=raw.get("canonical_name", ""),
                synonyms=raw.get("synonyms", []) or [],
                canonical_origin_doi=co.get("doi"),
                canonical_origin_authors=co.get("authors"),
                canonical_origin_year=co.get("year"),
                related_families=raw.get("related_families", []) or [],
                raw=raw,
            )
        )
    return families


def actionable_entries(entries: list[CatalogEntry]) -> list[CatalogEntry]:
    """
    Return only entries that v0.1 can act on:
      - both wrong_credit and correct_credit have a resolvable DOI
      - DOIs differ (so 'cited wrong but not right' is a meaningful question)

    Catalog entries that are surname-form errors only (no separate paper to flag)
    or that point to a separate-section-of-same-paper question are excluded.
    """
    out = []
    for e in entries:
        wrong = e.wrong_credit_resolution_dois
        right = e.correct_origin_resolution_dois
        if not wrong or not right:
            continue
        if set(wrong) & set(right):
            # Same paper on both sides (e.g., the "default M-bar" trap or
            # the sa-2021-twfe-contamination-prop-number where the issue is
            # about which equation in the same paper). Not a v0.1 graph edge.
            continue
        out.append(e)
    return out


if __name__ == "__main__":
    entries = load_catalog()
    families = load_taxonomy()
    actionable = actionable_entries(entries)
    print(f"Catalog: {len(entries)} entries total")
    print(f"  Actionable (v0.1, distinct wrong/right DOIs): {len(actionable)}")
    print(f"Taxonomy: {len(families)} families")
    print("\nActionable catalog entries:")
    for e in actionable:
        print(f"  - {e.id}")
        print(f"      method: {e.method}")
        print(f"      wrong:  {e.wrong_credit_authors} ({e.wrong_credit_year}) doi={e.wrong_credit_doi}")
        print(f"      right:  {e.correct_credit_authors} ({e.correct_credit_year}) doi={e.correct_credit_doi or e.correct_credit_journal_doi}")
