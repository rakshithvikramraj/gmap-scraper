# Worldwide Geography Targeting — Design

**Date:** 2026-08-25
**Status:** approved in chat, not yet planned

## Goal

Let an operator aim a run at specific places anywhere in the world, through a
country → state → city selector, and see what the run will cost before
starting it.

## Why

Two things force this, and only one of them is a feature request.

The feature request: the tool is being repurposed from a padel club directory
into lead generation for a custom app development business. Verticals are
already selectable — the SEARCH TERMS box at `app.py:238` accepts any phrase —
but geography is fixed at "all 50 US states".

The forcing constraint: Google Maps caps a search at roughly 120 results. A
query for "restaurants in Texas" does not return Texas's restaurants, it
returns 120 of them. `README.md` already documents the symptom and prescribes
the manual workaround — splitting a state into city queries. City-level
targeting is that workaround, made first class. **It is a data-quality fix
before it is a convenience.**

## Scope

In: country/state/city selection, worldwide city data, query construction,
international phone handling, run cost estimation, coverage display, per-city
capping.

Out: the live results table, tech-signal qualifiers (has-an-app, site
platform, site age), a preset vertical list, non-English Maps locales. Each is
its own change.

## What exists today

| Thing | Where | Shape |
|---|---|---|
| Search URL | `scrape.py:82,90` | `"{term} in {state}"`, `hl=en&gl=us` hardcoded |
| Done marker | `scrape.py:493` | `mark_pair_done(term, state)` |
| Cache read | `scrape.py:498` | returns `set[tuple[str, str]]` |
| States | `scrape.py:39,52` | `ALL_50`, `STATES = ALL_50` |
| Coverage | `runstate.py:47` | `dict[state, status]`, done when `cached == len(terms)` |
| Grid | `widgets.py:226` | `CoverageGrid(master, labels, cols=10, ...)` |
| Abbreviations | `widgets.py` | `STATE_ABBR`, 50 US entries |
| Pacing | `scrape.py:657` | `PAUSE_LISTING = (1.0, 3.0)`, `PAUSE_QUERY = (5.0, 10.0)` |
| Phones | `scrape.py:159` | `PHONE_RE`, US formats |

## Design

### 1. Location data ships generated, and committed

A new generated module `geodata.py` holds three plain dict literals:

```python
COUNTRIES = {"US": "United States", "IN": "India", ...}
REGIONS   = {"US": {"TX": "Texas", ...}, ...}
CITIES    = {("US", "TX"): ["Houston", "San Antonio", ...], ...}
```

Source is GeoNames (CC BY 4.0): `countryInfo.txt`, `admin1CodesASCII.txt` and
`cities15000.zip` — roughly 26,000 cities worldwide with population ≥ 15,000.
Cities are sorted by population descending and **capped at 25 per region**.

A module, not a JSON or CSV data file, and the reason is packaging: a data
file needs an explicit PyInstaller `datas` entry plus a `_MEIPASS`-relative
lookup at runtime. That is precisely the class of bug that cost a build cycle
during packaging. A module is collected automatically and imports identically
frozen or not.

**The generated file is committed to the repository.** `tools/build_geodata.py`
regenerates it on demand; CI does not. Generating during a release build would
make a GeoNames outage break releases and make the shipped artifact
irreproducible locally. A deliberate maintainer refresh producing a reviewable
diff is worth more than automatic freshness for data that changes slowly.

`geo.py` holds the hand-written accessors over that data, so the generated
file stays pure data:

```python
@dataclass(frozen=True)
class Place:                                       # country/region/city, empty = "all of it"
    def query_text() -> str                        # "Austin, Texas, United States"
    def key() -> tuple[str, str, str]              # fixed arity, for done markers
    def label() -> str                             # "Austin, Texas", for a log line

def countries() -> list[str]                       # display names, sorted
def regions(country: str) -> list[str]             # display names, sorted
def cities(country: str, region: str) -> list[str] # display names, by population
def country_code(country: str) -> str              # "United States" -> "us"
def abbreviate(country: str, region: str) -> str   # "Texas" -> "TX"; "Maharashtra" -> "MAH"
def leaf_places(selection: Selection) -> list[Place]
def leaf_count(selection: Selection) -> int        # searches per term
```

`abbreviate` takes both parts because region names are not globally unique,
and because most of the world's admin1 codes are numeric — GeoNames gives
Maharashtra `"16"`, which tells an operator nothing in a coverage cell. Only
alphabetic codes are used; the rest fall back to three letters of the name.

The geography triple travels as one `Place` value rather than three
positional strings, so a signature change stays a signature change instead of
rippling through every call site as three arguments.

### 2. Names, not codes, are the identity

Every key that crosses a boundary — the done marker, the CSV column, the
coverage dict — uses the human-readable name (`"Texas"`, not `"TX"`). Codes
exist only inside `geo.py`, for the `gl` parameter.

This buys backward compatibility for free. A legacy done marker
`(term, "Texas")` reads as `(term, "United States", "Texas", "")` with no
migration step, because the region name is already what was stored.

### 3. Query construction

```python
def build_search_url(term, country="", region="", city="") -> str
```

The location string is the non-empty parts joined most-specific first:
`"dentist in Toronto, Ontario, Canada"`. An empty part means "all of the
parent", so `country="India"` alone searches India as a whole.

`gl` becomes the target country's ISO code. **`hl` stays `en` in every
country.** Varying `hl` would return localised page chrome and break every
selector in the `SELECTORS` block; varying only `gl` gets local results while
keeping the page parseable.

### 4. Done markers become four parts

```python
mark_pair_done(term, country, region="", city="")
read_cache() -> tuple[list[dict], set[tuple[str, str, str, str]]]
```

An empty trailing part means the whole of its parent, so a statewide run and a
city run coexist in one cache without colliding. Records themselves are
unaffected; only the completion markers change shape.

### 5. City and region come from the query, not the address

Today the address string is parsed to fill `city`, `state` and `zip`. Parsing
addresses correctly in every country is a large, low-value problem — and it is
unnecessary, because when the query was "dentist in Toronto, Ontario" the city
and region are **inputs**.

So: `city`, `region` and `country` columns are populated from the query that
found the listing. The full address string is kept verbatim in `address`.
Postcode extraction becomes best-effort and non-load-bearing.

When a query has no city (a whole-region or whole-country run), the city
column falls back to the existing address-derived value, which is what it
does today.

**Column changes.** `COLUMNS` (`scrape.py:58`) keeps every existing name and
gains three:

| Column | Change |
|---|---|
| `state` | unchanged name, now holds the region whatever it is called locally — province, prefecture, département |
| `country` | new, the country the listing was searched in |
| `search_country` | new, alongside the existing `search_state` |
| `search_city` | new, empty on a whole-region or whole-country run |

Existing names are kept rather than renamed to `region`: a rename would break
every CSV already produced and anything downstream reading them, for cosmetic
gain. `STAGE1_COLUMNS` gains the same three so a re-scrape does not blank them.

### 6. International phones via `phonenumbers`

Add the `phonenumbers` dependency (Apache 2.0, Google's libphonenumber ported
to Python). Parse with the query's country as the region hint, keep only
numbers it reports as valid, and store E.164.

This also closes a bug class already hit once in this codebase: a loose regex
manufactured `+14567890123` from a product SKU. `phonenumbers` validates
rather than pattern-matches, so a digit run that is not a real number in that
country is rejected.

`PHONE_RE` remains as the candidate-extraction pass; `phonenumbers` becomes
the validation gate behind it.

### 7. The selector screen

Geography does not fit in the setup panel, which already carries terms,
options and the coverage grid. It gets its own screen, reached from a
**Choose locations** button, with the current selection summarised on the
setup panel beside it.

Three panes, left to right: countries, regions of the highlighted country,
cities of the highlighted region. Each is a multi-select `tk.Listbox` with a
search box above it — 26,000 cities cannot be found by scrolling. `Listbox`
rather than `ttk.Treeview` with checkboxes, because Treeview has no native
checkbox and emulating one with tag images is fragile.

Selection is **not** the Listbox's own selection state, which is destroyed
whenever a pane repopulates. It lives in an explicit structure the panes
render from and write to:

```python
Selection = dict[str, dict[str, list[str]]]   # country -> region -> cities
```

An empty city list means the whole region; an empty region dict means the
whole country. A fourth pane lists what is currently selected, each row
removable — so a selection spanning many countries stays visible and
correctable without hunting back through the panes.

### 8. Cost preview

A line beneath the panes, recomputed on every selection change:

> **1,240 queries · about 21 hours**

Derived in `runstate.py`, pure and display-free like the rest of that module:

```python
def estimate_run(term_count, leaf_count, cap) -> tuple[int, float]
```

Queries are `term_count × leaf_count`. Seconds are
`queries × (mean(PAUSE_QUERY) + cap × (mean(PAUSE_LISTING) + LISTING_OVERHEAD))`,
reading the pacing constants from `scrape.py` rather than restating them, so
the estimate tracks any pacing change. `LISTING_OVERHEAD` is a single named
constant for page-load and extraction time, initially 1.5s.

The estimate is only meaningful because runs are capped — uncapped, a query
returns between 1 and 120 results and any figure would be a guess.

### 9. Cap per city

Today's "Stop after N clubs per state" becomes "Stop after N per city",
falling back to "per region" or "per country" wording to match the most
specific level of the selection. Wording only; the mechanism is unchanged.

### 10. Coverage display

The grid renders the **selected regions**, not a fixed 50. Cells are labelled
by region code where one exists, else the first three characters of the name.
`STATE_ABBR` is superseded by a `geo.abbreviate(name)` helper.

Above 60 selected regions a grid stops being readable, so beyond that the grid
is replaced by a single progress line — "34 of 210 regions complete" — with
the per-region detail available in the activity log.

`runstate.py:47`'s completeness test becomes
`cached == len(terms) × len(leaves in that region)`.

## Error handling

| Case | Behaviour |
|---|---|
| No location selected | Start is disabled, with "Choose at least one location" beside it |
| A stored selection names a country/region absent from a newer `geodata` | Dropped on load, reported once in the activity log; never blocks startup |
| `phonenumbers` cannot parse a candidate | Candidate discarded; not an error |
| Legacy two-part done marker | Read as `(term, country, region, "")` |
| Selection so large the estimate exceeds 24h | Estimate turns amber with "consider narrowing"; never blocks |

## Testing

Everything except the screen itself is pure and testable without a display,
matching how `runstate.py` is already tested.

- `geo.py` — lookups, `location_query` for every combination of empty parts,
  `country_code`, `abbreviate`, `leaf_count`
- `scrape.py` — `build_search_url` for all four selection shapes; `gl` varies
  and `hl` does not; four-part done markers round-trip; legacy two-part
  markers still read
- `runstate.py` — `estimate_run` arithmetic; coverage completeness with
  multiple cities per region
- `tools/build_geodata.py` — parsing a small fixture of real GeoNames lines,
  including the population sort and the 25-per-region cap
- Phone validation — real numbers accepted per country, SKU-like digit runs
  rejected

The selector screen is not unit-tested, consistent with existing practice for
Tk widgets.

## Suggested plan split

The spec is one coherent feature but has a clean seam, and each half is
separately shippable:

1. **Backend** — `geodata.py` and its generator, `geo.py`, `build_search_url`,
   four-part done markers, the new columns, `phonenumbers`. After this the CLI
   targets anywhere in the world; the GUI is unchanged.
2. **Frontend** — the selector screen, the cost preview, the coverage change,
   the cap wording. After this the GUI exposes it.

Landing 1 first means the risky part — a shared key that `scrape.py`,
`runstate.py` and `app.py` all read — is proven by the CLI and the test suite
before any UI depends on it.

## Visual direction

Settled by mockup, not by prose: `design/geography/` holds the four screens
(`Main` the selector, `Setup`, `Coverage`, `CoverageLarge`), seeded into a
canvas with `seed-canvas.mjs`.

The direction is **Console** — near-black canvas, a single lime accent,
hairline borders, no shadows, tabular numerals. It replaces the light
`PALETTE` in `widgets.py`, so this is a whole-app change: a half-dark app
looks broken.

**Lime doubles as the "finished" status.** The two never collide — the
selector has no statuses, the coverage grid has no selection — and a
completed state lighting up in the accent reads correctly. The consequence is
that green leaves the status palette entirely: at cell size it is too close to
lime to distinguish. Partly-done becomes amber, failed becomes coral.

**Unbounded is the display face, not the body face.** It carries the
wordmark, the tracked uppercase labels, the large numerals and the buttons.
Dense list rows stay in the system sans — Unbounded's wide geometric
letterforms are hard to read at 13px in a tight list, and those rows are where
an operator hunts for a city.

**Shipping Unbounded is a real task with a real failure mode.** Tk resolves
only fonts the OS has installed; it cannot load a web font. The `.ttf` must
ship inside the app and be registered at startup:

| Platform | Mechanism |
|---|---|
| macOS | `ATSApplicationFontsPath` in the bundle's `Info.plist`, fonts under `Contents/Resources/Fonts` |
| Windows | `AddFontResourceExW` via `ctypes`, with `FR_PRIVATE` |

Registration can fail, and `tkfont.Font(family=...)` silently substitutes when
a family is missing rather than raising. So the font layer must resolve the
family it actually got and fall back to the existing `UI_FACES` chain, the way
`resolve_face()` already does — never assume the registration worked.

## Consequences

- `geodata.py` is a large committed generated file. Diffs on refresh will be
  big and should not be reviewed line by line — the generator's tests are the
  gate.
- Runs get slower to *complete* while getting better per hour spent: a
  50-state statewide sweep is ~15 queries, the same states city-targeted is
  hundreds. The cost preview exists so that is a choice rather than a surprise.
- Every hour of run time is exposure to Google blocking the IP. Longer runs
  raise that; the existing block detection and resume behaviour absorb it.
