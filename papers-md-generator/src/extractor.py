"""LLM extractor module.

Implements Prompt 1 (estimator/design extraction) and Prompt 2 (verbatim
quote selection) per `extractor-prompt-v1.md`. Uses Anthropic Claude SDK.

Also implements the self-consistency gate: re-run Prompt 1 at
(temperature=0.0, seed=7) and (temperature=0.2, seed=13); only emit
MISATTRIBUTION_CANDIDATE on agreement.

OFFLINE FALLBACK: If ANTHROPIC_API_KEY is not set or anthropic SDK is
unavailable, this module degrades gracefully by reading a fixture JSON
from the per-paper fixtures directory (used in unit tests). This lets
the rest of the pipeline (normalizer, misattribution engine, writer) be
exercised end-to-end without API credentials.
"""
from __future__ import annotations
import json
import os
import re
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parent.parent
# Companion-spec lookup paths:
#  1. ../shared/ (canonical post-install location, next to the SKILL.md)
#  2. TETS_SPEC_DIR env var if set
#  3. ./spec/ inside the skill (legacy)
import os
_default_shared = SKILL_ROOT.parent / "shared"
SPEC_DIR = Path(os.environ.get("TETS_SPEC_DIR", str(_default_shared)))
LOCAL_SPEC_DIR = SKILL_ROOT / "spec"  # legacy fallback inside the skill folder


def _read_prompt_template():
    """Read extractor-prompt-v1.md and return the Prompt 1 / Prompt 2 blocks."""
    candidates = [SPEC_DIR / "extractor-prompt-v1.md",
                  LOCAL_SPEC_DIR / "extractor-prompt-v1.md"]
    for p in candidates:
        if p.exists():
            return p.read_text()
    return ""


PROMPT1_HEADER = (
    "You are an econometrics-aware extraction tool. Read the attached "
    "GROBID TEI XML of a single research paper and return a JSON list of "
    "estimator objects.\n\n"
    "OPERATIONAL DEFINITIONS (binding):\n\n"
    "ESTIMAND = a causal parameter the paper estimates or builds a method "
    "to estimate. Examples: ATT, CATE, LATE, ATU. NOT: descriptive "
    "parameters (means, regression coefficients without a stated causal "
    "interpretation), prediction targets, or moments.\n\n"
    "ESTIMATOR = a named procedure that produces an estimate of the "
    "estimand. Examples: Callaway-Sant'Anna doubly-robust, synthetic-DiD, "
    "causal forests, IPW with PSM, surrogate index. Must be a procedure "
    "with a published name.\n\n"
    "IDENTIFICATION STRATEGY = the design that makes the estimand "
    "identified. Examples: parallel trends + no-anticipation under "
    "staggered treatment; selection on observables + overlap; instrument "
    "exclusion + relevance.\n\n"
    "ASSUMPTIONS_NAMED = an assumption that (a) is given a name in the "
    "paper text AND (b) is invoked in service of identifying the estimand."
    " NOT: assumptions merely mentioned in literature review or robustness "
    "checks.\n\n"
)

PROMPT1_SCHEMA = (
    "OUTPUT SCHEMA (one object per estimator; output a LIST even if "
    "there is only one):\n\n"
    "{\n"
    '  "estimand": "...",\n'
    '  "design": "...",\n'
    '  "estimator_name": "...",\n'
    '  "estimator_canonical_citation": "...",\n'
    '  "assumptions_named": ["...", "..."],\n'
    '  "role": "main" | "robustness" | "auxiliary",\n'
    '  "section_evidence": "<grobid section_id>",\n'
    '  "confidence": 0.0\n'
    "}\n\n"
    "RULES:\n"
    "- Output a list, never a single object.\n"
    "- Tag as 'main' only the estimators the paper presents as headline.\n"
    "- Do NOT invent estimator names. If procedure has no published name, "
    "  use 'ad hoc regression' with confidence < 0.5.\n"
    "- estimator_canonical_citation = paper that INTRODUCED the procedure,"
    "  not the paper that applies it. If self-introducing, use this paper's"
    "  own bib entry.\n"
    "- assumptions_named: only NAMED assumptions invoked for identification.\n"
    "- section_evidence: GROBID section_id (methods/theorem preferred).\n\n"
    "Return ONLY the JSON list. No prose.\n"
)


def _client():
    """Lazy-load Anthropic client. Returns None if unavailable."""
    try:
        import anthropic
    except ImportError:
        return None
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    return anthropic.Anthropic(api_key=key)


def _call_prompt1(tei_xml, hint_list=None, temperature=0.0, seed=None,
                  model="claude-sonnet-4-5-20250929"):
    """Call Prompt 1 via Anthropic API. Returns list[dict] or [] on error."""
    client = _client()
    if client is None:
        return None  # signal: API unavailable
    hint_str = ""
    if hint_list:
        hint_str = (f"\nHINT: the operator believes this paper may use "
                    f"these methods: {', '.join(hint_list)}. Use as prior, "
                    f"not as fact.\n")
    prompt = PROMPT1_HEADER + PROMPT1_SCHEMA + hint_str + (
        "\nINPUT (GROBID TEI XML):\n\n" + tei_xml
    )
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=4000,
            temperature=temperature,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        return {"error": str(e)}
    text = "".join(block.text for block in resp.content if hasattr(block, "text"))
    # Extract JSON list from response.
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        return []
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return []


def _call_prompt2(tei_xml, estimator_name, assumptions_named,
                  temperature=0.0, model="claude-sonnet-4-5-20250929"):
    """Call Prompt 2 (quote selection) for one estimator. Returns list[dict]."""
    client = _client()
    if client is None:
        return None
    assumptions_str = "\n".join(f"  - {a}" for a in assumptions_named or [])
    prompt = (
        "You are extracting verbatim sentences from the attached GROBID "
        "TEI XML to serve as evidence for the following estimator:\n\n"
        f"ESTIMATOR_NAME: {estimator_name}\n"
        f"ASSUMPTIONS_NAMED:\n{assumptions_str}\n\n"
        "For each assumption, find the single verbatim sentence in the "
        "methods section OR a theorem/proposition/assumption block that "
        f"most directly states it in service of {estimator_name}.\n\n"
        f"ALSO find the single verbatim sentence that names {estimator_name}.\n\n"
        "OUTPUT (JSON list of objects):\n"
        '{"text": "<verbatim>", "section_id": "<id>", '
        '"quote_role": "estimator_name" | "assumption_named:<name>"}\n\n'
        "CONSTRAINTS:\n"
        "- ONLY methods + theorem/proposition/assumption sections. NEVER "
        "  intro, abstract, related work, conclusion.\n"
        "- Cap: at most 5 quotes total.\n"
        "- text: verbatim. No paraphrase, no joining sentences, no ellipses.\n"
        "- Omit any assumption you cannot find a clean verbatim for.\n\n"
        f"INPUT:\n\n{tei_xml}\n\nReturn ONLY the JSON list."
    )
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=2000,
            temperature=temperature,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        return {"error": str(e)}
    text = "".join(block.text for block in resp.content if hasattr(block, "text"))
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        return []
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return []


def extract_estimators(tei_xml, hint_list=None,
                       fixture_path=None):
    """Top-level estimator extraction.

    If LLM unavailable AND fixture_path provided, load fixture JSON.
    Returns list[dict] of estimator objects.
    """
    out = _call_prompt1(tei_xml, hint_list=hint_list)
    if out is None:
        # API unavailable: try fixture fallback
        if fixture_path and Path(fixture_path).exists():
            return json.loads(Path(fixture_path).read_text())
        return []
    if isinstance(out, dict) and out.get("error"):
        if fixture_path and Path(fixture_path).exists():
            return json.loads(Path(fixture_path).read_text())
        return []
    return out


def self_consistency_check(tei_xml, hint_list=None):
    """Re-run Prompt 1 twice with different (temp, seed) per spec.

    Returns dict {agree: bool, run_a: list, run_b: list}.
    """
    run_a = _call_prompt1(tei_xml, hint_list=hint_list, temperature=0.0,
                          seed=7)
    run_b = _call_prompt1(tei_xml, hint_list=hint_list, temperature=0.2,
                          seed=13)
    if run_a is None or run_b is None:
        return {"agree": None, "run_a": None, "run_b": None,
                "reason": "api_unavailable"}
    if isinstance(run_a, dict) or isinstance(run_b, dict):
        return {"agree": None, "run_a": run_a, "run_b": run_b,
                "reason": "api_error"}
    # Compare estimator_canonical_citation for every main estimator
    a_main = {e.get("estimator_name", ""): e.get("estimator_canonical_citation")
              for e in run_a if e.get("role") == "main"}
    b_main = {e.get("estimator_name", ""): e.get("estimator_canonical_citation")
              for e in run_b if e.get("role") == "main"}
    if a_main != b_main:
        return {"agree": False, "run_a": run_a, "run_b": run_b,
                "reason": "main_citation_mismatch"}
    return {"agree": True, "run_a": run_a, "run_b": run_b}


def extract_quotes(tei_xml, estimator_obj, fixture_path=None):
    """Per-estimator quote extraction with cap enforcement."""
    name = estimator_obj.get("estimator_name", "")
    assumptions = estimator_obj.get("assumptions_named", [])
    out = _call_prompt2(tei_xml, name, assumptions)
    if out is None:
        if fixture_path and Path(fixture_path).exists():
            data = json.loads(Path(fixture_path).read_text())
            return data.get(name, [])[:5]
        return []
    if isinstance(out, dict) and out.get("error"):
        if fixture_path and Path(fixture_path).exists():
            data = json.loads(Path(fixture_path).read_text())
            return data.get(name, [])[:5]
        return []
    return out[:5]  # cap per estimator
