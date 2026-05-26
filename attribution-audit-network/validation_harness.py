#!/usr/bin/env python3
"""
validation_harness.py - CLI scaffold for the 50-paper hand-coded validation

Per CAPABILITY-07 §"Misattribution edge inference" and
attribution-validation-protocol.md, the v0.1 build MUST be audited by
hand-coding 50 papers stratified by method family:
  - top 5 method families, 10 papers each
  - random within each family stratum
  - hand-code: estimators used, bibliography membership, extension claims
  - compare to LLM/detector output
  - acceptance: precision >= 0.85, recall >= 0.60

v0.1 ships the engine. v0.1.x ships this validation. THIS SCRIPT does NOT
run the validation; it scaffolds the inputs (stratified sample + coding
template) so Victoria can hand-code, then computes precision/recall once
her coded CSV is back.

Usage:
  python3 validation_harness.py sample --output validation/sample.csv
  python3 validation_harness.py score --coded validation/sample-coded.csv \
      --graph output/network-graph.json --report validation/report.md
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections import defaultdict
from pathlib import Path


def cmd_sample(args):
    """Generate a stratified sample CSV for hand-coding."""
    with open(args.graph) as f:
        graph = json.load(f)

    edges = graph.get("edges", [])
    if not edges:
        print("No edges in graph. Run orchestrator.py first.", file=sys.stderr)
        return 1

    # Stratify by catalog_entry_id (proxy for method family)
    by_entry: dict[str, list[dict]] = defaultdict(list)
    for e in edges:
        eid = e.get("catalog_entry_id")
        if eid:
            by_entry[eid].append(e)

    random.seed(args.seed)
    sample = []
    for entry_id, entry_edges in by_entry.items():
        n = min(args.per_stratum, len(entry_edges))
        sample.extend(random.sample(entry_edges, n))

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "catalog_entry_id",
            "citing_openalex_id",
            "citing_doi",
            "citing_title",
            "method",
            "detector_says_miscites",
            "HAND_uses_method",          # to be filled by hand
            "HAND_cites_wrong_for_method",  # to be filled
            "HAND_cites_correct_origin",  # to be filled
            "HAND_extension_claim",       # to be filled
            "HAND_notes",
        ])
        for e in sample:
            w.writerow([
                e["catalog_entry_id"],
                e["source"],
                "",  # to be hydrated from graph nodes if needed
                "",
                e["method"],
                "candidate-mis-cites",
                "", "", "", "", "",
            ])

    print(f"Wrote stratified sample with {len(sample)} rows: {out}")
    print(f"Strata covered: {len(by_entry)}")
    return 0


def cmd_score(args):
    """Compute precision/recall against the hand-coded CSV."""
    rows = []
    with open(args.coded) as f:
        r = csv.DictReader(f)
        for row in r:
            rows.append(row)

    if not rows:
        print("Empty coded CSV.", file=sys.stderr)
        return 1

    # Detector positive = candidate-mis-cites
    # Hand truth positive = HAND_uses_method AND HAND_cites_wrong AND NOT HAND_cites_correct
    tp = fp = fn = tn = 0
    for row in rows:
        det_pos = row.get("detector_says_miscites", "") == "candidate-mis-cites"
        truth_pos = (
            row.get("HAND_uses_method", "").lower().startswith("y")
            and row.get("HAND_cites_wrong_for_method", "").lower().startswith("y")
            and not row.get("HAND_cites_correct_origin", "").lower().startswith("y")
        )
        if det_pos and truth_pos:
            tp += 1
        elif det_pos and not truth_pos:
            fp += 1
        elif not det_pos and truth_pos:
            fn += 1
        else:
            tn += 1

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0

    out = Path(args.report)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        f.write("# attribution-audit-network v0.1 validation report\n\n")
        f.write(f"Source: {args.coded}\n\n")
        f.write(f"|  | precision | recall |\n|---|---|---|\n")
        f.write(f"| value | {precision:.3f} | {recall:.3f} |\n")
        f.write(f"| spec target | >= 0.85 | >= 0.60 |\n\n")
        f.write(f"## Contingency\n")
        f.write(f"- TP: {tp}\n- FP: {fp}\n- FN: {fn}\n- TN: {tn}\n")

    print(f"Wrote {out}")
    print(f"Precision: {precision:.3f}  Recall: {recall:.3f}")
    if precision < 0.85:
        print("WARN: precision below spec target (0.85)", file=sys.stderr)
    if recall < 0.60:
        print("WARN: recall below spec target (0.60)", file=sys.stderr)
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    ap_sample = sub.add_parser("sample", help="Generate stratified hand-coding sample")
    ap_sample.add_argument("--graph", default="output/network-graph.json")
    ap_sample.add_argument("--per-stratum", type=int, default=10)
    ap_sample.add_argument("--seed", type=int, default=20260525)
    ap_sample.add_argument("--output", default="validation/sample.csv")
    ap_sample.set_defaults(func=cmd_sample)

    ap_score = sub.add_parser("score", help="Compute precision/recall vs hand-coded CSV")
    ap_score.add_argument("--coded", required=True)
    ap_score.add_argument("--graph", default="output/network-graph.json")
    ap_score.add_argument("--report", default="validation/report.md")
    ap_score.set_defaults(func=cmd_score)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
