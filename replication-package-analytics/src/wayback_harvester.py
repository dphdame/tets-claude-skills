"""
wayback_harvester.py — CAP-06 v0.1

openICPSR landing-page harvest via the Internet Archive Wayback Machine
(Cloudflare blocks direct openICPSR access; Wayback caches the public
project pages).

Returns (readme_text, file_listing, deposit_metadata) for a project ID.

Public API:
    harvest_openicpsr_via_wayback(project_id) -> dict with:
        readme_text:      str | None
        file_listing:     list[dict]   ({"name", "size", "type"})
        deposit_metadata: dict         (title, authors, doi, deposit_date, ...)
        snapshot_url:     str | None
        snapshot_date:    str | None
        notes:            list[str]
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

CDX_API = "http://web.archive.org/cdx/search/cdx"
WAYBACK_BASE = "https://web.archive.org/web"
ICPSR_PROJECT_URL_TMPL = "https://www.openicpsr.org/openicpsr/project/{pid}/version/V1/view"
ICPSR_PROJECT_URL_FALLBACK_TMPL = "https://www.openicpsr.org/openicpsr/project/{pid}"

DEFAULT_TIMEOUT = 30
USER_AGENT = "TooEarlyToSay-replication-landscape/0.1 (research; hello@tooearlytosay.com)"


def _cdx_query(url: str, timeout: int = DEFAULT_TIMEOUT) -> list[dict[str, str]]:
    """Hit the Wayback CDX API for snapshots of `url`. Returns list of dicts."""
    import requests  # type: ignore
    params = {
        "url": url,
        "output": "json",
        "filter": "statuscode:200",
        "limit": "20",
        "collapse": "digest",
    }
    try:
        r = requests.get(
            CDX_API, params=params, timeout=timeout,
            headers={"User-Agent": USER_AGENT},
        )
        if r.status_code != 200:
            return []
        rows = r.json()
        if not rows or len(rows) < 2:
            return []
        cols = rows[0]
        return [dict(zip(cols, row)) for row in rows[1:]]
    except Exception:
        return []


def _fetch_wayback(snapshot_url: str, timeout: int = DEFAULT_TIMEOUT, cache_dir: Path | None = None) -> str | None:
    import requests  # type: ignore
    if cache_dir is not None:
        cache_key = re.sub(r"\W+", "_", snapshot_url)[:200]
        cache_file = cache_dir / f"{cache_key}.html"
        if cache_file.exists():
            try:
                return cache_file.read_text(encoding="utf-8", errors="replace")
            except Exception:
                pass
    try:
        r = requests.get(
            snapshot_url, timeout=timeout,
            headers={"User-Agent": USER_AGENT},
            allow_redirects=True,
        )
        if r.status_code != 200:
            return None
        html = r.text
        if cache_dir is not None:
            try:
                cache_file.write_text(html, encoding="utf-8")
            except Exception:
                pass
        return html
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Landing-page parsers (openICPSR HTML is server-rendered in older snapshots,
# JS-shell in newer ones; we extract what we can from either).
# ---------------------------------------------------------------------------
_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_META_DOI_RE = re.compile(r'<meta\s+name=["\']citation_doi["\']\s+content=["\']([^"\']+)["\']', re.IGNORECASE)
_META_AUTHOR_RE = re.compile(r'<meta\s+name=["\']citation_author["\']\s+content=["\']([^"\']+)["\']', re.IGNORECASE)
_META_DATE_RE = re.compile(r'<meta\s+name=["\']citation_publication_date["\']\s+content=["\']([^"\']+)["\']', re.IGNORECASE)

# README text often appears in a div with class describing 'description' or 'abstract'
_DESC_BLOCK_RE = re.compile(
    r'<div[^>]*class="[^"]*(?:description|abstract|projectDescription|summary)[^"]*"[^>]*>(.*?)</div>',
    re.IGNORECASE | re.DOTALL,
)
# File listing patterns vary; capture filenames that look like deposit assets.
_FILE_ROW_RE = re.compile(
    r'<td[^>]*class="[^"]*(?:file|fileName)[^"]*"[^>]*>\s*<[^>]+>\s*([^<]+\.(?:do|R|r|py|ipynb|m|jl|sas|csv|dta|rdata|rds|md|txt|pdf|zip|tar|gz|do\b|ado|sthlp))',
    re.IGNORECASE,
)


def _strip_tags(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html or "")


def _collapse_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def parse_landing_html(html: str) -> dict[str, Any]:
    """Best-effort extraction; many openICPSR snapshots have sparse server-rendered HTML."""
    out: dict[str, Any] = {
        "title": None, "doi": None, "authors": [], "deposit_date": None,
        "description_text": None, "file_listing": [],
    }
    if not html:
        return out
    if (m := _TITLE_RE.search(html)):
        out["title"] = _collapse_ws(_strip_tags(m.group(1)))
    if (m := _META_DOI_RE.search(html)):
        out["doi"] = m.group(1).strip()
    out["authors"] = [a.strip() for a in _META_AUTHOR_RE.findall(html)]
    if (m := _META_DATE_RE.search(html)):
        out["deposit_date"] = m.group(1).strip()
    if (m := _DESC_BLOCK_RE.search(html)):
        out["description_text"] = _collapse_ws(_strip_tags(m.group(1)))
    seen = set()
    for fn in _FILE_ROW_RE.findall(html):
        f = fn.strip()
        if f and f not in seen:
            seen.add(f)
            out["file_listing"].append({"name": f, "size": None, "type": Path(f).suffix.lower()})
    return out


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def harvest_openicpsr_via_wayback(
    project_id: str,
    cache_dir: str | Path | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Look up most-recent Wayback snapshot, parse it, return components."""
    notes: list[str] = []
    cache = Path(cache_dir) if cache_dir else None
    if cache and not cache.exists():
        cache.mkdir(parents=True, exist_ok=True)

    snapshot_url = None
    snapshot_date = None
    html = None

    for url_tmpl in (ICPSR_PROJECT_URL_TMPL, ICPSR_PROJECT_URL_FALLBACK_TMPL):
        url = url_tmpl.format(pid=project_id)
        rows = _cdx_query(url, timeout=timeout)
        if not rows:
            notes.append(f"no_snapshots:{url}")
            continue
        rows.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
        for row in rows:
            ts = row.get("timestamp")
            original = row.get("original")
            if not ts or not original:
                continue
            snapshot_url = f"{WAYBACK_BASE}/{ts}/{original}"
            snapshot_date = ts
            html = _fetch_wayback(snapshot_url, timeout=timeout, cache_dir=cache)
            if html:
                break
        if html:
            break

    if not html:
        return {
            "readme_text": None,
            "file_listing": [],
            "deposit_metadata": {},
            "snapshot_url": snapshot_url,
            "snapshot_date": snapshot_date,
            "notes": notes + ["wayback_fetch_failed"],
        }

    parsed = parse_landing_html(html)
    readme_text = parsed.get("description_text")

    deposit_metadata = {
        "title": parsed.get("title"),
        "doi": parsed.get("doi"),
        "authors": parsed.get("authors"),
        "deposit_date": parsed.get("deposit_date"),
    }
    return {
        "readme_text": readme_text,
        "file_listing": parsed.get("file_listing", []),
        "deposit_metadata": deposit_metadata,
        "snapshot_url": snapshot_url,
        "snapshot_date": snapshot_date,
        "notes": notes,
    }


if __name__ == "__main__":
    import argparse, sys
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-id", required=True)
    ap.add_argument("--cache-dir", default=None)
    args = ap.parse_args()
    out = harvest_openicpsr_via_wayback(args.project_id, cache_dir=args.cache_dir)
    json.dump(out, sys.stdout, indent=2)
    sys.stdout.write("\n")
