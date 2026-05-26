"""Quote normalization pipeline per papers-md-schema-v1.md.

Six steps in order (no other transforms):
  1. NFKC unicode normalization
  2. Soft-hyphen (U+00AD) removal
  3. Ligature decomposition (7 explicit ligatures)
  4. Linebreak-hyphenation reattachment with exception list restore
  5. Whitespace collapse
  6. (no case folding, no punctuation removal, no stemming)

Match logic: exact substring first; on failure, Levenshtein <=2 per 100 chars,
tagged `verification_method: fuzzy<=2/100`. Failures dropped.
"""
from __future__ import annotations
import re
import unicodedata
from pathlib import Path

import yaml

try:
    import Levenshtein
except ImportError:
    Levenshtein = None  # type: ignore

LIGATURES = {
    "ﬁ": "fi",
    "ﬂ": "fl",
    "ﬀ": "ff",
    "ﬃ": "ffi",
    "ﬄ": "ffl",
    "ﬅ": "ft",
    "ﬆ": "st",
}

SOFT_HYPHEN = "­"

LINEBREAK_HYPHEN_RE = re.compile(r"(\w+)-\n\s*(\w+)")
MULTISPACE_RE = re.compile(r"\s+")


def _load_hyphen_exceptions(yaml_path: Path) -> set:
    if not yaml_path.exists():
        return set()
    data = yaml.safe_load(yaml_path.read_text()) or {}
    out = set()
    for _, val in data.items():
        if isinstance(val, list):
            for item in val:
                if isinstance(item, str):
                    out.add(item.strip().lower())
    return out


def load_hyphen_exceptions(yaml_path):
    return _load_hyphen_exceptions(Path(yaml_path))


def normalize(text, hyphen_exceptions=None):
    """Apply the six-step normalization pipeline."""
    if text is None:
        return ""
    if hyphen_exceptions is None:
        hyphen_exceptions = set()

    # Step 1: NFKC
    s = unicodedata.normalize("NFKC", text)
    # Step 2: soft-hyphen removal
    s = s.replace(SOFT_HYPHEN, "")
    # Step 3: ligature decomposition
    for lig, repl in LIGATURES.items():
        s = s.replace(lig, repl)
    # Step 4: linebreak-hyphenation reattachment with exception restore
    def _collapse(m):
        a, b = m.group(1), m.group(2)
        hy = f"{a}-{b}".lower()
        col = f"{a}{b}".lower()
        if hy in hyphen_exceptions or col in hyphen_exceptions:
            return f"{a}-{b}"
        return f"{a}{b}"
    s = LINEBREAK_HYPHEN_RE.sub(_collapse, s)
    # Step 5: whitespace collapse
    s = MULTISPACE_RE.sub(" ", s).strip()
    return s


def exact_match(quote, haystack):
    return quote in haystack


def fuzzy_match(quote, haystack, max_edits_per_100=2):
    """Sliding-window Levenshtein. Returns (matched, best_distance)."""
    if not quote:
        return False, None
    if Levenshtein is None:
        raise RuntimeError("python-Levenshtein not installed.")
    qlen = len(quote)
    threshold = max(1, (max_edits_per_100 * qlen + 99) // 100)
    best = None
    if qlen > len(haystack):
        return False, None
    for start in range(0, len(haystack) - qlen + 1):
        window = haystack[start:start + qlen]
        d = Levenshtein.distance(quote, window)
        if best is None or d < best:
            best = d
            if best <= threshold:
                return True, best
    return False, best


def verify_quote(candidate, haystack_normalized, hyphen_exceptions):
    """Run candidate through normalization + match. Returns (method, normalized)."""
    if not candidate or not candidate.strip():
        return "FAIL", None
    norm = normalize(candidate, hyphen_exceptions=hyphen_exceptions)
    if exact_match(norm, haystack_normalized):
        return "exact", norm
    matched, _ = fuzzy_match(norm, haystack_normalized)
    if matched:
        return "fuzzy<=2/100", norm
    return "FAIL", None
