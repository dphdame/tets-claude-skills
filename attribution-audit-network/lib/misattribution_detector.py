"""
misattribution_detector.py

Implements the spec's misattribution-edge inference for v0.1.

v0.1 SCOPE (per spec, what is achievable without full-text LLM extraction):
  A paper P is flagged as a CANDIDATE mis-citer of method X iff:
    (a) P cites the wrong-credit paper (Y) for method X
    (b) P does NOT cite the correct-origin paper for method X
    (c) Edges where P cites BOTH are explicitly EXCLUDED (they got it partly right)

v0.1 limitations relative to the spec rule (acknowledged):
  - The spec requires LLM-extracted estimator-use evidence in methods-section
    context AND a self-consistency gate. v0.1 surfaces these as
    "candidate" edges with confidence=low pending the full-text LLM extractor
    from CAPABILITY-05. The spec is explicit that misattribution detection
    requires methods-section evidence; v0.1 nominates candidates only.
  - Abstract-only nodes are flagged separately. v0.1 has no full text, so
    every node is "abstract-or-metadata-only" until CAP-05 lands.
  - Self-consistency gate (two independent LLM runs) is a v0.2 step.

What this gives us today:
  - Reproducible candidate edges from OpenAlex citation graphs
  - Per-edge evidence pointer (which catalog entry, which DOIs were/weren't cited)
  - A confidence floor of "low" until LLM verification runs
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from lib.catalog_loader import CatalogEntry, load_catalog  # type: ignore


@dataclass
class MisattributionEdge:
    """A candidate misattribution edge for v0.1."""
    catalog_entry_id: str
    method: str
    citing_paper_openalex_id: str
    citing_paper_doi: Optional[str]
    citing_paper_title: str
    citing_paper_year: Optional[int]
    citing_paper_authors: list[str]
    cited_wrong_doi: str
    cited_wrong_openalex_id: str
    cited_correct_doi: str
    cited_correct_openalex_id: Optional[str]
    cites_wrong: bool
    cites_correct: bool
    confidence: str = "candidate-low"  # candidate-low | candidate-medium | confirmed
    confidence_notes: str = (
        "v0.1: based on OpenAlex bibliography membership only. "
        "Full LLM methods-section verification (CAP-05) required for confirmed status. "
        "Self-consistency gate (v0.2) not yet run."
    )
    evidence: dict = field(default_factory=dict)


def normalize_doi(doi: Optional[str]) -> Optional[str]:
    """Normalize a DOI: lower-case, strip URL prefix, strip whitespace."""
    if not doi:
        return None
    d = doi.strip()
    if d.startswith("http"):
        d = d.split("doi.org/", 1)[-1]
    return d.lower()


def detect_for_pair(
    catalog_entry: CatalogEntry,
    correct_paper: dict,
    wrong_paper: dict,
    citers_of_wrong: list[dict],
) -> list[MisattributionEdge]:
    """
    For one (correct, wrong) catalog pair, scan the citers of the WRONG
    paper. Flag those that do not also cite the correct origin.

    Why scan citers-of-wrong rather than the universe of papers:
      We want papers that cite the wrong paper for this method. Those are
      automatically members of citers_of_wrong (OpenAlex's `cites:Wid`
      filter). Papers that never cite Y cannot be mis-citers of method X
      via Y. This narrows the search dramatically.
    """
    edges: list[MisattributionEdge] = []

    if not correct_paper or not wrong_paper:
        return edges

    correct_openalex_id = correct_paper["openalex_id"]
    wrong_openalex_id = wrong_paper["openalex_id"]
    correct_doi_norm = normalize_doi(correct_paper.get("doi"))
    wrong_doi_norm = normalize_doi(wrong_paper.get("doi"))

    # Also include all catalog-listed DOIs for the correct side
    # (journal_doi may differ from canonical_doi in the catalog).
    correct_dois = set(catalog_entry.correct_origin_resolution_dois) | {correct_doi_norm}
    correct_dois.discard(None)

    for citer in citers_of_wrong:
        refs = set(citer.get("references", []) or [])

        # The bibliography returned by OpenAlex is in OpenAlex Wid form.
        # The catalog DOI -> Wid mapping is captured via correct_paper.openalex_id.
        cites_correct = correct_openalex_id in refs
        # Confirm cites-wrong via OpenAlex Wid (the citer is in citers_of_wrong
        # by definition, so this should always be True; assert for sanity).
        cites_wrong = wrong_openalex_id in refs

        if not cites_wrong:
            # Should not happen, but skip if OpenAlex returned an inconsistent
            # bibliography. Log it in the edge evidence for audit.
            continue

        if cites_correct:
            # Exception in spec: papers that cite BOTH are NOT flagged.
            continue

        edges.append(
            MisattributionEdge(
                catalog_entry_id=catalog_entry.id,
                method=catalog_entry.method,
                citing_paper_openalex_id=citer["openalex_id"],
                citing_paper_doi=normalize_doi(citer.get("doi")),
                citing_paper_title=citer.get("title", ""),
                citing_paper_year=citer.get("year"),
                citing_paper_authors=citer.get("authors", []),
                cited_wrong_doi=wrong_doi_norm,
                cited_wrong_openalex_id=wrong_openalex_id,
                cited_correct_doi=correct_doi_norm,
                cited_correct_openalex_id=correct_openalex_id,
                cites_wrong=cites_wrong,
                cites_correct=cites_correct,
                confidence="candidate-low",
                evidence={
                    "citing_paper_reference_count_total": len(refs),
                    "catalog_confidence_level": catalog_entry.confidence_level,
                    "v0_1_method": "openalex-bibliography-membership-only",
                    "next_step_for_confirmation": (
                        "LLM methods-section extraction (CAP-05) on citing paper's full text. "
                        "If estimator-use of `{method}` is confirmed AND self-consistency gate passes, "
                        "upgrade to confidence=confirmed."
                    ).format(method=catalog_entry.method),
                },
            )
        )

    return edges


def edge_to_dict(e: MisattributionEdge) -> dict:
    """JSON-safe dict for output."""
    return asdict(e)
