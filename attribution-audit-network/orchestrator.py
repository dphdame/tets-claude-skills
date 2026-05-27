#!/usr/bin/env python3
"""
orchestrator.py - attribution-audit-network main entrypoint

Per CAPABILITY-07 (v0.1, OpenAlex-only):

  1. Load misattribution-catalog.yaml + method-taxonomy-v1.yaml
  2. For each actionable catalog entry, pull the (correct, wrong) DOI pair
     via the OpenAlex citation graph (wraps the existing
     citation_network_expansion.py script)
  3. Detect candidate mis-cite edges:
       - papers in citers_of_wrong whose bibliographies do NOT include
         the correct-origin OpenAlex Wid
  4. Build the d3/sigma-friendly graph JSON
  5. Write outputs:
       - <output>/network-graph.json  (full graph)
       - <output>/network-chains.json (main-view list per frontend-architecture-plan)
       - public/research/methodology/attribution-network/data/{network-graph,network-chains}.json

Usage:
  python3 orchestrator.py --smoke-test          # Run on 3 catalog entries
  python3 orchestrator.py                       # Run on all actionable entries
  python3 orchestrator.py --entry <id>          # Single entry
  python3 orchestrator.py --max-citing 30       # Cap forward citations per pair
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from datetime import datetime, timezone

# Make `lib` package importable whether we are run as `python orchestrator.py`
# or `python -m orchestrator`.
_SKILL_DIR = Path(__file__).resolve().parent
if str(_SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_DIR))

from lib.catalog_loader import (  # noqa: E402
    load_catalog,
    load_taxonomy,
    actionable_entries,
    CATALOG_PATH,
    TAXONOMY_PATH,
)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
from lib.network_expander import (  # noqa: E402
    expand_catalog_entry_pair,
    AttributionExpander,
)
from lib.misattribution_detector import detect_for_pair, edge_to_dict  # noqa: E402
from lib.graph_builder import build_graph, write_graph  # noqa: E402


SMOKE_TEST_IDS = [
    # 3 entries chosen to stress different shapes:
    "cs-not-yet-treated-mis-credited-to-sa",   # CS/SA - tight pair, both well-cited
    "causal-forests-vs-grf",                    # causal-forests vs GRF
    "flci-mis-credited-to-rr",                  # FLCI to Armstrong-Kolesar
]


def main():
    ap = argparse.ArgumentParser(description="attribution-audit-network v0.1 orchestrator")
    ap.add_argument("--smoke-test", action="store_true",
                    help=f"Run only on a 3-entry subset: {SMOKE_TEST_IDS}")
    ap.add_argument("--entry", action="append", default=None,
                    help="Catalog entry id(s) to process (repeatable). Overrides --smoke-test.")
    ap.add_argument("--max-citing", type=int, default=50,
                    help="Max forward-citation results per OpenAlex query (default 50)")
    ap.add_argument("--no-cache", action="store_true",
                    help="Disable per-DOI cache; force OpenAlex re-fetch")
    ap.add_argument("--output", type=Path,
                    default=Path(__file__).resolve().parent / "output",
                    help="Output directory")
    ap.add_argument("--publish-to-frontend", action="store_true",
                    help="Also copy outputs to public/research/methodology/attribution-network/data/")
    args = ap.parse_args()

    output_dir: Path = args.output
    output_dir.mkdir(parents=True, exist_ok=True)

    # Load catalog + taxonomy
    entries = load_catalog()
    actionable = actionable_entries(entries)
    by_id = {e.id: e for e in entries}

    # Select subset
    if args.entry:
        subset = [by_id[i] for i in args.entry if i in by_id]
        missing = [i for i in args.entry if i not in by_id]
        if missing:
            print(f"WARN: unknown entry ids skipped: {missing}", file=sys.stderr)
    elif args.smoke_test:
        subset = [by_id[i] for i in SMOKE_TEST_IDS if i in by_id]
    else:
        subset = actionable

    subset_actionable = [e for e in subset if e in actionable]
    if len(subset_actionable) != len(subset):
        skipped = [e.id for e in subset if e not in actionable]
        print(f"WARN: catalog entries skipped (not v0.1-actionable, missing distinct DOIs): {skipped}",
              file=sys.stderr)
        subset = subset_actionable

    print(f"=" * 70)
    print(f"attribution-audit-network v0.1 orchestrator")
    print(f"=" * 70)
    print(f"Catalog: {CATALOG_PATH}")
    print(f"Taxonomy: {TAXONOMY_PATH}")
    print(f"Catalog entries total: {len(entries)} | actionable: {len(actionable)}")
    print(f"This run: {len(subset)} entries")
    print(f"Output dir: {output_dir}")
    print()

    # Process each entry
    pairs = []
    edges_by_entry = {}

    for entry in subset:
        print(f"--- {entry.id} ---")
        print(f"  method: {entry.method}")
        wrong_doi = entry.wrong_credit_doi
        correct_doi = entry.correct_credit_doi or entry.correct_credit_journal_doi

        result = expand_catalog_entry_pair(
            correct_doi=correct_doi,
            wrong_doi=wrong_doi,
            max_citing=args.max_citing,
            use_cache=not args.no_cache,
        )
        result["catalog_entry_id"] = entry.id
        pairs.append(result)

        if not result["correct"]:
            print(f"  WARN: correct-origin DOI not resolved on OpenAlex: {correct_doi}")
        if not result["wrong"]:
            print(f"  WARN: wrong-credit DOI not resolved on OpenAlex: {wrong_doi}")

        edges = []
        if result["correct"] and result["wrong"]:
            edges = detect_for_pair(
                catalog_entry=entry,
                correct_paper=result["correct"],
                wrong_paper=result["wrong"],
                citers_of_wrong=result["citers_of_wrong"],
            )
        edges_by_entry[entry.id] = edges

        print(f"  citers of wrong: {len(result['citers_of_wrong'])}")
        print(f"  citers of correct: {len(result['citers_of_correct'])}")
        print(f"  candidate mis-cite edges: {len(edges)}")
        print()

    # Build and write graph
    graph = build_graph(
        pairs=pairs,
        edges_by_entry=edges_by_entry,
        catalog_by_id=by_id,
        subset_ids=[e.id for e in subset],
    )

    network_path = output_dir / "network-graph.json"
    chains_path = output_dir / "network-chains.json"
    write_graph(graph, network_path)
    # Chains-only file for the main view per frontend-architecture-plan §3.3
    chains_doc = {
        "version": graph["version"],
        "build_date": graph["build_date"],
        "catalog_version": "1.0",
        "chains": graph["chains"],
    }
    with open(chains_path, "w") as f:
        json.dump(chains_doc, f, indent=2)

    print(f"Wrote {network_path}")
    print(f"Wrote {chains_path}")

    if args.publish_to_frontend:
        frontend_dir = PROJECT_ROOT / "public" / "research" / "methodology" / "attribution-network" / "data"
        frontend_dir.mkdir(parents=True, exist_ok=True)
        for src in [network_path, chains_path]:
            dst = frontend_dir / src.name
            with open(src) as fin, open(dst, "w") as fout:
                fout.write(fin.read())
            print(f"Published to {dst}")

    # Summary
    print()
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Catalog entries processed: {len(subset)}")
    print(f"Nodes in graph: {len(graph['nodes'])}")
    print(f"Mis-cite candidate edges: {sum(len(v) for v in edges_by_entry.values())}")
    print()
    print("Per-chain:")
    for chain in graph["chains"]:
        print(f"  {chain['chain_id']:50s}  ev={chain['evidence_count']:4d}  "
              f"score={chain['rank_score']:5.2f}  conf={chain['confidence_level']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
