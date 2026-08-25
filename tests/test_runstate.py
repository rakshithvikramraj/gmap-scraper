import runstate


TERMS = ["padel club", "padel court"]
STATES = ["Texas", "Utah"]


def test_initial_state_is_idle_with_everything_pending():
    s = runstate.initial_state(set(), TERMS, STATES)
    assert s.status == "idle"
    assert s.coverage == {"Texas": "pending", "Utah": "pending"}
    assert s.queries_total == 4
    assert s.queries_done == 0


def test_initial_state_marks_a_state_done_only_when_every_term_is_cached():
    done = {("padel club", "Texas"), ("padel court", "Texas"),
            ("padel club", "Utah")}
    s = runstate.initial_state(done, TERMS, STATES)
    assert s.coverage["Texas"] == "done"
    assert s.coverage["Utah"] == "pending", "one term still outstanding"
    assert s.queries_done == 3


def test_fold_does_not_mutate_its_argument():
    s = runstate.initial_state(set(), TERMS, STATES)
    runstate.fold(s, "listing_saved", {"name": "X", "city": "Y", "state": "UT"})
    assert s.clubs == 0


def test_run_start_begins_the_clock():
    s = runstate.initial_state(set(), TERMS, STATES)
    s = runstate.fold(s, "run_start",
                      {"terms": TERMS, "states": STATES, "total_queries": 4},
                      now=100.0)
    assert s.status == "running"
    assert s.started_at == 100.0
    assert s.queries_total == 4


def test_a_clean_query_marks_the_state_done():
    s = runstate.initial_state(set(), TERMS, STATES)
    s = runstate.fold(s, "query_start", {"term": "padel club", "state": "Texas"})
    assert s.coverage["Texas"] == "active"
    s = runstate.fold(s, "query_done", {"term": "padel club", "state": "Texas",
                                        "scraped": 8, "failed": 0, "complete": True})
    assert s.coverage["Texas"] == "done"
    assert s.queries_done == 1


def test_an_incomplete_query_is_partial_not_done():
    s = runstate.initial_state(set(), TERMS, STATES)
    s = runstate.fold(s, "query_done", {"term": "padel club", "state": "Texas",
                                        "scraped": 3, "failed": 0, "complete": False})
    assert s.coverage["Texas"] == "partial"


def test_a_query_with_failed_listings_is_failed():
    s = runstate.initial_state(set(), TERMS, STATES)
    s = runstate.fold(s, "query_done", {"term": "padel club", "state": "Texas",
                                        "scraped": 5, "failed": 2, "complete": True})
    assert s.coverage["Texas"] == "failed"


def test_a_worse_outcome_wins_across_terms():
    s = runstate.initial_state(set(), TERMS, STATES)
    s = runstate.fold(s, "query_done", {"term": "padel club", "state": "Texas",
                                        "scraped": 5, "failed": 0, "complete": True})
    s = runstate.fold(s, "query_done", {"term": "padel court", "state": "Texas",
                                        "scraped": 1, "failed": 0, "complete": False})
    assert s.coverage["Texas"] == "partial", "a later partial must not be hidden by an earlier done"


def test_a_better_outcome_does_not_erase_a_failure():
    s = runstate.initial_state(set(), TERMS, STATES)
    s = runstate.fold(s, "query_failed", {"term": "padel club", "state": "Utah",
                                          "error": "TimeoutError: gone"})
    s = runstate.fold(s, "query_done", {"term": "padel court", "state": "Utah",
                                        "scraped": 4, "failed": 0, "complete": True})
    assert s.coverage["Utah"] == "failed"


def test_saved_listings_count_up():
    s = runstate.initial_state(set(), TERMS, STATES)
    for name in ("Padel Den", "SLC Padel Club"):
        s = runstate.fold(s, "listing_saved", {"name": name, "city": "Orem", "state": "UT"})
    assert s.clubs == 2
    assert "Padel Den" in s.log[-2]


def test_hitting_the_result_cap_is_recorded():
    s = runstate.initial_state(set(), TERMS, STATES)
    s = runstate.fold(s, "listings_found", {"term": "padel club", "state": "Texas",
                                            "count": 120, "at_cap": True})
    assert s.at_cap == ["Texas"]


def test_a_failed_query_is_recorded_with_its_reason():
    s = runstate.initial_state(set(), TERMS, STATES)
    s = runstate.fold(s, "query_failed", {"term": "padel club", "state": "Idaho",
                                          "error": "TimeoutError: no feed"})
    assert s.coverage.get("Idaho") == "failed"
    assert s.failures == [("Idaho", "TimeoutError: no feed")]
    assert s.queries_done == 1


def test_being_blocked_switches_status():
    s = runstate.initial_state(set(), TERMS, STATES)
    s = runstate.fold(s, "blocked", {"term": "padel club", "state": "Nevada",
                                     "consecutive": 3})
    assert s.status == "blocked"


def test_a_skipped_cached_query_advances_progress():
    s = runstate.initial_state(set(), TERMS, STATES)
    s = runstate.fold(s, "query_skipped", {"term": "padel club", "state": "Texas"})
    assert s.queries_done == 1


def test_stage_two_tracks_its_own_progress():
    s = runstate.initial_state(set(), TERMS, STATES)
    s = runstate.fold(s, "stage2_start", {"total": 40})
    assert s.enrich_total == 40
    s = runstate.fold(s, "enriched", {"index": 7, "total": 40,
                                      "name": "Padel Den", "error": ""})
    assert s.enrich_index == 7


def test_finishing_maps_reasons_to_a_status():
    s = runstate.initial_state(set(), TERMS, STATES)
    assert runstate.fold(s, "run_finished", {"reason": "done"}).status == "finished"
    assert runstate.fold(s, "run_finished", {"reason": "stopped"}).status == "finished"
    assert runstate.fold(s, "run_finished", {"reason": "blocked"}).status == "blocked"
    assert runstate.fold(s, "run_finished", {"reason": "crashed"}).status == "blocked"


def test_the_log_is_capped():
    s = runstate.initial_state(set(), TERMS, STATES)
    for i in range(runstate.MAX_LOG + 50):
        s = runstate.fold(s, "listing_saved", {"name": f"Club {i}", "city": "X", "state": "UT"})
    assert len(s.log) == runstate.MAX_LOG
    assert "Club 0" not in " ".join(s.log), "oldest lines must be dropped, not newest"


def test_an_unknown_event_is_ignored():
    s = runstate.initial_state(set(), TERMS, STATES)
    assert runstate.fold(s, "not_a_real_event", {"x": 1}) == s
