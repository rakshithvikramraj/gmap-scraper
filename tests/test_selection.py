import geo
import selection

US = "United States"
SEL = {US: {"Texas": ["Austin", "Dallas"], "Utah": []}, "Japan": {}}


def test_a_country_with_no_regions_means_the_whole_country():
    assert geo.leaf_places({"Japan": {}}) == [geo.Place(country="Japan")]


def test_toggling_a_country_off_removes_it_and_its_regions():
    after = selection.toggle_country(SEL, US)
    assert US not in after
    assert "Japan" in after, "the other country is untouched"


def test_toggling_a_country_on_selects_the_whole_country():
    assert selection.toggle_country({}, "Japan") == {"Japan": {}}


def test_toggling_never_mutates_the_input():
    before = {US: {"Texas": ["Austin"]}}
    snapshot = {US: {"Texas": ["Austin"]}}
    selection.toggle_city(before, US, "Texas", "Dallas")
    assert before == snapshot


def test_toggling_a_region_on_brings_its_country_with_it():
    assert selection.toggle_region({}, US, "Texas") == {US: {"Texas": []}}


def test_toggling_a_region_off_drops_its_cities_too():
    after = selection.toggle_region(SEL, US, "Texas")
    assert "Texas" not in after[US]
    assert after[US]["Utah"] == []


def test_toggling_off_the_last_region_leaves_nothing_selected():
    after = selection.toggle_region({US: {"Texas": []}}, US, "Texas")
    assert after == {}, "no regions left means nothing selected, not the whole country"


def test_toggling_a_city_on_brings_its_region_and_country_with_it():
    assert selection.toggle_city({}, US, "Texas", "Austin") == {US: {"Texas": ["Austin"]}}


def test_toggling_off_the_last_city_leaves_the_whole_region_selected():
    after = selection.toggle_city({US: {"Texas": ["Austin"]}}, US, "Texas", "Austin")
    assert after == {US: {"Texas": []}}, "the region stays, now meaning all of it"


def test_cities_stay_in_population_order_however_they_were_picked():
    after = selection.toggle_city({US: {"Texas": ["Dallas"]}}, US, "Texas", "Houston")
    assert after[US]["Texas"] == ["Houston", "Dallas"], "geo.cities order, not click order"


def test_a_city_below_the_population_cap_sorts_after_the_ranked_ones():
    after = selection.toggle_city({US: {"Texas": ["Kyle"]}}, US, "Texas", "Houston")
    assert after[US]["Texas"] == ["Houston", "Kyle"]


def test_toggle_place_dispatches_on_how_specific_the_place_is():
    assert selection.toggle_place({}, geo.Place(country="Japan")) == {"Japan": {}}
    assert selection.toggle_place(
        {}, geo.Place(country=US, region="Texas")) == {US: {"Texas": []}}
    assert selection.toggle_place(
        {}, geo.Place(country=US, region="Texas", city="Austin")) == {US: {"Texas": ["Austin"]}}


def test_a_country_is_on_when_any_of_it_is_selected():
    assert selection.is_country_on(SEL, US) is True
    assert selection.is_country_on(SEL, "France") is False


def test_a_region_is_on_whether_or_not_cities_are_picked():
    assert selection.is_region_on(SEL, US, "Texas") is True
    assert selection.is_region_on(SEL, US, "Utah") is True
    assert selection.is_region_on(SEL, US, "Ohio") is False


def test_a_city_is_on_only_when_named():
    assert selection.is_city_on(SEL, US, "Texas", "Austin") is True
    assert selection.is_city_on(SEL, US, "Texas", "Houston") is False
    assert selection.is_city_on(SEL, US, "Utah", "Provo") is False, \
        "a whole region is not every city named individually"


def test_the_country_note_counts_chosen_regions_against_the_total():
    assert selection.country_note(SEL, US) == "2/51"


def test_the_country_note_says_all_when_the_whole_country_is_taken():
    assert selection.country_note(SEL, "Japan") == "all"


def test_the_country_note_of_an_unselected_country_is_just_its_size():
    assert selection.country_note(SEL, "France") == "13"


def test_the_region_note_counts_chosen_cities_against_the_total():
    assert selection.region_note(SEL, US, "Texas") == "2/25"


def test_the_region_note_of_a_whole_region_is_just_its_size():
    assert selection.region_note(SEL, US, "Utah") == "25"


def test_summary_describes_each_country_in_one_line():
    assert selection.summary(SEL) == [
        ("Japan", "whole country"),
        (US, "2 states · 2 cities"),
    ]


def test_summary_says_whole_state_when_a_region_has_no_cities():
    assert selection.summary({US: {"Utah": []}}) == [(US, "1 state · whole state")]


def test_summary_of_nothing_is_nothing():
    assert selection.summary({}) == []


def test_the_cap_noun_follows_the_most_specific_level_chosen():
    assert selection.cap_noun(SEL) == "city"
    assert selection.cap_noun({US: {"Utah": []}}) == "state"
    assert selection.cap_noun({"Japan": {}}) == "country"
    assert selection.cap_noun({}) == "place"


def test_region_keys_lists_every_coverage_cell_the_run_will_paint():
    assert selection.region_keys(SEL) == [
        ("Japan", "Japan"), (US, "Texas"), (US, "Utah")]


def test_a_whole_country_gets_one_coverage_cell_named_after_it():
    assert selection.region_keys({"Japan": {}}) == [("Japan", "Japan")]


def test_from_states_migrates_the_old_us_only_preference():
    assert selection.from_states(["Texas", "Utah"]) == {US: {"Texas": [], "Utah": []}}


def test_from_states_of_an_empty_list_selects_nothing():
    assert selection.from_states([]) == {}


def test_normalise_accepts_the_shape_it_is_given():
    assert selection.normalise(SEL) == SEL


def test_normalise_repairs_json_that_lost_its_types():
    assert selection.normalise({US: {"Texas": None}}) == {US: {"Texas": []}}
    assert selection.normalise({US: None}) == {US: {}}
    assert selection.normalise("garbage") == {}
    assert selection.normalise([1, 2]) == {}


def test_prune_drops_places_a_newer_geodata_no_longer_knows():
    kept, dropped = selection.prune({US: {"Texas": ["Austin", "Atlantis"]},
                                     "Freedonia": {}})
    assert kept == {US: {"Texas": ["Austin", "Atlantis"]}}
    assert dropped == ["Freedonia"]


def test_prune_keeps_a_real_selection_untouched_and_reports_nothing():
    assert selection.prune(SEL) == (SEL, [])


def test_prune_drops_a_region_the_data_no_longer_has():
    kept, dropped = selection.prune({US: {"Texas": [], "Atlantis": []}})
    assert kept == {US: {"Texas": []}} and dropped == ["Atlantis"]


def test_prune_keeps_a_small_city_that_is_below_the_population_cap():
    """geo.cities is the top 25 only; a real smaller city must survive."""
    kept, dropped = selection.prune({US: {"Texas": ["Kyle"]}})
    assert kept == {US: {"Texas": ["Kyle"]}} and dropped == []
