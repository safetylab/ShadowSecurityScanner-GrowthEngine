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
python -m pytest                                # 168 tests, no network
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
| Controller | `agents/controller/main.py` | Task queue, audit log, migrations, config validation, state export/import |
| Market Research | `agents/research/main.py` | Fetches public RSS/Atom sources, deduplicates, scores relevance |
| Content Factory | `agents/content/main.py` | Builds drafts with mandatory source links, enforces the quality gate |
| SEO | `agents/seo/main.py` | Audits the site: status, metadata, canonicals, sitemap, robots, links |
| GitHub Growth | `agents/github/main.py` | Audits the repository, snapshots counters, drafts community files |
| Distribution | `agents/distribution/main.py` | Publishes to owned channels and rebuilds the RSS feed |
| Community Radar | `agents/community/main.py` | Finds threads worth a reply; drafts one, never posts |
| Analytics | `agents/analytics/main.py` | Records measurable metrics, writes daily/weekly/monthly reports |
| Optimisation | `agents/optimization/main.py` | Turns metrics and findings into advice with evidence |
| Dashboard | `agents/dashboard/main.py` | Renders one static HTML page from the engine's tables |

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
python agents/research/main.py sources               # also shows reachability
python agents/research/main.py collect --dry-run     # nothing is written
python agents/research/main.py collect --source "Trivy Releases"
python agents/research/main.py list --all            # keep same-title items
python agents/research/main.py list --include-excluded
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
- documents declaring a DTD or entity are refused before parsing;
- each run gets a `run_id`, so a report can state what *that* run added
  rather than everything fetched in a time window.

Two per-source settings in `research_sources.json` earn their place:

- `relevance_floor` — the baseline a source vouches for. An advisory feed
  is on topic by construction, and "Kiloview E3 NDI Encoder" is a real
  advisory that matches none of our keywords.
- `environments` — where the source is reachable. CISA answers HTTP 403
  to Actions runners, so it is marked `[local]` and shows up as
  `skipped`, not `failed`, on CI.

Relevance and publishability are separate. The score says how close an
item is to what the project is about; `excluded` says it is not digest
material (a webinar, a hiring post). A real advisory that also mentions a
webinar keeps its score and is merely kept out of drafts.

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

### SEO (read-only)

```bash
python agents/seo/main.py audit --check-links
python agents/seo/main.py report
python agents/seo/main.py links
```

Walks `sitemap.xml`, checks each page and reports worst-first. It visits
politely: half a second between requests and a hard 50-page cap. It never
changes the site — fixing a finding is a human decision.

### GitHub Growth (read-only)

```bash
python agents/github/main.py audit
python agents/github/main.py report
python agents/github/main.py templates     # drafts into content/drafts/github
python agents/github/main.py snapshot      # counters only
```

Audits metadata, README, community files, release hygiene and unanswered
issues, and records a counter snapshot per run. It never opens an issue
or pull request, never messages anyone, and touches no counter.
`GITHUB_TOKEN` is optional: it only raises the rate limit.

### Distribution

```bash
python agents/distribution/main.py queue
python agents/distribution/main.py approve --slug <slug>
python agents/distribution/main.py publish --slug <slug>
python agents/distribution/main.py rss
```

Publishes only to channels the project owns. An external channel is
refused outright. The quality gate runs **again** at publish time,
because a file can be edited by hand after it was generated, and the
daily cap from `policies.yml` applies.

### Community Radar (never posts)

```bash
python agents/community/main.py scan
python agents/community/main.py list --advice reply
python agents/community/main.py draft --id <n>
python agents/community/main.py review --id <n>
```

Searches Hacker News (public Algolia API) and GitHub issues, then judges
each thread on three axes: is it about our subject, did anyone ask for
input, and is it still open. Only a thread that clears all three is worth
a draft. A draft carries an affiliation disclosure and a checklist; you
post it yourself. The agent contains no posting code, and a test asserts
that against its own source.

### Analytics

```bash
python agents/analytics/main.py collect
python agents/analytics/main.py report --period weekly
python agents/analytics/main.py series --metric github_stars
```

Records only measurable values. Each report ends with what is *not*
measured and why: there is no privacy-friendly site analytics connected,
and installs are not observable without telemetry the project does not
ship. Those numbers are left out rather than estimated.

### Optimisation and dashboard

```bash
python agents/optimization/main.py analyse
python agents/optimization/main.py report
python agents/dashboard/main.py build      # analytics/dashboard.html
```

Optimisation attaches its evidence to every recommendation, so a wrong
one can be argued with. It changes no configuration, policy or goal. The
dashboard is a single static HTML file with no scripts, fonts or CDN
requests — one that phoned home would be telemetry by the back door.

## Layout

```text
sge/            shared library: db, config, net, feeds, relevance, quality,
                state, seo, repo_health, github, hackernews, metrics, feedgen
agents/         one CLI per agent (ten of them)
config/         brand, channels, policies, keywords, community, sources, rules
database/       migrations (the schema's only source of truth)
content/        templates, drafts, social and community drafts, published
analytics/      generated reports and dashboard.html
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

A runner is temporary: `database/growth.db` does not survive between runs
and is deliberately not committed. Without state, every run would report
all ~70 collected items as new.

State therefore travels as NDJSON on the orphan `state` branch
(`state/research_keys.ndjson`): one record per line, sorted by canonical
URL so the diff stays small, holding the keys needed to recognise an item
again but not article bodies — those are ~85% of the bytes and are only
needed inside the run that collected them. Roughly 400 bytes per item,
about 10 MB a year at the current rate.

The workflow does `state-import` → `collect` → `state-export` → commit,
and only the collecting job gets `contents: write`, pushing only to
`state`. A single `concurrency` group keeps writers serialised, because
SQLite and a git branch both tolerate exactly one.

Locally the same commands move state between machines:

```bash
python agents/controller/main.py state-export --path /tmp/state.ndjson
python agents/controller/main.py state-import --path /tmp/state.ndjson
```

The `schedule:` block in the workflow stays commented out until a few
manual runs have shown the round-trip working.

## Publishing the dashboard

`agents/dashboard/main.py build` writes `analytics/dashboard.html`, which
opens from disk and is uploaded by the weekly audit as an artifact.

To serve it at a URL instead, create the branch and turn Pages on once:

```bash
git worktree add --orphan -b gh-pages /tmp/pages
cp analytics/dashboard.html /tmp/pages/index.html
touch /tmp/pages/.nojekyll
(cd /tmp/pages && git add -A && git commit -m 'Dashboard' \
   && git push origin gh-pages)

gh api -X POST repos/safetylab/ShadowSecurityScanner-GrowthEngine/pages \
   -f 'source[branch]=gh-pages' -f 'source[path]=/'
```

Then remove the `if: false` from the "Publish the dashboard to Pages"
step in `weekly-audit.yml`, and each audit refreshes the page.

**This repository is public**, so the dashboard becomes public with it,
including the open findings of every audit. If that is not wanted, keep
the artifact and leave the step disabled.

## Tests

```bash
python -m pytest            # whole suite (168 tests)
python -m pytest -k state   # one area
```

The suite never touches the network: feed behaviour — timeouts,
oversized responses, malformed XML, HTML error pages — is exercised
against a local HTTP server, and every test uses its own database via
`SGE_DB_PATH`.

## Scheduled work

| Workflow | When | What it does |
| --- | --- | --- |
| `daily-research.yml` | 06:17 UTC daily | Collects sources, writes a report, saves state |
| `weekly-audit.yml` | Mondays 07:23 UTC | Site and repository audits, radar, metrics, advice, dashboard |
| `content-drafts.yml` | Manual only | Drafts a digest and uploads it for review |

Content drafting stays manual on purpose: a draft is a claim about
reality that a person has to check before it goes anywhere.

## Status

All eleven phases of the implementation plan are implemented. What is
deliberately absent: automatic posting to communities the project does
not own, any telemetry in the scanner, and any number the engine cannot
honestly measure.

Known limits worth keeping in mind:

- the CISA feed answers HTTP 403 to Actions runners, so the full set of
  six sources is only collected from a local or residential network;
- topic classification is a keyword heuristic and leaves a large share of
  items unclassified. `config/keywords.yml` needs extending as you see
  what the feeds actually publish, followed by `research rescore`;
- relevance scoring is transparent rather than clever: it explains every
  score, and it will miss things a model would catch.
