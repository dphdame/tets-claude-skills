"""
aea_journal_harvester.py — CAP-06 v0.1

AEA article-page scraper. Resolves a paper DOI to the openICPSR replication
project ID (most AEA submissions from 2019+ redirect there).

Public:
    resolve_aea_article_to_openicpsr(article_url) -> dict
    extract_replication_dois_from_article(article_url) -> list[str]
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

USER_AGENT = "TooEarlyToSay-replication-landscape/0.1 (research; hello@tooearlytosay.com)"
DEFAULT_TIMEOUT = 30

# openICPSR DOIs always look like 10.3886/EXXXXXXVN
OPENICPSR_DOI_RE = re.compile(r"10\.3886/E(\d{4,7})V?\d*", re.IGNORECASE)
DOI_LINK_RE = re.compile(
    r"""href=["'](https?://doi\.org/(10\.3886/E\d{4,7}V?\d*))["']""",
    re.IGNORECASE,
)
# AEA "replication package" link patterns
REPLICATION_LINK_RE = re.compile(
    r"""href=["']([^"']+)["'][^>]*>\s*(?:Replication\s+package|Data\s+and\s+code|Replication\s+files)""",
    re.IGNORECASE,
)


def _get(url: str, timeout: int = DEFAULT_TIMEOUT) -> str | None:
    try:
        import requests  # type: ignore
        r = requests.get(
            url, timeout=timeout,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*"},
            allow_redirects=True,
        )
        if r.status_code != 200:
            return None
        return r.text
    except Exception:
        return None


def extract_replication_dois_from_article(article_url: str) -> list[str]:
    """Scan an AEA article page for openICPSR replication DOIs."""
    html = _get(article_url)
    if not html:
        return []
    found = []
    seen = set()
    for m in DOI_LINK_RE.finditer(html):
        doi = m.group(2).strip()
        if doi not in seen:
            seen.add(doi)
            found.append(doi)
    # also scan for bare openICPSR DOI strings in body
    for m in OPENICPSR_DOI_RE.finditer(html):
        doi = m.group(0)
        if doi not in seen:
            seen.add(doi)
            found.append(doi)
    return found


def doi_to_project_id(doi: str) -> str | None:
    """10.3886/E198765V1 -> '198765'."""
    m = OPENICPSR_DOI_RE.search(doi)
    if not m:
        return None
    return m.group(1)


def resolve_aea_article_to_openicpsr(article_url: str) -> dict[str, Any]:
    """Return DOIs and openICPSR project IDs found on an AEA article page."""
    dois = extract_replication_dois_from_article(article_url)
    project_ids = [pid for pid in (doi_to_project_id(d) for d in dois) if pid]
    return {
        "article_url": article_url,
        "openicpsr_dois": dois,
        "project_ids": project_ids,
        "primary_project_id": project_ids[0] if project_ids else None,
    }


if __name__ == "__main__":
    import argparse, sys
    ap = argparse.ArgumentParser()
    ap.add_argument("--article-url", required=True)
    args = ap.parse_args()
    out = resolve_aea_article_to_openicpsr(args.article_url)
    json.dump(out, sys.stdout, indent=2)
    sys.stdout.write("\n")
