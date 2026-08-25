"""Regenerate geodata.py from the GeoNames dumps.

Run when the location data should be refreshed:

    uv run python tools/build_geodata.py

Deliberately not run in CI. Generating during a release would let a GeoNames
outage break a release and would make a shipped build irreproducible locally.
A refresh is a maintainer action that produces a reviewable diff.

Source: https://download.geonames.org/export/dump/ (CC BY 4.0)
"""

import io
import sys
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
        country_regions = grouped.setdefault(country_name, {})
        existing = country_regions.get(region_name)
        if existing is not None and existing != admin1:
            # Two admin1 codes render to the same display name within one
            # country. The second silently wins below, and parse_cities'
            # reverse index is built from this already-collapsed dict, so
            # any cities filed under the losing admin1 code vanish with no
            # trace. Warn on stderr so a maintainer running the refresh
            # notices and can rename one of them upstream.
            print(
                f"WARNING: {country_name!r} has two regions named {region_name!r} "
                f"(admin1 codes {existing!r} and {admin1!r}); keeping {admin1!r}, "
                f"cities under {existing!r} will be dropped",
                file=sys.stderr,
            )
        country_regions[region_name] = admin1
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
