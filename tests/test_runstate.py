import geo
import runstate
import scrape


TERMS = ["padel club", "padel court"]
STATES = ["Texas", "Utah"]
PLACES = [geo.Place(country="United States", region=s) for s in STATES]


def test_initial_state_is_idle_with_everything_pending():
    s = runstate.initial_state(set(), TERMS, PLACES)
    assert s.status == "idle"
    assert s.coverage == {"Texas": "pending", "Utah": "pending"}
    assert s.queries_total == 4
    assert s.queries_done == 0


def test_initial_state_marks_a_state_done_only_when_every_term_is_cached():
    done = {("padel club", "United States", "Texas", ""),
            ("padel court", "United States", "Texas", ""),
            ("padel club", "United States", "Utah", "")}
    s = runstate.initial_state(done, TERMS, PLACES)
    assert s.coverage["Texas"] == "done"
    assert s.coverage["Utah"] == "pending", "one term still outstanding"
    # The event stream owns queries_done: run_stage1 emits query_skipped for
    # every cached pair, so seeding it here as well double-counted the resume.
    assert s.queries_done == 0


def test_a_cached_state_paints_done_not_pending(tmp_path):
    """Regression: a bare state name can never match a four-part done marker.

    initial_state used to compare (term, "Texas") against done_pairs shaped
    (term, country, region, city); that comparison could never succeed, so a
    teammate reopening the app after a partial run saw every state grey
    ("pending") even though the cache said it was done.
    """
    cache = tmp_path / "cache.jsonl"
    place = geo.Place(country="United States", region="Texas")
    scrape.mark_pair_done("gym", place, cache)
    _, done = scrape.read_cache(cache)
    s = runstate.initial_state(done, ["gym"], [place])
    assert s.coverage == {"Texas": "done"}


def test_a_city_level_place_seeds_the_same_key_its_events_use():
    """Regression for the bug 7510a77 fixed reappearing at the city level.

    initial_state used to key coverage by place.label() ("Austin, Texas")
    while every event scrape.py emits keys it by place.coverage_key()
    ("Texas"). For a region-only place the two happened to be equal, which
    is why this went unnoticed until a city was added to the place: the
    seeded cell and the event cell disagreed, so the seeded cell never left
    "pending" and a second cell appeared beside it.
    """
    place = geo.Place(country="United States", region="Texas", city="Austin")
    s = runstate.initial_state(set(), ["gym"], [place])
    assert set(s.coverage) == {place.coverage_key()}

    s = runstate.fold(s, "query_start", {"term": "gym", "state": place.coverage_key()})
    s = runstate.fold(s, "query_done", {"term": "gym", "state": place.coverage_key(),
                                        "scraped": 3, "failed": 0, "complete": True})
    assert set(s.coverage) == {place.coverage_key()}, (
        "the event must land on the same cell initial_state seeded, not a second one"
    )
    assert s.coverage[place.coverage_key()] == "done"


def test_fold_does_not_mutate_its_argument():
    s = runstate.initial_state(set(), TERMS, PLACES)
    runstate.fold(s, "listing_saved", {"name": "X", "city": "Y", "state": "UT"})
    assert s.saved == 0


def test_run_start_begins_the_clock():
    s = runstate.initial_state(set(), TERMS, PLACES)
    s = runstate.fold(s, "run_start",
                      {"terms": TERMS, "states": STATES, "total_queries": 4},
                      now=100.0)
    assert s.status == "running"
    assert s.started_at == 100.0
    assert s.queries_total == 4


def test_a_clean_query_marks_its_own_term_done_but_not_the_whole_state():
    """One of two terms finishing has never actually finished the state.

    The cell used to paint done here, because coverage was assigned per event
    rather than derived from the terms underneath it.
    """
    s = runstate.initial_state(set(), TERMS, PLACES)
    s = runstate.fold(s, "query_start", {"term": "padel club", "state": "Texas"})
    assert s.coverage["Texas"] == "active"
    s = runstate.fold(s, "query_done", {"term": "padel club", "state": "Texas",
                                        "scraped": 8, "failed": 0, "complete": True})
    assert s.term_status["Texas"]["padel club"] == "done"
    assert s.coverage["Texas"] == "active", "padel court is still outstanding"
    assert s.queries_done == 1


def test_an_incomplete_query_is_partial_not_done():
    s = runstate.initial_state(set(), TERMS, PLACES)
    s = runstate.fold(s, "query_done", {"term": "padel club", "state": "Texas",
                                        "scraped": 3, "failed": 0, "complete": False})
    assert s.coverage["Texas"] == "partial"


def test_a_query_with_failed_listings_is_failed():
    s = runstate.initial_state(set(), TERMS, PLACES)
    s = runstate.fold(s, "query_done", {"term": "padel club", "state": "Texas",
                                        "scraped": 5, "failed": 2, "complete": True})
    assert s.coverage["Texas"] == "failed"


def test_a_worse_outcome_wins_across_terms():
    s = runstate.initial_state(set(), TERMS, PLACES)
    s = runstate.fold(s, "query_done", {"term": "padel club", "state": "Texas",
                                        "scraped": 5, "failed": 0, "complete": True})
    s = runstate.fold(s, "query_done", {"term": "padel court", "state": "Texas",
                                        "scraped": 1, "failed": 0, "complete": False})
    assert s.coverage["Texas"] == "partial", "a later partial must not be hidden by an earlier done"


def test_a_better_outcome_does_not_erase_a_failure():
    s = runstate.initial_state(set(), TERMS, PLACES)
    s = runstate.fold(s, "query_failed", {"term": "padel club", "state": "Utah",
                                          "error": "TimeoutError: gone"})
    s = runstate.fold(s, "query_done", {"term": "padel court", "state": "Utah",
                                        "scraped": 4, "failed": 0, "complete": True})
    assert s.coverage["Utah"] == "failed"


def test_saved_listings_count_up():
    s = runstate.initial_state(set(), TERMS, PLACES)
    for name in ("Padel Den", "SLC Padel Club"):
        s = runstate.fold(s, "listing_saved", {"name": name, "city": "Orem", "state": "UT"})
    assert s.saved == 2
    assert "Padel Den" in s.log[-2]


def test_hitting_the_result_cap_is_recorded():
    s = runstate.initial_state(set(), TERMS, PLACES)
    s = runstate.fold(s, "listings_found", {"term": "padel club", "state": "Texas",
                                            "count": 120, "at_cap": True})
    assert s.at_cap == ["Texas"]


def test_a_failed_query_is_recorded_with_its_reason():
    s = runstate.initial_state(set(), TERMS, PLACES)
    s = runstate.fold(s, "query_failed", {"term": "padel club", "state": "Idaho",
                                          "error": "TimeoutError: no feed"})
    assert s.coverage.get("Idaho") == "failed"
    assert s.failures == [("Idaho", "TimeoutError: no feed")]
    assert s.queries_done == 1


def test_being_blocked_switches_status():
    s = runstate.initial_state(set(), TERMS, PLACES)
    s = runstate.fold(s, "blocked", {"term": "padel club", "state": "Nevada",
                                     "consecutive": 3})
    assert s.status == "blocked"


def test_a_skipped_cached_query_advances_progress():
    s = runstate.initial_state(set(), TERMS, PLACES)
    s = runstate.fold(s, "query_skipped", {"term": "padel club", "state": "Texas"})
    assert s.queries_done == 1


def test_stage_two_tracks_its_own_progress():
    s = runstate.initial_state(set(), TERMS, PLACES)
    s = runstate.fold(s, "stage2_start", {"total": 40})
    assert s.enrich_total == 40
    s = runstate.fold(s, "enriched", {"index": 7, "total": 40,
                                      "name": "Padel Den", "error": ""})
    assert s.enrich_index == 7


def test_finishing_maps_reasons_to_a_status():
    s = runstate.initial_state(set(), TERMS, PLACES)
    assert runstate.fold(s, "run_finished", {"reason": "done"}).status == "finished"
    assert runstate.fold(s, "run_finished", {"reason": "stopped"}).status == "finished"
    assert runstate.fold(s, "run_finished", {"reason": "blocked"}).status == "blocked"
    assert runstate.fold(s, "run_finished", {"reason": "crashed"}).status == "blocked"


def test_the_log_is_capped():
    s = runstate.initial_state(set(), TERMS, PLACES)
    for i in range(runstate.MAX_LOG + 50):
        s = runstate.fold(s, "listing_saved", {"name": f"Club {i}", "city": "X", "state": "UT"})
    assert len(s.log) == runstate.MAX_LOG
    assert "Club 0" not in " ".join(s.log), "oldest lines must be dropped, not newest"


def test_an_unknown_event_is_ignored():
    s = runstate.initial_state(set(), TERMS, PLACES)
    assert runstate.fold(s, "not_a_real_event", {"x": 1}) == s


def test_elapsed_reads_as_hours_and_minutes():
    s = runstate.RunState(started_at=0.0)
    assert runstate.elapsed(s, 0) == "0m"
    assert runstate.elapsed(s, 90) == "1m"
    assert runstate.elapsed(s, 3600) == "1h 00m"
    assert runstate.elapsed(s, 4380) == "1h 13m"


def test_elapsed_is_blank_before_a_run_starts():
    assert runstate.elapsed(runstate.RunState(), 500) == ""


def test_remaining_extrapolates_from_work_already_done():
    s = runstate.RunState(started_at=0.0, queries_done=25, queries_total=100)
    assert runstate.remaining(s, 600) == "30m"


def test_remaining_is_blank_until_there_is_something_to_extrapolate_from():
    s = runstate.RunState(started_at=0.0, queries_done=0, queries_total=100)
    assert runstate.remaining(s, 600) == ""


def test_remaining_is_blank_when_finished():
    s = runstate.RunState(started_at=0.0, queries_done=100, queries_total=100)
    assert runstate.remaining(s, 600) == ""


def test_fill_rate_rows_follow_the_column_order_given():
    records = [{"name": "A", "phone": "+1"}, {"name": "B", "phone": ""}]
    rows = runstate.fill_rate_rows(records, ["name", "phone", "emails"])
    assert rows == [("name", 1.0), ("phone", 0.5), ("emails", 0.0)]


def test_fill_rate_rows_of_nothing_is_nothing():
    assert runstate.fill_rate_rows([], ["name"]) == []


def test_fill_rate_counts_a_legitimate_zero_as_filled():
    rows = runstate.fill_rate_rows([{"reviews": 0}], ["reviews"])
    assert rows == [("reviews", 1.0)], "0 reviews is real data, not a blank"


def test_fold_does_not_mutate_the_containers_it_updates():
    """The existing mutation test only checks an int, which can never alias.

    This one exercises all four mutable containers, so a fold that forgot to
    copy one would fail here instead of silently corrupting the caller's state.
    """
    s = runstate.initial_state(set(), TERMS, PLACES)
    coverage_before, log_before = dict(s.coverage), list(s.log)

    runstate.fold(s, "query_failed", {"term": "padel club", "state": "Texas",
                                      "error": "boom"})
    runstate.fold(s, "listings_found", {"term": "padel club", "state": "Texas",
                                        "count": 120, "at_cap": True})

    assert s.coverage == coverage_before, "coverage must be copied, not updated in place"
    assert s.failures == [], "failures must be copied, not appended to"
    assert s.at_cap == [], "at_cap must be copied, not appended to"
    assert s.log == log_before, "log must be copied, not appended to"


def test_a_resumed_run_ends_at_exactly_one_hundred_percent():
    terms, states = ["a", "b"], [f"S{i}" for i in range(10)]
    places = [geo.Place(country="United States", region=s) for s in states]
    done_states = set(states[:4])
    cached = {("a", "United States", s, "") for s in done_states}
    s = runstate.initial_state(cached, terms, places)
    s = runstate.fold(s, "run_start",
                      {"terms": terms, "states": states, "total_queries": 20}, now=0)
    for term in terms:
        for state in states:
            if term == "a" and state in done_states:
                s = runstate.fold(s, "query_skipped", {"term": term, "state": state})
            else:
                s = runstate.fold(s, "query_done", {"term": term, "state": state,
                                                    "scraped": 1, "failed": 0,
                                                    "complete": True})
    assert s.queries_done == s.queries_total == 20


PACING = ((5.0, 10.0), (1.0, 3.0), 1.5, 40)  # query pause, listing pause, overhead, uncapped


def test_estimate_counts_one_query_per_term_per_place():
    queries, _ = runstate.estimate_run(4, 79, 20, PACING)
    assert queries == 316


def test_estimate_prices_a_query_as_its_pause_plus_its_listings():
    _, seconds = runstate.estimate_run(1, 1, 20, PACING)
    # 7.5s mean query pause + 20 listings x (2.0s mean pause + 1.5s overhead)
    assert seconds == 7.5 + 20 * 3.5


def test_estimate_scales_with_both_terms_and_places():
    _, one = runstate.estimate_run(1, 1, 10, PACING)
    _, many = runstate.estimate_run(2, 3, 10, PACING)
    assert many == one * 6


def test_an_uncapped_run_is_priced_from_the_stated_assumption():
    _, capped = runstate.estimate_run(1, 1, 40, PACING)
    _, uncapped = runstate.estimate_run(1, 1, None, PACING)
    assert uncapped == capped, "cap=None must price at UNCAPPED_ASSUMPTION, not zero"


def test_estimating_nothing_costs_nothing():
    assert runstate.estimate_run(0, 12, 20, PACING) == (0, 0.0)
    assert runstate.estimate_run(3, 0, 20, PACING) == (0, 0.0)


def test_the_default_pacing_comes_from_scrape_so_it_cannot_drift():
    assert runstate.estimate_run(2, 5, 20) == runstate.estimate_run(
        2, 5, 20,
        (scrape.PAUSE_QUERY, scrape.PAUSE_LISTING,
         scrape.LISTING_OVERHEAD, scrape.UNCAPPED_ASSUMPTION))


CITY_PLACES = [
    geo.Place(country="United States", region="Texas", city="Austin"),
    geo.Place(country="United States", region="Texas", city="Dallas"),
]


def test_a_region_is_done_only_when_every_city_in_it_is_cached():
    done = {("padel club", "United States", "Texas", "Austin"),
            ("padel court", "United States", "Texas", "Austin")}
    s = runstate.initial_state(done, TERMS, CITY_PLACES)
    assert s.coverage["Texas"] == "pending", "Dallas is still outstanding"


def test_a_region_is_done_when_all_its_cities_and_terms_are_cached():
    done = {(term, "United States", "Texas", city)
            for term in TERMS for city in ("Austin", "Dallas")}
    s = runstate.initial_state(done, TERMS, CITY_PLACES)
    assert s.coverage["Texas"] == "done"


def test_a_cached_term_shows_done_across_the_whole_region():
    done = {("padel club", "United States", "Texas", "Austin"),
            ("padel club", "United States", "Texas", "Dallas")}
    s = runstate.initial_state(done, TERMS, CITY_PLACES)
    assert s.term_status["Texas"] == {"padel club": "done", "padel court": "pending"}


def test_a_term_cached_in_only_one_city_is_not_done_for_the_region():
    done = {("padel club", "United States", "Texas", "Austin")}
    s = runstate.initial_state(done, TERMS, CITY_PLACES)
    assert s.term_status["Texas"]["padel club"] == "pending"


def test_initial_state_counts_the_outstanding_places_per_term():
    s = runstate.initial_state(set(), TERMS, CITY_PLACES)
    assert s.term_left["Texas"] == {"padel club": 2, "padel court": 2}


def test_one_city_finishing_does_not_finish_the_term_for_the_region():
    s = runstate.initial_state(set(), TERMS, CITY_PLACES)
    s = runstate.fold(s, "query_done", {"term": "padel club", "state": "Texas",
                                        "scraped": 5, "failed": 0, "complete": True})
    assert s.term_status["Texas"]["padel club"] == "active", "Dallas has not run"
    assert s.coverage["Texas"] == "active", "and neither has the second term"


def test_the_last_city_finishing_finishes_the_term():
    s = runstate.initial_state(set(), TERMS, CITY_PLACES)
    for _ in range(2):
        s = runstate.fold(s, "query_done", {"term": "padel club", "state": "Texas",
                                            "scraped": 5, "failed": 0, "complete": True})
    assert s.term_status["Texas"]["padel club"] == "done"


def test_a_failure_in_one_city_is_not_erased_by_a_success_in_another():
    s = runstate.initial_state(set(), TERMS, CITY_PLACES)
    s = runstate.fold(s, "query_failed", {"term": "padel club", "state": "Texas",
                                          "error": "boom"})
    s = runstate.fold(s, "query_done", {"term": "padel club", "state": "Texas",
                                        "scraped": 5, "failed": 0, "complete": True})
    assert s.term_status["Texas"]["padel club"] == "failed"


def test_a_skipped_query_counts_towards_finishing_its_term():
    s = runstate.initial_state(set(), TERMS, CITY_PLACES)
    for _ in range(2):
        s = runstate.fold(s, "query_skipped", {"term": "padel club", "state": "Texas"})
    assert s.term_status["Texas"]["padel club"] == "done"


def test_starting_a_query_marks_its_term_active():
    s = runstate.initial_state(set(), TERMS, CITY_PLACES)
    s = runstate.fold(s, "query_start", {"term": "padel club", "state": "Texas"})
    assert s.term_status["Texas"]["padel club"] == "active"


def test_folding_never_mutates_the_term_status_of_the_previous_state():
    before = runstate.initial_state(set(), TERMS, CITY_PLACES)
    snapshot = {k: dict(v) for k, v in before.term_status.items()}
    runstate.fold(before, "query_start", {"term": "padel club", "state": "Texas"})
    assert before.term_status == snapshot, "fold must copy the nested dicts too"


def test_a_region_is_not_done_while_one_of_its_terms_is_outstanding():
    s = runstate.initial_state(set(), TERMS, PLACES)
    s = runstate.fold(s, "query_done", {"term": "padel club", "state": "Texas",
                                        "scraped": 8, "failed": 0, "complete": True})
    assert s.coverage["Texas"] == "active", "padel court has not run yet"


def test_a_region_turns_done_when_its_last_term_lands():
    s = runstate.initial_state(set(), TERMS, PLACES)
    for term in TERMS:
        s = runstate.fold(s, "query_done", {"term": term, "state": "Texas",
                                            "scraped": 8, "failed": 0, "complete": True})
    assert s.coverage["Texas"] == "done"


def test_coverage_tally_counts_finished_against_total():
    assert runstate.coverage_tally({"Texas": "done", "Utah": "pending",
                                    "Ohio": "done"}) == (2, 3)


def test_coverage_tally_of_nothing_is_zero_of_zero():
    assert runstate.coverage_tally({}) == (0, 0)


def test_country_tally_groups_regions_under_their_country():
    keys = [("United States", "Texas"), ("United States", "Utah"), ("Japan", "Japan")]
    coverage = {"Texas": "done", "Utah": "partial", "Japan": "failed"}
    assert runstate.country_tally(keys, coverage) == [
        ("United States", 1, 1, 0, 2),
        ("Japan", 0, 0, 1, 1),
    ]


def test_country_tally_keeps_the_order_the_keys_arrived_in():
    keys = [("Japan", "Japan"), ("United States", "Texas")]
    assert [row[0] for row in runstate.country_tally(keys, {})] == ["Japan", "United States"]
