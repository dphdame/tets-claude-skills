---
name: attribution-audit-network
description: |
  Build the TETS causal-inference attribution-audit network: a navigable map
  of who is credited for which method and where the literature gets it wrong.
  Primary visual: misattribution edges between (a) the correct origin paper
  for a method and (b) the paper observed citations commonly mis-credit.
  Wraps the existing citation_network_expansion.py over OpenAlex (v0.1) and
  the authored misattribution-catalog.yaml + method-taxonomy-v1.yaml.
  Triggers: "audit attribution network", "build misattribution graph",
  "build misattribution graph for [method]", "attribution audit", "/attribution-audit-network"
---

# attribution-audit-network

CAPABILITY-07 implementation. See spec:
`02_content-strategy/CAPABILITY-07-attribution-audit-network.md`.

## What this skill does

Builds the opinionated misattribution-edge network defined in CAPABILITY-07.
The differentiator vs. Connected Papers / ResearchRabbit is the misattribution
layer: edges that flag papers that cite the WRONG origin for a causal-inference
method.

This is **v0.1, OpenAlex-only.** Semantic Scholar API is currently 403-ing on
the on-disk key per `SESSION-LOG-2026-05-25.md`. v0.2 unlocks multi-source
citation merging once Victoria refreshes the key via
`api-feedback@semanticscholar.org`.

## Inputs (authored, read-only)

| File | Owner |
|---|---|
| `02_content-strategy/misattribution-catalog.yaml` | V. Cholette; 16 entries DOI-verified |
| `02_content-strategy/method-taxonomy-v1.yaml`     | V. Cholette; 14 families DOI-verified |
| `~/.claude/scripts/research/phase_02_literature/citation_network_expansion.py` | wrapped, not duplicated |

## Outputs

| File | Purpose |
|---|---|
| `.claude/skills/attribution-audit-network/output/network-graph.json`  | full d3/sigma graph (nodes + edges) |
| `.claude/skills/attribution-audit-network/output/network-chains.json` | main-view list per `frontend-architecture-plan.md` §3.3 |
| `public/research/methodology/attribution-network/data/network-{graph,chains}.json` | published copies (with `--publish-to-frontend`) |
| `public/research/methodology/attribution-network/index.html`          | static frontend stub loading the JSON |

## Workflow

### Phase 1: Catalog sanity-check

```bash
cd .claude/skills/attribution-audit-network
python3 -m lib.catalog_loader
```

Prints catalog stats and the actionable entries (those with both a wrong-credit
DOI and a correct-origin DOI, and those DOIs differ).

### Phase 2: Smoke test (3 entries)

```bash
python3 -m orchestrator --smoke-test --publish-to-frontend
```

Runs the full pipeline on 3 catalog entries:
- `cs-not-yet-treated-mis-credited-to-sa`
- `causal-forests-vs-grf`
- `flci-mis-credited-to-rr`

Hits OpenAlex (cached on disk under `~/.cache/tets-attribution-network/`).

### Phase 3: Full run (all actionable entries)

```bash
python3 -m orchestrator --publish-to-frontend
```

Processes every actionable catalog entry. Output is fully reproducible from
the cache; re-runs are free.

### Phase 4: Validation harness (post-build)

Per spec, trust requires running the 50-paper hand-coding validation.
**v0.1 does NOT run validation;** it scaffolds it.

```bash
python3 validation_harness.py sample --output validation/sample.csv
# Victoria hand-codes the CSV (columns prefixed HAND_)
python3 validation_harness.py score --coded validation/sample-coded.csv \
                                    --graph output/network-graph.json \
                                    --report validation/report.md
```

Acceptance: precision >= 0.85 on mis-cite edges, recall >= 0.60.

## v0.1 limitations (documented in graph output)

- OpenAlex-only citation edges (Semantic Scholar key 403; v0.2 unlock).
- Misattribution edges are CANDIDATES based on bibliography membership.
  Methods-section LLM verification (CAP-05) required for `confirmed` status.
- No version-merge across NBER WP + arXiv + journal DOI yet; each identifier
  is a separate node. v0.2 will collapse on `title+author+year` hash.
- No `claims_to_extend_misattributes` detector. `severity_weight=1.0` always.
- No SSRN nodes (documented capability limitation per spec).

## v0.2 unlock (one line)

Refresh the Semantic Scholar API key at `~/.config/tets/secrets.env`:

```bash
# Once new key arrives, replace SEMANTIC_SCHOLAR_API_KEY=... and re-run
chmod 600 ~/.config/tets/secrets.env
python3 -m orchestrator --publish-to-frontend  # picks up key automatically
```

## Files

```
.claude/skills/attribution-audit-network/
├── SKILL.md                      (this file)
├── orchestrator.py               (main entrypoint)
├── validation_harness.py         (50-paper sampling + scoring scaffold)
└── lib/
    ├── __init__.py
    ├── catalog_loader.py          (reads misattribution-catalog.yaml + method-taxonomy-v1.yaml)
    ├── network_expander.py        (wraps citation_network_expansion.py + disk cache)
    ├── misattribution_detector.py (bibliography-membership candidate edges)
    └── graph_builder.py           (d3/sigma-friendly JSON output)
```

## Open questions

See `02_content-strategy/CAP-07-OPEN-QUESTIONS-FOR-VICTORIA.md`.

## Blockers

See `02_content-strategy/CAP-07-BUILD-BLOCKERS.md`.
