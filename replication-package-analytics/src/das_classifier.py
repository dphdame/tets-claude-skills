"""
das_classifier.py — CAP-06 v0.1

Anthropic-API wrapper consuming das-classifier-prompt-v1.md.
Validates output against the das-output-v1 shape.
No-ops cleanly when ANTHROPIC_API_KEY unset.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any

PROMPT_VERSION = "das-classifier-v1"
SCHEMA_VERSION = "das-output-v1"

VALID_CLASSES = {"all_public", "public_with_scripts", "restricted_with_protocol", "opaque"}
VALID_SIGNALS = {
    "public_source_named", "public_url_resolvable", "data_bundled_in_package",
    "construction_script_present", "ipums_or_public_microdata", "fsrdc_pathway",
    "dua_pathway", "proprietary_vendor_with_license_url", "irb_protocol_number",
    "aea_standard_restricted_language", "available_upon_request_only",
    "proprietary_no_vendor", "internal_administrative_no_source",
    "no_das_present", "multiple_classes_present_most_restrictive_selected",
}

DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_MAX_TOKENS = 400
API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"

# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------
SYSTEM_MESSAGE = (
    "You are a data availability statement classifier for economics replication "
    "packages. You read a README excerpt and classify the data availability "
    "statement into exactly one of four classes: all_public, public_with_scripts, "
    "restricted_with_protocol, opaque. You output strict JSON matching the provided "
    "schema. You do not score quality, judge compliance, or recommend changes; you "
    "only classify what is stated."
)

USER_TEMPLATE = """Classify the data availability statement in the following README excerpt.

Class definitions:
- all_public: Every dataset is downloadable without restriction from a public source at deposit time. Raw data are either bundled in the package or come from a stable public URL.
- public_with_scripts: Raw data are public but require scripted construction. The package provides download scripts or fetch instructions for each public source.
- restricted_with_protocol: At least one dataset is restricted, but the README provides a concrete acquisition pathway: FSRDC project number, DUA template, vendor contact and license URL, IRB protocol number, or AEA-style standard restricted-data language.
- opaque: DAS is missing, "available upon request" with no further detail, or names a restricted source without any acquisition pathway.

Rules:
1. Pick exactly one class. The most restrictive applicable class wins when a package mixes data types.
2. "Restricted with pathway" beats "opaque" even if the pathway is a bare URL.
3. Proprietary data with named vendor and licensing URL is restricted_with_protocol. Proprietary data without either is opaque.
4. If multiple statements appear, classify the package as a whole.
5. Do not infer beyond the text. If the text does not name a source, do not invent one.

Confidence:
- Output a confidence score in [0.0, 1.0]. Calibrate so that 0.9+ means "the text contains explicit signals matching the class definition," 0.6-0.9 means "signals present but ambiguous," and below 0.6 means "best guess from sparse text."

Output JSON only. No prose. No markdown fences. No leading or trailing whitespace.

README excerpt:
\"\"\"
{readme_text}
\"\"\""""


# ---------------------------------------------------------------------------
# README preprocessing
# ---------------------------------------------------------------------------
_DAS_SECTION_RE = re.compile(
    r"(?im)^\s*#{1,6}\s*(data\s+availability|data)\s*$"
)
_MD_STRIP_RE = re.compile(r"[#*_`>\[\]\(\)!]")


def preprocess_readme(text: str, max_chars: int = 8000) -> str:
    """Strip markdown, prefer explicit `## Data` / `## Data Availability` section,
    truncate to max_chars."""
    if not text:
        return ""
    # Try to slice out an explicit DAS section
    m = _DAS_SECTION_RE.search(text)
    if m:
        chunk = text[m.start():]
        # cut at next header of same or higher level
        next_h = re.search(r"\n#{1,6}\s+\S", chunk[m.end() - m.start():])
        if next_h:
            chunk = chunk[: m.end() - m.start() + next_h.start()]
        text = chunk
    text = _MD_STRIP_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


# ---------------------------------------------------------------------------
# Output validation
# ---------------------------------------------------------------------------
def validate_output(d: Any) -> tuple[bool, str | None]:
    if not isinstance(d, dict):
        return False, "not_a_dict"
    for k in ("das_class", "confidence", "evidence_quote",
              "signals_detected", "ambiguity_notes",
              "prompt_version", "schema_version"):
        if k not in d:
            return False, f"missing_field:{k}"
    if d["das_class"] not in VALID_CLASSES:
        return False, f"bad_das_class:{d['das_class']}"
    try:
        c = float(d["confidence"])
    except (TypeError, ValueError):
        return False, "bad_confidence_type"
    if not 0.0 <= c <= 1.0:
        return False, f"bad_confidence_range:{c}"
    if not isinstance(d["evidence_quote"], str) or len(d["evidence_quote"]) > 280:
        return False, "bad_evidence_quote"
    if not isinstance(d["signals_detected"], list):
        return False, "bad_signals_detected_type"
    for s in d["signals_detected"]:
        if s not in VALID_SIGNALS:
            return False, f"bad_signal:{s}"
    if d["signals_detected"] == [] and d["das_class"] != "opaque":
        return False, "empty_signals_for_non_opaque"
    if d["ambiguity_notes"] is not None and not isinstance(d["ambiguity_notes"], str):
        return False, "bad_ambiguity_notes"
    if d["prompt_version"] != PROMPT_VERSION:
        return False, f"bad_prompt_version:{d['prompt_version']}"
    if d["schema_version"] != SCHEMA_VERSION:
        return False, f"bad_schema_version:{d['schema_version']}"
    return True, None


# ---------------------------------------------------------------------------
# No-op response (API key unset)
# ---------------------------------------------------------------------------
def _noop(reason: str) -> dict[str, Any]:
    return {
        "das_class": "opaque",
        "confidence": 0.0,
        "evidence_quote": "",
        "signals_detected": ["no_das_present"],
        "ambiguity_notes": reason,
        "prompt_version": PROMPT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "_meta": {"classifier_state": reason},
    }


def _no_das_present() -> dict[str, Any]:
    """Pre-API short-circuit when README is empty/missing."""
    return {
        "das_class": "opaque",
        "confidence": 1.0,
        "evidence_quote": "",
        "signals_detected": ["no_das_present"],
        "ambiguity_notes": None,
        "prompt_version": PROMPT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "_meta": {"classifier_state": "short_circuit_no_readme"},
    }


# ---------------------------------------------------------------------------
# Main classifier
# ---------------------------------------------------------------------------
def classify_das(
    readme_text: str | None,
    package_id: str = "",
    model: str = DEFAULT_MODEL,
    max_attempts: int = 2,
    timeout_s: int = 60,
) -> dict[str, Any]:
    """
    Returns the validated das-output-v1 shape (with optional `_meta` debug key).
    No-ops if ANTHROPIC_API_KEY is unset.
    """
    if not readme_text or not readme_text.strip():
        return _no_das_present()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return _noop("api_key_unset")

    try:
        import requests  # type: ignore
    except Exception:
        return _noop("requests_not_installed")

    prompt = USER_TEMPLATE.format(readme_text=preprocess_readme(readme_text))
    deterministic_user_id = hashlib.sha256(
        f"{package_id}|{PROMPT_VERSION}".encode()
    ).hexdigest()[:32]

    body = {
        "model": model,
        "max_tokens": DEFAULT_MAX_TOKENS,
        "temperature": 0.0,
        "system": SYSTEM_MESSAGE,
        "messages": [{"role": "user", "content": prompt}],
        "metadata": {"user_id": deterministic_user_id},
    }
    headers = {
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }

    last_err: str | None = None
    for attempt in range(max_attempts):
        try:
            r = requests.post(API_URL, json=body, headers=headers, timeout=timeout_s)
            if r.status_code != 200:
                last_err = f"http_{r.status_code}"
                time.sleep(1 + attempt * 3)
                continue
            data = r.json()
            blocks = data.get("content", [])
            text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
            if text.startswith("```"):
                text = re.sub(r"^```(?:json)?", "", text).rsplit("```", 1)[0].strip()
            try:
                out = json.loads(text)
            except Exception as e:
                last_err = f"json_parse_error:{e}"
                continue
            ok, why = validate_output(out)
            if ok:
                out["_meta"] = {"classifier_state": "ok", "attempt": attempt + 1}
                return out
            last_err = f"schema_invalid:{why}"
        except Exception as e:
            last_err = f"exception:{e.__class__.__name__}"
            time.sleep(1 + attempt * 3)

    return {
        "das_class": "opaque",
        "confidence": 0.0,
        "evidence_quote": "",
        "signals_detected": ["no_das_present"],
        "ambiguity_notes": f"classifier_failed:{last_err}",
        "prompt_version": PROMPT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "_meta": {"classifier_state": "failed", "last_error": last_err},
    }


if __name__ == "__main__":
    import argparse, sys
    ap = argparse.ArgumentParser()
    ap.add_argument("--readme-file", required=True)
    ap.add_argument("--package-id", default="cli-test")
    args = ap.parse_args()
    text = Path(args.readme_file).read_text(encoding="utf-8", errors="replace")
    out = classify_das(text, package_id=args.package_id)
    json.dump(out, sys.stdout, indent=2)
    sys.stdout.write("\n")
