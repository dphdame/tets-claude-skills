"""
static_analyzer.py — CAP-06 v0.1

All 12 per-package metric extractors per static-analyzer-design.md.

Path A (default): operates on `readme_text` + minimal metadata. Source-tree
metrics (folder structure, master script, seeds, hard-coded paths, version-pin
files, LOC) are NULLED with a `measurement_notes.general_notes` flag set to
"path_a_no_source_tree".

Path B: when `package_root` (path to unzipped package dir) is passed,
source-tree metrics activate.

Output: dict matching schemas/replication-package-v1.json (strict).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import date
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "replication-package-v1"

# ---------------------------------------------------------------------------
# Metric 1: software stack (extension census)
# ---------------------------------------------------------------------------
LANGUAGE_EXTENSIONS: dict[str, list[str]] = {
    "stata":  [".do", ".ado", ".dta", ".sthlp"],
    "r":      [".r", ".rmd", ".rdata", ".rds"],
    "python": [".py", ".ipynb"],
    "matlab": [".m", ".mat"],
    "julia":  [".jl"],
    "sas":    [".sas", ".sas7bdat"],
}
EXT_TO_LANG: dict[str, str] = {
    ext: lang for lang, exts in LANGUAGE_EXTENSIONS.items() for ext in exts
}

# ---------------------------------------------------------------------------
# Metric 2: folder structure
# ---------------------------------------------------------------------------
CODE_DIR_RE   = re.compile(r"^(code|scripts|src|programs|do|analysis)$", re.IGNORECASE)
DATA_DIR_RE   = re.compile(r"^(data|datasets|raw|input|inputs)$", re.IGNORECASE)
OUTPUT_DIR_RE = re.compile(r"^(output|outputs|results|tables|figures|exhibits|out)$", re.IGNORECASE)

# ---------------------------------------------------------------------------
# Metric 4: software-version regexes
# ---------------------------------------------------------------------------
SOFTWARE_VERSION_PATTERNS = [
    re.compile(r"\bStata\s+(?:SE\s+|MP\s+|IC\s+)?\d{1,2}(?:\.\d+)?\b", re.IGNORECASE),
    re.compile(r"\bR\s+(?:version\s+)?\d+\.\d+(?:\.\d+)?\b"),
    re.compile(r"\bR\s+\d+\.\d+(?:\.\d+)?\s+\(\d{4}-\d{2}-\d{2}\)"),
    re.compile(r"\bpython\s*[=]{1,2}\s*\d+\.\d+(?:\.\d+)?\b", re.IGNORECASE),
    re.compile(r"\bPython\s+(?:version\s+)?\d+\.\d+(?:\.\d+)?\b"),
    re.compile(r"\bMATLAB\s+R\d{4}[ab]\b"),
    re.compile(r"\bJulia\s+(?:version\s+)?\d+\.\d+(?:\.\d+)?\b", re.IGNORECASE),
    re.compile(r"\bSAS\s+(?:version\s+)?\d+\.\d+\b", re.IGNORECASE),
]

# ---------------------------------------------------------------------------
# Metric 5: runtime regexes
# ---------------------------------------------------------------------------
RUNTIME_PATTERNS = [
    re.compile(r"\b\d+(?:\.\d+)?\s*(?:hour|hours|hr|hrs|h)\b", re.IGNORECASE),
    re.compile(r"\b\d+(?:\.\d+)?\s*(?:minute|minutes|min|mins)\b", re.IGNORECASE),
    re.compile(r"\b\d+(?:\.\d+)?\s*(?:second|seconds|sec|secs)\b", re.IGNORECASE),
    re.compile(r"\b\d+(?:\.\d+)?\s*(?:day|days)\s+(?:to\s+(?:run|complete|execute))\b", re.IGNORECASE),
    re.compile(r"\b(?:overnight|over\s+night)\b", re.IGNORECASE),
    re.compile(r"\brequires?\s+(?:an?\s+)?HPC\b", re.IGNORECASE),
    re.compile(r"\bhigh[-\s]?performance\s+computing\b", re.IGNORECASE),
    re.compile(r"\b(?:total\s+)?(?:run|runtime|execution)\s+time\s*[:=]\s*\d+", re.IGNORECASE),
    re.compile(r"\bestimated\s+runtime\b", re.IGNORECASE),
]

# ---------------------------------------------------------------------------
# Metric 7: master script
# ---------------------------------------------------------------------------
MASTER_SCRIPT_PATTERNS = [
    re.compile(r"^run[_\-]?all\.(do|R|r|py|sh|jl|m)$"),
    re.compile(r"^master\.(do|R|r|py|sh|jl|m)$"),
    re.compile(r"^main\.(do|R|r|py|sh|jl|m)$"),
    re.compile(r"^driver\.(do|R|r|py|sh|jl|m)$"),
    re.compile(r"^Makefile$"),
    re.compile(r"^Snakefile$"),
    re.compile(r"^workflow\.(smk|nf|sh|py)$"),
    re.compile(r"^0+_?master\."),
    re.compile(r"^00[_\-]?run\."),
]

# ---------------------------------------------------------------------------
# Metric 8: seed
# ---------------------------------------------------------------------------
SEED_PATTERNS: dict[str, list[re.Pattern]] = {
    "stata":  [re.compile(r"\bset\s+seed\s+\d+\b", re.IGNORECASE)],
    "r":      [re.compile(r"\bset\.seed\s*\(\s*\d+\s*\)")],
    "python": [
        re.compile(r"\bnp\.random\.seed\s*\(\s*\d+\s*\)"),
        re.compile(r"\brandom\.seed\s*\(\s*\d+\s*\)"),
        re.compile(r"\btorch\.manual_seed\s*\(\s*\d+\s*\)"),
        re.compile(r"\btf\.random\.set_seed\s*\(\s*\d+\s*\)"),
        re.compile(r"\bnp\.random\.default_rng\s*\(\s*\d+\s*\)"),
    ],
    "julia":  [re.compile(r"\bRandom\.seed!\s*\(\s*\d+\s*\)")],
    "matlab": [re.compile(r"\brng\s*\(\s*\d+\s*\)")],
}

# ---------------------------------------------------------------------------
# Metric 9: hard-coded paths
# ---------------------------------------------------------------------------
HARDCODED_PATH_PATTERNS = [
    re.compile(r"""[\"\'](/Users/[^\"\'\s]+)[\"\']"""),
    re.compile(r"""[\"\'](/home/[^\"\'\s]+)[\"\']"""),
    re.compile(r"""[\"\'](/data/[^\"\'\s]+)[\"\']"""),
    re.compile(r"""[\"\'](/scratch/[^\"\'\s]+)[\"\']"""),
    re.compile(r"""[\"\'](/Volumes/[^\"\'\s]+)[\"\']"""),
    re.compile(r"""[\"\']([A-Za-z]:[\\/][^\"\'\s]+)[\"\']"""),
    re.compile(r"""[\"\'](\\\\[^\"\'\s]+)[\"\']"""),
    re.compile(r"""\bcd\s+[\"\']?(/[^\s\"\']+|[A-Z]:[\\/][^\s\"\']+)[\"\']?"""),
]

# ---------------------------------------------------------------------------
# Metric 10: version pin files
# ---------------------------------------------------------------------------
VERSION_PIN_FILES = {
    "renv.lock", "requirements.txt", "Pipfile.lock", "environment.yml",
    "poetry.lock", "conda-lock.yml", "Manifest.toml", "DESCRIPTION",
    "stata_packages.txt",
}

# ---------------------------------------------------------------------------
# Metric 12: reproducibility language
# ---------------------------------------------------------------------------
REPRODUCIBILITY_RE = re.compile(
    r"\b(reproducibility|replicate|replication|reproduce|reproducible)\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# cloc language -> internal taxonomy
# ---------------------------------------------------------------------------
CLOC_LANG_MAP = {
    "Stata": "stata",
    "R": "r",
    "Python": "python",
    "Jupyter Notebook": "python",
    "MATLAB": "matlab",
    "Julia": "julia",
    "SAS": "sas",
}


# ---------------------------------------------------------------------------
# Helper: README normalization + word count
# ---------------------------------------------------------------------------
_MD_STRIP_RE = re.compile(r"[#*_`>\[\]\(\)!\-]")


def normalize_readme(text: str) -> str:
    """Strip light Markdown formatting; collapse whitespace."""
    return _MD_STRIP_RE.sub(" ", text or "")


def word_count(text: str) -> int:
    if not text:
        return 0
    return len(text.split())


# ---------------------------------------------------------------------------
# Comment stripping per language for hard-coded path detection
# ---------------------------------------------------------------------------
_STATA_COMMENT_RE = re.compile(r"(^\s*\*.*$|//.*$)", re.MULTILINE)
_R_COMMENT_RE     = re.compile(r"#.*$", re.MULTILINE)
_PY_COMMENT_RE    = re.compile(r"#.*$", re.MULTILINE)


def strip_comments(source: str, language: str) -> str:
    if language == "stata":
        return _STATA_COMMENT_RE.sub("", source)
    if language in ("r", "python", "julia"):
        return _R_COMMENT_RE.sub("", source)
    return source


# ---------------------------------------------------------------------------
# Source-tree walker (Path B only)
# ---------------------------------------------------------------------------
def walk_source_files(root: Path) -> list[tuple[Path, str]]:
    """Yield (path, language) for every recognized source file under root."""
    out: list[tuple[Path, str]] = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        lang = EXT_TO_LANG.get(p.suffix.lower())
        if lang:
            out.append((p, lang))
    return out


def folder_structure(root: Path) -> dict[str, Any]:
    """Metric 2 — top-level dir conformance, with wrapper-dir normalization."""
    candidates = [p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")]
    if len(candidates) == 1:
        candidates = [p for p in candidates[0].iterdir() if p.is_dir() and not p.name.startswith(".")]
    has_code = any(CODE_DIR_RE.match(p.name) for p in candidates)
    has_data = any(DATA_DIR_RE.match(p.name) for p in candidates)
    has_out  = any(OUTPUT_DIR_RE.match(p.name) for p in candidates)
    return {
        "has_code_dir": has_code,
        "has_data_dir": has_data,
        "has_output_dir": has_out,
        "layout_score": int(has_code) + int(has_data) + int(has_out),
    }


def master_script(root: Path) -> tuple[bool, str | None]:
    """Metric 7."""
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        for pat in MASTER_SCRIPT_PATTERNS:
            if pat.match(p.name):
                return True, str(p.relative_to(root))
    return False, None


def seed_counts(files: list[tuple[Path, str]]) -> tuple[int, dict[str, int]]:
    """Metric 8."""
    by_lang: dict[str, int] = {}
    total = 0
    for path, lang in files:
        pats = SEED_PATTERNS.get(lang)
        if not pats:
            continue
        try:
            src = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        count = sum(len(p.findall(src)) for p in pats)
        if count:
            by_lang[lang] = by_lang.get(lang, 0) + count
            total += count
    return total, by_lang


def hardcoded_path_scan(files: list[tuple[Path, str]]) -> tuple[int, list[str]]:
    """Metric 9 — returns (count, examples up to 10)."""
    examples: list[str] = []
    count = 0
    for path, lang in files:
        try:
            src = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        src = strip_comments(src, lang)
        for pat in HARDCODED_PATH_PATTERNS:
            for m in pat.finditer(src):
                count += 1
                if len(examples) < 10:
                    line_no = src[: m.start()].count("\n") + 1
                    examples.append(f"{path.name}:{line_no}: {m.group(0)[:120]}")
    return count, examples


def version_pin_scan(root: Path) -> tuple[bool, list[str], bool | None]:
    """Metric 10."""
    found: list[str] = []
    req_pins: bool | None = None
    for depth in (root, *[p for p in root.iterdir() if p.is_dir()]):
        if not depth.is_dir():
            continue
        for name in VERSION_PIN_FILES:
            p = depth / name
            if p.exists() and name not in found:
                found.append(name)
                if name == "requirements.txt":
                    try:
                        body = p.read_text(encoding="utf-8", errors="replace")
                        req_pins = any(
                            re.match(r"^[A-Za-z0-9_\-]+==.+$", ln.strip())
                            for ln in body.splitlines()
                        )
                    except Exception:
                        req_pins = None
    return bool(found), sorted(found), req_pins


def cloc_summary(root: Path) -> tuple[int, dict[str, int]]:
    """Metric 11 — shell out to cloc 2.x and parse JSON."""
    try:
        proc = subprocess.run(
            ["cloc", "--json", "--quiet", str(root)],
            capture_output=True, text=True, timeout=120,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return 0, {}
        data = json.loads(proc.stdout)
    except Exception:
        return 0, {}
    by_lang: dict[str, int] = {}
    total = 0
    for k, v in data.items():
        if k in ("header", "SUM"):
            continue
        loc = int(v.get("code", 0))
        if not loc:
            continue
        internal = CLOC_LANG_MAP.get(k, "other")
        by_lang[internal] = by_lang.get(internal, 0) + loc
        total += loc
    return total, by_lang


def primary_language(by_lang: dict[str, int]) -> tuple[str, dict[str, float]]:
    """Apply spec rule: ≥100 LOC to count; mixed if leader < 60%."""
    contenders = {k: v for k, v in by_lang.items() if v >= 100 and k != "other"}
    if not contenders:
        return "none", {}
    total = sum(contenders.values())
    shares = {k: round(v / total, 4) for k, v in contenders.items()}
    leader, leader_loc = max(
        sorted(contenders.items(), key=lambda kv: (-kv[1], kv[0])),
        key=lambda kv: kv[1],
    )
    if shares[leader] < 0.60 and len(contenders) >= 2:
        return "mixed", shares
    return leader, shares


# ---------------------------------------------------------------------------
# README-only metrics (always available)
# ---------------------------------------------------------------------------
def readme_metrics(readme_text: str | None, readme_filename: str | None) -> dict[str, Any]:
    if not readme_text:
        return {
            "present": False,
            "filename": readme_filename,
            "word_count": None,
            "das_class": None,
            "das_class_confidence": None,
            "das_evidence_quote": None,
            "das_prompt_version": None,
            "has_software_versions": False,
            "has_runtime_estimate": False,
            "has_reproducibility_language": False,
        }
    plain = normalize_readme(readme_text)
    return {
        "present": True,
        "filename": readme_filename,
        "word_count": word_count(plain),
        "das_class": None,        # filled by das_classifier downstream
        "das_class_confidence": None,
        "das_evidence_quote": None,
        "das_prompt_version": None,
        "has_software_versions": any(p.search(readme_text) for p in SOFTWARE_VERSION_PATTERNS),
        "has_runtime_estimate": any(p.search(readme_text) for p in RUNTIME_PATTERNS),
        "has_reproducibility_language": bool(REPRODUCIBILITY_RE.search(readme_text)),
    }


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------
def analyze_package(
    package_id: str,
    source_repository: str = "openicpsr",
    readme_text: str | None = None,
    readme_filename: str | None = None,
    package_root: str | Path | None = None,
    crawl_date: str | None = None,
    package_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Build a v1 record.

    Path A: pass only `readme_text` (+ optional package_metadata). Source-tree
            metrics are NULLED with a measurement_notes.general_notes flag.
    Path B: pass `package_root` to a directory containing the unzipped package.
    """
    crawl_date = crawl_date or date.today().isoformat()
    errors: list[dict[str, str]] = []
    warnings: list[str] = []
    notes: dict[str, str | None] = {
        "stata_regex_uncertainty": None,
        "readme_parse_uncertainty": None,
        "language_classification_uncertainty": None,
        "general_notes": None,
    }

    rec_readme = readme_metrics(readme_text, readme_filename)

    if package_root is None:
        # ---- Path A: README-only, source-tree metrics nulled ----
        notes["general_notes"] = "path_a_no_source_tree"
        record: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "package_id": package_id,
            "crawl_date": crawl_date,
            "source_repository": source_repository,
            "package_metadata": package_metadata or {
                "deposit_year": None, "associated_journal": None, "doi": None,
                "has_supplement": False, "size_mb": None, "file_count": None,
            },
            "primary_language": "none",
            "language_share": {},
            "folder_structure": {
                "has_code_dir": False, "has_data_dir": False,
                "has_output_dir": False, "layout_score": 0,
            },
            "readme": rec_readme,
            "code_metrics": {
                "loc_total": 0,
                "loc_by_language": {},
                "has_master_script": False,
                "master_script_path": None,
                "seed_set_count": 0,
                "seed_set_by_language": {},
                "hardcoded_paths_count": 0,
                "hardcoded_paths_examples": [],
            },
            "dependencies": {
                "has_version_pins": False,
                "version_pin_files": [],
                "requirements_txt_has_pins": None,
            },
            "measurement_notes": notes,
            "errors": errors,
            "warnings": warnings,
        }
        return record

    # ---- Path B: full source-tree analysis ----
    root = Path(package_root)
    if not root.is_dir():
        errors.append({"code": "UNZIP_FAILED", "message": f"package_root not a dir: {root}", "file": None})
        notes["general_notes"] = "path_b_root_not_directory"
        # fall through with empty source metrics
        root = None  # type: ignore

    fs = folder_structure(root) if root else {
        "has_code_dir": False, "has_data_dir": False,
        "has_output_dir": False, "layout_score": 0,
    }
    has_master, master_path = master_script(root) if root else (False, None)
    src_files = walk_source_files(root) if root else []
    seed_total, seed_by_lang = seed_counts(src_files)
    hc_count, hc_examples = hardcoded_path_scan(src_files)
    has_pins, pin_files, req_pins = version_pin_scan(root) if root else (False, [], None)
    loc_total, loc_by_lang = cloc_summary(root) if root else (0, {})
    prim_lang, lang_share = primary_language(loc_by_lang)

    if "stata" in loc_by_lang and loc_by_lang["stata"] >= 100:
        notes["stata_regex_uncertainty"] = (
            "Seed and hard-coded-path detection in Stata is regex-only; "
            "treat counts as lower bounds. 95% CI to be reported from N=10 "
            "Stata hand-coded validation set."
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "package_id": package_id,
        "crawl_date": crawl_date,
        "source_repository": source_repository,
        "package_metadata": package_metadata or {
            "deposit_year": None, "associated_journal": None, "doi": None,
            "has_supplement": False, "size_mb": None, "file_count": None,
        },
        "primary_language": prim_lang,
        "language_share": lang_share,
        "folder_structure": fs,
        "readme": rec_readme,
        "code_metrics": {
            "loc_total": loc_total,
            "loc_by_language": loc_by_lang,
            "has_master_script": has_master,
            "master_script_path": master_path,
            "seed_set_count": seed_total,
            "seed_set_by_language": seed_by_lang,
            "hardcoded_paths_count": hc_count,
            "hardcoded_paths_examples": hc_examples,
        },
        "dependencies": {
            "has_version_pins": has_pins,
            "version_pin_files": pin_files,
            "requirements_txt_has_pins": req_pins,
        },
        "measurement_notes": notes,
        "errors": errors,
        "warnings": warnings,
    }


if __name__ == "__main__":
    import argparse, sys
    ap = argparse.ArgumentParser()
    ap.add_argument("--package-id", required=True)
    ap.add_argument("--readme-text-file", default=None)
    ap.add_argument("--package-root", default=None)
    ap.add_argument("--source-repository", default="openicpsr")
    args = ap.parse_args()

    readme_text = None
    if args.readme_text_file:
        readme_text = Path(args.readme_text_file).read_text(encoding="utf-8", errors="replace")

    rec = analyze_package(
        package_id=args.package_id,
        source_repository=args.source_repository,
        readme_text=readme_text,
        package_root=args.package_root,
    )
    json.dump(rec, sys.stdout, indent=2)
    sys.stdout.write("\n")
