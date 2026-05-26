"""
graph_builder.py

Assemble the v0.1 attribution-audit graph in d3/sigma-friendly JSON.

Output schema:

  {
    "version": "0.1",
    "build_date": "ISO8601",
    "build_source": "openalex-only",
    "nodes": [
      {
        "id": "Wxxx",
        "doi": "10.xxxx/yyyy",
        "title": "...",
        "year": 2021,
        "authors": ["..."],
        "journal": "...",
        "cited_by_count": 1234,
        "node_type": "correct_origin" | "wrong_credit" | "citing"
      }
    ],
    "edges": [
      {
        "source": "Wciter",
        "target": "Wcited",
        "edge_type": "uses" | "mis-cites" | "develops",
        "weight": 1.0,
        "catalog_entry_id": "...",      // only for mis-cites
        "method": "...",                  // only for mis-cites
        "confidence": "candidate-low",   // only for mis-cites
      }
    ],
    "chains": [
      {
        "chain_id": "<catalog_entry_id>",
        "method": "...",
        "correct_origin": {...},
        "wrong_credit": {...},
        "evidence_count": N,
        "confidence_level": "consensus | strong | provisional",
        "rank_score": float
      }
    ],
    "build_meta": {
      "catalog_entries_processed": N,
      "actionable_entries": N,
      "smoke_test_subset": [id, id, id] | null
    }
  }
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from lib.catalog_loader import CatalogEntry  # type: ignore
from lib.misattribution_detector import MisattributionEdge, edge_to_dict  # type: ignore


def _node_record(paper: dict, node_type: str) -> dict:
    return {
        "id": paper["openalex_id"],
        "doi": paper.get("doi"),
        "title": paper.get("title", ""),
        "year": paper.get("year"),
        "authors": paper.get("authors", []),
        "journal": paper.get("journal"),
        "cited_by_count": paper.get("cited_by_count", 0),
        "node_type": node_type,
    }


def _rank_score(evidence_count: int, severity_weight: float = 1.0) -> float:
    """
    rank_score per CAPABILITY-07 spec §"Misattribution-chain ranking score":
      rank_score = log(1 + N_observed_misattribution_papers) * severity_weight
      severity_weight = 3.0 if claims_to_extend_misattributes; 1.0 if uses-only

    v0.1 has no claims_to_extend detector, so severity_weight is always 1.0.
    """
    return math.log(1 + evidence_count) * severity_weight


def build_graph(
    pairs: list[dict],
    edges_by_entry: dict[str, list[MisattributionEdge]],
    catalog_by_id: dict[str, CatalogEntry],
    subset_ids: Optional[list[str]] = None,
) -> dict:
    """
    Assemble nodes + edges + chains.

    Args:
        pairs: per-entry pull results from expand_catalog_entry_pair
        edges_by_entry: catalog_entry_id -> list[MisattributionEdge]
        catalog_by_id: catalog_entry_id -> CatalogEntry
        subset_ids: if set, only these entry IDs were processed
    """
    nodes_by_id: dict[str, dict] = {}
    edges: list[dict] = []
    chains: list[dict] = []

    for pair in pairs:
        entry = catalog_by_id[pair["catalog_entry_id"]]
        correct = pair["correct"]
        wrong = pair["wrong"]

        if correct:
            n = _node_record(correct, "correct_origin")
            # If a node is both correct_origin (for entry A) and wrong_credit
            # (for entry B), keep the most informative type. correct_origin wins.
            existing = nodes_by_id.get(n["id"])
            if not existing or existing["node_type"] != "correct_origin":
                nodes_by_id[n["id"]] = n

        if wrong:
            n = _node_record(wrong, "wrong_credit")
            existing = nodes_by_id.get(n["id"])
            if not existing:
                nodes_by_id[n["id"]] = n

        # Add an explicit 'develops' edge from the canonical-origin node
        # to a conceptual method node? Spec says develops is paper-to-method-family;
        # we represent it as a paper attribute via node_type instead, to keep
        # the v0.1 graph paper-only.

        # Edges
        for edge in edges_by_entry.get(pair["catalog_entry_id"], []):
            # Add the citing paper as a node (if not already)
            if edge.citing_paper_openalex_id not in nodes_by_id:
                nodes_by_id[edge.citing_paper_openalex_id] = {
                    "id": edge.citing_paper_openalex_id,
                    "doi": edge.citing_paper_doi,
                    "title": edge.citing_paper_title,
                    "year": edge.citing_paper_year,
                    "authors": edge.citing_paper_authors,
                    "journal": None,
                    "cited_by_count": 0,
                    "node_type": "citing",
                }

            edges.append({
                "source": edge.citing_paper_openalex_id,
                "target": edge.cited_wrong_openalex_id,
                "edge_type": "mis-cites",
                "weight": 0.5,  # per spec edge-weight table
                "catalog_entry_id": edge.catalog_entry_id,
                "method": edge.method,
                "confidence": edge.confidence,
                "evidence": edge.evidence,
            })

        # Chain
        entry_edges = edges_by_entry.get(pair["catalog_entry_id"], [])
        ev_count = len(entry_edges)
        chains.append({
            "chain_id": entry.id,
            "method": entry.method,
            "correct_origin": {
                "openalex_id": correct["openalex_id"] if correct else None,
                "doi": (correct or {}).get("doi"),
                "authors": entry.correct_credit_authors,
                "year": entry.correct_credit_year,
                "title": (correct or {}).get("title", ""),
            },
            "wrong_credit": {
                "openalex_id": wrong["openalex_id"] if wrong else None,
                "doi": (wrong or {}).get("doi"),
                "authors": entry.wrong_credit_authors,
                "year": entry.wrong_credit_year,
                "title": (wrong or {}).get("title", ""),
            },
            "evidence_count": ev_count,
            "confidence_level": entry.confidence_level,
            "rank_score": _rank_score(ev_count),
            "catalog_evidence": entry.evidence,
            "catalog_sources_of_claim": entry.sources_of_claim,
        })

    chains.sort(key=lambda c: c["rank_score"], reverse=True)

    return {
        "version": "0.1",
        "build_date": datetime.now(timezone.utc).isoformat(),
        "build_source": "openalex-only",
        "spec_reference": "02_content-strategy/CAPABILITY-07-attribution-audit-network.md",
        "nodes": list(nodes_by_id.values()),
        "edges": edges,
        "chains": chains,
        "build_meta": {
            "catalog_entries_processed": len(pairs),
            "smoke_test_subset": subset_ids,
            "limitations_v0_1": [
                "OpenAlex-only citation edges (Semantic Scholar key 403; v0.2 unlock).",
                "Misattribution edges are CANDIDATES based on bibliography membership; "
                "methods-section LLM verification (CAP-05) required for confirmed status.",
                "No version-merge across NBER WP + arXiv + journal DOI yet; each identifier "
                "is a separate node. v0.2 will collapse on title+author+year hash.",
                "No 'claims_to_extend_misattributes' detector. severity_weight=1.0 for all chains.",
                "No SSRN nodes (documented capability limitation per spec).",
            ],
        },
    }


def write_graph(graph: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(graph, f, indent=2)
