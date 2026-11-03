# Shadow Growth Engine

Automation that supports the growth of
[ShadowSecurityScanner](https://github.com/safetylab/ShadowSecurityScanner)
— collecting public security research, turning it into drafts that always
cite their sources, and reporting on what happened.

It runs on free tooling only: Python 3.12, the standard library, SQLite
and GitHub Actions. No paid APIs, no always-on server.

**What it does not do.** It never inflates stars, forks or views, never
creates fake accounts, issues or reviews, and never posts automatically
to communities it does not own. Drafts for Reddit, Hacker News, LinkedIn
and X are written for a human to read, edit and post manually. See
[`config/policies.yml`](config/policies.yml).

## Quick start

```bash
git clone https://github.com/safetylab/ShadowSecurityScanner-GrowthEngine.git
cd ShadowSecurityScanner-GrowthEngine

python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

python agents/controller/main.py config-check   # validate configuration
python agents/controller/main.py init           # create the database
python -m pytest                                # 140 tests, no network
```

Then collect research and build a draft:

```bash
python agents/research/main.py collect          # all enabled sources
python agents/research/main.py list --limit 10 --min-relevance 30
python agents/research/main.py report --days 1  # analytics/reports/

python agents/content/main.py draft --topic advisories --limit 5
python agents/content/main.py list
```

## Agents

| Agent | Entry point | What it does |
| --- | --- | --- |
| Controller | `agents/controller/main.py` | Task queue, audit log, migrations, config validation |
| Market Research | `agents/research/main.py` | Fetches public RSS/Atom sources, deduplicates, scores relevance |
| Content Factory | `agents/content/main.py` | Builds drafts with mandatory source links, enforces the quality gate |

Every agent has a `--help` on each subcommand, logs to stderr and to
`logs/sge.log`, and exits non-zero on failure.

### Controller

```bash
python agents/controller/main.py init
python agents/controller/main.py add --key research-2026-11-03 \
    --agent research --type collect --payload '{"source": "CISA"}'
python agents/controller/main.py list --status pending
python agents/controller/main.py set-status --key research-2026-11-03 \
    --status completed
python agents/controller/main.py stats
python agents/controller/main.py config-check
```

`add` is idempotent: `task_key` is the idempotency key, so re-running a
scheduled job never queues the same work twice.

### Market Research

```bash
python agents/research/main.py sources
python agents/research/main.py collect --dry-run     # nothing is written
python agents/research/main.py collect --source "Trivy Releases"
```

Sources live in [`config/research_sources.json`](config/research_sources.json).
Guarantees:

- deduplication by **canonical URL** — tracking parameters, fragments,
  default ports and trailing slashes collapse to one identifier;
- a failing source is recorded in `source_runs` and skipped; the others
  still run. The command only fails when *every* source failed;
- feeds are parsed **as they stream in** and parsing stops at
  `max_items_per_feed`. The CISA feed is ~6 MB; reading it whole only to
  truncate it produced invalid XML, which is what the earlier prototype
  did;
- a source that answers with an HTML error page is a failure, not an
  empty success;
- documents declaring a DTD or entity are refused before parsing.

### Content Factory

```bash
python agents/content/main.py draft --topic advisories --limit 5
python agents/content/main.py check --slug <slug>
python agents/content/main.py social --channel reddit --slug <slug> \
    --community "r/netsec (read the rules first)"
```

Drafts land in `content/drafts/` (digests) and `content/social/`
(external channels) and are registered in `content_items`. Nothing is
published: `social` refuses any channel that is not `draft_only`, and
every draft carries a review checklist a human must tick off.

The quality gate ([`sge/quality.py`](sge/quality.py), configured in
[`config/publishing-rules.yml`](config/publishing-rules.yml)) rejects
content that has no sources, does not link the sources it claims, lacks
the project link, still contains placeholders, or makes unverifiable
claims such as "100% detection". A rejected draft is not written to disk
at all.

## Layout

```text
sge/            shared library: db, config, net, feeds, relevance, quality
agents/         one CLI per agent
config/         brand, channels, policies, keywords, sources, publishing rules
database/       migrations (the schema's only source of truth)
content/        templates, drafts, social drafts
analytics/      generated reports
tests/          pytest suite, no network access
```

### Configuration

`config-check` validates every file and enforces the rules that matter:
external channels cannot be set to `automatic`, `paid_apis` cannot be
turned on, sources must use HTTPS, and the security and publishing policy
flags cannot be silently switched off.

## Security

- Secrets only ever come from the environment or GitHub Actions Secrets.
  `.gitignore` excludes `.env`, keys and `*.db`.
- Logs pass through a redacting filter: values of `*TOKEN*`, `*SECRET*`,
  `*KEY*` and `*PASSWORD*` environment variables and token-shaped
  strings are masked before a record is formatted.
- All outbound HTTP goes through [`sge/net.py`](sge/net.py): HTTPS-only
  URL validation, timeouts, a response size cap, a redirect policy that
  refuses non-http(s) targets, and an identifying User-Agent.
- Third-party actions are pinned to a full commit SHA.
- Workflows declare minimal `permissions` and are manual-only for now.

## State on CI

A GitHub Actions runner is temporary, so `database/growth.db` does not
survive between independent runs, and the database is deliberately not
committed. Until state is persisted somewhere safe, scheduled collection
stays disabled: `.github/workflows/daily-research.yml` is
`workflow_dispatch` only and defaults to `--dry-run`. The workflow also
uses a single `concurrency` group, because SQLite tolerates exactly one
writer.

## Tests

```bash
python -m pytest            # whole suite
python -m pytest -k feeds   # one area
```

The suite never touches the network: feed behaviour — timeouts,
oversized responses, malformed XML, HTML error pages — is exercised
against a local HTTP server, and every test uses its own database via
`SGE_DB_PATH`.

## Status

Phases 1–3 of the implementation plan are in place: foundation,
market research, content factory. Still to come: SEO audit, GitHub
growth, community radar, analytics, the optimisation agent, and
scheduled automation once CI state handling is settled.
