# Personalized Data / Analytics / ML Job Aggregator

A personal fork of [job-board-aggregator](https://github.com/Feashliaa/job-board-aggregator), retargeted to one job seeker's search instead of a general-purpose board.

It scrapes a curated set of companies across six ATS platforms (Greenhouse, Lever, Ashby, Workday, BambooHR, iCIMS), keeps only US data, analytics, and ML roles that fit the CV, and publishes a small static site. The scope is **Greater Boston plus US-remote**: hybrid/onsite jobs within 30 miles of Boston are primary, New England jobs outside that radius and US-remote jobs with no stated location fall into a "maybe" bucket, and everything non-US is dropped.

Every kept job is enriched with a short description-based summary and a list of extracted skills, sorted so **Data Scientist titles come first**, and grouped on the site into three categories with a freshness filter and "New" badges.

## What it does

The pipeline, per run:

1. **Fetch** the companies in each `data/<platform>_companies.json` list, in parallel, recording a per-company fetch outcome (status, HTTP code, count, truncation) in `diagnostics.json`.
2. **Filter titles** against CV-based patterns. Titles are classified `primary`, `maybe`, or `excluded` with a reason. A plain "senior"/"sr"/level-III title lands in `maybe` (reason `tier_senior_signal`) rather than being dropped; only the explicit senior terms (director, VP, principal, etc.) are excluded outright.
3. **Enrich details** for kept candidates — fetch and clean the job description so the summary, skills, category gate, and PhD check have text to work with.
4. **Gate by PhD and industry** (see below), then classify location.
5. **Apply the location rule** — keep within 30 miles of Boston, send New England and unspecified-US-remote to `maybe`, drop non-US.
6. **Summarize and extract skills** from the description.
7. **Sort** by Title_Priority (Data Scientist first), then newest, apply per-bucket caps, and build the site.

### Categories

Every kept job is assigned exactly one category (excluded jobs get none):

- **`data_scientist`** — a Data Scientist title (data scientist / data science in any form, RWE/RWD scientist, decision scientist, and applied/research scientist when the title also carries a data qualifier). Kept in any industry; shown first on the site with a "Data Scientist" badge and Title_Priority 1.
- **`life_sciences_research`** — a research or market-research title that is *not* independently a primary title, kept only when a life-sciences signal appears in the title or description.
- **`other_target`** — everything else that is kept.

The category is orthogonal to the `primary` / `maybe` bucket: a job keeps its Match_Type, Match_Reason, and Title_Priority regardless of category.

### The life-sciences gate and PhD exclusion

- **`non_target_industry`** — a research/market-research title (one that does not independently match a non-research primary pattern) is kept only if `LIFE_SCIENCES_SIGNAL_TERMS` matches the title or description. No signal means the job is excluded with reason `non_target_industry`. This **fails closed**: if the detail fetch failed and the title alone carries no signal, the job is excluded and counted under the detail-failure tally. Primary non-research titles (Data Scientist, Biostatistician, Epidemiologist, Analytics Engineer, Data Engineer) are never gated this way.
- **`phd_required`** — after enrichment, a description that requires a PhD (`PHD_REQUIRED_PATTERNS`) with no softening phrasing nearby (`PHD_SOFT_PATTERNS`) is excluded with reason `phd_required`. "PhD preferred", "Master's or PhD", "MS/PhD", "PhD or equivalent", and similar keep the job.

Skills such as SAS, Epic, or ICD-10 are **extracted and displayed only** — they are never used as filters.

## Configuration

All tuning lives in [`scripts/personal_config.py`](scripts/personal_config.py) (plus one environment variable, `JD_BACKEND`). Edit a value, rerun, and the behavior changes with no other code edits. The knobs:

### Location

| Knob | Default | What it does |
| --- | --- | --- |
| `BOSTON_CENTER_LAT` | `42.3601` | Reference latitude for the Boston radius. |
| `BOSTON_CENTER_LNG` | `-71.0589` | Reference longitude for the Boston radius. |
| `BOSTON_RADIUS_MILES` | `30` | Great-circle distance from the center within which hybrid/onsite jobs are kept as primary. |
| `SECONDARY_REGION_STATES` | `{"MA","NH","RI","CT","VT","ME"}` (New England) | US states whose hybrid/onsite jobs *outside* the radius go to the `maybe` bucket (reason `location_new_england`). Set to `set()` to turn the secondary region off, so those jobs become `outside_radius` rejects. |

### JD summarizer backend

| Knob | Default | What it does |
| --- | --- | --- |
| `JD_BACKEND` (environment variable) | `keyword` | Selects the description summarizer backend. `keyword` is the offline, network-free default. `llm` is a stub that falls back to `keyword` with one warning. An unrecognized value also falls back to `keyword` with one warning. Set it as a repository variable for the workflows, or export it locally. |

### Workday pagination

| Knob | Default | What it does |
| --- | --- | --- |
| `WORKDAY_SEARCH_TERMS` | 13 CV terms (below) | One paginated Workday query is run **per term**, deduplicated by requisition ID. Each term costs **at least one request per tenant**, so the request count per tenant scales with this list. An empty list switches to a single full-pagination query with no search text. Default terms: `data scientist`, `data science`, `data analyst`, `analytics`, `analyst`, `statistic`, `biostatistic`, `epidemiolog`, `outcomes research`, `real world`, `heor`, `insights`, `data`. |
| `WORKDAY_MAX_PAGES` | `25` | Stop a Workday query after this many pages and mark it `truncated`. |

### Detail enrichment

| Knob | Default | What it does |
| --- | --- | --- |
| `DETAIL_MAX_WORKERS` | `8` | Maximum concurrent detail-fetch requests per platform. |
| `DETAIL_MAX_PER_RUN` | `None` | Maximum detail fetches per platform per run (`None` = unlimited). Skipped fetches are counted in diagnostics. |
| `PLATFORM_TIME_BUDGET_MINUTES` | `None` (off) | Optional safety net. If set, a platform stops starting *new* company fetches once its elapsed time passes this many minutes (recorded as `not_fetched_time_budget` in `diagnostics.json`). Off by default; full runs of roughly 40 minutes are fine and do not need it. |

### Title matching

| Knob | What it does |
| --- | --- |
| `TITLE_PATTERNS` | Primary titles. Matching any of these (and not excluded) keeps the job in the primary bucket. Built around the CV role families: data science/analytics, RWE/RWD, HEOR, epidemiology, biostatistics, statistical/SAS programming, research analyst, analytics engineer, BI, market research/insights, data and ML engineering, and scientist/quant roles. |
| `MAYBE_PATTERNS` | Adjacent titles shown in a separate "maybe" bucket for manual review (clinical research coordinator/associate, clinical data manager, data manager, product analyst, operations research, data strategist, analytics/data manager). Promote good matches up into `TITLE_PATTERNS`. |
| `DATA_SCIENTIST_PATTERNS` | A kept title matching any of these gets Title_Priority 1 and is listed first. Supported by `QUALIFIED_SCIENTIST_PATTERN` and `DATA_QUALIFIER_PATTERN`, which promote "applied/research scientist" to priority 1 only when the title carries a data qualifier. |
| `SENIOR_EXCLUDE_PATTERN` | Titles matching this are excluded as too senior (`senior_excluded`): principal, director, VP/vice president, head of, chief, distinguished, staff, senior manager, group manager. "senior"/"sr" alone are deliberately **not** here — they reach the tier rule and land in `maybe`. |
| `TITLE_EXCLUDE_PATTERNS` | Titles excluded outright regardless of other matches: intern/internship, co-op, apprentice, the German `m/w/d` marker, security/cyber/SOC analyst, finance/claims analyst, help/service desk. |

### Recruiter detection and diagnostics

| Knob | Default | What it does |
| --- | --- | --- |
| `RECRUITER_ALLOWLIST` | `set()` | Company slugs that are never flagged as recruiters, even if a recruiter term appears in the slug or name. Recruiter terms are matched as whole tokens. |
| `WATCHLIST` | `set()` | Company slugs whose per-job filter decisions are written in detail to the diagnostics report, for inspection. |

### Category / PhD gates

| Knob | What it does |
| --- | --- |
| `LIFE_SCIENCES_SIGNAL_TERMS` | Terms (pharma, biotech, clinical, oncology, RWE/RWD, HEOR, payer/provider, genomics, …) that, matched in the title or description, mark a life-sciences signal. Used to gate research/market-research titles into `life_sciences_research`; absence excludes them as `non_target_industry`. |
| `PHD_REQUIRED_PATTERNS` | Phrasings that indicate a PhD is required ("phd required", "requires a phd", "must have a phd", "doctorate required", …). A match near a PhD mention excludes the job as `phd_required`. |
| `PHD_SOFT_PATTERNS` | Softening phrasings ("phd preferred", "phd a plus", "master's or phd", "ms/phd", "or equivalent", …). A match near the PhD mention vetoes the exclusion, so PhD-*preferred* jobs are kept. |

### Publishing caps

| Knob | Default | What it does |
| --- | --- | --- |
| `MAX_PUBLISHED_JOBS` | `10000` | Cap on primary-bucket jobs published. The sort runs first, so the newest, highest-priority jobs survive the cap; the rest are counted as `cap_truncated`. |
| `MAX_MAYBE_JOBS` | `2000` | Same, for the maybe bucket. |

### Site display behavior

The published site reads these from the built data; the defaults are baked into the frontend:

- **New label** — a "New" badge on any job whose `first_seen` is within the last **3 days** (`NEW_BADGE_DAYS = 3`).
- **Freshness filter** — a control (off / "Any time" by default) that limits the list to jobs whose posting date or `first_seen` is within the last **3, 7, or 14 days**.
- **Three sections** — Data Scientist (first, expanded), Life-Sciences Research / Market Research, and Other Targets, each keeping its primary/maybe split, plus a category filter. The default sort is Data Scientist first, then newest.
- Maybe jobs show a readable Match_Reason badge, including `location_new_england` (with distance), `remote_unspecified`, `tier_senior_signal`, `title_maybe`, and `title_generic_analyst`.

## Adding a company

Each platform has its own list in `data/<platform>_companies.json` — a flat JSON array of slugs, kept sorted. Add the company's slug and rerun. Use the Company_Harness (below) to confirm the slug fetches before committing.

### Greenhouse, Lever, Ashby, BambooHR

Add the bare board token used in the company's careers URL (for example `10xgenomics` for `boards.greenhouse.io/10xgenomics`) to the matching `data/greenhouse_companies.json`, `data/lever_companies.json`, `data/ashby_companies.json`, or `data/bamboohr_companies.json`.

### Workday

Workday slugs use the format **`tenant|wdN|site_id`**, three pipe-separated parts:

- `tenant` — the company's Workday tenant (for example `iqvia`).
- `wdN` — the data-center shard from the host, `wd1`–`wd5`, `wd503`, etc. (the `wdN` in `tenant.wdN.myworkdayjobs.com`). It is validated against `wd\d+`.
- `site_id` — the career-site path segment (the external site id).

Example: `iqvia|wd1|iqvia`. Entries with a malformed tenant/shard are skipped as `invalid_slug`.

### iCIMS

iCIMS careers live at `<subdomain>.icims.com`. The fetcher resolves the host from the slug using two rules:

- **Known-prefix slugs** — if the slug already starts with a known subdomain prefix (`careers-`, `jobs-`, `external-`, `uscareers-`, `global-`, `en-`, and the other prefixes in `ICIMS_PREFIXES`), it is used as-is: it tries `<slug>.icims.com` first, then falls back to `careers-<slug>.icims.com`.
- **Bare slugs** — otherwise it tries `careers-<slug>.icims.com` first, then falls back to `<slug>.icims.com`.

So add `theharrispoll` as a bare slug (resolves to `careers-theharrispoll.icims.com`), but add `careers-gbrx` with its prefix intact.

## Local development and testing

```bash
# From the repo root
pip install -r requirements.txt -r requirements-dev.txt
```

### Company_Harness

Run one company's fetcher and the full filter chain in isolation. It is scoped, so it never writes the dead-slug cache, and it prints the fetch outcome, a per-job decision table, and per-reason counts:

```bash
python scripts/diagnose_company.py <platform> <slug> [--detail] [--limit N] [--all]
```

- `<platform>` — one of `greenhouse`, `lever`, `ashby`, `workday`, `bamboohr`, `icims`.
- `--detail` — also run detail enrichment and the summarizer, and print extracted skills and the summary per kept job.
- `--limit N` — show at most N rows. `--all` — include excluded jobs (default shows kept jobs only).

Example:

```bash
python scripts/diagnose_company.py workday "iqvia|wd1|iqvia" --detail
```

### Offline test suite

The default suite needs no network — it runs against captured fixtures:

```bash
pytest -m "not network"
```

This is the suite the workflows run before scraping; a failure stops the run.

### Network test suite

The network-marked tests hit live ATS endpoints and are opt-in:

```bash
pytest -m network
```

### Scoped scraper runs

Run a slice of the scraper locally without touching shared caches:

```bash
cd scripts
python scraper.py --source manual --platforms workday,icims --max-companies 50
python scraper.py --source manual --slugs "iqvia|wd1|iqvia"
```

Any of `--platforms`, `--max-companies`, or `--slugs` marks the run scoped; scoped runs never write dead-slug caches. Every run writes `scripts/output/diagnostics.json` and prints a summary table.

## Running the workflows

Two GitHub Actions workflows drive the project:

- **Update personal job board** (`.github/workflows/update-job-board.yml`) — scrapes, builds the static site, and deploys it to GitHub Pages. Runs on a daily schedule, on pushes to `main`, on pull requests, and on manual dispatch.
- **Update Job Listings** (`.github/workflows/scrape-jobs.yml`) — the upstream data-repo pipeline (scrape → merge → trend snapshot → release / data-repo push).

### Repository variables and secrets

Set these under **Settings → Secrets and variables → Actions**:

| Name | Type | Purpose |
| --- | --- | --- |
| `DATA_REPO` | Variable | `owner/repo` of the separate data repository. Data-repo steps are gated on this being non-empty; leave it unset to skip them. |
| `JD_BACKEND` | Variable | Summarizer backend. Defaults to `keyword` when unset. |
| `SITE_URL` | Variable | Base URL of the deployed site, used to fetch the previous `jobs.json` so `first_seen` carries forward. Defaults to `https://<owner>.github.io/<repo>`. |
| `DATA_REPO_TOKEN` | Secret | Token that authenticates the force-push to the data repo. Only needed when `DATA_REPO` is set. |

### Running on a branch

Push your branch and trigger the workflow (via **Run workflow** / `workflow_dispatch`, a pull request, or a push). **Publishing is gated to `main`**: on any non-`main` ref the deploy job and the data-repo push are skipped, so a branch run builds and validates everything without touching the live site or data.

### Where to find the artifacts

Branch and PR runs upload their output as a workflow artifact instead of publishing. Open the run in the **Actions** tab and download the artifact from the run summary:

- **Update personal job board** → artifact `personal-job-board`, containing `scripts/output/all_jobs.json`, `scripts/output/diagnostics.json`, and the built `site/` directory.
- **Update Job Listings** → artifact `scraper-output`, containing `scripts/output/all_jobs.json` and `scripts/output/diagnostics.json`.

Read `diagnostics.json` to see per-platform fetch outcomes, per-reason decision counts, per-category counts, cap truncations, and (if the time budget is set) `not_fetched_time_budget` tallies.

## License

Code is licensed under the MIT License. The curated company datasets in `data/` are from the upstream project under [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/).

This is a personal fork of [job-board-aggregator](https://github.com/Feashliaa/job-board-aggregator) by [Riley Dorrington](https://github.com/Feashliaa).
