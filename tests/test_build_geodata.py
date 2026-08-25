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


def test_a_region_name_collision_is_reported_not_silently_dropped(countries, capsys):
    # Two different admin1 codes in the same country that happen to render to
    # the same display name would otherwise collide silently in REGIONS, and
    # any cities filed under the losing code would vanish from CITIES with no
    # trace. The generator must at least surface this on stderr.
    regions = build_geodata.parse_regions(
        "US.XX\tDuplicate\tDuplicate\t1\nUS.YY\tDuplicate\tDuplicate\t2\n"
    )
    grouped = build_geodata.group_regions(regions, countries)

    warning = capsys.readouterr().err
    assert "United States" in warning
    assert "Duplicate" in warning
    assert "XX" in warning
    assert "YY" in warning
    # One of the two codes still survives - a collision is reported, not a
    # silent total loss of the region.
    assert grouped["United States"]["Duplicate"] in ("XX", "YY")


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
