"""
run_pipeline.py — CAP-06 v0.1 orchestrator

Modes:
  smoke      — N=10 packages from data/seeds/smoke-10.txt; Path A only;
               minimal Wayback fetch + static analysis on README text.
  analytic   — N=500 stratified sample (Path A by default; Path B if
               openicpsr_authenticated.py is present).
  validation — N=30 hand-coded subset re-run; emits comparison CSV.

Outputs land under:
    <gdrive>/.../replication-landscape/data/packages/{package_id}.json
    <gdrive>/.../replication-landscape/work/logs/run-{mode}-{ts}.log

Eventual public dataset push target: github.com/dphdame/replication-landscape-data
(NOT pushed in v0.1; remains in GDrive 02_content-strategy/).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

# Allow running as `python3 src/run_pipeline.py` from skill root or as module.
HERE = Path(__file__).resolve().parent
SKILL_ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from static_analyzer import analyze_package  # noqa: E402
from das_classifier import classify_das       # noqa: E402
from wayback_harvester import harvest_openicpsr_via_wayback  # noqa: E402

# Default paths (writable GDrive layout per spec)
# LANDSCAPE_BASE is where the skill reads seed files and writes per-package JSON outputs.
# Defaults to ./replication-landscape under the current working directory; override
# by setting TETS_LANDSCAPE_BASE in the environment.
LANDSCAPE_BASE = Path(
    os.environ.get("TETS_LANDSCAPE_BASE", str(Path.cwd() / "replication-landscape"))
)
DEFAULT_SEEDS = {
    "smoke": LANDSCAPE_BASE / "data" / "seeds" / "smoke-10.txt",
    "analytic": LANDSCAPE_BASE / "data" / "seeds" / "analytic-500.txt",
    "validation": LANDSCAPE_BASE / "data" / "seeds" / "validation-30.txt",
}
PKG_DIR = LANDSCAPE_BASE / "data" / "packages"
LOG_DIR = LANDSCAPE_BASE / "work" / "logs"
CACHE_DIR = LANDSCAPE_BASE / "work" / "wayback_cache"


def _ensure_dirs() -> None:
    for d in (PKG_DIR, LOG_DIR, CACHE_DIR):
        d.mkdir(parents=True, exist_ok=True)


def _has_path_b() -> bool:
    return (HERE / "openicpsr_authenticated.py").exists()


def _load_seeds(path: Path) -> list[str]:
    if not path.exists():
        return []
    lines = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        lines.append(ln)
    return lines


def _parse_seed_line(line: str) -> dict[str, str]:
    """
    Seed line format (tab- or pipe-separated; flexible):
        <project_id>\t<source_repository>\t<journal>\t<year>\t<doi>
    Minimum: just <project_id>.
    """
    parts = [p.strip() for p in (line.split("\t") if "\t" in line else line.split("|"))]
    rec = {
        "project_id": parts[0],
        "source_repository": parts[1] if len(parts) > 1 else "openicpsr",
        "associated_journal": parts[2] if len(parts) > 2 else None,
        "deposit_year": parts[3] if len(parts) > 3 else None,
        "doi": parts[4] if len(parts) > 4 else None,
    }
    return rec


def _process_one(seed: dict[str, str], cache_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Returns (record, metric_summary). metric_summary is a small dict used
    by the smoke aggregator (e.g., which metrics populated).
    """
    pid = seed["project_id"]
    package_id = f"openicpsr/{pid}"
    harvest = harvest_openicpsr_via_wayback(pid, cache_dir=str(cache_dir))
    readme_text = harvest.get("readme_text")
    deposit_md = harvest.get("deposit_metadata") or {}

    package_metadata = {
        "deposit_year": int(seed["deposit_year"]) if (seed.get("deposit_year") or "").isdigit() else None,
        "associated_journal": seed.get("associated_journal") or deposit_md.get("doi"),
        "doi": seed.get("doi") or deposit_md.get("doi"),
        "has_supplement": False,
        "size_mb": None,
        "file_count": len(harvest.get("file_listing") or []) or None,
    }

    record = analyze_package(
        package_id=package_id,
        source_repository=seed.get("source_repository", "openicpsr"),
        readme_text=readme_text,
        readme_filename="(wayback landing-page description)" if readme_text else None,
        package_metadata=package_metadata,
    )

    # DAS classification (no-ops if no API key)
    das_out = classify_das(readme_text or "", package_id=package_id)
    record["readme"]["das_class"] = das_out.get("das_class")
    record["readme"]["das_class_confidence"] = das_out.get("confidence")
    record["readme"]["das_evidence_quote"] = (das_out.get("evidence_quote") or "")[:280] or None
    record["readme"]["das_prompt_version"] = das_out.get("prompt_version")

    if not readme_text:
        record["warnings"].append("wayback_returned_no_readme_text")
    if harvest.get("notes"):
        for n in harvest["notes"]:
            record["warnings"].append(f"wayback:{n}")

    # Tiny per-package metric summary for smoke aggregator
    summary = {
        "package_id": package_id,
        "readme_present": bool(readme_text),
        "readme_word_count": record["readme"]["word_count"],
        "has_software_versions": record["readme"]["has_software_versions"],
        "has_runtime_estimate": record["readme"]["has_runtime_estimate"],
        "has_reproducibility_language": record["readme"]["has_reproducibility_language"],
        "das_class": record["readme"]["das_class"],
        "das_state": (das_out.get("_meta") or {}).get("classifier_state"),
        "file_listing_count": len(harvest.get("file_listing") or []),
        "snapshot_date": harvest.get("snapshot_date"),
        "errors": [e["code"] for e in record.get("errors", [])],
        "wayback_failed": "wayback:wayback_fetch_failed" in record["warnings"]
                          or any(w.startswith("wayback:no_snapshots") for w in record["warnings"]),
    }
    return record, summary


def _write_record(record: dict[str, Any]) -> Path:
    pid_safe = record["package_id"].replace("/", "__")
    out = PKG_DIR / f"{pid_safe}.json"
    out.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return out


def _log(log_file: Path, line: str) -> None:
    with log_file.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def smoke_aggregate(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(summaries)
    if n == 0:
        return {"n_packages": 0}
    def cnt(pred):
        return sum(1 for s in summaries if pred(s))
    metrics_populated = {
        "readme_present":               cnt(lambda s: s["readme_present"]),
        "readme_word_count_nonzero":    cnt(lambda s: (s["readme_word_count"] or 0) > 0),
        "has_software_versions":        cnt(lambda s: s["has_software_versions"]),
        "has_runtime_estimate":         cnt(lambda s: s["has_runtime_estimate"]),
        "has_reproducibility_language": cnt(lambda s: s["has_reproducibility_language"]),
        "das_class_assigned":           cnt(lambda s: s["das_class"] is not None),
        "file_listing_nonempty":        cnt(lambda s: s["file_listing_count"] > 0),
        "wayback_snapshot_found":       cnt(lambda s: s["snapshot_date"] is not None),
    }
    failures = cnt(lambda s: s["wayback_failed"]) + cnt(lambda s: bool(s["errors"]))
    classifier_state_breakdown: dict[str, int] = {}
    for s in summaries:
        st = s["das_state"] or "unknown"
        classifier_state_breakdown[st] = classifier_state_breakdown.get(st, 0) + 1
    return {
        "n_packages": n,
        "metrics_populated": metrics_populated,
        "metrics_populated_pct": {k: round(v / n * 100, 1) for k, v in metrics_populated.items()},
        "failures": failures,
        "classifier_state_breakdown": classifier_state_breakdown,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["smoke", "analytic", "validation"], default="smoke")
    ap.add_argument("--seed-file", default=None)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    _ensure_dirs()
    seed_path = Path(args.seed_file) if args.seed_file else DEFAULT_SEEDS[args.mode]
    seeds_raw = _load_seeds(seed_path)
    if args.limit:
        seeds_raw = seeds_raw[: args.limit]

    if not seeds_raw:
        print(f"ERROR: no seeds found at {seed_path}", file=sys.stderr)
        return 2

    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    log_file = LOG_DIR / f"run-{args.mode}-{ts}.log"
    _log(log_file, f"START mode={args.mode} n={len(seeds_raw)} seed={seed_path}")
    _log(log_file, f"path_b_active={_has_path_b()} api_key_set={bool(os.environ.get('ANTHROPIC_API_KEY'))}")

    summaries: list[dict[str, Any]] = []
    failures: list[tuple[str, str]] = []
    t0 = time.time()
    for line in seeds_raw:
        seed = _parse_seed_line(line)
        pid = seed["project_id"]
        try:
            record, summary = _process_one(seed, CACHE_DIR)
            out_path = _write_record(record)
            summaries.append(summary)
            _log(log_file, f"OK {pid} -> {out_path.name} "
                           f"readme_words={summary['readme_word_count']} "
                           f"das={summary['das_class']} "
                           f"files={summary['file_listing_count']}")
        except Exception as e:
            tb = traceback.format_exc(limit=4)
            failures.append((pid, f"{e}\n{tb}"))
            _log(log_file, f"FAIL {pid} {e.__class__.__name__}: {e}")

    elapsed = time.time() - t0
    aggregate = smoke_aggregate(summaries)
    aggregate["elapsed_seconds"] = round(elapsed, 1)
    aggregate["uncaught_failures"] = len(failures)
    aggregate["mode"] = args.mode

    print("\n=== run summary ===")
    print(json.dumps(aggregate, indent=2))
    if failures:
        print(f"\n{len(failures)} uncaught failures (first 3 reasons):")
        for pid, msg in failures[:3]:
            print(f"  {pid}: {msg.splitlines()[0]}")

    _log(log_file, "END " + json.dumps(aggregate))
    print(f"\nlog: {log_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
