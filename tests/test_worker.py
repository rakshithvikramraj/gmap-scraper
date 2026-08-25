"""The worker thread's outcome matrix, with no Tk and no browser.

_run_worker is driven as an unbound function against a stub that only needs an
event queue, which is the whole point of the worker touching nothing but
self.events: no window has to exist to test what it decides.
"""

import queue
import types

import app
import scrape


class _Stub:
    """Enough of App for _run_worker, with no Tk involved."""

    def __init__(self):
        self.events = queue.Queue()


def _run(monkeypatch, *, blocked=False, stopped=False, raises=False, enrich=True):
    calls = []
    monkeypatch.setattr(scrape, "read_cache", lambda *a, **k: ([], set()))
    monkeypatch.setattr(scrape, "write_csv", lambda *a, **k: calls.append("csv"))
    monkeypatch.setattr(scrape, "stop_requested", lambda: stopped)

    def fake_stage1(*a, **k):
        calls.append("stage1")
        if blocked:
            scrape.emit("blocked", term="t", state="s", consecutive=3)
        if raises:
            raise RuntimeError("boom")

    monkeypatch.setattr(scrape, "run_stage1", fake_stage1)
    monkeypatch.setattr(scrape, "run_stage2", lambda *a, **k: calls.append("stage2"))

    stub = _Stub()
    app.App._run_worker(stub, {"terms": ["t"], "states": ["s"], "limit": None,
                               "headed": False, "force": False, "enrich": enrich})
    kinds = []
    while not stub.events.empty():
        kinds.append(stub.events.get_nowait())
    return calls, kinds


def test_a_clean_run_enriches_and_reports_done(monkeypatch):
    calls, kinds = _run(monkeypatch)
    assert "stage2" in calls
    assert kinds[-1] == ("run_finished", {"reason": "done"})


def test_a_blocked_run_never_enriches(monkeypatch):
    calls, kinds = _run(monkeypatch, blocked=True)
    assert "stage2" not in calls, "enriching after a block wastes hours behind a paused panel"
    assert kinds[-1] == ("run_finished", {"reason": "blocked"})


def test_a_stopped_run_reports_stopped(monkeypatch):
    calls, kinds = _run(monkeypatch, stopped=True)
    assert "stage2" not in calls
    assert kinds[-1] == ("run_finished", {"reason": "stopped"})


def test_a_crash_still_reports_run_finished(monkeypatch):
    calls, kinds = _run(monkeypatch, raises=True)
    assert kinds[-1] == ("run_finished", {"reason": "crashed"})
    assert any(k == "query_failed" for k, _ in kinds)


def test_enrichment_can_be_switched_off(monkeypatch):
    calls, _ = _run(monkeypatch, enrich=False)
    assert "stage2" not in calls
