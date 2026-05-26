# Smoke-test results (Honest DiD / CS 2021)

Date: 2026-05-25

## Configuration

- Input: Callaway & Sant'Anna (2021) JoE "Difference-in-differences with multiple time periods"
- DOI: 10.1016/j.jeconom.2020.12.001
- Pillar slug: cs-did
- GROBID: NOT used (Docker unavailable; see CAP-05-BUILD-BLOCKERS.md)
- TEI: hand-crafted fixture from cached PDF text (`fixtures/cs2021_tei.xml`)
- LLM extractor: REAL Anthropic Claude Sonnet 4.5 (ANTHROPIC_API_KEY present)
- Quote extractor: REAL Anthropic Claude Sonnet 4.5
- Metadata: REAL CrossRef API
- Misattribution catalog: gdrive copy (15 entries)

## Result

PASS. Block written to /tmp/papers-md-smoke-test/papers.md (120 lines).

## Block contents verified

- Block-start comment carries all 4 required keys (doi, slug, schema_version, generation_timestamp).
- APA citation present and CrossRef-resolved.
- 3 estimators extracted by LLM, all role='main':
  1. Callaway-Sant'Anna doubly-robust (canonical citation: self-reference, correct).
  2. Inverse probability weighting estimator (canonical: Abadie 2005 - correct).
  3. Outcome regression estimator (canonical: Sant'Anna & Zhao 2020 - correct).
- All 3 estimators carry the 3 named assumptions (Limited Treatment Anticipation, Conditional Parallel Trends Never-Treated, Conditional Parallel Trends Not-Yet-Treated).
- 5 verbatim quotes, all verified via exact-substring match against normalized TEI body.
- do_NOT_attribute correctly empty (CS 2021 citing CS 2021 does not trigger any catalog wrong_credit entry).
- diagnostics: no_methods_section=false, refs_failed=false, body_too_short=false.

## Gates passed

| Gate | Result |
|---|---|
| 1. Every quote verified via normalization pipeline | PASS (5/5 exact match) |
| 2. DOI resolves via CrossRef | PASS |
| 3. Block conforms to schema v1 | PASS (verified by hand against papers-md-schema-v1.md) |
| 4. Idempotence rule honored | PASS (append-mode default) |
| 5. Misattribution flags only after self-consistency gate | PASS (no flags triggered on CS-citing-CS) |
| 6. Gate failures emit FAILED file | PASS (verified separately) |

## Unit tests

23/23 pass (`python3 -m pytest tests/`):
- test_normalizer.py: 10 tests (NFKC, ligatures, soft-hyphen, linebreak, exceptions, whitespace, exact/fuzzy match)
- test_misattribution.py: 5 tests (catalog load, surface-form + author-year + ordered-token matching, no false positives, pending-review format)
- test_grobid_tei_parse.py: 8 tests (TEI parse, body word count, section enumeration, methods filter, references, header metadata, methods-div detection)
