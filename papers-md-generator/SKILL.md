---
name: papers-md-generator
description: Causal-inference citation-audit assistant that ingests a PDF or DOI, delegates PDF parsing to GROBID (Docker), bibliographic metadata to CrossRef + Semantic Scholar, and produces a verified papers.md block documenting estimator, design, identification claims, named assumptions, and verbatim quote evidence cross-referenced against a curated 15-entry misattribution-trap catalog. Phase 1 grounding artifact for every TETS causal-inference pillar. Hard-delegates PDF parsing, bibliographic resolution, and section hierarchy; value-add layer is the causal-method extraction, quote normalization (NFKC, ligatures, linebreak hyphenation with exceptions), misattribution-trap engine, and self-consistency gate for misattribution flags. Triggers include "generate papers.md entry for [DOI/PDF]", "papers-md for [pillar]", "/papers-md-generator".
---

# papers-md-generator

Build the Phase-1 grounding artifact (papers.md block) for a single research paper in a TETS causal-inference pillar. The block joins to the cross-pillar misattribution-trap catalog and is the input to downstream citation-cross-validator and hallucination-audit-pillar skills.

This skill does NOT rebuild PDF parsing, bibliographic metadata, or section hierarchy. Those are delegated to GROBID (Docker), CrossRef, and Semantic Scholar Graph API per CAP-05 spec "Hard delegations". The value-add is the causal-inference audit layer.

## When to Use

Phase 1 of any pillar build, OR ad-hoc citation grounding for a single paper. Run BEFORE drafting an article that will cite the paper. Run BEFORE the citation-cross-validator skill (which consumes the papers.md it produces).

Do NOT use to:
- Summarize a paper for a general reader
- Verify URL liveness (use hallucination-audit-pillar HA-02)
- Cross-check a draft against an existing papers.md (use citation-cross-validator)
- Fetch citation count or h-index (use Semantic Scholar Graph API directly)

## Inputs

| Name | Type | Default | Notes |
|---|---|---|---|
| input | string | required | DOI (10.xxxx/yyy), arXiv ID (arxiv:2201.01194), NBER WP (nber:26463), or local PDF path. |
| pillar_slug | string | required | Determines output directory: citation-audit/<pillar>/papers.md. |
| estimator_hints | list[str] | optional | Hint list of expected estimators. Lowers extractor false-negatives on multi-method papers. |
| grobid_url | string | http://localhost:8070 | GROBID base URL. Skill assumes container already started. |
| output_dir | string | citation-audit/<pillar>/ | Where to write the block. |
| dry_run | bool | false | If true, print papers.md draft to stdout but do not write to file. |
| idempotence | append/overwrite/skip | append | How to handle existing block with same DOI. |

## Outputs (always one of)

1. Success: append-mode write of a schema-v1 block per papers-md-schema-v1.md.
2. Failure: papers-md-draft-FAILED.md with reason code (no_main_estimator_extracted, no_canonical_citation_resolvable, grobid_body_too_short, doi_unresolved, quote_pipeline_all_failed) and triggering diagnostics.
3. Misattribution flag (if any): queued to misattribution-flags-pending-review.md. NEVER auto-promoted into the block do_NOT_attribute list. Human review required before promotion.

## Workflow (Phase-1 grounding)

1. Resolve identifier — CrossRef first; on 404 fall back to DataCite; on still-404 try Semantic Scholar title-author-year hash. URLs accepting 200/301/302 only.
2. Parse PDF via GROBID — POST /api/processFulltextDocument with consolidateHeader=1, consolidateCitations=1, includeRawCitations=1, teiCoordinates=ref,biblStruct,figure,formula,head. Cache TEI to citation-audit/<pillar>/tei/<slug>.tei.xml.
3. Run Prompt 1 (estimator/design extraction) per extractor-prompt-v1.md.
4. Self-consistency gate — for any estimator triggering misattribution-catalog match: re-run Prompt 1 with temperature=0.0/seed=7 and temperature=0.2/seed=13; only emit MISATTRIBUTION_CANDIDATE on agreement.
5. Run Prompt 2 (verbatim quote selection) per estimator object. Cap: 5 per estimator, 15 per paper.
6. Quote verification pipeline — NFKC, soft-hyphen removal, ligature decomposition, linebreak-hyphenation reattachment (with hyphen-compound-exceptions.yaml), whitespace collapse, exact-match first then Levenshtein <=2/100. Drop failures.
7. Misattribution-trap engine — match every extracted estimator_canonical_citation against misattribution-catalog.yaml wrong_credit entries. On self-consistency-passing match: queue to misattribution-flags-pending-review.md.
8. Write block — assemble per schema v1; check gates; honor idempotence; write.

## References

- Spec: 02_content-strategy/CAPABILITY-05-papers-md-generator.md
- Schema: 02_content-strategy/papers-md-schema-v1.md
- Catalogs: 02_content-strategy/misattribution-catalog.yaml, method-taxonomy-v1.yaml
- Prompts: 02_content-strategy/extractor-prompt-v1.md
- Hyphen exceptions: 02_content-strategy/hyphen-compound-exceptions.yaml
- GROBID setup: 02_content-strategy/grobid-setup.md

## Triggers

- "generate papers.md entry for [DOI or PDF]"
- "generate papers.md for [pillar]"
- "ingest [paper] into papers.md"
- "/papers-md-generator"

## Hard delegations (NOT rebuilt by this skill)

| Need | Delegated to |
|---|---|
| PDF to TEI XML | GROBID (Docker) |
| Bibliographic metadata | GROBID + CrossRef + Semantic Scholar (fallback chain) |
| Citation graph | Semantic Scholar Graph API |
| Section hierarchy | GROBID TEI |

## Failure modes

| Reason | Condition |
|---|---|
| no_main_estimator_extracted | Zero estimators returned with role='main'. |
| no_canonical_citation_resolvable | All main estimators have null estimator_canonical_citation. |
| grobid_body_too_short | GROBID body under 500 words. |
| doi_unresolved | DOI failed across CrossRef, DataCite, Semantic Scholar. |
| quote_pipeline_all_failed | Every quote candidate dropped through the normalization pipeline. |
| grobid_unreachable | GROBID container not responding at grobid_url. |

## CLI

```bash
# Production (with GROBID + Anthropic API):
python3 .claude/skills/papers-md-generator/src/cli.py \
  --input citation-audit/honest-did/pdfs/cs2021.pdf \
  --pillar-slug cs-did

# Offline smoke-test (TEI + estimator + quote fixtures):
python3 .claude/skills/papers-md-generator/src/cli.py \
  --input cs2021.pdf \
  --pillar-slug cs-did \
  --tei-fixture .claude/skills/papers-md-generator/fixtures/cs2021_tei.xml \
  --estimator-fixture .claude/skills/papers-md-generator/fixtures/cs2021_estimators.json \
  --quote-fixture .claude/skills/papers-md-generator/fixtures/cs2021_quotes.json \
  --dry-run
```

## Testability without Docker

For modules other than GROBID (extractor, misattribution engine, normalizer, writer), see tests/ for fixture-driven unit tests using a hand-crafted TEI XML. Run via `python3 -m pytest tests/`.
