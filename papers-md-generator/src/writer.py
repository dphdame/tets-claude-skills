"""papers.md block writer per schema v1.

Writes a block delimited by HTML comments with the four required keys
(doi, slug, schema_version, generation_timestamp). Honors idempotence:
on rerun, parse existing papers.md, locate block with same DOI, prompt
(a) overwrite (b) append (c) skip; default `append`.
"""
from __future__ import annotations
import datetime as _dt
import re
from pathlib import Path


SCHEMA_VERSION = "1.0"


def _utc_now():
    return _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def render_block(doi, slug, header_meta, estimators, quotes_by_estimator,
                 do_not_attribute, diagnostics, fetched_urls,
                 generation_timestamp=None, confidence_per_field=None):
    """Render a schema-v1 papers.md block as a string.

    Args:
      doi: e.g. "10.1016/j.jeconom.2020.12.001" or "arxiv:..."
      slug: pillar slug.
      header_meta: dict with title, authors, year, citation.
      estimators: list of estimator dicts (validated against schema).
      quotes_by_estimator: dict[estimator_name] -> list of quote dicts.
      do_not_attribute: list[str] of misattribution warnings.
      diagnostics: dict with no_methods_section, refs_failed, body_too_short.
      fetched_urls: list[{url, status_code, fetched_at}].
    """
    ts = generation_timestamp or _utc_now()
    title = header_meta.get("title", "")
    authors = header_meta.get("authors", []) or []
    auth_short = ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else "")
    year = header_meta.get("year", "n.d.")
    citation = header_meta.get("citation", "")

    lines = []
    lines.append(
        f"<!-- papers.md v1 BLOCK START | doi:{doi} | slug:{slug} | "
        f"schema_version:{SCHEMA_VERSION} | generation_timestamp:{ts} -->"
    )
    lines.append(f"## {auth_short} ({year}). {title}.")
    lines.append("")
    lines.append(f"**citation:** {citation}")
    lines.append(f"**doi:** {doi}")
    lines.append("")
    lines.append("**fetched_urls:**")
    for u in fetched_urls or []:
        lines.append(f"  - url: {u.get('url', '')}")
        lines.append(f"    status_code: {u.get('status_code', '')}")
        lines.append(f"    fetched_at: {u.get('fetched_at', '')}")
    lines.append("")
    lines.append("**estimators:**")
    for e in estimators or []:
        lines.append(f"  - estimand: {e.get('estimand', '')}")
        lines.append(f"    design: {e.get('design', '')}")
        lines.append(f"    estimator_name: {e.get('estimator_name', '')}")
        lines.append(
            f"    estimator_canonical_citation: "
            f"{e.get('estimator_canonical_citation') or 'null'}"
        )
        lines.append("    assumptions_named:")
        for a in e.get("assumptions_named", []) or []:
            lines.append(f"      - {a}")
        lines.append(f"    role: {e.get('role', '')}")
        lines.append(f"    section_evidence: {e.get('section_evidence', '')}")
        lines.append(f"    confidence: {e.get('confidence', '')}")
    lines.append("")
    lines.append("**verbatim_quotes:**")
    for est_name, qlist in (quotes_by_estimator or {}).items():
        for q in qlist:
            lines.append(f"  - text: {_yaml_str(q.get('text', ''))}")
            lines.append(f"    section_id: {q.get('section_id', '')}")
            page = q.get("page")
            lines.append(f"    page: {page if page is not None else 'null'}")
            lines.append(
                f"    verification_method: {q.get('verification_method', '')}"
            )
            lines.append(f"    quote_role: {q.get('quote_role', '')}")
    lines.append("")
    lines.append("**do_NOT_attribute:**")
    for d in do_not_attribute or []:
        lines.append(f"  - {d}")
    if not do_not_attribute:
        lines.append("  # (empty: no traps from catalog applied)")
    lines.append("")
    lines.append("**grobid_diagnostics:**")
    diag = diagnostics or {}
    lines.append(f"  no_methods_section: {str(diag.get('no_methods_section', False)).lower()}")
    lines.append(f"  refs_failed: {str(diag.get('refs_failed', False)).lower()}")
    lines.append(f"  body_too_short: {str(diag.get('body_too_short', False)).lower()}")
    if confidence_per_field:
        lines.append("")
        lines.append("**confidence_per_field:**")
        for k, v in confidence_per_field.items():
            lines.append(f"  {k}: {v}")
    lines.append("<!-- papers.md v1 BLOCK END -->")
    lines.append("")
    return "\n".join(lines)


def _yaml_str(s):
    """Render a string as a YAML-safe value (quoted if contains special chars)."""
    if not s:
        return '""'
    if any(c in s for c in [":", "#", "\n", '"', "'"]):
        escaped = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
        return f'"{escaped}"'
    return f'"{s}"'


def render_failed(doi, slug, reason, diagnostics, fetched_urls,
                  generation_timestamp=None):
    """Render papers-md-draft-FAILED.md block."""
    ts = generation_timestamp or _utc_now()
    lines = []
    lines.append(
        f"<!-- papers.md v1 BLOCK START | doi:{doi} | slug:{slug} | "
        f"schema_version:1.0-FAILED | generation_timestamp:{ts} -->"
    )
    lines.append(f"## FAILED: {doi}")
    lines.append("")
    lines.append(f"**reason:** {reason}")
    lines.append("")
    lines.append("**diagnostics:**")
    for k, v in (diagnostics or {}).items():
        lines.append(f"  {k}: {v}")
    lines.append("")
    lines.append("**fetched_urls:**")
    for u in fetched_urls or []:
        lines.append(f"  - url: {u.get('url', '')}")
        lines.append(f"    status_code: {u.get('status_code', '')}")
        lines.append(f"    fetched_at: {u.get('fetched_at', '')}")
    lines.append("<!-- papers.md v1 BLOCK END -->")
    lines.append("")
    return "\n".join(lines)


BLOCK_START_RE = re.compile(
    r"<!-- papers\.md v1 BLOCK START \| doi:([^|]+) \|.*?-->", re.DOTALL
)
BLOCK_END_RE = re.compile(r"<!-- papers\.md v1 BLOCK END -->")


def find_existing_block(papers_md_path, doi):
    """Return (start_idx, end_idx) of existing block with same doi, or None."""
    p = Path(papers_md_path)
    if not p.exists():
        return None
    text = p.read_text()
    for m in BLOCK_START_RE.finditer(text):
        if m.group(1).strip() == doi.strip():
            end_m = BLOCK_END_RE.search(text, m.end())
            if end_m:
                return (m.start(), end_m.end())
    return None


def write_block(papers_md_path, block_text, doi,
                idempotence_mode="append"):
    """Write block to papers.md honoring idempotence.

    Modes:
      - 'append': append new block; never touch existing
      - 'overwrite': replace existing block with same doi
      - 'skip': do nothing if existing block found

    Returns dict {action: 'appended'|'overwritten'|'skipped', path: str}.
    """
    p = Path(papers_md_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    existing = find_existing_block(papers_md_path, doi) if p.exists() else None
    if existing and idempotence_mode == "skip":
        return {"action": "skipped", "path": str(p), "reason": "exists"}
    if existing and idempotence_mode == "overwrite":
        text = p.read_text()
        new_text = text[:existing[0]] + block_text + text[existing[1]:]
        p.write_text(new_text)
        return {"action": "overwritten", "path": str(p)}
    # default: append
    mode = "a" if p.exists() else "w"
    with open(p, mode) as f:
        if mode == "a" and not p.read_text().endswith("\n"):
            f.write("\n")
        f.write(block_text)
        f.write("\n")
    return {"action": "appended", "path": str(p)}


def queue_misattribution_flag(pending_review_path, entry_md):
    """Append a flag entry to misattribution-flags-pending-review.md."""
    p = Path(pending_review_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        p.write_text(
            "# Misattribution Flags Pending Review\n\n"
            "These are MISATTRIBUTION_CANDIDATE flags that passed the\n"
            "self-consistency gate but have NOT been promoted to any\n"
            "papers.md `do_NOT_attribute` list. Pillar author confirms\n"
            "each against the source PDF before manual promotion.\n\n"
        )
    with open(p, "a") as f:
        f.write(entry_md)
        f.write("\n")
    return str(p)
