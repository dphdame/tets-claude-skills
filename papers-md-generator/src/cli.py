"""CLI entrypoint for papers-md-generator.

Usage:
  python3 -m src.cli --input <DOI|PDF_PATH> --pillar-slug <slug>
                     [--grobid-url URL] [--output-dir DIR]
                     [--estimator-hints "CS DiD,sdid"] [--dry-run]
                     [--idempotence append|overwrite|skip]
                     [--tei-fixture PATH]  # offline: bypass GROBID
                     [--estimator-fixture PATH]  # offline: bypass LLM
                     [--quote-fixture PATH]
"""
from __future__ import annotations
import argparse
import datetime as _dt
import json
import sys
from pathlib import Path

# Make this script runnable directly (python3 src/cli.py) by adding parent
# to sys.path so absolute imports work.
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from src import grobid_client, metadata, normalizer, misattribution, extractor, writer


# Companion-spec lookup:
#  1. ../shared/ (canonical post-install location)
#  2. TETS_SPEC_DIR env var override
import os as _os
SKILL_ROOT = HERE.parent
SHARED_SPEC_DIR = Path(_os.environ.get("TETS_SPEC_DIR",
                                        str(SKILL_ROOT.parent / "shared")))


def _utc_now():
    return _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def find_spec_file(name):
    """Find a companion spec file in the shared/ directory next to the skill."""
    candidates = [SHARED_SPEC_DIR / name, SKILL_ROOT / "spec" / name]
    for p in candidates:
        if p.exists():
            return p
    return None


def run(args):
    log_urls = []
    diagnostics = {
        "no_methods_section": False,
        "refs_failed": False,
        "body_too_short": False,
    }

    pillar_slug = args.pillar_slug
    output_dir = Path(args.output_dir) if args.output_dir else \
        Path(f"citation-audit/{pillar_slug}")
    output_dir.mkdir(parents=True, exist_ok=True)
    papers_md_path = output_dir / "papers.md"
    pending_review = output_dir / "misattribution-flags-pending-review.md"

    # Load companion catalogs
    hyphen_path = find_spec_file("hyphen-compound-exceptions.yaml")
    catalog_path = find_spec_file("misattribution-catalog.yaml")
    hyphen_exceptions = normalizer.load_hyphen_exceptions(hyphen_path) if hyphen_path else set()
    catalog = misattribution.load_catalog(catalog_path) if catalog_path else []

    # ---- Step 1: Obtain TEI ----
    tei_xml = None
    if args.tei_fixture:
        tei_xml = Path(args.tei_fixture).read_text()
        log_urls.append({"url": f"file://{args.tei_fixture}",
                         "status_code": 200, "fetched_at": _utc_now(),
                         "note": "tei_fixture"})
    else:
        input_arg = args.input
        if Path(input_arg).exists() and Path(input_arg).suffix.lower() == ".pdf":
            client = grobid_client.GrobidClient(base_url=args.grobid_url)
            if not client.alive():
                _fail("grobid_unreachable",
                      f"GROBID not responding at {args.grobid_url}",
                      papers_md_path, output_dir, args, log_urls,
                      diagnostics)
                return 2
            try:
                tei_xml = client.process_fulltext(input_arg)
                # Cache TEI
                tei_dir = output_dir / "tei"
                tei_dir.mkdir(parents=True, exist_ok=True)
                slug = Path(input_arg).stem
                (tei_dir / f"{slug}.tei.xml").write_text(tei_xml)
            except grobid_client.GrobidUnavailable as e:
                _fail("grobid_unreachable", str(e), papers_md_path,
                      output_dir, args, log_urls, diagnostics)
                return 2
        else:
            # Treat as DOI/id; resolve metadata; cannot get TEI without PDF
            meta = metadata.resolve(input_arg, log_urls)
            if not meta:
                _fail("doi_unresolved",
                      f"could not resolve {input_arg} via CrossRef/DataCite/S2",
                      papers_md_path, output_dir, args, log_urls,
                      diagnostics)
                return 2
            print("[INFO] DOI resolved but no PDF supplied. Cannot extract.",
                  file=sys.stderr)
            print(json.dumps(meta, indent=2), file=sys.stderr)
            _fail("no_pdf_for_extraction",
                  "DOI resolved but no PDF provided; supply local PDF path",
                  papers_md_path, output_dir, args, log_urls, diagnostics)
            return 2

    # ---- Step 2: Parse TEI ----
    root = grobid_client.parse_tei(tei_xml)
    body_words = grobid_client.get_body_word_count(root)
    if body_words < 500:
        diagnostics["body_too_short"] = True
        _fail("grobid_body_too_short",
              f"GROBID body word count = {body_words} (< 500)",
              papers_md_path, output_dir, args, log_urls, diagnostics)
        return 2
    diagnostics["no_methods_section"] = not grobid_client.has_methods_div(root)
    refs = grobid_client.get_references(root)
    diagnostics["refs_failed"] = (len(refs) == 0)
    header_meta = grobid_client.get_header_metadata(root)

    # ---- Step 3: Resolve DOI ----
    doi_to_use = header_meta.get("doi") or args.input
    if Path(args.input).exists() and not header_meta.get("doi"):
        # PDF given but no DOI in header; try inferring from filename
        doi_to_use = ""
    meta_resolved = None
    if doi_to_use:
        meta_resolved = metadata.resolve(doi_to_use, log_urls)
    if meta_resolved:
        header_meta.update({
            "title": meta_resolved.get("title") or header_meta.get("title"),
            "authors": meta_resolved.get("authors") or header_meta.get("authors"),
            "year": meta_resolved.get("year") or header_meta.get("year"),
            "journal": meta_resolved.get("journal") or header_meta.get("journal"),
            "doi": meta_resolved.get("doi") or doi_to_use,
        })
        header_meta["citation"] = metadata.format_apa(meta_resolved)
    else:
        header_meta["citation"] = metadata.format_apa(header_meta) if header_meta.get("title") else ""

    # Final DOI value for block header
    final_doi = header_meta.get("doi") or doi_to_use or "unknown"

    # ---- Step 4: Extract estimators ----
    hint_list = []
    if args.estimator_hints:
        hint_list = [h.strip() for h in args.estimator_hints.split(",")
                     if h.strip()]
    estimators = extractor.extract_estimators(
        tei_xml, hint_list=hint_list,
        fixture_path=args.estimator_fixture,
    )

    # ---- Step 5: Gate checks ----
    main_ests = [e for e in (estimators or []) if e.get("role") == "main"]
    if not main_ests:
        _fail("no_main_estimator_extracted",
              f"got {len(estimators or [])} estimators, none with role='main'",
              papers_md_path, output_dir, args, log_urls, diagnostics)
        return 2
    canonical_present = any(
        e.get("estimator_canonical_citation") for e in main_ests
    )
    if not canonical_present:
        _fail("no_canonical_citation_resolvable",
              "every main estimator has null estimator_canonical_citation",
              papers_md_path, output_dir, args, log_urls, diagnostics)
        return 2

    # ---- Step 6: Quote selection per estimator ----
    haystack_norm = normalizer.normalize(
        grobid_client.get_body_text(root),
        hyphen_exceptions=hyphen_exceptions,
    )
    quotes_by_estimator = {}
    sections = grobid_client.get_sections(root)
    methods_sec_ids = {s["section_id"]: s
                       for s in grobid_client.find_methods_sections(sections)}
    total_quotes = 0
    QUOTES_TOTAL_CAP = 15
    for est in estimators:
        if total_quotes >= QUOTES_TOTAL_CAP:
            break
        candidates = extractor.extract_quotes(
            tei_xml, est, fixture_path=args.quote_fixture,
        )
        verified = []
        for q in candidates:
            sec_id = q.get("section_id", "")
            # Drop quotes from forbidden sections
            if sec_id and sec_id not in methods_sec_ids:
                # Still allow if the verification just succeeds in body;
                # but per spec, section must be methods/theorem/assumption.
                continue
            method, norm_text = normalizer.verify_quote(
                q.get("text", ""), haystack_norm, hyphen_exceptions,
            )
            if method == "FAIL":
                continue
            sec_meta = methods_sec_ids.get(sec_id, {})
            verified.append({
                "text": norm_text,
                "section_id": sec_id,
                "page": sec_meta.get("page"),
                "verification_method": method,
                "quote_role": q.get("quote_role", ""),
            })
            total_quotes += 1
            if total_quotes >= QUOTES_TOTAL_CAP:
                break
        quotes_by_estimator[est.get("estimator_name", "?")] = verified[:5]

    # ---- Step 7: Misattribution engine ----
    pending_entries = []
    sc_gate_results = {}
    for est in main_ests:
        cite = est.get("estimator_canonical_citation")
        if not cite:
            continue
        matches = misattribution.find_misattribution(cite, catalog)
        if not matches:
            continue
        # Self-consistency gate
        sc = extractor.self_consistency_check(tei_xml, hint_list=hint_list)
        sc_gate_results[est.get("estimator_name", "?")] = sc
        if sc.get("agree") is True:
            pending_entries.append(
                misattribution.format_pending_review_entry(
                    final_doi, est.get("estimator_name", "?"),
                    cite, matches, self_consistency_passed=True,
                )
            )
        elif sc.get("agree") is None and not args.estimator_fixture:
            # API unavailable; flag as INCONSISTENT-equivalent: do not assert
            print("[WARN] Self-consistency gate could not run (LLM unavailable). "
                  "Misattribution flag DROPPED per spec safety default.",
                  file=sys.stderr)
        else:
            # INCONSISTENT
            inconsistent_path = output_dir / "misattribution-flag-INCONSISTENT.md"
            with open(inconsistent_path, "a") as f:
                f.write(f"## {final_doi} - {est.get('estimator_name', '?')}\n")
                f.write(f"Reason: {sc.get('reason')}\n")
                f.write(f"Run A: {json.dumps(sc.get('run_a'))[:300]}\n")
                f.write(f"Run B: {json.dumps(sc.get('run_b'))[:300]}\n\n")

    # Misattribution flags NEVER auto-promoted to do_NOT_attribute.
    do_not_attribute = []  # empty by default; populated only after human review

    # ---- Step 8: Quote pipeline gate ----
    if all(len(v) == 0 for v in quotes_by_estimator.values()):
        # Still write a block; per spec gate 6 only fails on non-quote
        # field failures. Quotes can be empty if all dropped. But spec
        # also defines quote_pipeline_all_failed as a failure code.
        # We treat empty as a soft warning: drop a diagnostic but emit.
        # Per spec safety: if EVERY candidate dropped via pipeline, FAIL.
        if quotes_by_estimator:  # candidates existed
            _fail("quote_pipeline_all_failed",
                  "all candidate quotes failed normalization match",
                  papers_md_path, output_dir, args, log_urls, diagnostics)
            return 2

    # ---- Step 9: Render block ----
    block = writer.render_block(
        doi=final_doi,
        slug=pillar_slug,
        header_meta=header_meta,
        estimators=estimators,
        quotes_by_estimator=quotes_by_estimator,
        do_not_attribute=do_not_attribute,
        diagnostics=diagnostics,
        fetched_urls=log_urls,
    )

    if args.dry_run:
        print(block)
        return 0

    # Queue misattribution flags
    for entry_md in pending_entries:
        path = writer.queue_misattribution_flag(pending_review, entry_md)
        print(f"[INFO] Queued misattribution flag to {path}", file=sys.stderr)

    res = writer.write_block(papers_md_path, block, final_doi,
                             idempotence_mode=args.idempotence)
    print(f"[OK] {res['action']}: {res['path']}")
    return 0


def _fail(reason, message, papers_md_path, output_dir, args, log_urls, diagnostics):
    print(f"[FAIL] {reason}: {message}", file=sys.stderr)
    failed_path = output_dir / "papers-md-draft-FAILED.md"
    doi = args.input
    block = writer.render_failed(
        doi=doi,
        slug=args.pillar_slug,
        reason=reason,
        diagnostics={**diagnostics, "message": message},
        fetched_urls=log_urls,
    )
    with open(failed_path, "a") as f:
        f.write(block + "\n")
    print(f"[FAIL] wrote: {failed_path}", file=sys.stderr)


def main():
    p = argparse.ArgumentParser(prog="papers-md-generator")
    p.add_argument("--input", required=True,
                   help="DOI, arxiv:ID, nber:WP, or local PDF path")
    p.add_argument("--pillar-slug", required=True,
                   help="Pillar slug, e.g. cs-did")
    p.add_argument("--grobid-url", default="http://localhost:8070")
    p.add_argument("--output-dir", default=None)
    p.add_argument("--estimator-hints", default="",
                   help="Comma-separated list of expected estimators")
    p.add_argument("--dry-run", action="store_true",
                   help="Print block to stdout, do not write")
    p.add_argument("--idempotence", default="append",
                   choices=["append", "overwrite", "skip"])
    p.add_argument("--tei-fixture", default=None,
                   help="Path to pre-generated TEI XML (bypass GROBID)")
    p.add_argument("--estimator-fixture", default=None,
                   help="Path to JSON of estimator objects (bypass LLM)")
    p.add_argument("--quote-fixture", default=None,
                   help="Path to JSON {estimator_name: [quotes]} (bypass LLM)")
    args = p.parse_args()
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
