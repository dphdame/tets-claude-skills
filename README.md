# tets-claude-skills

Claude Code skills for applied economists. Three tools that each address a recurring 20-minute chore in empirical methods work, built as blueprints rather than databases — the schema and inference rules ship; the seed catalogs we publish are starters that readers extend with their own material.

The skills are introduced in a four-part series on [Too Early To Say](https://tooearlytosay.com):

- [Running Claude Code skills, for applied economists](https://tooearlytosay.com/research/methodology/claude-code-skills-setup/) — setup and a first invocation
- [A reference library for empirical methods](https://tooearlytosay.com/research/methodology/papers-md-generator/) — `/papers-md-generator`
- [A common shape for econ replication packages](https://tooearlytosay.com/research/methodology/replication-package-analytics/) — `/replication-package-analytics`
- [A field map for causal-inference methods](https://tooearlytosay.com/research/methodology/attribution-audit-network/) — `/attribution-audit-network`

## What's in here

```
tets-claude-skills/
├── papers-md-generator/              # Methods-section structure from paper PDFs
├── replication-package-analytics/    # Compliance metrics for econ replication packages
├── attribution-audit-network/        # Field map of causal-inference methods and miscitations
├── shared/
│   ├── method-taxonomy-v1.yaml       # Method-family schema (used by attribution-audit-network)
│   └── misattribution-catalog.yaml   # Known miscitation patterns (used by papers-md-generator + attribution-audit-network)
├── LICENSE
└── README.md
```

Each skill folder has its own `SKILL.md` (the Claude Code contract) and a `requirements.txt`. The `shared/` directory holds the catalog YAMLs that two of the skills read.

## Install

The skills are Claude Code skills, not standalone CLI tools. They run inside a Claude Code session and are invoked as `/papers-md-generator`, `/replication-package-analytics`, `/attribution-audit-network` at the session prompt.

### 1. Prerequisites

- [Claude Code](https://docs.claude.com/en/docs/claude-code) installed (`npm install -g @anthropic-ai/claude-code`)
- Python 3.11 or later
- For `papers-md-generator`: [Docker](https://www.docker.com/products/docker-desktop) (to run GROBID locally)

### 2. Clone

```bash
git clone https://github.com/<owner>/tets-claude-skills.git
cd tets-claude-skills
```

### 3. Copy each skill into the Claude Code skills directory

```bash
mkdir -p ~/.claude/skills
cp -r papers-md-generator ~/.claude/skills/
cp -r replication-package-analytics ~/.claude/skills/
cp -r attribution-audit-network ~/.claude/skills/
cp -r shared ~/.claude/skills/
```

The `shared/` folder must sit next to the skill folders so each skill can find the catalog YAMLs relative to its own location (`../shared/`).

### 4. Install Python dependencies

For each skill that runs Python helpers:

```bash
pip install -r ~/.claude/skills/papers-md-generator/requirements.txt
pip install -r ~/.claude/skills/replication-package-analytics/requirements.txt
pip install -r ~/.claude/skills/attribution-audit-network/requirements.txt
```

A single virtualenv works for all three.

### 5. (Optional) For `papers-md-generator`: start GROBID

```bash
docker pull lfoppiano/grobid:0.8.0
docker run -t --rm -p 8070:8070 lfoppiano/grobid:0.8.0
```

Leave this running in a separate terminal while invoking the skill.

### 6. Verify

Open a Claude Code session in any project directory:

```bash
cd ~/projects/some-working-dir
claude
```

At the session prompt, type `/help` and confirm the three skills appear in the list. Then try a smoke run of the lowest-barrier one:

```
/replication-package-analytics mode=smoke
```

## Configuration

The skills look for their companion catalogs in `../shared/` relative to the skill folder. Override paths with environment variables:

| Variable | What it overrides | Default |
|---|---|---|
| `TETS_SPEC_DIR` | Where `papers-md-generator` looks for its extractor-prompt spec | `<skill>/../shared/` |
| `TETS_CATALOG_PATH` | Where the misattribution catalog lives | `<skill>/../shared/misattribution-catalog.yaml` |
| `TETS_LANDSCAPE_BASE` | Where `replication-package-analytics` writes Wayback output | `./replication-landscape` |
| `TETS_SECRETS_PATH` | Optional local file with `SEMANTIC_SCHOLAR_API_KEY=...` | `~/.config/claude-skills/secrets.env` |
| `ANTHROPIC_API_KEY` | Claude Code authentication (or subscription login) | unset |
| `SEMANTIC_SCHOLAR_API_KEY` | Higher OpenAlex / S2 rate limits in `attribution-audit-network` | unset |

## Status

This is v0.1.0. The schemas, inference rules, and seed catalogs are stable enough to use; the public install path has been validated on a fresh Python environment but has not yet been smoke-tested across many machines. Edge cases will surface. The tool articles above explain what's still in v0.1.0 form per skill.

## Contributing

Catalog extensions, schema improvements, and bug reports are welcome.

- Bugs and installation issues: [open an issue](https://github.com/<owner>/tets-claude-skills/issues)
- Method-family entries to add to `shared/method-taxonomy-v1.yaml`: PR or [get in touch](https://tooearlytosay.com/contact/)
- Misattribution-catalog additions: PR with primary-source evidence (DOI of the citing paper + the actual claim made)

## License

MIT — see [LICENSE](LICENSE).

## Citation

Cholette, V. (2026, May 26). tets-claude-skills (v0.1.0) [Software]. Too Early To Say. https://github.com/<owner>/tets-claude-skills
