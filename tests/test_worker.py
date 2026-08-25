"""The worker thread's outcome matrix, with no Tk and no browser.

_run_worker is driven as an unbound function against a stub that only needs an
event queue, which is the whole point of the worker touching nothing but
self.events: no window has to exist to test what it decides.
"""

import queue
import types

import app
import geo
import scrape


class _Stub:
    """Enough of App for _run_worker, with no Tk involved."""

    def __init__(self):
        self.events = queue.Queue()


def _run(monkeypatch, *, blocked=False, stopped=False, raises=False, enrich=True):
    calls = []
    captured = {}
    monkeypatch.setattr(scrape, "read_cache", lambda *a, **k: ([], set()))
    monkeypatch.setattr(scrape, "write_csv", lambda *a, **k: calls.append("csv"))
    monkeypatch.setattr(scrape, "stop_requested", lambda: stopped)

    def fake_stage1(terms, places, **kwargs):
        calls.append("stage1")
        captured["terms"] = terms
        captured["places"] = places
        if blocked:
            scrape.emit("blocked", term="t", state="s", consecutive=3)
        if raises:
            raise RuntimeError("boom")

    monkeypatch.setattr(scrape, "run_stage1", fake_stage1)
    monkeypatch.setattr(scrape, "run_stage2", lambda *a, **k: calls.append("stage2"))

    stub = _Stub()
    app.App._run_worker(stub, {"terms": ["t"], "limit": None,
                               "selection": {"United States": {"Texas": ["Austin"]}},
                               "headed": False, "force": False, "enrich": enrich})
    kinds = []
    while not stub.events.empty():
        kinds.append(stub.events.get_nowait())
    return calls, kinds, captured


def test_a_clean_run_enriches_and_reports_done(monkeypatch):
    calls, kinds, _ = _run(monkeypatch)
    assert "stage2" in calls
    assert kinds[-1] == ("run_finished", {"reason": "done"})


def test_a_blocked_run_never_enriches(monkeypatch):
    calls, kinds, _ = _run(monkeypatch, blocked=True)
    assert "stage2" not in calls, "enriching after a block wastes hours behind a paused panel"
    assert kinds[-1] == ("run_finished", {"reason": "blocked"})


def test_a_stopped_run_reports_stopped(monkeypatch):
    calls, kinds, _ = _run(monkeypatch, stopped=True)
    assert "stage2" not in calls
    assert kinds[-1] == ("run_finished", {"reason": "stopped"})


def test_a_crash_still_reports_run_finished(monkeypatch):
    calls, kinds, _ = _run(monkeypatch, raises=True)
    assert kinds[-1] == ("run_finished", {"reason": "crashed"})
    assert any(k == "query_failed" for k, _ in kinds)


def test_enrichment_can_be_switched_off(monkeypatch):
    calls, _, _ = _run(monkeypatch, enrich=False)
    assert "stage2" not in calls


def test_the_worker_passes_places_not_state_names(monkeypatch):
    # The GUI stores a Selection dict; run_stage1 takes geo.Place. Nothing
    # else catches the mismatch, because run_stage1 is monkeypatched here.
    _, _, captured = _run(monkeypatch)
    assert all(isinstance(p, geo.Place) for p in captured["places"])
    assert captured["places"] == [
        geo.Place(country="United States", region="Texas", city="Austin")]


def test_the_worker_searches_every_place_the_selection_names(monkeypatch):
    """A city-level selection must expand to one place per city."""
    calls = []
    monkeypatch.setattr(scrape, "read_cache", lambda *a, **k: ([], set()))
    monkeypatch.setattr(scrape, "write_csv", lambda *a, **k: None)
    monkeypatch.setattr(scrape, "stop_requested", lambda: False)
    monkeypatch.setattr(scrape, "run_stage2", lambda *a, **k: None)
    monkeypatch.setattr(scrape, "run_stage1",
                        lambda terms, places, **kw: calls.append(places))
    stub = _Stub()
    app.App._run_worker(stub, {
        "terms": ["gym"], "limit": None, "enrich": False,
        "headed": False, "force": False,
        "selection": {"United States": {"Texas": ["Houston", "Dallas"], "Utah": []},
                      "Japan": {}}})
    assert [p.query_text() for p in calls[0]] == [
        "Japan",
        "Houston, Texas, United States",
        "Dallas, Texas, United States",
        "Utah, United States",
    ]
