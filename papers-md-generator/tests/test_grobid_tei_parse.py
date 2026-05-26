"""Unit tests for GROBID TEI parsing (offline, uses fixture)."""
import sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from src import grobid_client

FIXTURE = HERE.parent / "fixtures" / "cs2021_tei.xml"


def test_fixture_exists():
    assert FIXTURE.exists()


def test_parse_tei():
    xml = FIXTURE.read_text()
    root = grobid_client.parse_tei(xml)
    assert root is not None


def test_get_body_text_above_500_words():
    xml = FIXTURE.read_text()
    root = grobid_client.parse_tei(xml)
    wc = grobid_client.get_body_word_count(root)
    # Fixture is short; this test confirms whether the gate fires.
    # We expect the fixture to be above 500 to pass the body_too_short gate.
    assert wc > 500, f"fixture has {wc} words; needs >500 for pipeline"


def test_get_sections():
    xml = FIXTURE.read_text()
    root = grobid_client.parse_tei(xml)
    secs = grobid_client.get_sections(root)
    ids = [s["section_id"] for s in secs]
    assert "sec-2" in ids
    assert "sec-2-2" in ids
    assert "sec-2-3" in ids
    assert "sec-2-4" in ids


def test_find_methods_sections_excludes_intro_conclusion():
    xml = FIXTURE.read_text()
    root = grobid_client.parse_tei(xml)
    secs = grobid_client.get_sections(root)
    methods = grobid_client.find_methods_sections(secs)
    method_ids = {s["section_id"] for s in methods}
    assert "sec-1" not in method_ids  # intro
    assert "sec-6" not in method_ids  # conclusion
    # sec-2 series should appear
    assert any(sid.startswith("sec-2") for sid in method_ids)


def test_get_references():
    xml = FIXTURE.read_text()
    root = grobid_client.parse_tei(xml)
    refs = grobid_client.get_references(root)
    assert len(refs) >= 3
    # Sun & Abraham reference should be in there
    sa_refs = [r for r in refs if "Sun" in " ".join(r["authors"])]
    assert sa_refs


def test_has_methods_div():
    xml = FIXTURE.read_text()
    root = grobid_client.parse_tei(xml)
    assert grobid_client.has_methods_div(root)


def test_get_header_metadata():
    xml = FIXTURE.read_text()
    root = grobid_client.parse_tei(xml)
    h = grobid_client.get_header_metadata(root)
    assert "Callaway" in " ".join(h["authors"])
    assert h["doi"] == "10.1016/j.jeconom.2020.12.001"
    assert h["year"] == "2021"
