---
name: replication-package-analytics
description: Population-level static analytics for econ replication packages. Crawls openICPSR + AEA replication deposits via Internet Archive Wayback (Cloudflare blocks direct openICPSR access in v0.1), extracts the 12 per-package metrics from CAPABILITY-06 (extension census, folder structure, README presence/word-count, software-version mentions, runtime estimates, master-script detection, seed setting, hard-coded paths, version-pin files, LOC per language, reproducibility language) plus a Claude-Sonnet DAS classifier (4 classes, pinned prompt, zero-shot, strict-JSON validated). Emits one record per package conforming to replication-package-v1.json. Path A (README + landing-page) is default; Path B (authenticated openICPSR download for full source-tree analysis) is a v0.2 stub. Output panel feeds a TETS landscape post + future github.com/dphdame/replication-landscape-data. Triggers include "/replication-package-analytics", "audit replication packages", "replication landscape", "replication package metrics".
---

# replication-package-analytics

Population-level measurement of the econ reproducibility stack across openICPSR and AEA replication deposits. Produces a panel dataset of structural compliance metrics, not a per-package linter and not an AEA-policy grade. Backs a TETS post titled approximately *"What 500 replication packages look like: an empirical audit of the econ reproducibility stack."*

## When to Use

- Building the v0.1 landscape post (N=10 smoke -> N=500 analytic -> N=30 hand-coded validation)
- Adding a new journal slice to the panel
- Re-running with refreshed Wayback snapshots
- Producing the per-package JSON records that the TETS dashboard reads

Do NOT use to:

- Grade individual packages against AEA policy (no normative scoring; CAP-06 is descriptive)
- Execute or sandbox replication code (out of scope for v1)
- Audit citations or methods inside the paper itself (use papers-md-generator + hallucination-audit-pillar)

## Inputs

| Name | Type | Default | Notes |
|---|---|---|---|
| mode | enum | smoke | smoke (N=10) / analytic (N=500) / validation (N=30) |
| seed_file | path | data/seeds/{mode}-N.txt | Tab-separated: project_id, source_repository, journal, year, doi |
| ANTHROPIC_API_KEY | env | unset | DAS classifier no-ops cleanly when unset |
| OPENICPSR_EMAIL / OPENICPSR_PASSWORD | env | unset | Required only when Path B module is active |

## Outputs

| Path | Contents |
|---|---|
| data/packages/{package_id_safe}.json | One v1 record per package |
| work/logs/run-{mode}-{ts}.log | Per-run log: OK/FAIL per seed, end aggregate |
| work/wayback_cache/*.html | Cached Wayback landing pages (re-runnable offline) |

Eventual public-dataset target: `github.com/dphdame/replication-landscape-data`. v0.1 leaves the panel under GDrive `02_content-strategy/replication-landscape/data/`; push to GitHub is deferred (see Victoria notes in `02_content-strategy/CAP-06-OPEN-QUESTIONS-FOR-VICTORIA.md`).

## Workflow

1. Resolve seed -> openICPSR project ID (AEA articles -> DOI -> openICPSR project; non-AEA -> manual seed line).
2. Harvest landing page via Wayback CDX -> most recent 200 snapshot -> parse description text + file listing + DOI metadata.
3. Run static analyzer (Path A: README-only metrics; source-tree metrics nulled with `general_notes: path_a_no_source_tree`).
4. Run DAS classifier (Claude Sonnet, temperature=0, deterministic user_id hash, strict-JSON validation).
5. Assemble v1 record; write to `data/packages/{pid}.json`.
6. Aggregate run summary (metrics-populated counts, classifier-state breakdown, failures).
7. After hand-coding (N=30), `validation/` scripts join machine vs hand-coded records and emit per-metric precision/recall + 95% binomial CIs for the post's measurement-uncertainty section.

## CLI

```bash
# Smoke test (N=10 from data/seeds/smoke-10.txt)
python3 src/run_pipeline.py --mode smoke

# Analytic run (after Victoria builds analytic-500.txt)
python3 src/run_pipeline.py --mode analytic

# Validation comparison (after hand_code_template.csv is filled)
python3 src/run_pipeline.py --mode validation
```

## Hard delegations (NOT rebuilt by this skill)

| Need | Delegated to |
|---|---|
| LOC per language | `cloc 2.08` (Homebrew) |
| DAS classification | Anthropic Messages API (Sonnet 4.6 primary, Opus 4.7 fallback per `replication-config-v1.yaml`) |
| openICPSR landing-page access | Internet Archive Wayback CDX (Cloudflare 403s direct openICPSR) |
| openICPSR authenticated zip download | v0.2 stub at `src/openicpsr_authenticated.py.stub` |

## References

- Spec: `02_content-strategy/CAPABILITY-06-replication-package-analytics.md`
- Static-analyzer design: `02_content-strategy/static-analyzer-design.md`
- DAS classifier prompt v1: `02_content-strategy/das-classifier-prompt-v1.md`
- Output schema: `02_content-strategy/replication-landscape-schema-v1.json` (also vendored at `skill/schemas/replication-package-v1.json`)
- Pinned config: `02_content-strategy/replication-config-v1.yaml`
- Discovery plan: `02_content-strategy/openicpsr-discovery-plan.md`

## Triggers

- `/replication-package-analytics`
- "audit replication packages"
- "replication landscape"
- "replication package metrics"
- "run replication crawl"

## Failure modes (Path A)

| Reason | Condition | Handling |
|---|---|---|
| wayback_fetch_failed | CDX returns 0 rows or all snapshots return non-200 | warning, record retained with nulled README metrics |
| no_das_present | README empty/missing | DAS classifier short-circuits to opaque, confidence=1.0 |
| api_key_unset | No ANTHROPIC_API_KEY | DAS classifier emits opaque + ambiguity_notes="api_key_unset" |
| schema_invalid | LLM returned non-conforming JSON | one retry, then opaque + last_error |

## v0.2 upgrade path

`mv src/openicpsr_authenticated.py.stub src/openicpsr_authenticated.py` and implement per the docstring. `run_pipeline.py` auto-detects the live module and switches every package to Path B (authenticated download + full source-tree analysis: folder structure, master script, seed counts, hard-coded paths, version-pin files, cloc LOC).

## Testability without API access

- `static_analyzer.py` runs end-to-end with `readme_text` passed as a string; Path A has zero external dependencies beyond stdlib.
- `das_classifier.py` no-ops cleanly without `ANTHROPIC_API_KEY`.
- `wayback_harvester.py` requires `requests` and live network; bypassable by pre-populating `work/wayback_cache/`.

## Sibling capabilities

- CAPABILITY-05 papers-md-generator (PDF -> verified papers.md block, per-paper)
- CAPABILITY-07 attribution-audit-network (cross-paper attribution graph)
