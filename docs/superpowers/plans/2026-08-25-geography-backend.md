# Worldwide Geography Targeting — Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the scraper target any country, state or city in the world from the command line, with resume markers that keep working.

**Architecture:** A generated `geodata.py` holds GeoNames-derived location data as plain dict literals. `geo.py` wraps it in accessors plus a `Place` value object that carries country/region/city. `scrape.py` swaps its two-part `(term, state)` key for a four-part one, takes city and region from the query rather than from parsing addresses, and validates phones with `phonenumbers` instead of a US-only regex.

**Tech Stack:** Python 3.12, GeoNames (CC BY 4.0), `phonenumbers` (Apache 2.0), pytest.

**Spec:** `docs/superpowers/specs/2026-08-25-geography-targeting-design.md`

## Global Constraints

- Names, never codes, are the identity in every key that crosses a boundary: done markers, CSV columns, coverage. `"Texas"`, not `"TX"`. Codes exist only inside `geo.py`, for the `gl` parameter and abbreviations.
- An empty part means "all of the parent". `Place(country="India")` searches India as a whole.
- A legacy two-part done marker `{"type": "pair", "term": t, "state": s}` must still read as done, as `(t, "United States", s, "")`. Runs were US-only before this change.
- `hl=en` stays fixed in every country. Only `gl` varies. Localised page chrome would break every selector in the `SELECTORS` block.
- Existing CSV column names are kept, never renamed. `state` now holds the region whatever it is called locally.
- Event payloads keep emitting `state=` with the region name and *add* `country=`/`city=`. `runstate.py` reads `data["state"]` and must keep working untouched — the GUI is a later plan.
- `geodata.py` is generated and committed. CI never regenerates it.
- Cheap model is fine for Tasks 1, 3, 4 and 6. Task 2 and Task 5 touch shared contracts.

---

## File Structure

| File | Responsibility |
|---|---|
| `tools/build_geodata.py` | **new** — parse GeoNames dumps, render `geodata.py`. Parsing is pure and separately testable from downloading. |
| `geodata.py` | **new, generated** — `COUNTRIES`, `REGIONS`, `CITIES` as dict literals. Never hand-edited. |
| `geo.py` | **new** — `Place` plus accessors over `geodata`. No I/O, no tkinter. |
| `scrape.py` | modify — query building, done markers, record columns, phone validation |
| `tests/test_build_geodata.py` | **new** — parsing and rendering against real GeoNames lines |
| `tests/test_geo.py` | **new** — `Place`, accessors, selection counting |
| `tests/fixtures/geonames/*.txt` | **new** — small real samples |
| `pyproject.toml` | add `phonenumbers` |

---

### Task 1: GeoNames parser and the generated data module

**Files:**
- Create: `tools/build_geodata.py`
- Create: `tests/test_build_geodata.py`
- Create: `tests/fixtures/geonames/countryInfo.txt`, `tests/fixtures/geonames/admin1CodesASCII.txt`, `tests/fixtures/geonames/cities15000.txt`
- Create: `geodata.py` (by running the generator)

**Interfaces:**
- Consumes: nothing
- Produces: `geodata.COUNTRIES: dict[str, str]` (country name → ISO2), `geodata.REGIONS: dict[str, dict[str, str]]` (country name → region name → admin1 code), `geodata.CITIES: dict[tuple[str, str], list[str]]` ((country name, region name) → city names, most populous first, at most 25)

- [ ] **Step 1: Write the fixtures**

`tests/fixtures/geonames/countryInfo.txt` — tab-separated, real format, comment lines kept so the parser must skip them:

```
# GeoNames.org Country Information
#ISO	ISO3	ISO-Numeric	fips	Country	Capital	Area(in sq km)	Population	Continent
US	USA	840	US	United States	Washington	9629091	327167434	NA
IN	IND	356	IN	India	New Delhi	3287590	1352617328	AS
GB	GBR	826	UK	United Kingdom	London	244820	67141684	EU
```

`tests/fixtures/geonames/admin1CodesASCII.txt` — `code<TAB>name<TAB>asciiname<TAB>geonameid`:

```
US.TX	Texas	Texas	4736286
US.CA	California	California	5332921
IN.16	Maharashtra	Maharashtra	1264418
GB.ENG	England	England	6269131
```

`tests/fixtures/geonames/cities15000.txt` — 19 tab-separated fields. Only 1 (name), 8 (country code), 10 (admin1) and 14 (population) matter:

```
4699066	Houston	Houston		29.76328	-95.36327	P	PPLA2	US		TX	201		2314157		32	America/Chicago	2023-03-02
4726206	San Antonio	San Antonio		29.42412	-98.49363	P	PPLA2	US		TX	029		1469845		198	America/Chicago	2023-03-02
4671654	Austin	Austin		30.26715	-97.74306	P	PPLA2	US		TX	453		964177		149	America/Chicago	2023-03-02
5368361	Los Angeles	Los Angeles		34.05223	-118.24368	P	PPLA2	US		CA	037		3971883		86	America/Los_Angeles	2023-03-02
1275339	Mumbai	Mumbai		19.07283	72.88261	P	PPLA	IN		16	517		12691836		8	Asia/Kolkata	2023-03-02
2643743	London	London		51.50853	-0.12574	P	PPLC	GB		ENG		7556900		25	Europe/London	2023-03-02
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_build_geodata.py
from pathlib import Path

import pytest

from tools import build_geodata

FIXTURES = Path(__file__).parent / "fixtures" / "geonames"


@pytest.fixture
def countries():
    return build_geodata.parse_countries((FIXTURES / "countryInfo.txt").read_text(encoding="utf-8"))


@pytest.fixture
def regions():
    return build_geodata.parse_regions((FIXTURES / "admin1CodesASCII.txt").read_text(encoding="utf-8"))


def test_countries_map_display_name_to_iso_code(countries):
    assert countries["United States"] == "US"
    assert countries["India"] == "IN"


def test_country_comment_lines_are_skipped(countries):
    # Every GeoNames dump opens with # lines, including a header row of
    # field names that would otherwise parse as a country called "#ISO".
    assert not any(name.startswith("#") for name in countries)
    assert len(countries) == 3


def test_regions_are_grouped_by_country_name(countries, regions):
    grouped = build_geodata.group_regions(regions, countries)
    assert grouped["United States"]["Texas"] == "TX"
    assert grouped["India"]["Maharashtra"] == "16"


def test_regions_of_an_unknown_country_are_dropped(countries):
    # A region whose ISO prefix is not in countryInfo has no country to hang
    # off, and keying it by the raw code would leak codes into a name-keyed map.
    regions = build_geodata.parse_regions("ZZ.01\tNowhere\tNowhere\t1\n")
    assert build_geodata.group_regions(regions, countries) == {}


def test_cities_are_keyed_by_country_and_region_name(countries, regions):
    grouped = build_geodata.group_regions(regions, countries)
    cities = build_geodata.parse_cities(
        (FIXTURES / "cities15000.txt").read_text(encoding="utf-8"), countries, grouped, limit=25
    )
    assert cities[("United States", "Texas")] == ["Houston", "San Antonio", "Austin"]


def test_cities_are_ordered_by_population_descending(countries, regions):
    grouped = build_geodata.group_regions(regions, countries)
    cities = build_geodata.parse_cities(
        (FIXTURES / "cities15000.txt").read_text(encoding="utf-8"), countries, grouped, limit=25
    )
    # Houston 2314157 > San Antonio 1469845 > Austin 964177
    assert cities[("United States", "Texas")][0] == "Houston"


def test_the_limit_keeps_the_most_populous(countries, regions):
    grouped = build_geodata.group_regions(regions, countries)
    cities = build_geodata.parse_cities(
        (FIXTURES / "cities15000.txt").read_text(encoding="utf-8"), countries, grouped, limit=2
    )
    assert cities[("United States", "Texas")] == ["Houston", "San Antonio"]


def test_a_city_with_no_admin1_is_dropped(countries, regions):
    # Field 10 empty means GeoNames has no state for it. There is nowhere in
    # a country -> state -> city picker to put such a city.
    grouped = build_geodata.group_regions(regions, countries)
    row = "1\tOrphan\tOrphan\t\t0\t0\tP\tPPL\tUS\t\t\t\t\t\t9999999\t\t0\tUTC\t2023-01-01\n"
    assert build_geodata.parse_cities(row, countries, grouped, limit=25) == {}


def test_rendered_module_is_importable_python(countries, regions):
    grouped = build_geodata.group_regions(regions, countries)
    cities = build_geodata.parse_cities(
        (FIXTURES / "cities15000.txt").read_text(encoding="utf-8"), countries, grouped, limit=25
    )
    source = build_geodata.render_module(countries, grouped, cities)
    namespace: dict = {}
    exec(compile(source, "geodata.py", "exec"), namespace)
    assert namespace["COUNTRIES"]["India"] == "IN"
    assert namespace["CITIES"][("United States", "Texas")][0] == "Houston"


def test_rendered_module_is_deterministic(countries, regions):
    # Regenerating without upstream changes must produce a zero-line diff,
    # or every refresh looks like a data change.
    grouped = build_geodata.group_regions(regions, countries)
    cities = build_geodata.parse_cities(
        (FIXTURES / "cities15000.txt").read_text(encoding="utf-8"), countries, grouped, limit=25
    )
    first = build_geodata.render_module(countries, grouped, cities)
    second = build_geodata.render_module(countries, grouped, cities)
    assert first == second
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_build_geodata.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools'`

- [ ] **Step 4: Write the generator**

Create `tools/__init__.py` (empty file — makes `from tools import build_geodata` resolve).

```python
# tools/build_geodata.py
"""Regenerate geodata.py from the GeoNames dumps.

Run when the location data should be refreshed:

    uv run python tools/build_geodata.py

Deliberately not run in CI. Generating during a release would let a GeoNames
outage break a release and would make a shipped build irreproducible locally.
A refresh is a maintainer action that produces a reviewable diff.

Source: https://download.geonames.org/export/dump/ (CC BY 4.0)
"""

import io
import urllib.request
import zipfile
from pathlib import Path

BASE = "https://download.geonames.org/export/dump"
CITY_LIMIT = 25          # cities kept per region, most populous first
OUTPUT = Path(__file__).resolve().parent.parent / "geodata.py"


def parse_countries(text: str) -> dict[str, str]:
    """{country display name: ISO2}. Comment lines are skipped."""
    countries: dict[str, str] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) < 5:
            continue
        iso, name = fields[0].strip(), fields[4].strip()
        if iso and name:
            countries[name] = iso
    return countries


def parse_regions(text: str) -> dict[str, str]:
    """{"US.TX": "Texas"} - still keyed by code; group_regions renames them."""
    regions: dict[str, str] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) < 2:
            continue
        code, name = fields[0].strip(), fields[1].strip()
        if code and name:
            regions[code] = name
    return regions


def group_regions(regions: dict[str, str], countries: dict[str, str]) -> dict[str, dict[str, str]]:
    """{country name: {region name: admin1 code}}, dropping unknown countries."""
    by_iso = {iso: name for name, iso in countries.items()}
    grouped: dict[str, dict[str, str]] = {}
    for code, region_name in regions.items():
        iso, _, admin1 = code.partition(".")
        country_name = by_iso.get(iso)
        if not country_name or not admin1:
            continue
        grouped.setdefault(country_name, {})[region_name] = admin1
    return grouped


def parse_cities(
    text: str,
    countries: dict[str, str],
    grouped: dict[str, dict[str, str]],
    limit: int = CITY_LIMIT,
) -> dict[tuple[str, str], list[str]]:
    """{(country name, region name): [city names]}, most populous first."""
    by_iso = {iso: name for name, iso in countries.items()}
    # (country, region) -> region's admin1 code, for the reverse lookup below.
    region_of: dict[tuple[str, str], str] = {}
    for country_name, regions in grouped.items():
        for region_name, admin1 in regions.items():
            region_of[(country_name, admin1)] = region_name

    collected: dict[tuple[str, str], list[tuple[int, str]]] = {}
    for line in text.splitlines():
        fields = line.split("\t")
        if len(fields) < 15:
            continue
        name, iso, admin1, population = (
            fields[1].strip(), fields[8].strip(), fields[10].strip(), fields[14].strip()
        )
        country_name = by_iso.get(iso)
        if not country_name or not admin1 or not name:
            continue
        region_name = region_of.get((country_name, admin1))
        if not region_name:
            continue
        try:
            size = int(population)
        except ValueError:
            size = 0
        collected.setdefault((country_name, region_name), []).append((size, name))

    cities: dict[tuple[str, str], list[str]] = {}
    for key, entries in collected.items():
        # Sort by population descending, then name, so equal populations do
        # not reorder between runs and produce a spurious diff.
        entries.sort(key=lambda pair: (-pair[0], pair[1]))
        cities[key] = [name for _, name in entries[:limit]]
    return cities


def render_module(
    countries: dict[str, str],
    grouped: dict[str, dict[str, str]],
    cities: dict[tuple[str, str], list[str]],
) -> str:
    """The source of geodata.py. Sorted throughout, so a refresh diffs cleanly."""
    lines = [
        '"""Location data generated from GeoNames. Do not edit by hand.',
        "",
        "Regenerate with:  uv run python tools/build_geodata.py",
        "Source: https://download.geonames.org/export/dump/ (CC BY 4.0)",
        '"""',
        "",
        "COUNTRIES = {",
    ]
    for name in sorted(countries):
        lines.append(f"    {name!r}: {countries[name]!r},")
    lines += ["}", "", "REGIONS = {"]
    for country in sorted(grouped):
        lines.append(f"    {country!r}: {{")
        for region in sorted(grouped[country]):
            lines.append(f"        {region!r}: {grouped[country][region]!r},")
        lines.append("    },")
    lines += ["}", "", "CITIES = {"]
    for key in sorted(cities):
        lines.append(f"    {key!r}: {cities[key]!r},")
    lines += ["}", ""]
    return "\n".join(lines)


def _download(name: str) -> str:
    url = f"{BASE}/{name}"
    with urllib.request.urlopen(url) as response:      # noqa: S310 - fixed host
        payload = response.read()
    if name.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            inner = name[: -len(".zip")] + ".txt"
            payload = archive.read(inner)
    return payload.decode("utf-8")


def main() -> None:
    print("Downloading GeoNames dumps...")
    countries = parse_countries(_download("countryInfo.txt"))
    regions = parse_regions(_download("admin1CodesASCII.txt"))
    grouped = group_regions(regions, countries)
    cities = parse_cities(_download("cities15000.zip"), countries, grouped)
    OUTPUT.write_text(render_module(countries, grouped, cities), encoding="utf-8")
    total = sum(len(names) for names in cities.values())
    print(f"Wrote {OUTPUT} - {len(countries)} countries, "
          f"{sum(len(r) for r in grouped.values())} regions, {total} cities")


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_build_geodata.py -q`
Expected: PASS, 9 tests, no warnings.

- [ ] **Step 6: Generate the real data module**

Run: `uv run python tools/build_geodata.py`
Expected: prints roughly `195 countries, 3900+ regions, 20000+ cities`.

Then confirm it imports and is plausible:

```bash
uv run python -c "
import geodata
print(len(geodata.COUNTRIES), 'countries')
print(geodata.COUNTRIES['United States'], geodata.REGIONS['United States']['Texas'])
print(geodata.CITIES[('United States', 'Texas')][:3])
"
```
Expected: `US TX` and `['Houston', 'San Antonio', 'Dallas']`.

If the download fails, report BLOCKED rather than hand-writing `geodata.py`.

- [ ] **Step 7: Commit**

```bash
git add tools/ geodata.py tests/test_build_geodata.py tests/fixtures/geonames/
git commit -m "feat: generate worldwide location data from GeoNames"
```

---

### Task 2: Place and the geo accessors

**Files:**
- Create: `geo.py`
- Create: `tests/test_geo.py`

**Interfaces:**
- Consumes: `geodata.COUNTRIES`, `geodata.REGIONS`, `geodata.CITIES` from Task 1
- Produces:
  - `geo.Place(country: str = "", region: str = "", city: str = "")` — frozen dataclass with `.parts() -> tuple[str, ...]`, `.query_text() -> str`, `.key() -> tuple[str, str, str]`, `.label() -> str`
  - `geo.countries() -> list[str]`
  - `geo.regions(country: str) -> list[str]`
  - `geo.cities(country: str, region: str) -> list[str]`
  - `geo.country_code(country: str) -> str` — lowercase ISO2 for `gl`, `""` when unknown
  - `geo.abbreviate(country: str, region: str) -> str`
  - `geo.leaf_places(selection: Selection) -> list[Place]`
  - `geo.leaf_count(selection: Selection) -> int`
  - `Selection = dict[str, dict[str, list[str]]]` — country → region → cities

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_geo.py
import geo


# --- Place ----------------------------------------------------------------

def test_query_text_orders_most_specific_first():
    place = geo.Place(country="United States", region="Texas", city="Austin")
    assert place.query_text() == "Austin, Texas, United States"


def test_query_text_of_a_whole_state_omits_the_city():
    assert geo.Place(country="United States", region="Texas").query_text() == "Texas, United States"


def test_query_text_of_a_whole_country_is_just_the_country():
    assert geo.Place(country="India").query_text() == "India"


def test_an_empty_place_has_no_query_text():
    assert geo.Place().query_text() == ""


def test_key_is_always_three_parts():
    # Fixed arity matters: the done-marker tuple is compared by equality, and
    # a variable-length key would never match.
    assert geo.Place(country="India").key() == ("India", "", "")


def test_label_reads_broadest_first_for_a_human():
    # query_text is for Google, label is for the operator's log line.
    place = geo.Place(country="United States", region="Texas", city="Austin")
    assert place.label() == "Austin, Texas"


def test_label_of_a_whole_country_names_the_country():
    assert geo.Place(country="India").label() == "India"


def test_place_is_hashable():
    # Places land in sets when the runner deduplicates work.
    assert len({geo.Place(country="India"), geo.Place(country="India")}) == 1


# --- accessors ------------------------------------------------------------

def test_countries_are_sorted_display_names():
    names = geo.countries()
    assert "United States" in names
    assert names == sorted(names)


def test_regions_of_a_country_are_sorted():
    names = geo.regions("United States")
    assert "Texas" in names
    assert names == sorted(names)


def test_regions_of_an_unknown_country_is_empty():
    assert geo.regions("Atlantis") == []


def test_cities_keep_population_order_not_alphabetical():
    names = geo.cities("United States", "Texas")
    assert names[0] == "Houston"
    assert names != sorted(names)


def test_cities_of_an_unknown_region_is_empty():
    assert geo.cities("United States", "Atlantis") == []


def test_country_code_is_lowercased_for_the_gl_parameter():
    assert geo.country_code("United States") == "us"
    assert geo.country_code("India") == "in"


def test_country_code_of_an_unknown_country_is_empty():
    assert geo.country_code("Atlantis") == ""


def test_abbreviate_uses_the_real_region_code():
    assert geo.abbreviate("United States", "Texas") == "TX"


def test_abbreviate_falls_back_to_three_letters():
    # Most of the world's admin1 codes are numeric ("16" for Maharashtra),
    # which tells an operator nothing in a coverage cell.
    assert geo.abbreviate("India", "Maharashtra") == "MAH"


def test_abbreviate_of_an_unknown_region_still_returns_something():
    assert geo.abbreviate("Atlantis", "Nowhere") == "NOW"


# --- selection ------------------------------------------------------------

def test_a_country_with_no_regions_is_one_leaf():
    assert geo.leaf_count({"India": {}}) == 1


def test_a_region_with_no_cities_is_one_leaf():
    assert geo.leaf_count({"United States": {"Texas": []}}) == 1


def test_each_city_is_its_own_leaf():
    assert geo.leaf_count({"United States": {"Texas": ["Austin", "Dallas"]}}) == 2


def test_leaves_add_up_across_countries():
    selection = {
        "United States": {"Texas": ["Austin", "Dallas"], "California": ["Fresno"]},
        "India": {"Maharashtra": []},
        "United Kingdom": {},
    }
    assert geo.leaf_count(selection) == 5


def test_leaf_places_match_the_count():
    selection = {"United States": {"Texas": ["Austin", "Dallas"]}, "India": {}}
    places = geo.leaf_places(selection)
    assert len(places) == geo.leaf_count(selection)
    assert geo.Place(country="India") in places
    assert geo.Place(country="United States", region="Texas", city="Austin") in places


def test_an_empty_selection_has_no_leaves():
    assert geo.leaf_places({}) == []
    assert geo.leaf_count({}) == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_geo.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'geo'`

- [ ] **Step 3: Write `geo.py`**

```python
"""Places, and the data behind them.

Pure lookups over the generated `geodata` module plus a `Place` value that
carries a country/region/city triple around. No I/O, no tkinter, no clock -
everything here is testable without a display, like `runstate`.

Display names are the identity throughout. Codes appear only where a machine
needs one: `country_code` for the Maps `gl` parameter, `abbreviate` for a
coverage cell too narrow to hold a name.
"""

from dataclasses import dataclass

import geodata

# country -> region -> cities. An empty dict or list means "all of it".
Selection = dict[str, dict[str, list[str]]]


@dataclass(frozen=True)
class Place:
    """Somewhere to search. Empty parts mean the whole of the parent."""

    country: str = ""
    region: str = ""
    city: str = ""

    def parts(self) -> tuple[str, ...]:
        """The non-empty parts, broadest first."""
        return tuple(p for p in (self.country, self.region, self.city) if p)

    def query_text(self) -> str:
        """What follows "in" in a Maps search - most specific first.

        Google resolves "Austin, Texas, United States" reliably; leading with
        the country does not.
        """
        return ", ".join(reversed(self.parts()))

    def key(self) -> tuple[str, str, str]:
        """Fixed-arity identity for done markers and dict keys."""
        return (self.country, self.region, self.city)

    def label(self) -> str:
        """Short human form for a log line or a status header."""
        if self.city:
            return f"{self.city}, {self.region}" if self.region else self.city
        return self.region or self.country


def countries() -> list[str]:
    return sorted(geodata.COUNTRIES)


def regions(country: str) -> list[str]:
    return sorted(geodata.REGIONS.get(country, {}))


def cities(country: str, region: str) -> list[str]:
    """Most populous first - the order the generator wrote, not alphabetical.

    The order is the ranking reason, and re-sorting would discard it.
    """
    return list(geodata.CITIES.get((country, region), ()))


def country_code(country: str) -> str:
    """Lowercase ISO2 for the Maps `gl` parameter, or "" if unknown."""
    return geodata.COUNTRIES.get(country, "").lower()


def abbreviate(country: str, region: str) -> str:
    """A short label for a coverage cell.

    Prefers the real admin1 code, but only when it is alphabetic: most of the
    world's are numeric ("16" for Maharashtra), which tells an operator
    nothing. Those fall back to the first three letters of the name.
    """
    code = geodata.REGIONS.get(country, {}).get(region, "")
    if code.isalpha():
        return code.upper()
    return region[:3].upper()


def leaf_places(selection: Selection) -> list[Place]:
    """One Place per search that a selection implies.

    A country with no regions is one search; a region with no cities is one
    search; otherwise one per city.
    """
    places: list[Place] = []
    for country in sorted(selection):
        chosen = selection[country]
        if not chosen:
            places.append(Place(country=country))
            continue
        for region in sorted(chosen):
            names = chosen[region]
            if not names:
                places.append(Place(country=country, region=region))
                continue
            for city in names:
                places.append(Place(country=country, region=region, city=city))
    return places


def leaf_count(selection: Selection) -> int:
    """How many searches per term the selection implies."""
    return len(leaf_places(selection))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_geo.py -q`
Expected: PASS, 24 tests, no warnings.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS — 141 existing plus the new tests, nothing broken.

- [ ] **Step 6: Commit**

```bash
git add geo.py tests/test_geo.py
git commit -m "feat: Place value and geography accessors"
```

---

### Task 3: Search URLs for any place

**Files:**
- Modify: `scrape.py` — `MAPS_SEARCH_URL` (line 82), `build_search_url` (line 90)
- Test: `tests/test_parsing.py` (append)

**Interfaces:**
- Consumes: `geo.Place`, `geo.country_code` from Task 2
- Produces: `scrape.build_search_url(term: str, place: geo.Place) -> str`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_parsing.py`:

```python
import geo


def test_search_url_puts_the_city_first():
    url = scrape.build_search_url("dental clinic", geo.Place("United States", "Texas", "Austin"))
    assert "dental+clinic+in+Austin%2C+Texas%2C+United+States" in url


def test_search_url_for_a_whole_country():
    url = scrape.build_search_url("gym", geo.Place(country="India"))
    assert "gym+in+India" in url


def test_search_url_sets_gl_from_the_country():
    url = scrape.build_search_url("gym", geo.Place(country="India"))
    assert "gl=in" in url


def test_search_url_keeps_hl_english_everywhere():
    # Localised page chrome would break every selector in SELECTORS.
    url = scrape.build_search_url("gym", geo.Place(country="Japan", region="Kanagawa"))
    assert "hl=en" in url
    assert "hl=ja" not in url


def test_an_unknown_country_falls_back_to_us():
    url = scrape.build_search_url("gym", geo.Place(country="Atlantis"))
    assert "gl=us" in url


def test_search_url_of_an_empty_place_searches_the_bare_term():
    url = scrape.build_search_url("gym", geo.Place())
    assert "gym" in url
    assert "+in+" not in url
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_parsing.py -k search_url -q`
Expected: FAIL — `TypeError: build_search_url() takes 2 positional arguments but ...` or an AttributeError on `Place`.

- [ ] **Step 3: Replace the URL builder**

In `scrape.py`, add `import geo` beside `import paths`, then replace lines 82 and 88-93:

```python
# gl is filled per query; hl stays English in every country so the page text
# the SELECTORS block matches on does not change under us.
MAPS_SEARCH_URL = "https://www.google.com/maps/search/{query}?hl=en&gl={gl}"

DEFAULT_GL = "us"


def build_search_url(term: str, place: "geo.Place") -> str:
    """URL for a Maps search of `term` within `place`.

    An empty place searches the bare term, which is what a caller with no
    geography selected means.
    """
    location = place.query_text()
    query = f"{term} in {location}" if location else term
    return MAPS_SEARCH_URL.format(
        query=quote_plus(query),
        gl=geo.country_code(place.country) or DEFAULT_GL,
    )
```

- [ ] **Step 4: Update the call site**

`run_stage1` currently calls `build_search_url(term, state)`. Change the loop to build a `Place`. Full replacement for the nested loop header, keeping the existing body:

```python
    for term in terms:
        for place in places:
            if stop_requested():
                break
            url = build_search_url(term, place)
```

`run_stage1`'s signature becomes:

```python
def run_stage1(terms, places, limit=None, headless=True, force=False) -> None:
```

Every `state` reference inside the loop becomes `place`. The `emit` calls keep a `state=` key for `runstate`, which this plan does not touch:

```python
emit("query_start", term=term, state=place.region or place.country,
     country=place.country, city=place.city)
```

Apply the same `state=`/`country=`/`city=` shape to `query_done`, `query_failed`, `query_skipped`, `listings_found` and `listing_saved`.

- [ ] **Step 5: Update the CLI entry point**

In `main()`, build places from the `--states` argument so the command line keeps working:

```python
    places = [geo.Place(country="United States", region=state) for state in args.states]
```

Add `--country`, `--region` and `--city` arguments, each repeatable, that override `--states` when present:

```python
    parser.add_argument("--country", action="append", default=[],
                        help="search a whole country, repeatable")
    parser.add_argument("--city", action="append", default=[],
                        metavar="CITY,REGION,COUNTRY",
                        help='e.g. --city "Austin,Texas,United States", repeatable')
```

```python
    if args.country or args.city:
        places = [geo.Place(country=name) for name in args.country]
        for entry in args.city:
            city, _, rest = entry.partition(",")
            region, _, country = rest.partition(",")
            places.append(geo.Place(country=country.strip(),
                                    region=region.strip(), city=city.strip()))
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest -q`
Expected: PASS. If a `runstate` test fails on a missing `state` key, the `emit` call was changed rather than extended — put `state=` back.

- [ ] **Step 7: Smoke-test the CLI without a network run**

```bash
uv run python -c "
import geo, scrape
print(scrape.build_search_url('dental clinic', geo.Place('United States','Texas','Austin')))
print(scrape.build_search_url('gym', geo.Place(country='India')))
"
```
Expected: two URLs, the first with `gl=us`, the second `gl=in`, both `hl=en`.

- [ ] **Step 8: Commit**

```bash
git add scrape.py tests/test_parsing.py
git commit -m "feat: search URLs for any country, state or city"
```

---

### Task 4: Four-part done markers, backward compatible

**Files:**
- Modify: `scrape.py` — `mark_pair_done` (line 493), `read_cache` (line 498), the `should_mark_done` call site
- Test: `tests/test_parsing.py` (append)

**Interfaces:**
- Consumes: `geo.Place` from Task 2
- Produces:
  - `scrape.mark_pair_done(term: str, place: geo.Place, path: Path | None = None) -> None`
  - `scrape.read_cache(path=None) -> tuple[list[dict], set[tuple[str, str, str, str]]]` — the set holds `(term, country, region, city)`
  - `scrape.pair_key(term: str, place: geo.Place) -> tuple[str, str, str, str]`

- [ ] **Step 1: Write the failing tests**

```python
def test_a_done_marker_round_trips(tmp_path):
    cache = tmp_path / "cache.jsonl"
    place = geo.Place("United States", "Texas", "Austin")
    scrape.mark_pair_done("gym", place, cache)
    _, done = scrape.read_cache(cache)
    assert scrape.pair_key("gym", place) in done


def test_a_statewide_marker_does_not_satisfy_a_city(tmp_path):
    cache = tmp_path / "cache.jsonl"
    scrape.mark_pair_done("gym", geo.Place("United States", "Texas"), cache)
    _, done = scrape.read_cache(cache)
    assert scrape.pair_key("gym", geo.Place("United States", "Texas", "Austin")) not in done


def test_a_legacy_two_part_marker_still_reads_as_done(tmp_path):
    # Written by every run before this change, when the scraper was US-only.
    cache = tmp_path / "cache.jsonl"
    cache.write_text('{"type": "pair", "term": "padel club", "state": "Texas"}\n',
                     encoding="utf-8")
    _, done = scrape.read_cache(cache)
    assert ("padel club", "United States", "Texas", "") in done


def test_legacy_and_new_markers_coexist(tmp_path):
    cache = tmp_path / "cache.jsonl"
    cache.write_text('{"type": "pair", "term": "gym", "state": "Texas"}\n', encoding="utf-8")
    scrape.mark_pair_done("gym", geo.Place("India", "Maharashtra", "Mumbai"), cache)
    _, done = scrape.read_cache(cache)
    assert ("gym", "United States", "Texas", "") in done
    assert ("gym", "India", "Maharashtra", "Mumbai") in done


def test_records_are_unaffected_by_the_marker_change(tmp_path):
    cache = tmp_path / "cache.jsonl"
    scrape.append_record({"place_key": "abc", "name": "A Gym"}, cache)
    scrape.mark_pair_done("gym", geo.Place(country="India"), cache)
    records, done = scrape.read_cache(cache)
    assert [r["name"] for r in records] == ["A Gym"]
    assert len(done) == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_parsing.py -k marker -q`
Expected: FAIL — `AttributeError: module 'scrape' has no attribute 'pair_key'`

- [ ] **Step 3: Rewrite the marker functions**

```python
LEGACY_COUNTRY = "United States"


def pair_key(term: str, place: "geo.Place") -> tuple[str, str, str, str]:
    """Identity of one search, for the resume set."""
    return (term, *place.key())


def mark_pair_done(term: str, place: "geo.Place", path: Path | None = None) -> None:
    """Record that a (term, place) search finished, so re-runs can skip it."""
    _append_line({"type": "pair", "term": term, "country": place.country,
                  "state": place.region, "city": place.city}, path)
```

Inside `read_cache`, replace the `elif kind == "pair":` branch:

```python
            elif kind == "pair":
                # Markers written before worldwide support carry no country,
                # and every one of those runs was US-only.
                pairs.add((
                    obj.get("term", ""),
                    obj.get("country", "") or LEGACY_COUNTRY,
                    obj.get("state", ""),
                    obj.get("city", ""),
                ))
```

and widen the annotations:

```python
def read_cache(path: Path | None = None) -> tuple[list[dict], set[tuple[str, str, str, str]]]:
    ...
    pairs: set[tuple[str, str, str, str]] = set()
```

- [ ] **Step 4: Update the two call sites in `run_stage1`**

The skip check:

```python
                if not force and pair_key(term, place) in done:
                    emit("query_skipped", term=term, state=place.region or place.country,
                         country=place.country, city=place.city)
                    continue
```

and the completion:

```python
                if should_mark_done(failed, complete) and not stop_requested():
                    mark_pair_done(term, place)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest -q`
Expected: PASS, everything green.

- [ ] **Step 6: Commit**

```bash
git add scrape.py tests/test_parsing.py
git commit -m "feat: four-part resume markers that still read the old ones"
```

---

### Task 5: Location columns taken from the query

**Files:**
- Modify: `scrape.py` — `COLUMNS` (line 58), `STAGE1_COLUMNS` (line 66), `build_record` (line 557), `scrape_listing` (line 723)
- Test: `tests/test_parsing.py` (append)

**Interfaces:**
- Consumes: `geo.Place` from Task 2
- Produces:
  - `scrape.build_record(raw: dict, term: str, place: geo.Place, now: str) -> dict`
  - `scrape.scrape_listing(page, url: str, term: str, place: geo.Place) -> dict`
  - `COLUMNS` gains `country`, `search_country`, `search_city`

- [ ] **Step 1: Write the failing tests**

```python
def test_city_and_state_come_from_the_query_not_the_address():
    # Parsing addresses correctly in every country is a large, low-value
    # problem, and unnecessary: the query already said where we searched.
    raw = {"url": "https://maps.google.com/?cid=1", "name": "A Gym",
           "address_label": "Address: 12 Some Road, Whoknows"}
    record = scrape.build_record(raw, "gym", geo.Place("India", "Maharashtra", "Mumbai"), "now")
    assert record["city"] == "Mumbai"
    assert record["state"] == "Maharashtra"
    assert record["country"] == "India"


def test_the_full_address_is_kept_verbatim():
    raw = {"url": "https://maps.google.com/?cid=1", "name": "A Gym",
           "address_label": "Address: 12 Some Road, Whoknows"}
    record = scrape.build_record(raw, "gym", geo.Place("India", "Maharashtra", "Mumbai"), "now")
    assert record["address"] == "12 Some Road, Whoknows"


def test_a_whole_country_run_falls_back_to_the_address_for_city():
    # No city was searched for, so the address is the only source there is.
    raw = {"url": "https://maps.google.com/?cid=1", "name": "A Gym",
           "address_label": "Address: 1 Main St, Austin, TX 78701"}
    record = scrape.build_record(raw, "gym", geo.Place(country="United States"), "now")
    assert record["city"] == "Austin"
    assert record["country"] == "United States"


def test_search_columns_record_what_was_asked_for():
    record = scrape.build_record({"url": "", "name": "A"}, "gym",
                                 geo.Place("India", "Maharashtra", "Mumbai"), "now")
    assert record["search_term"] == "gym"
    assert record["search_country"] == "India"
    assert record["search_state"] == "Maharashtra"
    assert record["search_city"] == "Mumbai"


def test_search_city_is_empty_on_a_whole_region_run():
    record = scrape.build_record({"url": "", "name": "A"}, "gym",
                                 geo.Place("India", "Maharashtra"), "now")
    assert record["search_city"] == ""


def test_new_columns_are_in_both_column_lists():
    # A column missing from STAGE1_COLUMNS is blanked by every re-scrape.
    for column in ("country", "search_country", "search_city"):
        assert column in scrape.COLUMNS
        assert column in scrape.STAGE1_COLUMNS
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_parsing.py -k "query_not_the_address or search_columns or new_columns" -q`
Expected: FAIL — `KeyError: 'country'`

- [ ] **Step 3: Widen the column lists**

Replace lines 58-70 of `scrape.py`:

```python
COLUMNS = [
    "place_key", "name", "category", "address", "city", "state", "zip",
    "country", "phone", "website", "rating", "reviews", "latitude",
    "longitude", "maps_url", "emails", "owner_name", "owner_phone",
    "other_phones", "instagram", "facebook", "linkedin", "search_term",
    "search_country", "search_state", "search_city", "scraped_at",
    "enrich_error",
]

STAGE1_COLUMNS = [
    "place_key", "name", "category", "address", "city", "state", "zip",
    "country", "phone", "website", "rating", "reviews", "latitude",
    "longitude", "maps_url", "search_term", "search_country", "search_state",
    "search_city", "scraped_at",
]
```

- [ ] **Step 4: Take location from the query in `build_record`**

Replace the signature and the location half of the body:

```python
def build_record(raw: dict, term: str, place: "geo.Place", now: str) -> dict:
```

```python
    url = raw.get("url", "")
    address = clean_address_label(raw.get("address_label", ""))
    address_city, address_state, postcode = split_address(address)
    ...
    record.update({
        ...
        "address": address,
        # The query is authoritative: when we searched "in Mumbai", the city
        # is an input, not something to infer from a foreign address format.
        # Only a whole-region or whole-country run falls back to parsing.
        "city": place.city or address_city,
        "state": place.region or address_state,
        "zip": postcode,
        "country": place.country,
        ...
        "search_term": term,
        "search_country": place.country,
        "search_state": place.region,
        "search_city": place.city,
        "scraped_at": now,
    })
```

- [ ] **Step 5: Thread the place through `scrape_listing`**

```python
def scrape_listing(page, url: str, term: str, place: "geo.Place") -> dict:
```

and inside it, the `build_record(raw, term, state, now)` call becomes `build_record(raw, term, place, now)`. Update both call sites in `run_stage1`.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest -q`
Expected: PASS. Existing `build_record` tests that pass a state string will fail with `AttributeError: 'str' object has no attribute 'city'` — update them to pass `geo.Place(country="United States", region="Texas")`.

- [ ] **Step 7: Commit**

```bash
git add scrape.py tests/test_parsing.py
git commit -m "feat: location columns from the query rather than the address"
```

---

### Task 6: International phone validation

**Files:**
- Modify: `pyproject.toml`, `scrape.py` — `normalize_phone` (line ~180), `extract_phones` (line 190), `enrich_website` (line 437)
- Test: `tests/test_parsing.py` (append)

**Interfaces:**
- Consumes: `geo.country_code` from Task 2
- Produces:
  - `scrape.normalize_phone(text: str, region: str = "US") -> str` — E.164 or `""`
  - `scrape.extract_phones(text: str, region: str = "US") -> list[str]`
  - `scrape.enrich_website(url: str, fetch_fn, listing_phone: str = "", region: str = "US") -> dict[str, str]`

- [ ] **Step 1: Add the dependency**

```bash
uv add phonenumbers
```

- [ ] **Step 2: Write the failing tests**

```python
def test_a_us_number_normalises_to_e164():
    assert scrape.normalize_phone("(512) 555-0142") == "+15125550142"


def test_an_indian_number_needs_its_region():
    assert scrape.normalize_phone("022 2822 1234", region="IN") == "+912228221234"


def test_the_same_digits_are_invalid_in_another_region():
    # Region is not decoration: it decides validity.
    assert scrape.normalize_phone("022 2822 1234", region="US") == ""


def test_a_product_code_is_rejected():
    # The regex predecessor manufactured +14567890123 out of a SKU. A
    # validator rejects what a pattern would have accepted.
    assert scrape.normalize_phone("SKU 1234567890123") == ""


def test_a_too_short_run_of_digits_is_rejected():
    assert scrape.normalize_phone("call 12345") == ""


def test_an_international_prefix_is_honoured_over_the_region():
    assert scrape.normalize_phone("+44 20 7946 0958", region="US") == "+442079460958"


def test_extract_phones_deduplicates_in_order():
    text = "Call (512) 555-0142 or 512-555-0142 or (512) 555-0143"
    assert scrape.extract_phones(text) == ["+15125550142", "+15125550143"]


def test_extract_phones_drops_invalid_candidates():
    assert scrape.extract_phones("order SKU 1234567890123 today") == []
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_parsing.py -k phone -q`
Expected: FAIL — the existing `normalize_phone` returns `"+1" + digits`, so `test_a_product_code_is_rejected` fails with `assert '+11234567890123' == ''`.

- [ ] **Step 4: Replace the normaliser**

Add `import phonenumbers` to the imports, then replace `normalize_phone`:

```python
def normalize_phone(text: str, region: str = "US") -> str:
    """`text` as an E.164 number, or "" if it is not a real number there.

    Validation, not pattern matching. The regex this replaced turned a
    product code into +14567890123, because thirteen digits look like a phone
    number to a pattern and like nothing at all to a validator.

    `region` decides what a number without a + prefix means; a number that
    carries its own country code ignores it.
    """
    try:
        parsed = phonenumbers.parse(text, region or "US")
    except phonenumbers.NumberParseException:
        return ""
    if not phonenumbers.is_valid_number(parsed):
        return ""
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
```

- [ ] **Step 5: Thread the region through extraction**

```python
def extract_phones(text: str, region: str = "US") -> list[str]:
    """Valid phone numbers in `text`, E.164, deduplicated, in order.

    PHONE_RE still finds the candidates; normalize_phone decides which are
    real. Keeping the regex as a first pass avoids handing every digit run in
    a page to the parser.
    """
    seen: list[str] = []
    for match in PHONE_RE.finditer(text):
        number = normalize_phone(match.group(0), region)
        if number and number not in seen:
            seen.append(number)
    return seen
```

`enrich_website` gains `region: str = "US"` and passes it to every `extract_phones` and `normalize_phone` call in its body. `run_stage2` passes `region=geo.country_code(record.get("search_country", "")).upper() or "US"`.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest -q`
Expected: PASS. Some existing phone tests may assert on numbers that are not valid US numbers (555-01xx is reserved but valid in libphonenumber's data; a made-up area code may not be). Where an existing test breaks, replace its number with a real-format one rather than loosening the validator — that is the whole point of the change.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock scrape.py tests/test_parsing.py
git commit -m "feat: validate phone numbers per country with libphonenumber"
```

---

## Verification

After Task 6, one real end-to-end check against Google. It costs a few minutes and one small scrape:

```bash
uv run python scrape.py --city "Austin,Texas,United States" --terms "dental clinic" --limit 3 --no-enrich
```

Expected: three records in `data/results.csv` with `city=Austin`, `state=Texas`, `country=United States`, `search_city=Austin`.

Then confirm resume works:

```bash
uv run python scrape.py --city "Austin,Texas,United States" --terms "dental clinic" --limit 3 --no-enrich
```

Expected: the query is skipped, no browser opens for it.

## Out of scope

The selector screen, the cost preview, the coverage changes and the Console
restyle are the next two plans. This plan leaves the GUI working exactly as it
does today, because `runstate` still receives a `state=` key on every event.
