"""DOI / bibliographic metadata resolution.

Fallback chain per spec section 'DOI/URL resolution rules':
  CrossRef -> DataCite -> Semantic Scholar (title-author-year hash)

URL fetch: 200/301/302 accepted; 4xx/5xx rejected with reason; paywall
(403 + 'subscribe' or 'sign in') logged as ACCESS_RESTRICTED.
"""
from __future__ import annotations
import datetime as _dt
import os
import re
from urllib.parse import quote_plus

import requests

CROSSREF_URL = "https://api.crossref.org/works/{doi}"
DATACITE_URL = "https://api.datacite.org/dois/{doi}"
S2_DOI_URL = "https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}"
S2_SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"


def _utc_now():
    return _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_s2_key():
    """Read SEMANTIC_SCHOLAR_API_KEY from secrets file if present."""
    key = os.environ.get("SEMANTIC_SCHOLAR_API_KEY")
    if key:
        return key
    # Optional convenience: read from a local secrets file if env var isn't set.
    # Default path can be overridden with TETS_SECRETS_PATH.
    path = os.path.expanduser(os.environ.get("TETS_SECRETS_PATH", "~/.config/claude-skills/secrets.env"))
    if not os.path.exists(path):
        return None
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() in ("SEMANTIC_SCHOLAR_API_KEY", "S2_API_KEY"):
                return v.strip().strip('"').strip("'")
    return None


def normalize_doi(doi_or_url):
    """Strip URL prefix; return bare DOI."""
    if not doi_or_url:
        return ""
    s = doi_or_url.strip()
    for prefix in ("https://doi.org/", "http://doi.org/",
                   "https://dx.doi.org/", "http://dx.doi.org/",
                   "doi:", "DOI:"):
        if s.lower().startswith(prefix.lower()):
            s = s[len(prefix):]
            break
    return s.strip()


def is_doi(s):
    return bool(re.match(r"^10\.\d{4,9}/[^\s]+$", s.strip()))


def fetch_crossref(doi, log):
    url = CROSSREF_URL.format(doi=quote_plus(doi, safe="/"))
    try:
        r = requests.get(
            url,
            headers={
                "User-Agent": "TETS-papers-md-generator/1.0 (mailto:hello@tooearlytosay.com)",
                "Accept": "application/json",
            },
            timeout=15,
        )
    except requests.RequestException as e:
        log.append({"url": url, "status_code": None, "fetched_at": _utc_now(),
                    "error": str(e)})
        return None
    log.append({"url": url, "status_code": r.status_code,
                "fetched_at": _utc_now()})
    if r.status_code != 200:
        return None
    msg = r.json().get("message", {})
    authors = []
    for a in msg.get("author", []) or []:
        family = a.get("family") or ""
        given = a.get("given") or ""
        if family:
            authors.append(f"{family}, {given[:1]}." if given else family)
    year = ""
    issued = msg.get("issued", {}).get("date-parts") or []
    if issued and issued[0]:
        year = str(issued[0][0])
    return {
        "source": "crossref",
        "title": (msg.get("title") or [""])[0],
        "authors": authors,
        "doi": msg.get("DOI", doi),
        "year": year,
        "journal": (msg.get("container-title") or [""])[0],
        "volume": msg.get("volume", ""),
        "issue": msg.get("issue", ""),
        "page": msg.get("page", ""),
    }


def fetch_datacite(doi, log):
    url = DATACITE_URL.format(doi=quote_plus(doi, safe="/"))
    try:
        r = requests.get(url, timeout=15,
                         headers={"Accept": "application/json"})
    except requests.RequestException as e:
        log.append({"url": url, "status_code": None,
                    "fetched_at": _utc_now(), "error": str(e)})
        return None
    log.append({"url": url, "status_code": r.status_code,
                "fetched_at": _utc_now()})
    if r.status_code != 200:
        return None
    attrs = r.json().get("data", {}).get("attributes", {})
    authors = []
    for c in attrs.get("creators", []) or []:
        n = c.get("name") or c.get("familyName") or ""
        if n:
            authors.append(n)
    return {
        "source": "datacite",
        "title": (attrs.get("titles") or [{}])[0].get("title", ""),
        "authors": authors,
        "doi": attrs.get("doi", doi),
        "year": str(attrs.get("publicationYear", "")),
        "journal": attrs.get("publisher", ""),
        "volume": "",
        "issue": "",
        "page": "",
    }


OPENALEX_DOI_URL = "https://api.openalex.org/works/doi:{doi}"


def fetch_openalex(doi, log):
    """DOI-keyed OpenAlex lookup. Public API, no key required.

    Returns dict on success, None on 404 or transport error.
    Used as the second metadata source for cross-validation when
    Semantic Scholar is unavailable.
    """
    url = OPENALEX_DOI_URL.format(doi=doi)
    try:
        r = requests.get(url, timeout=15,
                         headers={"User-Agent": "tets-claude-skills/0.1 (mailto:hello@tooearlytosay.com)"})
    except requests.RequestException as e:
        log.append({"url": url, "status_code": None,
                    "fetched_at": _utc_now(), "error": str(e)})
        return None
    log.append({"url": url, "status_code": r.status_code,
                "fetched_at": _utc_now()})
    if r.status_code != 200:
        return None
    j = r.json()
    authors = [a["author"]["display_name"]
               for a in (j.get("authorships") or [])
               if a.get("author", {}).get("display_name")]
    return {
        "source": "openalex",
        "title": j.get("title", ""),
        "authors": authors,
        "doi": (j.get("doi") or doi).replace("https://doi.org/", ""),
        "year": str(j.get("publication_year", "")),
        "journal": (j.get("primary_location") or {}).get("source", {}).get("display_name", "") or "",
        "volume": str((j.get("biblio") or {}).get("volume", "") or ""),
        "issue": str((j.get("biblio") or {}).get("issue", "") or ""),
        "page": ((j.get("biblio") or {}).get("first_page", "") or "")
                + (("-" + (j.get("biblio") or {}).get("last_page", ""))
                    if (j.get("biblio") or {}).get("last_page") else ""),
    }


def fetch_semantic_scholar(doi, log, api_key=None):
    """DOI-keyed S2 lookup. Returns None on 403 (fallback) or 404."""
    url = S2_DOI_URL.format(doi=quote_plus(doi, safe="/"))
    headers = {"Accept": "application/json"}
    if api_key:
        headers["x-api-key"] = api_key
    fields = "title,authors,year,venue,externalIds"
    try:
        r = requests.get(url, params={"fields": fields}, headers=headers,
                         timeout=15)
    except requests.RequestException as e:
        log.append({"url": url, "status_code": None,
                    "fetched_at": _utc_now(), "error": str(e)})
        return None
    log.append({"url": url, "status_code": r.status_code,
                "fetched_at": _utc_now()})
    if r.status_code == 403:
        log.append({"note": "S2_API_KEY_403_FALLBACK_TO_CROSSREF_ONLY"})
        return None
    if r.status_code != 200:
        return None
    j = r.json()
    authors = []
    for a in j.get("authors", []) or []:
        nm = a.get("name") or ""
        if nm:
            authors.append(nm)
    return {
        "source": "semantic_scholar",
        "title": j.get("title", ""),
        "authors": authors,
        "doi": (j.get("externalIds") or {}).get("DOI") or doi,
        "year": str(j.get("year", "")),
        "journal": j.get("venue", ""),
        "volume": "",
        "issue": "",
        "page": "",
    }


def resolve(doi_or_id, log):
    """Resolve a DOI (or arxiv: / nber: id) to bibliographic metadata.

    Returns dict on success, None on doi_unresolved.
    """
    s2_key = _load_s2_key()
    s = doi_or_id.strip()
    # Handle arxiv: / nber: prefixes
    if s.lower().startswith("arxiv:"):
        arxiv_id = s.split(":", 1)[1].strip()
        return _fetch_arxiv(arxiv_id, log)
    if s.lower().startswith("nber:"):
        nber = s.split(":", 1)[1].strip()
        return _build_nber_stub(nber, log)
    doi = normalize_doi(s)
    if not is_doi(doi):
        return None
    # Cross-source order: CrossRef first (most coverage for econ DOIs),
    # then OpenAlex (publicly accessible, used for cross-validation),
    # then DataCite (covers ICPSR / Zenodo / dataset DOIs),
    # then Semantic Scholar (only useful with a valid key — the unauthenticated
    # tier IP-throttles too aggressively for pipeline use, returning 429 with
    # no Retry-After header even after 60s backoff).
    meta = fetch_crossref(doi, log)
    if meta:
        return meta
    meta = fetch_openalex(doi, log)
    if meta:
        return meta
    meta = fetch_datacite(doi, log)
    if meta:
        return meta
    meta = fetch_semantic_scholar(doi, log, api_key=s2_key)
    return meta


def _fetch_arxiv(arxiv_id, log):
    """Fetch arXiv metadata via arXiv API."""
    url = f"http://export.arxiv.org/api/query?id_list={arxiv_id}"
    try:
        r = requests.get(url, timeout=15)
    except requests.RequestException as e:
        log.append({"url": url, "status_code": None,
                    "fetched_at": _utc_now(), "error": str(e)})
        return None
    log.append({"url": url, "status_code": r.status_code,
                "fetched_at": _utc_now()})
    if r.status_code != 200:
        return None
    # Minimal parse
    body = r.text
    title_m = re.search(r"<entry>.*?<title>(.*?)</title>", body, re.DOTALL)
    title = title_m.group(1).strip() if title_m else ""
    authors = re.findall(r"<author>\s*<name>(.*?)</name>", body)
    year_m = re.search(r"<published>(\d{4})", body)
    year = year_m.group(1) if year_m else ""
    return {
        "source": "arxiv",
        "title": title,
        "authors": authors,
        "doi": f"arxiv:{arxiv_id}",
        "year": year,
        "journal": "arXiv preprint",
        "volume": "",
        "issue": "",
        "page": "",
    }


def _build_nber_stub(nber, log):
    """NBER WP: build stub identifier; full metadata requires CrossRef on 10.3386/."""
    crossref_doi = f"10.3386/w{nber.lstrip('w')}"
    log.append({"note": f"Resolving NBER WP {nber} via CrossRef DOI {crossref_doi}"})
    meta = fetch_crossref(crossref_doi, log)
    if meta:
        meta["doi"] = f"nber:{nber}"
        return meta
    return None


def format_apa(meta):
    """Produce APA-format citation string."""
    if not meta:
        return ""
    authors = meta.get("authors") or []
    auth_str = ", ".join(authors) if authors else "Unknown"
    year = meta.get("year", "n.d.")
    title = meta.get("title", "")
    journal = meta.get("journal", "")
    volume = meta.get("volume", "")
    issue = meta.get("issue", "")
    page = meta.get("page", "")
    doi = meta.get("doi", "")
    out = f"{auth_str} ({year}). {title}."
    if journal:
        out += f" {journal}"
        if volume:
            out += f", {volume}"
            if issue:
                out += f"({issue})"
        if page:
            out += f", {page}"
        out += "."
    if doi and not doi.startswith(("arxiv:", "nber:")):
        out += f" https://doi.org/{doi}"
    elif doi.startswith("arxiv:"):
        out += f" https://arxiv.org/abs/{doi.split(':',1)[1]}"
    elif doi.startswith("nber:"):
        out += f" https://www.nber.org/papers/{doi.split(':',1)[1]}"
    return out


def fetch_url_with_status(url, log):
    """Fetch URL; log 200/301/302 as OK; 403+paywall as ACCESS_RESTRICTED."""
    try:
        r = requests.get(url, timeout=15, allow_redirects=False)
    except requests.RequestException as e:
        log.append({"url": url, "status_code": None,
                    "fetched_at": _utc_now(), "error": str(e)})
        return False
    log.append({"url": url, "status_code": r.status_code,
                "fetched_at": _utc_now()})
    if r.status_code in (200, 301, 302):
        return True
    if r.status_code == 403:
        body = r.text.lower() if r.text else ""
        if "subscribe" in body or "sign in" in body or "sign-in" in body:
            log.append({"url": url, "status": "ACCESS_RESTRICTED"})
    return False
