# Offline test fixtures

These files feed the offline unit suite (`pytest -m "not network"`) so it runs
with no network access (Requirement 16.1, 16.4). They are tracked in git despite
the global `*.json` ignore because `.gitignore` whitelists
`!scripts/tests/fixtures/**/*.json` (added in task 1.1).

Regenerate the platform fixtures and the small location map with:

```bash
python scripts/tests/fixtures/capture_fixtures.py            # live where reachable
python scripts/tests/fixtures/capture_fixtures.py --offline  # hand-built only
python scripts/tests/fixtures/capture_fixtures.py workday    # one platform
```

The script does read-only GET/POST calls, trims each response to a few postings,
and falls back to a representative hand-built fixture (matching the shapes in
`design.md` → "Research summary") when an endpoint is unreachable. Live network
calls here are expected and allowed: this is a capture tool run by hand, not part
of the offline suite.

## Provenance (last capture)

The capture script prints a provenance list at the end of each run. As last
captured:

| Fixture | Source | Notes |
|---|---|---|
| `workday/list_page1.json` | **live** | IQVIA (`iqvia\|wd1\|iqvia`), `searchText="data scientist"`, offset 0. Real `total` on page 1, real req IDs in `bulletFields`, and `"N Locations"` rows (e.g. "2 Locations"). |
| `workday/list_page2.json` | **live** | offset 20. Shows `total: 0` on later pages (the pagination bug the fetcher must tolerate, Requirement 3.2). |
| `workday/detail.json` | **live** | one posting's `jobPostingInfo` (description HTML, `jobReqId`, `location`, `additionalLocations`, `country`, `remoteType`, `timeType`). |
| `ashby/board.json` | **live** | `api.ashbyhq.com/posting-api/job-board/{slug}`. Has `descriptionPlain`, `isRemote`, `workplaceType`, `location`, `secondaryLocations`, `address.postalAddress.addressCountry`, `publishedAt`, `jobUrl`. |
| `lever/postings.json` | **live** | `/v0/postings/{slug}`. Has `workplaceType`, `country`, `categories.allLocations`, `descriptionPlain`, `createdAt`, `hostedUrl`. |
| `greenhouse/list.json` | **live** | `/v1/boards/{slug}/jobs`. Has `first_published`, `id`, `location`, `absolute_url` (no `content` in the list). |
| `greenhouse/detail.json` | **live** | `/v1/boards/{slug}/jobs/{id}?content=true`. `content` is **entity-escaped HTML** (`&lt;p&gt;...`), which the `html_to_text` tests need (Requirements 10.3, 10.8). |
| `icims/sitemap_theharrispoll.xml` | hand-built | Live endpoint returned HTTP 405 to the capture UA. Shape matches a real iCIMS `sitemap.xml` (`<urlset>` of `<url><loc>` job links). `theharrispoll` is a **bare** slug → host `careers-theharrispoll.icims.com`. |
| `icims/sitemap_careers-gbrx.xml` | hand-built | Live endpoint returned HTTP 405. `careers-gbrx` **already carries a prefix** → host `careers-gbrx.icims.com` (the host-resolution case in Requirement 4.1). |
| `icims/jobpage_theharrispoll.html` | hand-built | iCIMS job page with a JSON-LD `JobPosting` block: `title`, `description`, `datePosted`, `jobLocation[].address`, `jobLocationType`. One `jobLocation` carries the `"UNAVAILABLE"` placeholder values the enricher must drop (design Component 6). |
| `icims/jobpage_careers-gbrx.html` | hand-built | Same shape, different title, for the second tenant. |
| `bamboohr/list.json` | hand-built | Live `careers/list` was not reachable as JSON from the capture host. Shape matches the real list: `result[].{id, jobOpeningName, atsLocation{city,state,country}, isRemote, locationType, datePosted}`. |
| `bamboohr/detail.json` | hand-built | `careers/{id}/detail` → `result.jobOpening.{description, datePosted, ...}`. |
| `locations_small.json` | hand-built (curated from `data/locations.json`) | See below. |

Re-running the capture replaces these in place; the hand-built ones only change
if you edit the fallbacks in `capture_fixtures.py`. When an endpoint that was
hand-built becomes reachable again, re-running will overwrite it with live data
and update this table's "Source" column accordingly.

## BambooHR `locationType` code mapping (pinned)

BambooHR's list response encodes the workplace type as a numeric string in
`locationType`. The mapping is pinned from captured data and used by the
BambooHR fetcher (task 4.4) and the Location_Classifier:

| `locationType` | Workplace_Type |
|---|---|
| `"0"` | `onsite` |
| `"1"` | `remote` |
| `"2"` | `hybrid` |
| any other / missing | `None` (fall back to `isRemote` and `atsLocation`) |

This mapping lives in `capture_fixtures.py` as `BAMBOOHR_LOCATION_TYPE` and is
mirrored in `bamboohr/list.json` (one posting per code: `"0"`, `"1"`, `"2"`).
The `bamboohr/list.json` fixture is currently hand-built, so if a live capture
later contradicts this mapping, update both the table above and the fetcher.

## `locations_small.json`

A small GeoNames-shaped lookup extracted from `data/locations.json`, mirroring
`conftest.py`'s `_SMALL_LOCATION_ROWS` so the file fixture and the
`small_location_maps` session fixture agree. Schema matches the full file:
`["city", "admin", "country", "lat", "lng"]`, where `admin` is the GeoNames
admin1 code — the **two-letter US state code** for US rows — and is kept on every
row (Requirement 8.11).

It includes:

- **Greater Boston (inside the 30-mile radius):** Cambridge, Waltham, Burlington,
  Quincy, Framingham, Lowell (plus Boston itself).
- **New England outside the radius (Secondary_Region):** Worcester MA,
  Providence RI, Hartford CT.
- **US outside New England:** San Francisco CA, New York City NY, Chicago IL.
- **Ambiguous cities** (same name in another country): Cambridge (GB), Quincy (FR),
  Burlington (CA) — these test US-preference and ambiguity detection
  (Requirements 8.5, 8.6).
- **Non-US cities:** Toronto CA, London GB, Berlin DE.

The capture script checks each curated city against `data/locations.json` and
prints a note if any is missing, but the curated rows are authoritative because
the full file holds many same-named cities and the tests need fixed coordinates.

## Title and location case fixtures

- `titles_wanted.txt` — wanted titles (Requirement 7.19) with optional
  `expected_match | expected_reason | expected_priority` columns pinning the
  senior and priority cases (Requirements 18.8–18.10). Every row must classify
  `primary` or `maybe`.
- `titles_junk.txt` — junk titles (Requirement 7.20) with an optional
  `expected_exclusion_reason` column. Every row must classify `excluded`.
- `locations_cases.json` — Location_Classifier / Location_Filter cases
  (Requirements 9.11–9.14, 9.17, 9.18), resolved through `locations_small.json`.

## JD fixtures (`jds/*.json`)

Each file has `title`, `description`, `expected_skills`, and `must_not_match`
(Requirement 11.10), plus extra expectation fields where a requirement names an
outcome (PhD exclusion, category gate). Canonical skill names match the
Skills_Dictionary in `design.md` Component 7.

| File | Purpose |
|---|---|
| `heor_claims.json` | HEOR/claims JD with ICD-10, CPT, NDC, DRG, MarketScan (Requirements 11.9, 11.10). |
| `biostatistics.json` | Biostatistics JD with SAS, GEE, Kaplan-Meier, Cox regression, survival analysis (Requirement 11.4). |
| `epic_clarity_ordinary_words.json` | Uses "epic" and "clarity" as ordinary words; `Epic/Clarity` must NOT match (Requirement 11.4). |
| `phd_required.json` | PhD **required** phrasing → excluded `phd_required` (Requirement 20.7). |
| `phd_preferred.json` | PhD **preferred** / "Master's or PhD" → kept (Requirements 20.4, 20.7). |
| `market_research_life_sciences.json` | "Market Research Analyst" with a life-sciences description → Category `life_sciences_research` (Requirement 19.12). |
| `market_research_non_life_sciences.json` | Same title, non-life-sciences description → excluded `non_target_industry` (Requirement 19.12). |
| `research_analyst_life_sciences.json` | Research_Title with a life-sciences description → kept `life_sciences_research` (Requirement 19.2). |
