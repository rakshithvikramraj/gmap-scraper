import pytest

import geo
import scrape


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


def test_a_region_without_a_country_cannot_be_constructed():
    # --region "Texas" with no comma partitions to country="" -- this is
    # what makes that state unreachable rather than merely unreached.
    with pytest.raises(ValueError):
        geo.Place(region="Texas")


def test_a_city_without_a_country_cannot_be_constructed():
    with pytest.raises(ValueError):
        geo.Place(city="Austin")


def test_a_city_without_a_country_cannot_be_constructed_even_with_a_region():
    with pytest.raises(ValueError):
        geo.Place(region="Texas", city="Austin")


def test_coverage_key_of_a_region_level_place_is_the_region():
    place = geo.Place(country="United States", region="Texas")
    assert place.coverage_key() == "Texas"


def test_coverage_key_of_a_city_level_place_is_still_the_region():
    # The bug this guards: label() gives "Austin, Texas" for the same place,
    # and that is a different string -- a coverage cell keyed by one and
    # painted by the other never leaves "pending".
    place = geo.Place(country="United States", region="Texas", city="Austin")
    assert place.coverage_key() == "Texas"
    assert place.coverage_key() != place.label()


def test_coverage_key_of_a_whole_country_place_is_the_country():
    assert geo.Place(country="India").coverage_key() == "India"


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


def test_search_finds_a_city_without_knowing_its_region():
    hits = geo.search_places("Austin")
    assert geo.Place(country="United States", region="Texas", city="Austin") in hits


def test_search_finds_a_region():
    hits = geo.search_places("Maharashtra")
    assert geo.Place(country="India", region="Maharashtra") in hits


def test_search_finds_a_country():
    assert geo.Place(country="Japan") in geo.search_places("Japan")


def test_search_is_case_insensitive():
    assert geo.search_places("austin") == geo.search_places("Austin")


def test_search_puts_broader_places_first():
    """A country match outranks a city match, so "India" is not buried."""
    kinds = [len(p.parts()) for p in geo.search_places("India")]
    assert kinds == sorted(kinds), "countries, then regions, then cities"


def test_search_prefers_a_name_that_starts_with_the_query():
    hits = [p.city for p in geo.search_places("York") if p.city]
    assert hits.index("York") < hits.index("New York City"), \
        "a prefix hit ranks above a substring hit"


def test_search_ignores_a_query_too_short_to_be_useful():
    assert geo.search_places("a") == [], "one letter would match half the world"


def test_search_respects_its_limit():
    assert len(geo.search_places("san", limit=5)) == 5


def test_search_returns_nothing_for_a_place_that_does_not_exist():
    assert geo.search_places("Zzzyxxqq") == []


def test_every_us_state_abbreviates_to_a_unique_two_letter_code():
    """Replaces widgets.STATE_ABBR, which only ever knew the 50 states."""
    codes = {geo.abbreviate("United States", s) for s in scrape.ALL_50}
    assert len(codes) == 50, "the 50 states must not collide in a coverage cell"
    assert geo.abbreviate("United States", "Texas") == "TX"


def test_a_region_with_a_numeric_code_falls_back_to_its_name():
    """GeoNames gives Maharashtra "16", which tells an operator nothing."""
    assert geo.abbreviate("India", "Maharashtra") == "MAH"
