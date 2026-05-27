"""GROBID HTTP client. Treats GROBID as a black box.

Endpoints (per grobid-setup.md):
  POST /api/processFulltextDocument
  POST /api/processReferences
  POST /api/processHeaderDocument
  GET  /api/isalive
  GET  /api/version
"""
from __future__ import annotations
import re
from pathlib import Path

import requests
from lxml import etree

TEI_NS = {"tei": "http://www.tei-c.org/ns/1.0"}


class GrobidUnavailable(RuntimeError):
    pass


class GrobidClient:
    def __init__(self, base_url="http://localhost:8070", timeout=120):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def alive(self):
        try:
            r = requests.get(f"{self.base_url}/api/isalive", timeout=5)
            return r.status_code == 200 and r.text.strip().lower() == "true"
        except requests.RequestException:
            return False

    def version(self):
        try:
            r = requests.get(f"{self.base_url}/api/version", timeout=5)
            return r.text.strip()
        except requests.RequestException:
            return None

    def process_fulltext(self, pdf_path, consolidate_header=1,
                         consolidate_citations=1, include_raw=1,
                         coordinates="ref,biblStruct,figure,formula,head"):
        """Submit PDF to /api/processFulltextDocument.

        Returns TEI XML string. Raises GrobidUnavailable on failure.
        """
        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            raise FileNotFoundError(pdf_path)
        if not self.alive():
            raise GrobidUnavailable(
                f"GROBID not responding at {self.base_url}/api/isalive"
            )
        with open(pdf_path, "rb") as f:
            files = {"input": (pdf_path.name, f, "application/pdf")}
            data = {
                "consolidateHeader": str(consolidate_header),
                "consolidateCitations": str(consolidate_citations),
                "includeRawCitations": str(include_raw),
                "teiCoordinates": coordinates,
            }
            r = requests.post(
                f"{self.base_url}/api/processFulltextDocument",
                files=files,
                data=data,
                timeout=self.timeout,
                headers={"Accept": "application/xml"},
            )
        if r.status_code != 200:
            raise GrobidUnavailable(
                f"GROBID returned {r.status_code}: {r.text[:200]}"
            )
        return r.text


# ----- TEI parsing helpers (offline; no GROBID call required) -----

def parse_tei(tei_xml):
    """Parse TEI XML string to lxml ElementTree root."""
    if isinstance(tei_xml, str):
        tei_xml = tei_xml.encode("utf-8")
    return etree.fromstring(tei_xml)


def get_body_text(root):
    """Concatenate all text under <text><body>...</body></text>."""
    nodes = root.xpath("//tei:text/tei:body//text()", namespaces=TEI_NS)
    return " ".join(s.strip() for s in nodes if s.strip())


def get_body_word_count(root):
    text = get_body_text(root)
    return len(re.findall(r"\w+", text))


def get_sections(root):
    """Return list of {section_id, head, text, page} for all <div> in body.

    section_id is the @xml:id when present, else synthesized from position.
    """
    sections = []
    divs = root.xpath("//tei:text/tei:body//tei:div", namespaces=TEI_NS)
    for i, div in enumerate(divs):
        xml_id = div.get("{http://www.w3.org/XML/1998/namespace}id") or f"sec-{i+1}"
        head_nodes = div.xpath("./tei:head", namespaces=TEI_NS)
        head = head_nodes[0].text.strip() if head_nodes and head_nodes[0].text else ""
        # type attr (methods, results, etc.)
        sec_type = div.get("type") or ""
        # All text in this div
        texts = div.xpath(".//text()", namespaces=TEI_NS)
        body_text = " ".join(t.strip() for t in texts if t.strip())
        # Page from coords on head if present
        page = None
        if head_nodes:
            coords = head_nodes[0].get("coords")
            if coords:
                # GROBID coords format: page,x,y,w,h (or list ;-separated)
                first = coords.split(";")[0]
                parts = first.split(",")
                if parts and parts[0].isdigit():
                    page = int(parts[0])
        sections.append({
            "section_id": xml_id,
            "head": head,
            "type": sec_type,
            "text": body_text,
            "page": page,
        })
    return sections


def find_methods_sections(sections):
    """Return sections likely to be methods/theorem/assumption blocks.

    Per spec quote-source constraint: ONLY methods + theorem/proposition/
    assumption blocks; NEVER intro, related work, conclusion.
    """
    methods_heads = (
        "method", "theorem", "proposition", "assumption", "lemma",
        "identification", "model", "estimation", "estimator", "setup",
        "framework",
    )
    forbidden_heads = (
        "introduction", "intro", "related work", "literature",
        "conclusion", "discussion", "background", "summary",
        "acknowledg", "appendix", "abstract",
    )
    out = []
    for s in sections:
        head_lo = s["head"].lower()
        sec_type_lo = (s["type"] or "").lower()
        if any(f in head_lo for f in forbidden_heads):
            continue
        if sec_type_lo in ("methods", "method"):
            out.append(s)
            continue
        if any(m in head_lo for m in methods_heads):
            out.append(s)
    return out


def has_methods_div(root):
    """True iff TEI has either an explicit <div type='methods'> or a head
    matching the methods/theorem heuristic used by find_methods_sections.

    GROBID 0.9.0-crf and earlier do not emit type attributes on body divs.
    Falling back to head-text heuristic recovers methods sections from real
    papers whose authors use natural language ("Estimation", "Identification
    strategy", "Empirical framework") rather than the literal head "Methods".
    """
    if root.xpath("//tei:div[@type='methods']", namespaces=TEI_NS):
        return True
    sections = get_sections(root)
    return len(find_methods_sections(sections)) > 0


def get_references(root):
    """Return list of {raw, title, authors, year, doi} from <biblStruct>."""
    refs = []
    bibls = root.xpath("//tei:listBibl/tei:biblStruct", namespaces=TEI_NS)
    for b in bibls:
        # Raw
        raw_nodes = b.xpath(".//tei:note[@type='raw_reference']/text()",
                            namespaces=TEI_NS)
        raw = raw_nodes[0] if raw_nodes else ""
        # Title
        title_nodes = b.xpath(".//tei:title[@type='main']/text()",
                              namespaces=TEI_NS)
        title = title_nodes[0] if title_nodes else ""
        # Authors
        author_names = []
        for pers in b.xpath(".//tei:author/tei:persName", namespaces=TEI_NS):
            surname = pers.xpath("./tei:surname/text()", namespaces=TEI_NS)
            forename = pers.xpath("./tei:forename/text()", namespaces=TEI_NS)
            sn = surname[0] if surname else ""
            fn = forename[0] if forename else ""
            if sn:
                author_names.append(f"{sn}, {fn[:1]}." if fn else sn)
        # Year
        year_nodes = b.xpath(".//tei:imprint/tei:date/@when",
                             namespaces=TEI_NS)
        year = year_nodes[0][:4] if year_nodes else ""
        # DOI
        doi_nodes = b.xpath(".//tei:idno[@type='DOI']/text()",
                            namespaces=TEI_NS)
        doi = doi_nodes[0] if doi_nodes else ""
        refs.append({
            "raw": raw,
            "title": title,
            "authors": author_names,
            "year": year,
            "doi": doi,
        })
    return refs


def get_header_metadata(root):
    """Return {title, authors, doi, year, journal} from <teiHeader>."""
    title_nodes = root.xpath(
        "//tei:teiHeader//tei:titleStmt/tei:title/text()", namespaces=TEI_NS
    )
    title = title_nodes[0].strip() if title_nodes else ""
    authors = []
    for pers in root.xpath(
        "//tei:teiHeader//tei:fileDesc//tei:author/tei:persName",
        namespaces=TEI_NS,
    ):
        surname = pers.xpath("./tei:surname/text()", namespaces=TEI_NS)
        forename = pers.xpath("./tei:forename/text()", namespaces=TEI_NS)
        sn = surname[0] if surname else ""
        fn = forename[0] if forename else ""
        if sn:
            authors.append(f"{sn}, {fn[:1]}." if fn else sn)
    doi_nodes = root.xpath(
        "//tei:teiHeader//tei:idno[@type='DOI']/text()", namespaces=TEI_NS
    )
    doi = doi_nodes[0].strip() if doi_nodes else ""
    year_nodes = root.xpath(
        "//tei:teiHeader//tei:date/@when", namespaces=TEI_NS
    )
    year = year_nodes[0][:4] if year_nodes else ""
    journal_nodes = root.xpath(
        "//tei:teiHeader//tei:monogr/tei:title/text()", namespaces=TEI_NS
    )
    journal = journal_nodes[0].strip() if journal_nodes else ""
    return {
        "title": title,
        "authors": authors,
        "doi": doi,
        "year": year,
        "journal": journal,
    }
