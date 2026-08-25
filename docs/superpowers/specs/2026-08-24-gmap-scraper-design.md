# Google Maps Club Scraper — Design

**Date:** 2026-08-24
**Status:** Design approved; implementation plan pending

## 1. Purpose

A single editable Python script that collects business listings for padel and
sports clubs across all 50 US states from Google Maps, enriches each listing
with contact details taken from the club's own website, and writes the result
into a Google Sheet. The output is an outreach list.

## 2. Scope

In scope:

- Scrape Google Maps search results for each (search term x US state) pair.
- Open each listing's detail panel and extract the core business record.
- Fetch each club's own website to extract emails, phone numbers,
  owner-adjacent contacts, and social links.
- Upsert rows into a Google Sheet via Application Default Credentials.
- Write a local CSV backup and a resume cache on every run.

Out of scope (v1):

- The Google Places API. Deliberately rejected in favour of browser scraping.
- Personal contact data from people-search sites or data brokers. Only
  information the club publishes on its own website is collected.
- Scheduling. Running the script on a timer is the operator's concern.
- Any web UI, dashboard, or database beyond the Sheet and the CSV.
- Proxy rotation. Added only if Google starts blocking the run.

## 3. Decisions

| Decision | Choice | Rationale |
| --- | --- | --- |
| Data source | Browser scraping of `google.com/maps` | Chosen by the operator over the Places API. No API cost, no key, and every field visible in the UI is reachable. Accepted trade-offs: violates Google's Terms of Service, and breaks when Google changes its markup. |
| Browser driver | Playwright, headless Chromium | Auto-waits on elements, which removes the `sleep()`-based timing code that makes Selenium scrapers flaky. Ships its own browser build. |
| Geographic slicing | One query per state, 50 per term | A Maps results panel caps at roughly 120 listings, so coverage is a partitioning problem. Padel venues cluster into a few metros, so state-level slicing is expected to be near-complete for padel specifically. |
| Listing extraction | Click into each listing's detail panel | Results cards omit phone and website for most venues, and card parsing depends on randomized class names. The detail panel exposes stable semantic attributes. |
| Element selection | `data-item-id` attributes, not CSS classes | Google's class names (`hfpxzc`, `MW4etd`) are build artifacts that change every few weeks. `data-item-id` values are semantic and have been stable for years. This is the single largest factor in the script's shelf life. |
| File layout | One script, `scrape.py` | The operator asked for a simple script. A single file with four marked sections is easier to hand-edit than a package. |
| Sheets auth | Application Default Credentials via gcloud | Google discourages downloadable service-account keys. ADC keeps no key file in the project, and because the script runs as the sheet's owner there is no sharing step to get wrong. Trade-off: requires the gcloud CLI and cannot run unattended on a server. |
| Sheet write mode | Upsert keyed on place ID | Re-runs update existing rows rather than appending duplicates, so the sheet stays usable as a living list. |

## 4. Architecture

```
CONFIG (top of scrape.py)
   |
   v
Stage 1: Maps scrape          Playwright + Chromium
   for term in SEARCH_TERMS:
     for state in STATES:
       open search URL -> scroll feed -> click each listing
       -> read detail panel -> append to cache.jsonl
   |
   v
Stage 2: Website enrichment   httpx + BeautifulSoup
   for each record with a website:
     fetch homepage -> follow contact/about/team links
     -> extract emails, phones, owner heuristic, socials
   |
   v
Stage 3: Sheets write         gspread + google-auth
   load existing rows -> upsert by place_key -> batched update
   also write data/results.csv
```

A bare `python scrape.py` runs all three stages in order in one process. Each
stage reads and writes `data/cache.jsonl`, so any stage can also be re-run on
its own without repeating the ones before it.

## 5. File layout

```
gmap-scraper/
├── scrape.py            # CONFIG block + three stages
├── requirements.txt
├── README.md            # setup, including gcloud auth steps
├── .gitignore
├── tests/
│   ├── test_parsing.py
│   └── fixtures/        # saved HTML for parser tests
└── data/                # gitignored
    ├── cache.jsonl
    └── results.csv
```

## 6. Configuration

A single block at the top of `scrape.py` is the only part expected to be
edited during normal use:

```python
SEARCH_TERMS = ["padel club", "padel court", "padel tennis"]
STATES       = ALL_50            # or ["Texas", "Florida"]
SHEET_URL    = "https://docs.google.com/spreadsheets/d/1hq6DPxz2j59HHPj8VMmtH5Vl7TnlZyPG4nCy_0Lonfk/edit"
WORKSHEET    = "clubs"
ENRICH_SITES = True
HEADLESS     = True
```

`SEARCH_TERMS` ships with padel defaults. The operator will replace this list
with their own terms; the defaults exist so the script runs out of the box.

## 7. Data model

One row per club. Columns, in sheet order:

| Column | Source | Notes |
| --- | --- | --- |
| `place_key` | Maps URL | Stable hex feature ID parsed from the `!1s0x...:0x...` segment. Upsert key. |
| `name` | Detail panel `h1` | |
| `category` | Detail panel category button | e.g. "Padel club" |
| `address` | `button[data-item-id="address"]` | Full formatted address |
| `city`, `state`, `zip` | Parsed from `address` | Best-effort split |
| `phone` | `button[data-item-id^="phone:tel:"]` | The listed business number |
| `website` | `a[data-item-id="authority"]` | |
| `rating`, `reviews` | Detail panel header | Floats and ints; blank when unrated |
| `latitude`, `longitude` | Maps URL `!3d<lat>!4d<lng>` | |
| `maps_url` | Page URL | |
| `emails` | Stage 2 | Semicolon-joined |
| `owner_name`, `owner_phone` | Stage 2 heuristic | Often blank; see section 9 |
| `other_phones` | Stage 2 | Semicolon-joined, excludes `phone` |
| `instagram`, `facebook`, `linkedin` | Stage 2 | |
| `search_term`, `search_state` | Stage 1 | Provenance, useful for debugging coverage |
| `scraped_at` | Stage 1 | ISO 8601 UTC |

## 8. Stage 1 — Maps scrape

For each `(term, state)` pair:

1. Navigate to
   `https://www.google.com/maps/search/{quote(term + " in " + state)}?hl=en&gl=us`.
2. If a cookie-consent interstitial appears, accept it and continue.
3. Wait for the results feed, `div[role="feed"]`.
4. Scroll the feed in a loop until either the end-of-list sentinel appears or
   the listing count stops increasing across three consecutive scrolls.
5. Collect each listing's href, then visit each in turn and read the detail
   panel using these selectors:
   - address: `button[data-item-id="address"]`, value in `aria-label`
   - phone: `button[data-item-id^="phone:tel:"]`, value in the attribute suffix
   - website: `a[data-item-id="authority"]`, value in `href`
   - name: the panel `h1`
   - rating and review count: the header block adjacent to the `h1`
6. Append each record to `data/cache.jsonl` immediately.

Class-name-based selectors for the feed and its cards will be verified against
the live page during implementation and isolated into named constants so a
future break is a one-line fix.

Pacing: 1–3s randomized between listings, 5–10s between queries, a realistic
desktop user agent, and a 1280x900 viewport.

Blocking: if a CAPTCHA or "before you continue" interstitial is detected, the
run pauses and logs the URL. Setting `HEADLESS = False` lets the operator
solve it by hand and let the run continue.

Deduplication: records are keyed on `place_key`. The same club found under two
terms or two states is written once, with the first-seen provenance retained.

## 9. Stage 2 — Website enrichment

For each record with a `website`:

1. GET the homepage with a 15s timeout, following redirects.
2. Collect same-domain links whose href or anchor text matches
   `contact|about|team|staff|coaches`, and fetch up to three of them.
3. From the combined text and HTML of those pages:
   - **Emails**: regex matches plus `mailto:` hrefs. Discard any whose local
     part is `noreply`, `no-reply`, or `donotreply`; whose domain is a known
     platform artifact (`sentry.io`, `wixpress.com`, `example.com`); which end
     in an image extension; or whose local part is a long hex string.
   - **Phones**: North American formats plus `tel:` hrefs, normalized to
     `+1XXXXXXXXXX`.
   - **Owner heuristic**: for each phone match, take a 120-character window on
     either side. If that window contains `owner`, `founder`, `co-founder`,
     `proprietor`, `general manager`, `club manager`, `director`, `president`,
     `principal`, or `CEO`, treat it as a candidate and look for an adjacent
     two-token Title Case name, preferring one that precedes the phone. The
     first candidate populates `owner_name` and `owner_phone`; every other
     number goes to `other_phones`.
   - **Socials**: first `instagram.com`, `facebook.com`, and `linkedin.com`
     link found.

Expected owner hit rate is 20–35%. Most clubs publish only a general contact
number, and `owner_name` / `owner_phone` will be blank for those. This is a
known and accepted limitation, not a defect.

Failures (timeout, TLS error, 404) are recorded on the row and never abort the
run.

## 10. Stage 3 — Sheets write

1. Authenticate with `google.auth.default()`, which reads the Application
   Default Credentials written by `gcloud auth application-default login`.
2. Open the sheet by URL; create the worksheet and write the header row if it
   does not exist.
3. Read the existing `place_key` column into a map of key to row number.
4. For each record: update the existing row in place, or append.
5. Batch writes in chunks of 500 rows to stay inside gspread's quota.
6. Write `data/results.csv` unconditionally, so a Sheets failure never costs a
   scrape.

## 11. Resume and resilience

- Every listing is appended to `data/cache.jsonl` the moment it is read, so a
  crash three hours in loses nothing.
- Completed `(term, state)` pairs are recorded in the cache and skipped on
  re-run.
- Navigation failures retry three times with exponential backoff, then the
  pair is logged as failed and the run moves on.
- `--force` re-scrapes pairs already in the cache.

## 12. CLI

```
python scrape.py                       # full run, all terms and states
python scrape.py --states TX,FL        # subset of states
python scrape.py --limit 5             # first N listings per query, smoke test
python scrape.py --no-enrich           # skip stage 2
python scrape.py --sheets-only         # push existing cache to Sheets
python scrape.py --headed              # visible browser, for CAPTCHA solving
python scrape.py --force               # ignore the resume cache
```

Command-line flags override the `CONFIG` block for that run; `CONFIG` supplies
the default for anything not passed.

## 13. Testing

Unit tests with pytest cover the pure functions, run against saved HTML
fixtures rather than the live site:

- email extraction and junk filtering
- phone normalization
- the owner name/phone heuristic, including the no-match case
- address splitting into city, state, ZIP
- `place_key` and lat/lng parsing from a Maps URL

No automated tests hit Google. Such tests would be flaky by construction and
would provide no signal about correctness. Stage 1 is verified manually with
`--states TX --limit 5`.

## 14. Risks and limitations

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Terms of Service | Scraping Maps breaches Google's ToS. Accepted by the operator with the trade-off understood. | Conservative pacing; no attempt to defeat anti-bot measures beyond normal browsing behaviour. |
| Selector rot | Google changes markup and extraction silently returns blanks. | Prefer `data-item-id`; keep every selector in one named-constant block; a post-run summary reports the fill rate per column so a break is visible immediately. |
| IP blocking | Run stalls behind a CAPTCHA. | Randomized pacing, resume cache, `--headed` to solve by hand. Proxies only if this becomes routine. |
| 120-result cap | Dense states undersample. | The per-state result count is logged. Any state at or near the cap is a signal to re-run it sliced by metro. |
| Owner data sparsity | `owner_phone` blank for most rows. | Documented expectation; `other_phones` and `emails` carry the fallback contact path. |

## 15. Runtime

Roughly 2–4 seconds per listing, plus 3–6 seconds per enriched website. For an
estimated 800 US padel venues, a full run is about 45–90 minutes. A broader
term list scales close to linearly.

## 16. Deliverables

1. `scrape.py` with the config block and all three stages.
2. `requirements.txt`.
3. `README.md` covering install, Playwright browser download, the one-time
   `gcloud auth application-default login` setup, and how to edit
   `SEARCH_TERMS`.
4. `tests/` with fixtures and the parser tests from section 13.
5. `.gitignore` covering `data/`, `.venv/`, and, defensively, `credentials.json`.
