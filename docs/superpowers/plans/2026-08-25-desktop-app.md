# Club Scraper Desktop App Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Tkinter window that runs the existing club scraper, shows a multi-hour run at a glance, and can be handed to macOS and Windows teammates from a private git repository.

**Architecture:** Tkinter owns the main thread; the scrape runs in a worker thread and reports through an event stream drained on a `root.after` tick. A pure module folds those events into plain data, so the interesting logic is unit-testable with no browser, network or display. Custom `Canvas` widgets supply the rounded buttons and the coverage grid that `ttk` cannot draw.

**Tech Stack:** Python 3.11+, Tkinter/`ttk` (forced `clam` theme), Playwright (existing), `uv` for distribution, pytest.

**Spec:** `docs/superpowers/specs/2026-08-25-desktop-app-design.md`

**Visual design:** `design/Main.dc.html`, `design/Running.dc.html`, `design/Results.dc.html`, `design/Blocked.dc.html` and `design/canvas.json`. These carry exact spacing, sizes and structure — read the relevant one before laying out a panel rather than inventing geometry.

## Global Constraints

- Python >= 3.11. Development machine has 3.13.2.
- **`scrape.py`'s existing 74 tests must stay green after every task.** They are the guard on the only working, tested code this plan touches.
- `emit()` calls are added **beside** existing `print()` calls, never replacing them. CLI output must stay byte-identical.
- Only the run path emits: `scrape_query`, `run_stage1`, `run_stage2`. Sheets and auth messages stay print-only.
- **No test may make a network request, launch a browser, or open a Tk window.** Widgets are tested only through their pure geometry helpers.
- Tkinter is not thread-safe: **only the main thread touches a widget.** Nothing in `runstate.py`, `settings.py` or the worker function may import or call `tkinter`.
- Playwright uses the **sync** API on the worker thread. No asyncio anywhere.
- `ttk` is forced to the `clam` theme on every platform. Never use `aqua` or `vista`.
- Palette is exactly:
  ```python
  PALETTE = {
      "bg": "#f9f9f6", "panel": "#f3f3f0", "sunken": "#ededea", "line": "#dad9d5",
      "ink": "#2c2a25", "muted": "#71706b", "faint": "#86857f", "field": "#fefdfc",
      "accent": "#3b6fbc", "accent_d": "#2559a3",
      "done": "#50a069", "partial": "#dea645", "failed": "#c74f47",
  }
  ```
- Font faces resolve at startup against `tkinter.font.families()`, first match wins:
  `UI_FACES = ("Segoe UI", "Helvetica Neue", "DejaVu Sans")`,
  `MONO_FACES = ("Consolas", "Menlo", "DejaVu Sans Mono", "Courier New")`.
- Every filesystem path is a `pathlib.Path`. No `~` string concatenation.
- The scraper's pacing (`PAUSE_LISTING`, `PAUSE_QUERY`) is untouchable.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `scrape.py` | Unchanged scraping. Gains an event hook and a stop flag. |
| `runstate.py` | Pure. Folds an event stream into a `RunState`; formats durations and fill rates. No I/O, no tkinter. |
| `settings.py` | Loads and saves the remembered term list, states and options. Pure JSON plus one file read/write. No tkinter. |
| `widgets.py` | `RoundedButton` and `CoverageGrid`, drawn on a `tk.Canvas`, plus their pure geometry helpers. |
| `app.py` | The window: theme, layout, the four states, the worker thread and the event pump. |
| `pyproject.toml`, `uv.lock` | Locked dependencies so every teammate resolves identical versions. |
| `setup.sh`, `setup.bat`, `run.sh`, `run.bat` | One-command bootstrap and launcher per platform. |
| `tests/test_runstate.py` | Reducer and formatter tests. |
| `tests/test_settings.py` | Settings round-trip and fallback tests. |
| `tests/test_widgets.py` | Pure geometry tests for the custom widgets. |

---

### Task 1: Event hook and stop flag in `scrape.py`

**Files:**
- Modify: `scrape.py`
- Test: `tests/test_events.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `subscribe(listener) -> None` where `listener(kind: str, data: dict)`
  - `unsubscribe(listener) -> None`
  - `emit(kind: str, **data) -> None`
  - `request_stop() -> None`, `stop_requested() -> bool`, `clear_stop() -> None`
  - Twelve event kinds, listed in spec section 5.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_events.py`:

```python
import scrape


def test_subscribe_receives_emitted_events():
    seen = []
    listener = lambda kind, data: seen.append((kind, data))
    scrape.subscribe(listener)
    try:
        scrape.emit("query_start", term="padel club", state="Utah")
    finally:
        scrape.unsubscribe(listener)
    assert seen == [("query_start", {"term": "padel club", "state": "Utah"})]


def test_unsubscribe_stops_delivery():
    seen = []
    listener = lambda kind, data: seen.append(kind)
    scrape.subscribe(listener)
    scrape.unsubscribe(listener)
    scrape.emit("query_start", term="x", state="y")
    assert seen == []


def test_emit_survives_a_listener_that_raises():
    seen = []

    def boom(kind, data):
        raise RuntimeError("listener bug")

    scrape.subscribe(boom)
    scrape.subscribe(lambda kind, data: seen.append(kind))
    try:
        scrape.emit("query_start", term="x", state="y")
    finally:
        scrape.unsubscribe(boom)
        scrape._listeners.clear()
    assert seen == ["query_start"], "a broken listener must not stop the run or block others"


def test_emit_with_no_listeners_is_harmless():
    scrape._listeners.clear()
    scrape.emit("query_start", term="x", state="y")


def test_stop_flag_round_trip():
    scrape.clear_stop()
    assert scrape.stop_requested() is False
    scrape.request_stop()
    assert scrape.stop_requested() is True
    scrape.clear_stop()
    assert scrape.stop_requested() is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_events.py -v`
Expected: FAIL with `AttributeError: module 'scrape' has no attribute 'subscribe'`

- [ ] **Step 3: Add the hook to `scrape.py`**

Add `import threading` to the stdlib group of the top import block (alphabetically between `re` and `time`). Then add this block immediately above the `# Stage 1` Maps-scraper section:

```python
# ---------------------------------------------------------------------------
# Event hook - lets a GUI observe a run without parsing printed output
# ---------------------------------------------------------------------------

_listeners: list = []
_stop = threading.Event()


def subscribe(listener) -> None:
    """Register listener(kind, data), called for every emitted event."""
    if listener not in _listeners:
        _listeners.append(listener)


def unsubscribe(listener) -> None:
    """Remove a previously registered listener. Silent if absent."""
    if listener in _listeners:
        _listeners.remove(listener)


def emit(kind: str, **data) -> None:
    """Notify every listener.

    Never raises. A listener is UI code running on another thread; a bug there
    must not abort a scrape that has been running for hours.
    """
    for listener in list(_listeners):
        try:
            listener(kind, data)
        except Exception:
            pass


def request_stop() -> None:
    """Ask the current run to stop at the next listing boundary."""
    _stop.set()


def stop_requested() -> bool:
    return _stop.is_set()


def clear_stop() -> None:
    """Reset the flag. Call before starting a run."""
    _stop.clear()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_events.py -v`
Expected: 5 passed

- [ ] **Step 5: Add emits beside the prints in `scrape_query`**

Do not remove or alter any `print()`. Add an `emit()` next to each, and two stop checks.

After `print(f"  single result for {term} / {state}")` add:
```python
        emit("listings_found", term=term, state=state, count=1, at_cap=False)
```

In the single-result success branch, after `append_record(...)` and before `return 1, 0, True`, add:
```python
            emit("listing_saved", name=record.get("name", ""),
                 city=record.get("city", ""), state=record.get("state", ""))
```
This requires binding the record first: change `append_record(scrape_listing(page, page.url, term, state))` to
```python
            record = scrape_listing(page, page.url, term, state)
            append_record(record)
```

After `print(f"  skipped the single result: ...")` add:
```python
            emit("listing_failed", error=f"{type(exc).__name__}: {exc}")
```

After `print(f"  no results feed for {term} / {state}")` add:
```python
        emit("listings_found", term=term, state=state, count=0, at_cap=False)
```

After `print(f"  {len(links)} listings for {term} / {state}")` add:
```python
    emit("listings_found", term=term, state=state, count=len(links),
         at_cap=len(links) >= 118)
```

In the per-listing loop, replace the body so a saved record emits and a stop is honoured:
```python
    for link in links:
        if stop_requested():
            break
        try:
            record = scrape_listing(page, link, term, state)
            append_record(record)
            scraped += 1
            emit("listing_saved", name=record.get("name", ""),
                 city=record.get("city", ""), state=record.get("state", ""))
        except Exception as exc:
            failed += 1
            print(f"  skipped a listing: {type(exc).__name__}: {exc}")
            emit("listing_failed", error=f"{type(exc).__name__}: {exc}")
        _pause(PAUSE_LISTING)
```

A stop mid-query leaves `complete` as computed, but `run_stage1` will not mark the pair done, because Step 6 checks the flag before marking.

- [ ] **Step 6: Add emits and the stop check to `run_stage1`**

Immediately after `_, done = read_cache()` add:
```python
    emit("run_start", terms=list(terms), states=list(states),
         total_queries=len(terms) * len(states))
```

Inside the state loop, after `print(f"skip (cached): {term} / {state}")` add:
```python
                    emit("query_skipped", term=term, state=state)
```

After `print(f"searching: {term} / {state}")` add:
```python
                emit("query_start", term=term, state=state)
```

At the top of the state loop body, before the cached check, add the stop check:
```python
                if stop_requested():
                    browser.close()
                    return
```

After `print(f"  FAILED {term} / {state}: {exc}")` add:
```python
                    emit("query_failed", term=term, state=state,
                         error=f"{type(exc).__name__}: {exc}")
```

Inside the circuit-breaker branch, after its `print(...)` call, add:
```python
                        emit("blocked", term=term, state=state,
                             consecutive=consecutive_failures)
```

Replace the marking block so it emits the outcome either way:
```python
                consecutive_failures = 0
                if should_mark_done(failed, complete) and not stop_requested():
                    mark_pair_done(term, state)
                else:
                    reason = (
                        f"{failed} listing(s) failed" if failed
                        else "coverage incomplete"
                    )
                    print(
                        f"  {reason}; leaving {term} / {state} unmarked "
                        "so a re-run retries it"
                    )
                emit("query_done", term=term, state=state, scraped=scraped,
                     failed=failed, complete=complete)
```

- [ ] **Step 7: Add emits and the stop check to `run_stage2`**

After `print(f"enriching {len(targets)} club websites")` add:
```python
    emit("stage2_start", total=len(targets))
```

In the enrichment loop, add a stop check at the top of the body and an emit after `append_record(updated)`:
```python
    for index, record in enumerate(targets, start=1):
        if stop_requested():
            break
        ...
        append_record(updated)
        emit("enriched", index=index, total=len(targets),
             name=record.get("name", ""), error=enrichment["enrich_error"])
```

- [ ] **Step 8: Run the whole suite**

Run: `pytest -v`
Expected: **79 passed** (74 existing, unchanged, plus 5 new).

If any of the 74 fails, stop and report it — the guard has caught a real regression, and no emit is worth breaking the scraper for.

- [ ] **Step 9: Verify CLI output is unchanged**

Run: `python scrape.py --help`
Expected: identical usage text, all eight flags.

- [ ] **Step 10: Commit**

```bash
git add scrape.py tests/test_events.py
git commit -m "feat: event hook and stop flag for GUI observation"
```

---

### Task 2: `runstate.py` — the reducer

**Files:**
- Create: `runstate.py`
- Test: `tests/test_runstate.py`

**Interfaces:**
- Consumes: the twelve event kinds from Task 1.
- Produces:
  - `RunState` dataclass with fields `status, coverage, clubs, queries_done, queries_total, started_at, current, log, failures, at_cap, stage, enrich_index, enrich_total, finish_reason`
  - `initial_state(done_pairs: set, terms: list[str], states: list[str]) -> RunState`
  - `fold(state: RunState, kind: str, data: dict, now: float | None = None) -> RunState`

This module is the reason the app is testable. It must not import `tkinter`, must not read files, and must not mutate its argument.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_runstate.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_runstate.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'runstate'`

- [ ] **Step 3: Write `runstate.py`**

```python
"""Folds a scrape's event stream into the data the window paints.

Pure by design: no tkinter, no files, no clock of its own. That is what makes
the app's behaviour testable without opening a window or a browser.
"""

from dataclasses import dataclass, field, replace

MAX_LOG = 200

# Ordered so a state can advance from transient to settled, but a settled
# outcome can only ever get worse. "active" must rank BELOW "done", or a
# state that starts scraping can never finish: query_done would be unable to
# replace the "active" that query_start set.
_RANK = {"pending": 0, "active": 1, "done": 2, "partial": 3, "failed": 4}


@dataclass
class RunState:
    status: str = "idle"                 # idle | running | finished | blocked
    coverage: dict = field(default_factory=dict)
    clubs: int = 0
    queries_done: int = 0
    queries_total: int = 0
    started_at: float | None = None
    current: str = ""
    log: list = field(default_factory=list)
    failures: list = field(default_factory=list)
    at_cap: list = field(default_factory=list)
    stage: str = ""
    enrich_index: int = 0
    enrich_total: int = 0
    finish_reason: str = ""


def initial_state(done_pairs, terms, states) -> RunState:
    """Seed from the resume cache, before any event arrives.

    A state counts as done only when every term is cached for it, because one
    outstanding term still means work to do there.
    """
    coverage = {}
    for state in states:
        cached = sum(1 for term in terms if (term, state) in done_pairs)
        coverage[state] = "done" if cached == len(terms) and terms else "pending"
    queries_done = sum(
        1 for term in terms for state in states if (term, state) in done_pairs
    )
    return RunState(
        coverage=coverage,
        queries_total=len(terms) * len(states),
        queries_done=queries_done,
    )


def _worse(current: str, candidate: str) -> str:
    return candidate if _RANK[candidate] > _RANK.get(current, 0) else current


def _outcome(failed: int, complete: bool) -> str:
    if not complete:
        return "partial"
    return "failed" if failed else "done"


def fold(state: RunState, kind: str, data: dict, now: float | None = None) -> RunState:
    """Return the state after one event. Never mutates `state`."""
    s = replace(
        state,
        coverage=dict(state.coverage),
        log=list(state.log),
        failures=list(state.failures),
        at_cap=list(state.at_cap),
    )

    def note(line: str) -> None:
        s.log.append(line)
        if len(s.log) > MAX_LOG:
            del s.log[: len(s.log) - MAX_LOG]

    if kind == "run_start":
        s.status = "running"
        s.stage = "Collecting listings"
        s.queries_total = data["total_queries"]
        s.started_at = now
        note(f"Starting {data['total_queries']} searches")

    elif kind == "query_start":
        s.coverage[data["state"]] = _worse(s.coverage.get(data["state"], "pending"), "active")
        s.current = f"Searching “{data['term']}” in {data['state']}"
        note(s.current)

    elif kind == "query_skipped":
        s.queries_done += 1
        note(f"Skipping {data['state']} — already done")

    elif kind == "listings_found":
        s.current = f"{data['count']} listings in {data['state']}"
        note(s.current)
        if data.get("at_cap") and data["state"] not in s.at_cap:
            s.at_cap.append(data["state"])

    elif kind == "listing_saved":
        s.clubs += 1
        where = f" · {data['city']}, {data['state']}" if data.get("city") else ""
        note(f"Saved “{data['name']}”{where}")

    elif kind == "listing_failed":
        note(f"Skipped one listing — {data.get('error', 'unknown problem')}")

    elif kind == "query_done":
        s.queries_done += 1
        s.coverage[data["state"]] = _worse(
            s.coverage.get(data["state"], "pending"),
            _outcome(data["failed"], data["complete"]),
        )
        note(f"Finished {data['state']} — {data['scraped']} clubs")

    elif kind == "query_failed":
        s.queries_done += 1
        s.coverage[data["state"]] = _worse(s.coverage.get(data["state"], "pending"), "failed")
        s.failures.append((data["state"], data.get("error", "")))
        note(f"{data['state']} failed — {data.get('error', '')}")

    elif kind == "blocked":
        s.status = "blocked"
        s.finish_reason = "blocked"
        note(f"Stopped at {data['state']} — Google is not answering")

    elif kind == "stage2_start":
        s.stage = "Looking up contact details"
        s.enrich_total = data["total"]
        s.enrich_index = 0
        note(f"Looking up contact details for {data['total']} clubs")

    elif kind == "enriched":
        s.enrich_index = data["index"]
        s.current = f"Contact details {data['index']} of {data['total']}"

    elif kind == "run_finished":
        s.finish_reason = data["reason"]
        s.status = "finished" if data["reason"] in ("done", "stopped") else "blocked"
        s.current = ""

    return s
```

Note the coverage rule: `_worse` means a state that failed under one search term stays red even when a later term succeeds there. Reporting the worst outcome is the honest choice — the alternative silently paints over the very failure the operator needs to see.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_runstate.py -v`
Expected: 18 passed

- [ ] **Step 5: Run the whole suite**

Run: `pytest -v`
Expected: **97 passed** (79 from Task 1 plus 18 new).

- [ ] **Step 6: Commit**

```bash
git add runstate.py tests/test_runstate.py
git commit -m "feat: pure reducer folding scrape events into window state"
```

---

### Task 3: `runstate.py` — formatters

**Files:**
- Modify: `runstate.py`
- Test: `tests/test_runstate.py`

**Interfaces:**
- Consumes: `RunState` from Task 2.
- Produces:
  - `elapsed(state: RunState, now: float) -> str`
  - `remaining(state: RunState, now: float) -> str`
  - `fill_rate_rows(records: list[dict], columns: list[str]) -> list[tuple[str, float]]`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_runstate.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_runstate.py -k "elapsed or remaining or fill_rate" -v`
Expected: 8 failures with `AttributeError: module 'runstate' has no attribute 'elapsed'`

- [ ] **Step 3: Write the implementation**

Append to `runstate.py`:

```python
def _hm(seconds: float) -> str:
    """Duration as "0m", "13m" or "1h 13m"."""
    minutes = int(seconds // 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"


def elapsed(state: RunState, now: float) -> str:
    """How long the run has been going, or "" before it starts."""
    if state.started_at is None:
        return ""
    return _hm(max(0.0, now - state.started_at))


def remaining(state: RunState, now: float) -> str:
    """Rough time left, extrapolated from the pace so far.

    Blank until at least one query has finished, because an estimate from a
    sample of zero is a guess dressed up as information.
    """
    if state.started_at is None or not state.queries_done:
        return ""
    left = state.queries_total - state.queries_done
    if left <= 0:
        return ""
    per_query = (now - state.started_at) / state.queries_done
    return _hm(per_query * left)


def fill_rate_rows(records, columns) -> list:
    """(column, fraction non-empty) for each column, in the order given.

    A value of 0 counts as filled: a club with zero reviews is real data, and
    treating it as missing would make the reviews column permanently look
    healthy or permanently look broken depending on which way you counted.
    """
    if not records:
        return []
    total = len(records)
    return [
        (
            column,
            sum(1 for r in records if str(r.get(column, "")).strip()) / total,
        )
        for column in columns
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest -v`
Expected: **105 passed**

- [ ] **Step 5: Commit**

```bash
git add runstate.py tests/test_runstate.py
git commit -m "feat: duration and fill-rate formatters"
```

---

### Task 4: `settings.py` — remembered preferences

**Files:**
- Create: `settings.py`
- Test: `tests/test_settings.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `DEFAULTS: dict`
  - `load(path: Path) -> dict`
  - `save(prefs: dict, path: Path) -> None`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_settings.py`:

```python
import json
import settings


def test_load_returns_defaults_when_the_file_is_absent(tmp_path):
    prefs = settings.load(tmp_path / "nope.json")
    assert prefs == settings.DEFAULTS
    assert prefs is not settings.DEFAULTS, "callers must not be able to edit the defaults"


def test_save_then_load_round_trips(tmp_path):
    path = tmp_path / "settings.json"
    settings.save({"terms": ["padel club"], "states": ["Utah"], "enrich": False}, path)
    assert settings.load(path)["terms"] == ["padel club"]
    assert settings.load(path)["enrich"] is False


def test_a_corrupt_file_falls_back_instead_of_crashing(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{not json")
    assert settings.load(path) == settings.DEFAULTS


def test_unknown_keys_are_dropped_and_missing_keys_are_filled(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"terms": ["x"], "nonsense": 1}))
    prefs = settings.load(path)
    assert prefs["terms"] == ["x"]
    assert "nonsense" not in prefs
    assert prefs["enrich"] == settings.DEFAULTS["enrich"]


def test_save_creates_the_parent_directory(tmp_path):
    path = tmp_path / "data" / "settings.json"
    settings.save({"terms": ["x"]}, path)
    assert path.exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_settings.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'settings'`

- [ ] **Step 3: Write `settings.py`**

```python
"""Remembered window preferences.

Preferences only. This is not the resume cache: deleting this file loses no
scraped data, only the operator's choice of search terms and states.
"""

import json
from pathlib import Path

import scrape

DEFAULTS = {
    "terms": list(scrape.SEARCH_TERMS),
    "states": list(scrape.STATES),
    "enrich": True,
    "headed": False,
    "force": False,
    "limit": None,
}


def load(path: Path) -> dict:
    """Preferences from `path`, falling back to defaults for anything missing.

    Any unreadable or malformed file yields the defaults rather than an error:
    a corrupted preferences file must never stop someone opening the app.
    """
    prefs = {key: (list(value) if isinstance(value, list) else value)
             for key, value in DEFAULTS.items()}
    try:
        stored = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return prefs
    if not isinstance(stored, dict):
        return prefs
    for key in DEFAULTS:
        if key in stored:
            prefs[key] = stored[key]
    return prefs


def save(prefs: dict, path: Path) -> None:
    """Write the known preference keys to `path`, creating its directory."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    keep = {key: prefs.get(key, DEFAULTS[key]) for key in DEFAULTS}
    target.write_text(json.dumps(keep, indent=2) + "\n", encoding="utf-8")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest -v`
Expected: **110 passed**

- [ ] **Step 5: Commit**

```bash
git add settings.py tests/test_settings.py
git commit -m "feat: remembered search terms, states and options"
```

---

### Task 5: `widgets.py` — `RoundedButton`

**Files:**
- Create: `widgets.py`
- Test: `tests/test_widgets.py`

**Interfaces:**
- Consumes: the `PALETTE` from Global Constraints.
- Produces:
  - `rounded_points(x1, y1, x2, y2, r) -> list[float]`
  - `button_width(text_width: int, pad_x: int, min_width: int = 0) -> int`
  - `class RoundedButton(tk.Canvas)` with `__init__(master, text, command=None, *, kind="secondary", font=None, radius=5, pad_x=14, height=28, min_width=0)`, `set_enabled(bool)`, `set_text(str)`

`ttk` cannot draw a rounded corner under any theme, so this is a `Canvas` doing it by hand. A drawn control gets none of a real button's behaviour for free, so every behaviour below is a requirement: a thing that looks like a button and does not act like one is worse than a square button.

- [ ] **Step 1: Write the failing tests**

These cover only the pure geometry — the drawing and event behaviour is verified by hand in Task 9, because a test that opens a Tk window would violate the no-display constraint.

Create `tests/test_widgets.py`:

```python
import scrape
import widgets


def test_rounded_points_traces_the_corners_in_order():
    pts = widgets.rounded_points(0, 0, 100, 40, 5)
    assert len(pts) == 24, "12 x,y pairs: two per corner plus the corner itself"
    assert pts[0] == 5 and pts[1] == 0, "starts just right of the top-left corner"


def test_rounded_points_stays_inside_the_rectangle():
    pts = widgets.rounded_points(10, 20, 110, 60, 6)
    xs, ys = pts[0::2], pts[1::2]
    assert min(xs) == 10 and max(xs) == 110
    assert min(ys) == 20 and max(ys) == 60


def test_radius_is_clamped_to_the_shorter_side():
    pts = widgets.rounded_points(0, 0, 100, 10, 40)
    ys = pts[1::2]
    assert min(ys) == 0 and max(ys) == 10, "an oversized radius must not bulge past the box"


def test_a_zero_radius_gives_square_corners():
    pts = widgets.rounded_points(0, 0, 10, 10, 0)
    assert pts[0] == 0 and pts[1] == 0


def test_button_width_pads_both_sides_of_the_label():
    assert widgets.button_width(60, 14) == 88


def test_button_width_respects_a_minimum():
    assert widgets.button_width(10, 14, min_width=120) == 120


def test_every_state_has_an_abbreviation():
    missing = [s for s in scrape.ALL_50 if s not in widgets.STATE_ABBR]
    assert missing == [], f"no abbreviation for {missing}"
    assert len(set(widgets.STATE_ABBR.values())) == 50, "abbreviations must be unique"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_widgets.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'widgets'`

- [ ] **Step 3: Write the geometry and the state map**

Create `widgets.py`:

```python
"""Canvas-drawn controls that ttk cannot provide.

ttk widgets are square under every theme, so the rounded buttons in the design
are drawn by hand here. A drawn control inherits none of a real button's
behaviour, so RoundedButton reimplements hover, press, focus, keyboard
activation and disabled state explicitly.
"""

import tkinter as tk

PALETTE = {
    "bg": "#f9f9f6", "panel": "#f3f3f0", "sunken": "#ededea", "line": "#dad9d5",
    "ink": "#2c2a25", "muted": "#71706b", "faint": "#86857f", "field": "#fefdfc",
    "accent": "#3b6fbc", "accent_d": "#2559a3",
    "done": "#50a069", "partial": "#dea645", "failed": "#c74f47",
}

STATE_ABBR = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
    "California": "CA", "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE",
    "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID",
    "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS",
    "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD",
    "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN",
    "Mississippi": "MS", "Missouri": "MO", "Montana": "MT", "Nebraska": "NE",
    "Nevada": "NV", "New Hampshire": "NH", "New Jersey": "NJ",
    "New Mexico": "NM", "New York": "NY", "North Carolina": "NC",
    "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK", "Oregon": "OR",
    "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC",
    "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT",
    "Vermont": "VT", "Virginia": "VA", "Washington": "WA",
    "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY",
}


def rounded_points(x1, y1, x2, y2, r):
    """Corner points for a rounded rectangle, for create_polygon(smooth=True).

    Each corner contributes three points so the smoothing curves tightly
    instead of bowing the straight edges. The radius is clamped to half the
    shorter side, otherwise an oversized radius bulges outside the box.
    """
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    return [
        x1 + r, y1, x2 - r, y1, x2, y1,
        x2, y1 + r, x2, y2 - r, x2, y2,
        x2 - r, y2, x1 + r, y2, x1, y2,
        x1, y2 - r, x1, y1 + r, x1, y1,
    ]


def button_width(text_width: int, pad_x: int, min_width: int = 0) -> int:
    """Button width for a label of `text_width` pixels, padded both sides."""
    return max(min_width, int(text_width) + pad_x * 2)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_widgets.py -v`
Expected: 7 passed

- [ ] **Step 5: Add the `RoundedButton` class**

Append to `widgets.py`:

```python
_KINDS = {
    "primary": {
        "fill": PALETTE["accent"], "hover": PALETTE["accent_d"],
        "text": "#ffffff", "border": PALETTE["accent"],
    },
    "secondary": {
        "fill": PALETTE["field"], "hover": PALETTE["sunken"],
        "text": PALETTE["ink"], "border": PALETTE["line"],
    },
    "danger": {
        "fill": PALETTE["field"], "hover": "#f7ecea",
        "text": PALETTE["failed"], "border": PALETTE["failed"],
    },
}
_DISABLED = {
    "fill": PALETTE["sunken"], "hover": PALETTE["sunken"],
    "text": PALETTE["faint"], "border": PALETTE["line"],
}


class RoundedButton(tk.Canvas):
    """A button with rounded corners, drawn because ttk cannot draw one.

    Reimplements what ttk would have given for free: hover, press, keyboard
    focus and activation, and a disabled state. Press tracks the pointer, so
    dragging off the button and releasing cancels the click, which is what
    every real button does and what a naive press/release binding gets wrong.
    """

    def __init__(self, master, text, command=None, *, kind="secondary",
                 font=None, radius=5, pad_x=14, height=28, min_width=0, **kw):
        super().__init__(master, height=height, highlightthickness=0, bd=0,
                         bg=kw.pop("bg", PALETTE["bg"]), takefocus=1, **kw)
        self._text = text
        self._command = command
        self._colors = _KINDS.get(kind, _KINDS["secondary"])
        self._font = font
        self._radius = radius
        self._pad_x = pad_x
        self._height = height
        self._min_width = min_width
        self._enabled = True
        self._hover = False
        self._pressed = False
        self._focused = False

        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<FocusIn>", self._on_focus_in)
        self.bind("<FocusOut>", self._on_focus_out)
        self.bind("<space>", self._on_key)
        self.bind("<Return>", self._on_key)
        self.bind("<Configure>", lambda _e: self._draw())

        self._resize()

    # -- public ------------------------------------------------------------
    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        self.configure(takefocus=1 if enabled else 0)
        if not enabled:
            self._hover = self._pressed = False
        self._draw()

    def set_text(self, text: str) -> None:
        self._text = text
        self._resize()

    # -- internals ---------------------------------------------------------
    def _resize(self) -> None:
        # self._font is a tkinter.font.Font, which measures text itself. Going
        # via tk.font would need a separate import that tkinter does not give
        # you with "import tkinter as tk".
        measured = self._font.measure(self._text) if self._font else len(self._text) * 7
        self.configure(width=button_width(measured, self._pad_x, self._min_width))
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        colors = self._colors if self._enabled else _DISABLED
        fill = colors["hover"] if (self._hover and self._enabled) else colors["fill"]
        if self._pressed and self._enabled:
            fill = colors["hover"]
        w = max(self.winfo_width(), 1)
        h = max(self.winfo_height(), self._height)
        inset = 1
        self.create_polygon(
            rounded_points(inset, inset, w - inset, h - inset, self._radius),
            smooth=True, fill=fill, outline=colors["border"], width=1,
        )
        if self._focused and self._enabled:
            self.create_polygon(
                rounded_points(inset + 2, inset + 2, w - inset - 2, h - inset - 2,
                               max(0, self._radius - 2)),
                smooth=True, fill="", outline=PALETTE["accent_d"], width=1,
            )
        self.create_text(w / 2, h / 2, text=self._text, fill=colors["text"],
                         font=self._font)

    def _invoke(self) -> None:
        if self._enabled and self._command:
            self._command()

    def _inside(self, event) -> bool:
        return 0 <= event.x <= self.winfo_width() and 0 <= event.y <= self.winfo_height()

    def _on_enter(self, _e): self._hover = True; self._draw()
    def _on_leave(self, _e): self._hover = False; self._pressed = False; self._draw()
    def _on_focus_in(self, _e): self._focused = True; self._draw()
    def _on_focus_out(self, _e): self._focused = False; self._draw()

    def _on_press(self, _e):
        if not self._enabled:
            return
        self._pressed = True
        self.focus_set()
        self._draw()

    def _on_release(self, event):
        was_pressed, self._pressed = self._pressed, False
        self._draw()
        if was_pressed and self._inside(event):
            self._invoke()

    def _on_key(self, _e):
        self._invoke()
        return "break"
```

- [ ] **Step 6: Run the whole suite**

Run: `pytest -v`
Expected: **117 passed**

- [ ] **Step 7: Commit**

```bash
git add widgets.py tests/test_widgets.py
git commit -m "feat: RoundedButton with hover, press, focus and keyboard states"
```

---

### Task 6: `widgets.py` — `CoverageGrid`

**Files:**
- Modify: `widgets.py`
- Test: `tests/test_widgets.py`

**Interfaces:**
- Consumes: `PALETTE`, `STATE_ABBR` from Task 5.
- Produces:
  - `cell_rects(count, cols, cell_w, cell_h, gap, x0=0, y0=0) -> list[tuple[int, int, int, int]]`
  - `grid_height(count, cols, cell_h, gap) -> int`
  - `class CoverageGrid(tk.Canvas)` with `__init__(master, labels: list[str], *, cols=10, cell_h=44, gap=6, font=None)` and `update_coverage(coverage: dict[str, str]) -> None`

Fifty coloured rectangles on one `Canvas`, not fifty widgets: a repaint is then a single redraw instead of fifty layout passes, which matters when it happens on every event tick.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_widgets.py`:

```python
def test_cell_rects_lays_out_row_by_row():
    rects = widgets.cell_rects(3, cols=2, cell_w=100, cell_h=40, gap=10)
    assert rects[0] == (0, 0, 100, 40)
    assert rects[1] == (110, 0, 210, 40), "second cell sits one gap to the right"
    assert rects[2] == (0, 50, 100, 90), "third cell wraps to the next row"


def test_cell_rects_honours_an_origin():
    rects = widgets.cell_rects(1, cols=5, cell_w=20, cell_h=10, gap=4, x0=7, y0=9)
    assert rects[0] == (7, 9, 27, 19)


def test_cell_rects_of_nothing_is_empty():
    assert widgets.cell_rects(0, cols=10, cell_w=10, cell_h=10, gap=2) == []


def test_grid_height_counts_partial_rows():
    assert widgets.grid_height(50, cols=10, cell_h=44, gap=6) == 44 * 5 + 6 * 4
    assert widgets.grid_height(51, cols=10, cell_h=44, gap=6) == 44 * 6 + 6 * 5
    assert widgets.grid_height(0, cols=10, cell_h=44, gap=6) == 0


def test_status_colour_covers_every_state_the_reducer_can_produce():
    for status in ("pending", "done", "active", "partial", "failed"):
        assert status in widgets.STATUS_COLORS
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_widgets.py -k "cell_rects or grid_height or status_colour" -v`
Expected: 5 failures with `AttributeError: module 'widgets' has no attribute 'cell_rects'`

- [ ] **Step 3: Write the geometry**

Append to `widgets.py`:

```python
import math

STATUS_COLORS = {
    "pending": (PALETTE["sunken"], PALETTE["line"], PALETTE["faint"]),
    "done":    ("#e4f1e8", PALETTE["done"], "#2f6b45"),
    "active":  ("#e3ecf8", PALETTE["accent"], PALETTE["accent_d"]),
    "partial": ("#faf0dc", PALETTE["partial"], "#8a6520"),
    "failed":  ("#f8e8e6", PALETTE["failed"], "#8f342e"),
}


def cell_rects(count, cols, cell_w, cell_h, gap, x0=0, y0=0):
    """Bounding boxes for `count` cells flowing left to right, top to bottom."""
    rects = []
    for index in range(count):
        row, col = divmod(index, cols)
        x = x0 + col * (cell_w + gap)
        y = y0 + row * (cell_h + gap)
        rects.append((x, y, x + cell_w, y + cell_h))
    return rects


def grid_height(count, cols, cell_h, gap) -> int:
    """Pixel height the grid needs, counting a partial final row."""
    if not count:
        return 0
    rows = math.ceil(count / cols)
    return rows * cell_h + (rows - 1) * gap
```

Move `import math` up into the module's import block beside `import tkinter as tk` rather than leaving it mid-file.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_widgets.py -v`
Expected: 12 passed

- [ ] **Step 5: Add the `CoverageGrid` class**

Append to `widgets.py`:

```python
class CoverageGrid(tk.Canvas):
    """Every state's progress as one grid of coloured cells.

    Drawn on a single Canvas rather than built from one widget per state: a
    repaint happens on every event tick, and fifty widgets would mean fifty
    layout passes each time.
    """

    def __init__(self, master, labels, *, cols=10, cell_h=44, gap=6, font=None, **kw):
        super().__init__(master, highlightthickness=0, bd=0,
                         bg=kw.pop("bg", PALETTE["bg"]), **kw)
        self._labels = list(labels)
        self._cols = cols
        self._cell_h = cell_h
        self._gap = gap
        self._font = font
        self._coverage = {}
        self.configure(height=grid_height(len(self._labels), cols, cell_h, gap))
        self.bind("<Configure>", lambda _e: self._draw())

    def update_coverage(self, coverage: dict) -> None:
        self._coverage = dict(coverage)
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        width = max(self.winfo_width(), 1)
        cell_w = max(1, (width - self._gap * (self._cols - 1)) / self._cols)
        rects = cell_rects(len(self._labels), self._cols, cell_w,
                           self._cell_h, self._gap)
        for label, (x1, y1, x2, y2) in zip(self._labels, rects):
            status = self._coverage.get(label, "pending")
            fill, border, ink = STATUS_COLORS.get(status, STATUS_COLORS["pending"])
            self.create_rectangle(x1, y1, x2, y2, fill=fill, outline=border,
                                  width=2 if status == "active" else 1)
            self.create_text((x1 + x2) / 2, (y1 + y2) / 2,
                             text=STATE_ABBR.get(label, label[:2].upper()),
                             fill=ink, font=self._font)
```

- [ ] **Step 6: Run the whole suite**

Run: `pytest -v`
Expected: **122 passed**

- [ ] **Step 7: Commit**

```bash
git add widgets.py tests/test_widgets.py
git commit -m "feat: CoverageGrid drawn as one canvas of state cells"
```

---

### Task 7: `app.py` — window shell, theme and the setup state

**Files:**
- Create: `app.py`
- Test: launch it and look

**Interfaces:**
- Consumes: `settings.load/save/DEFAULTS`, `runstate.initial_state`, `widgets.RoundedButton/CoverageGrid/PALETTE/STATE_ABBR`, `scrape.SEARCH_TERMS/ALL_50/read_cache/CSV_PATH`.
- Produces:
  - `SETTINGS_PATH: Path`
  - `resolve_face(candidates: tuple[str, ...]) -> str`
  - `build_fonts() -> dict[str, tkfont.Font]`
  - `apply_theme(root) -> None`
  - `class App(tk.Tk)` with `self.state`, `self.prefs`, `self.events`, and panel builders.

This task ends with a window that opens, looks like `design/Main.dc.html`, remembers your terms, and does nothing when you press Start. Wiring comes in Task 9.

- [ ] **Step 1: Write `app.py`'s foundation**

Create `app.py`:

```python
"""Desktop window for the club scraper.

Owns the main thread. Never scrapes: a worker thread does that and reports
through an event queue this window drains on a timer. Nothing here may be
called from the worker, because Tk is not thread-safe.
"""

import queue
import threading
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import ttk

import runstate
import scrape
import settings
from widgets import PALETTE, CoverageGrid, RoundedButton

SETTINGS_PATH = Path("data") / "settings.json"

UI_FACES = ("Segoe UI", "Helvetica Neue", "DejaVu Sans")
MONO_FACES = ("Consolas", "Menlo", "DejaVu Sans Mono", "Courier New")


def resolve_face(candidates) -> str:
    """First installed family from `candidates`, else Tk's own default.

    No single face ships on both macOS and Windows, so the app picks per
    machine rather than naming one and getting a silent substitution.
    """
    available = set(tkfont.families())
    for name in candidates:
        if name in available:
            return name
    return tkfont.nametofont("TkDefaultFont").actual("family")


def build_fonts() -> dict:
    ui, mono = resolve_face(UI_FACES), resolve_face(MONO_FACES)
    return {
        "ui": tkfont.Font(family=ui, size=12),
        "ui_bold": tkfont.Font(family=ui, size=12, weight="bold"),
        "small": tkfont.Font(family=ui, size=10),
        "label": tkfont.Font(family=ui, size=9, weight="bold"),
        "big": tkfont.Font(family=ui, size=22, weight="bold"),
        "mono": tkfont.Font(family=mono, size=11),
        "cell": tkfont.Font(family=ui, size=10, weight="bold"),
    }


def apply_theme(root) -> None:
    """Force clam and restyle it to the design palette.

    Native themes are deliberately rejected: the app must look the same on
    macOS and Windows, and clam is the one theme present on both that accepts
    a full restyle.
    """
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background=PALETTE["bg"], foreground=PALETTE["ink"],
                    fieldbackground=PALETTE["field"], bordercolor=PALETTE["line"],
                    lightcolor=PALETTE["line"], darkcolor=PALETTE["line"])
    style.configure("TFrame", background=PALETTE["bg"])
    style.configure("Panel.TFrame", background=PALETTE["panel"])
    style.configure("Card.TFrame", background=PALETTE["field"],
                    relief="solid", borderwidth=1)
    style.configure("TLabel", background=PALETTE["bg"], foreground=PALETTE["ink"])
    style.configure("Muted.TLabel", foreground=PALETTE["muted"])
    style.configure("Faint.TLabel", foreground=PALETTE["faint"])
    style.configure("TCheckbutton", background=PALETTE["bg"],
                    focuscolor=PALETTE["accent"])
    style.configure("TProgressbar", background=PALETTE["accent"],
                    troughcolor=PALETTE["sunken"], borderwidth=0, thickness=8)
    style.configure("Treeview", background=PALETTE["field"],
                    fieldbackground=PALETTE["field"], borderwidth=1, rowheight=26)
    style.configure("Treeview.Heading", background=PALETTE["panel"],
                    foreground=PALETTE["muted"], relief="flat")
    style.map("Treeview", background=[("selected", "#dfe8f6")],
              foreground=[("selected", PALETTE["ink"])])
```

- [ ] **Step 2: Add the `App` class and its setup panel**

Append to `app.py`. Read `design/Main.dc.html` before laying this out — it carries the exact spacing, column widths and section order this panel must reproduce.

```python
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Club Scraper")
        self.geometry("1100x740")
        self.minsize(960, 660)
        self.configure(bg=PALETTE["bg"])

        self.fonts = build_fonts()
        apply_theme(self)

        self.prefs = settings.load(SETTINGS_PATH)
        records, done_pairs = scrape.read_cache()
        self.state = runstate.initial_state(
            done_pairs, self.prefs["terms"], self.prefs["states"]
        )
        self.state.clubs = len(records)

        self.events = queue.Queue()
        self.worker = None
        self.stop_flag = threading.Event()

        self._build_toolbar()
        self._body = ttk.Frame(self)
        self._body.pack(fill="both", expand=True)
        self._panels = {}
        self._build_setup()
        self._build_statusbar()
        self.show("setup")

    # -- chrome ------------------------------------------------------------
    def _build_toolbar(self):
        bar = ttk.Frame(self, style="Panel.TFrame", padding=(16, 11))
        bar.pack(fill="x")
        # bg must match the frame behind it: a Canvas paints its own background,
        # so a button on the panel-coloured toolbar would otherwise sit in a
        # visible rectangle of the wrong colour.
        self.start_btn = RoundedButton(bar, "Start scrape", self.on_start,
                                       kind="primary", font=self.fonts["ui_bold"],
                                       height=32, min_width=140, bg=PALETTE["panel"])
        self.start_btn.pack(side="left")
        RoundedButton(bar, "Open output folder", self.on_open_folder,
                      font=self.fonts["ui"], bg=PALETTE["panel"]
                      ).pack(side="left", padx=(10, 0))

        right = ttk.Frame(bar, style="Panel.TFrame")
        right.pack(side="right")
        ttk.Label(right, text="SAVING TO", style="Faint.TLabel",
                  font=self.fonts["label"], background=PALETTE["panel"]).pack(side="left", padx=(0, 8))
        ttk.Label(right, text=str(scrape.CSV_PATH), font=self.fonts["mono"],
                  style="Muted.TLabel", background=PALETTE["panel"]).pack(side="left")

    def _build_statusbar(self):
        bar = ttk.Frame(self, style="Panel.TFrame", padding=(14, 6))
        bar.pack(fill="x", side="bottom")
        self.status_dot = tk.Canvas(bar, width=9, height=9, highlightthickness=0,
                                    bd=0, bg=PALETTE["panel"])
        self.status_dot.pack(side="left", padx=(0, 7))
        self.status_text = ttk.Label(bar, text="Ready", font=self.fonts["small"],
                                     background=PALETTE["panel"])
        self.status_text.pack(side="left")
        self.status_detail = ttk.Label(bar, text="", font=self.fonts["small"],
                                       style="Muted.TLabel", background=PALETTE["panel"])
        self.status_detail.pack(side="left", padx=(16, 0))
        self.status_right = ttk.Label(bar, text="", font=self.fonts["small"],
                                      style="Muted.TLabel", background=PALETTE["panel"])
        self.status_right.pack(side="right")

    def show(self, name: str) -> None:
        for key, panel in self._panels.items():
            panel.pack_forget()
        self._panels[name].pack(fill="both", expand=True)
        self._visible = name

    # -- setup panel -------------------------------------------------------
    def _build_setup(self):
        panel = ttk.Frame(self._body, padding=(16, 14))
        self._panels["setup"] = panel

        top = ttk.Frame(panel)
        top.pack(fill="x")

        terms_box = ttk.Frame(top)
        terms_box.pack(side="left", fill="y")
        ttk.Label(terms_box, text="SEARCH TERMS", style="Faint.TLabel",
                  font=self.fonts["label"]).pack(anchor="w", pady=(0, 6))
        self.terms_list = tk.Listbox(
            terms_box, width=38, height=5, font=self.fonts["ui"],
            bg=PALETTE["field"], fg=PALETTE["ink"], relief="solid", bd=1,
            highlightthickness=0, selectbackground="#dfe8f6",
            selectforeground=PALETTE["ink"], activestyle="none",
        )
        self.terms_list.pack()
        for term in self.prefs["terms"]:
            self.terms_list.insert("end", term)
        row = ttk.Frame(terms_box)
        row.pack(fill="x", pady=(6, 0))
        self.term_entry = ttk.Entry(row, font=self.fonts["ui"])
        self.term_entry.pack(side="left", fill="x", expand=True)
        RoundedButton(row, "Add", self.on_add_term, font=self.fonts["ui"],
                      height=26, pad_x=10).pack(side="left", padx=(6, 0))
        RoundedButton(row, "Remove", self.on_remove_term, font=self.fonts["ui"],
                      height=26, pad_x=10).pack(side="left", padx=(6, 0))

        opts = ttk.Frame(top, padding=(22, 0, 0, 0))
        opts.pack(side="left", fill="both", expand=True)
        ttk.Label(opts, text="OPTIONS", style="Faint.TLabel",
                  font=self.fonts["label"]).pack(anchor="w", pady=(0, 6))
        self.var_enrich = tk.BooleanVar(value=self.prefs["enrich"])
        self.var_headed = tk.BooleanVar(value=self.prefs["headed"])
        self.var_force = tk.BooleanVar(value=self.prefs["force"])
        for text, var in (
            ("Look up contact details on club websites", self.var_enrich),
            ("Show the browser while it works", self.var_headed),
            ("Re-scrape states already finished", self.var_force),
        ):
            ttk.Checkbutton(opts, text=text, variable=var).pack(anchor="w", pady=2)
        ttk.Label(opts, wraplength=520, style="Muted.TLabel", font=self.fonts["small"],
                  text=("Paced to about 3 seconds per club, so a full run takes hours. "
                        "You can close this window and pick it up later.")
                  ).pack(anchor="w", pady=(10, 0))

        grid_head = ttk.Frame(panel)
        grid_head.pack(fill="x", pady=(16, 6))
        ttk.Label(grid_head, text="COVERAGE", style="Faint.TLabel",
                  font=self.fonts["label"]).pack(side="left")
        self.legend = ttk.Label(grid_head, style="Muted.TLabel", font=self.fonts["small"],
                                text="finished · partly done · failed · not started")
        self.legend.pack(side="right")

        self.grid_setup = CoverageGrid(panel, scrape.ALL_50, font=self.fonts["cell"])
        self.grid_setup.pack(fill="x")
        self.grid_setup.update_coverage(self.state.coverage)

    # -- actions (wired in Task 9) ----------------------------------------
    def on_add_term(self):
        term = self.term_entry.get().strip()
        if term and term not in self.terms_list.get(0, "end"):
            self.terms_list.insert("end", term)
            self.term_entry.delete(0, "end")

    def on_remove_term(self):
        for index in reversed(self.terms_list.curselection()):
            self.terms_list.delete(index)

    def on_open_folder(self):
        import subprocess
        import sys
        folder = Path(scrape.CSV_PATH).parent.resolve()
        folder.mkdir(parents=True, exist_ok=True)
        if sys.platform == "darwin":
            subprocess.run(["open", str(folder)], check=False)
        elif sys.platform.startswith("win"):
            subprocess.run(["explorer", str(folder)], check=False)
        else:
            subprocess.run(["xdg-open", str(folder)], check=False)

    def on_start(self):
        pass  # Task 9

    def current_prefs(self) -> dict:
        return {
            "terms": list(self.terms_list.get(0, "end")),
            "states": list(scrape.ALL_50),
            "enrich": self.var_enrich.get(),
            "headed": self.var_headed.get(),
            "force": self.var_force.get(),
            "limit": self.prefs.get("limit"),
        }


if __name__ == "__main__":
    App().mainloop()
```

- [ ] **Step 3: Check the suite still passes**

Run: `pytest -v`
Expected: **122 passed**. `app.py` has no tests of its own; this confirms importing nothing broke.

- [ ] **Step 4: Launch it and look**

Run: `python app.py`

Check, against `design/Main.dc.html`:
- The window opens at 1100x740 and does not error.
- Colours match the palette — warm off-white background, not the OS default grey.
- The term list shows the three padel defaults.
- The coverage grid shows 50 cells with correct state abbreviations.
- Buttons have rounded corners.
- Tab moves focus between buttons and shows a focus ring; Space and Return activate the focused button; "Add" with text in the box adds a term.

Report anything that does not match, with a screenshot if you can take one.

- [ ] **Step 5: Commit**

```bash
git add app.py
git commit -m "feat: window shell, forced clam theme and setup panel"
```

---

### Task 8: `app.py` — running, results and interrupted panels

**Files:**
- Modify: `app.py`
- Test: launch it and look

**Interfaces:**
- Consumes: everything from Task 7.
- Produces: `_build_running()`, `_build_results()`, `_build_blocked()`, and `render()` which paints the visible panel from `self.state`.

Read `design/Running.dc.html`, `design/Results.dc.html` and `design/Blocked.dc.html` before laying each one out.

- [ ] **Step 1: Add `import time` to the stdlib import group**

`render()` below needs it, and Task 9 relies on it too.

- [ ] **Step 2: Add the three panels**

Append these methods to `App`, before the actions section:

```python
    # -- running panel -----------------------------------------------------
    def _build_running(self):
        panel = ttk.Frame(self._body, padding=(16, 14))
        self._panels["running"] = panel

        head = ttk.Frame(panel)
        head.pack(fill="x")
        self.run_current = ttk.Label(head, text="", font=self.fonts["ui_bold"])
        self.run_current.pack(side="left")
        self.run_stage = ttk.Label(head, text="", style="Muted.TLabel",
                                   font=self.fonts["small"])
        self.run_stage.pack(side="left", padx=(12, 0))

        self.run_bar = ttk.Progressbar(panel, mode="determinate", maximum=100)
        self.run_bar.pack(fill="x", pady=(9, 8))

        stats = ttk.Frame(panel)
        stats.pack(fill="x")
        self.run_counts = ttk.Label(stats, text="", style="Muted.TLabel",
                                    font=self.fonts["small"])
        self.run_counts.pack(side="left")
        ttk.Label(stats, style="Muted.TLabel", font=self.fonts["small"],
                  text="Progress is saved as it goes").pack(side="right")

        ttk.Label(panel, text="COVERAGE", style="Faint.TLabel",
                  font=self.fonts["label"]).pack(anchor="w", pady=(16, 6))
        self.grid_running = CoverageGrid(panel, scrape.ALL_50, cell_h=40,
                                         font=self.fonts["cell"])
        self.grid_running.pack(fill="x")

        ttk.Label(panel, text="ACTIVITY", style="Faint.TLabel",
                  font=self.fonts["label"]).pack(anchor="w", pady=(14, 6))
        self.log_box = tk.Text(panel, height=7, font=self.fonts["mono"],
                               bg=PALETTE["field"], fg=PALETTE["muted"],
                               relief="solid", bd=1, highlightthickness=0,
                               wrap="none", state="disabled")
        self.log_box.pack(fill="both", expand=True)

    # -- results panel -----------------------------------------------------
    def _build_results(self):
        panel = ttk.Frame(self._body, padding=(16, 14))
        self._panels["results"] = panel

        head = ttk.Frame(panel)
        head.pack(fill="x")
        self.res_count = ttk.Label(head, text="", font=self.fonts["big"])
        self.res_count.pack(side="left")
        ttk.Label(head, text="clubs saved", style="Muted.TLabel",
                  font=self.fonts["ui"]).pack(side="left", padx=(9, 0), pady=(9, 0))
        self.res_summary = ttk.Label(head, text="", style="Muted.TLabel",
                                     font=self.fonts["small"])
        self.res_summary.pack(side="left", padx=(26, 0), pady=(9, 0))

        self.res_warning = ttk.Label(panel, text="", style="Muted.TLabel",
                                     font=self.fonts["small"], wraplength=1040)
        self.res_warning.pack(fill="x", pady=(8, 0))

        body = ttk.Frame(panel)
        body.pack(fill="both", expand=True, pady=(12, 0))

        cols = ("name", "where", "phone", "email", "rating")
        self.table = ttk.Treeview(body, columns=cols, show="headings", height=12)
        for col, title, width in (
            ("name", "Club", 280), ("where", "Where", 150), ("phone", "Phone", 130),
            ("email", "Email", 220), ("rating", "Rating", 90),
        ):
            self.table.heading(col, text=title)
            self.table.column(col, width=width, anchor="w")
        self.table.pack(side="left", fill="both", expand=True)

        health = ttk.Frame(body, padding=(16, 0, 0, 0))
        health.pack(side="right", fill="y")
        ttk.Label(health, text="HOW COMPLETE THE DATA IS", style="Faint.TLabel",
                  font=self.fonts["label"]).pack(anchor="w", pady=(0, 8))
        self.health_rows = ttk.Frame(health)
        self.health_rows.pack(fill="both", expand=True)

        actions = ttk.Frame(panel)
        actions.pack(fill="x", pady=(12, 0))
        RoundedButton(actions, "Open results file", self.on_open_folder,
                      kind="primary", font=self.fonts["ui_bold"], height=30).pack(side="left")
        RoundedButton(actions, "Start a new run", lambda: self.show("setup"),
                      font=self.fonts["ui"], height=30).pack(side="left", padx=(10, 0))

    # -- interrupted panel -------------------------------------------------
    def _build_blocked(self):
        panel = ttk.Frame(self._body, padding=(16, 16))
        self._panels["blocked"] = panel

        card = ttk.Frame(panel, style="Card.TFrame", padding=(18, 16))
        card.pack(fill="x")
        self.blocked_title = ttk.Label(card, text="Google has stopped answering",
                                       font=self.fonts["ui_bold"],
                                       background=PALETTE["field"])
        self.blocked_title.pack(anchor="w")
        self.blocked_body = ttk.Label(
            card, wraplength=700, style="Muted.TLabel", font=self.fonts["ui"],
            background=PALETTE["field"], text="")
        self.blocked_body.pack(anchor="w", pady=(6, 0))
        ttk.Label(card, background=PALETTE["field"], font=self.fonts["small"],
                  wraplength=700, foreground="#2f6b45",
                  text=("Nothing has been lost. Every club found so far is already "
                        "written to disk, so continuing picks up where it stopped.")
                  ).pack(anchor="w", pady=(10, 0))

        row = ttk.Frame(card, style="Card.TFrame")
        row.pack(anchor="w", pady=(14, 0))
        RoundedButton(row, "Carry on from here", self.on_start, kind="primary",
                      font=self.fonts["ui_bold"], height=30).pack(side="left")
        RoundedButton(row, "Finish here and keep what I have",
                      lambda: self.show("results"), font=self.fonts["ui"],
                      height=30).pack(side="left", padx=(9, 0))

        ttk.Label(panel, text="WHERE IT GOT TO", style="Faint.TLabel",
                  font=self.fonts["label"]).pack(anchor="w", pady=(18, 6))
        self.grid_blocked = CoverageGrid(panel, scrape.ALL_50, cell_h=40,
                                         font=self.fonts["cell"])
        self.grid_blocked.pack(fill="x")
        self.blocked_detail = ttk.Label(panel, text="", style="Muted.TLabel",
                                        font=self.fonts["small"], wraplength=1040)
        self.blocked_detail.pack(anchor="w", pady=(10, 0))
```

Register them in `__init__` by adding these two lines after `self._build_setup()`:

```python
        self._build_running()
        self._build_results()
        self._build_blocked()
```

- [ ] **Step 3: Add `render()`**

Append to `App`:

```python
    def render(self, now: float | None = None) -> None:
        """Paint the visible panel from self.state. Main thread only."""
        now = time.monotonic() if now is None else now
        s = self.state

        dot = {"idle": PALETTE["partial"], "running": PALETTE["accent"],
               "finished": PALETTE["done"], "blocked": PALETTE["partial"]}[s.status]
        self.status_dot.delete("all")
        self.status_dot.create_oval(0, 0, 9, 9, fill=dot, outline=dot)
        self.status_text.configure(
            text={"idle": "Ready", "running": "Running",
                  "finished": "Finished", "blocked": "Paused"}[s.status])

        if s.status == "running":
            self.run_current.configure(text=s.current or "Working…")
            self.run_stage.configure(text=s.stage)
            done_pct = (100 * s.queries_done / s.queries_total) if s.queries_total else 0
            self.run_bar.configure(value=done_pct)
            self.run_counts.configure(
                text=f"{s.queries_done} of {s.queries_total} searches   ·   "
                     f"{s.clubs} clubs saved   ·   {runstate.elapsed(s, now)} elapsed"
                     + (f"   ·   about {runstate.remaining(s, now)} left"
                        if runstate.remaining(s, now) else ""))
            self.grid_running.update_coverage(s.coverage)
            self.log_box.configure(state="normal")
            self.log_box.delete("1.0", "end")
            self.log_box.insert("1.0", "\n".join(s.log[-7:]))
            self.log_box.configure(state="disabled")
            self.status_detail.configure(text=f"{s.clubs} clubs")
            self.status_right.configure(text="Safe to close — picks up where it left off")

        elif s.status == "finished":
            self.res_count.configure(text=f"{s.clubs:,}")
            self.res_summary.configure(
                text=f"Finished in {runstate.elapsed(s, now)} · "
                     f"{s.queries_done} of {s.queries_total} searches")
            partial = [st for st, v in s.coverage.items() if v == "partial"]
            self.res_warning.configure(
                text=(f"{len(partial)} states are only partly covered "
                      f"({', '.join(partial[:4])}) — they hit Google's 120-result limit."
                      if partial else ""))
            self._fill_table()
            self._fill_health()
            self.status_detail.configure(text=str(scrape.CSV_PATH))
            self.status_right.configure(text="")

        elif s.status == "blocked":
            crashed = s.finish_reason == "crashed"
            self.blocked_title.configure(
                text="Something went wrong" if crashed else "Google has stopped answering")
            self.blocked_body.configure(
                text=(s.failures[-1][1] if crashed and s.failures else
                      "Three searches failed one after another, which usually means "
                      "Google wants someone to prove they are not a robot. The run "
                      "paused itself rather than keep hammering the site."))
            self.grid_blocked.update_coverage(s.coverage)
            failed = [st for st, v in s.coverage.items() if v == "failed"]
            self.blocked_detail.configure(
                text=(f"{', '.join(failed)} failed and will be tried again next run."
                      if failed else ""))
            self.status_detail.configure(text=f"{s.clubs} clubs saved")
            self.status_right.configure(text="Waiting for you")

        else:
            self.grid_setup.update_coverage(s.coverage)
            self.status_detail.configure(
                text=f"{s.queries_total} searches queued · {s.clubs} clubs cached")
            self.status_right.configure(text="")

    def _fill_table(self):
        for row in self.table.get_children():
            self.table.delete(row)
        records, _ = scrape.read_cache()
        for record in records[:200]:
            self.table.insert("", "end", values=(
                record.get("name", ""),
                f"{record.get('city', '')}, {record.get('state', '')}".strip(", "),
                record.get("phone", ""),
                (record.get("emails", "") or "").split(";")[0].strip(),
                f"{record.get('rating', '')} · {record.get('reviews', '')}".strip(" ·"),
            ))

    def _fill_health(self):
        for child in self.health_rows.winfo_children():
            child.destroy()
        records, _ = scrape.read_cache()
        interesting = ["name", "address", "phone", "website", "emails",
                       "instagram", "owner_name", "owner_phone"]
        for column, rate in runstate.fill_rate_rows(records, interesting):
            row = ttk.Frame(self.health_rows)
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=column, width=13, style="Muted.TLabel",
                      font=self.fonts["small"]).pack(side="left")
            meter = tk.Canvas(row, width=120, height=6, highlightthickness=0,
                              bd=0, bg=PALETTE["sunken"])
            meter.pack(side="left", padx=6)
            colour = PALETTE["done"] if rate >= 0.4 else PALETTE["partial"]
            meter.create_rectangle(0, 0, max(1, 120 * rate), 6,
                                   fill=colour, outline=colour)
            ttk.Label(row, text=f"{rate:.0%}", width=5, style="Muted.TLabel",
                      font=self.fonts["small"]).pack(side="left")
```

- [ ] **Step 4: Check the suite still passes**

Run: `pytest -v`
Expected: **122 passed**

- [ ] **Step 5: Launch and inspect each panel**

Run: `python app.py`, then from a Python prompt or by temporarily calling `self.show(...)` in `__init__`, view each of `"running"`, `"results"` and `"blocked"` against its design file. Revert any temporary call before committing.

- [ ] **Step 6: Commit**

```bash
git add app.py
git commit -m "feat: running, results and interrupted panels"
```

---

### Task 9: `app.py` — the worker thread and the event pump

**Files:**
- Modify: `app.py`
- Test: launch it and run a real short scrape

**Interfaces:**
- Consumes: `scrape.subscribe/unsubscribe/request_stop/stop_requested/clear_stop/run_stage1/run_stage2/write_csv/read_cache`, `runstate.fold`.
- Produces: `on_start`, `on_stop`, `_on_event`, `_run_worker`, `_pump`, `_finish`, `on_close`.

This is the only task where threads matter. Two rules, and every bug in this task will be a violation of one of them: **the worker never touches a widget**, and **the main thread never blocks**.

- [ ] **Step 1: Add the imports**

`import time` is already there from Task 8. Change the tkinter import line to also bring in `messagebox`:

```python
from tkinter import messagebox, ttk
```

- [ ] **Step 2: Replace the `on_start` stub with the full wiring**

Replace `def on_start(self): pass` with:

```python
    def on_start(self):
        if self.worker and self.worker.is_alive():
            return self.on_stop()

        prefs = self.current_prefs()
        if not prefs["terms"]:
            messagebox.showwarning("No search terms",
                                   "Add at least one search term before starting.")
            return
        settings.save(prefs, SETTINGS_PATH)
        self.prefs = prefs

        scrape.clear_stop()
        records, done_pairs = scrape.read_cache()
        self.state = runstate.initial_state(done_pairs, prefs["terms"], prefs["states"])
        self.state.clubs = len(records)

        scrape.subscribe(self._on_event)
        self.worker = threading.Thread(target=self._run_worker, args=(prefs,),
                                       daemon=True)
        self.worker.start()

        self.start_btn.set_text("Stop")
        self.show("running")
        self.render()
        self.after(100, self._pump)

    def on_stop(self):
        """Ask the run to stop at the next listing boundary."""
        scrape.request_stop()
        self.start_btn.set_text("Stopping…")
        self.start_btn.set_enabled(False)

    # -- worker thread -----------------------------------------------------
    def _on_event(self, kind, data):
        """Called ON THE WORKER THREAD. Must only enqueue — never touch a widget."""
        self.events.put((kind, data))

    def _run_worker(self, prefs):
        """The whole scrape, off the main thread.

        Wraps everything: an exception here must reach the window as a crashed
        run rather than killing a thread silently and leaving a spinner going
        forever.
        """
        saw_blocked = {"hit": False}

        def watch(kind, _data):
            if kind == "blocked":
                saw_blocked["hit"] = True

        scrape.subscribe(watch)
        reason = "done"
        try:
            scrape.run_stage1(
                prefs["terms"], prefs["states"],
                limit=prefs.get("limit"),
                headless=not prefs["headed"],
                force=prefs["force"],
            )
            if prefs["enrich"] and not scrape.stop_requested():
                scrape.run_stage2(force=prefs["force"])
            records, _ = scrape.read_cache()
            scrape.write_csv(records)
            if saw_blocked["hit"]:
                reason = "blocked"
            elif scrape.stop_requested():
                reason = "stopped"
        except Exception as exc:
            reason = "crashed"
            self.events.put(("query_failed", {
                "term": "", "state": "the run",
                "error": f"{type(exc).__name__}: {exc}",
            }))
        finally:
            scrape.unsubscribe(watch)
            self.events.put(("run_finished", {"reason": reason}))

    # -- main thread -------------------------------------------------------
    def _pump(self):
        """Drain the queue, fold each event, repaint once. Main thread only."""
        now = time.monotonic()
        finished = False
        for _ in range(500):
            try:
                kind, data = self.events.get_nowait()
            except queue.Empty:
                break
            self.state = runstate.fold(self.state, kind, data, now=now)
            if kind == "run_finished":
                finished = True

        self.render(now)

        if finished:
            self._finish()
        elif (self.worker and self.worker.is_alive()) or not self.events.empty():
            self.after(100, self._pump)

    def _finish(self):
        scrape.unsubscribe(self._on_event)
        self.worker = None
        self.start_btn.set_text("Start scrape")
        self.start_btn.set_enabled(True)
        self.show("results" if self.state.status == "finished" else "blocked")
        self.render()

    def on_close(self):
        if self.worker and self.worker.is_alive():
            if not messagebox.askokcancel(
                "Stop the run?",
                "A scrape is running. Everything found so far is already saved, "
                "and the next run will pick up where this one stops.\n\n"
                "Stop it and close?",
            ):
                return
            scrape.request_stop()
        self.destroy()
```

The events are drained in a batch and the window repaints **once** per tick. Repainting per event would make a fast burst — a state finishing 40 listings — redraw the coverage grid 40 times in one frame.

`_pump` reschedules itself only while the worker lives or the queue still holds something, so an idle window costs nothing.

- [ ] **Step 3: Wire the close handler**

In `__init__`, after `self.show("setup")`, add:

```python
        self.protocol("WM_DELETE_WINDOW", self.on_close)
```

- [ ] **Step 4: Check the suite still passes**

Run: `pytest -v`
Expected: **122 passed**

- [ ] **Step 5: Run a real short scrape**

Temporarily set the limit so this takes a minute rather than hours: in `current_prefs`, change `"limit": self.prefs.get("limit")` to `"limit": 2`, and start the app with only one term and one state by editing `data/settings.json` or removing terms in the UI.

Run: `python app.py`, remove all but one term, press Start.

Expected:
- The window switches to the running panel and stays responsive — you can move and resize it while the scrape works.
- The current-activity line updates, the progress bar advances, the activity log fills.
- The coverage grid turns the running state blue, then green when it finishes.
- The club counter increases.
- At the end the results panel appears with the table and the health meters populated.
- `data/results.csv` exists and holds the scraped clubs.

- [ ] **Step 6: Test stopping mid-run**

Start another run and press Stop while it is working.

Expected: the button reads "Stopping…", the run halts within a few seconds (one listing's pacing), the results panel appears with whatever was found, and `data/cache.jsonl` holds those clubs. Press Start again: it resumes rather than restarting.

- [ ] **Step 7: Test closing mid-run**

Start a run and close the window. Expected: a confirmation dialog; cancelling keeps running, confirming stops and closes without a traceback in the terminal.

- [ ] **Step 8: Restore the limit**

Change `"limit": 2` back to `"limit": self.prefs.get("limit")`. Confirm with `git diff` that no test-only edit remains.

- [ ] **Step 9: Commit**

```bash
git add app.py
git commit -m "feat: worker thread, event pump and stop control"
```

---

### Task 10: Packaging for teammates

**Files:**
- Create: `pyproject.toml`, `.gitattributes`, `setup.command`, `run.command`, `setup.bat`, `run.bat`
- Generate: `uv.lock`
- Modify: `README.md`, `.gitignore`

**Interfaces:**
- Consumes: the finished app.
- Produces: no code.

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "club-scraper"
version = "1.0.0"
description = "Scrapes club listings from Google Maps into a spreadsheet."
requires-python = ">=3.11"
dependencies = [
    "playwright>=1.48",
    "httpx>=0.27",
    "beautifulsoup4>=4.12",
    "gspread>=6.1",
    "google-auth>=2.35",
]

[dependency-groups]
dev = ["pytest>=8.0"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 2: Generate the lockfile**

Run: `uv lock`
Expected: `uv.lock` is created. Commit it — it is what makes every teammate resolve the same Playwright version, and therefore see the same selector behaviour.

- [ ] **Step 3: Write `.gitattributes`**

Batch files must have CRLF endings or Windows will not run them.

```
*.bat text eol=crlf
*.command text eol=lf
*.sh text eol=lf
```

- [ ] **Step 4: Write `setup.command`**

```bash
#!/usr/bin/env bash
# One-time setup. Installs uv, Python, the dependencies and a browser.
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v uv >/dev/null 2>&1; then
  echo "Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

echo "Installing Python..."
uv python install 3.12

echo "Installing dependencies..."
uv sync

echo "Downloading the browser (about 150MB, one time)..."
uv run playwright install chromium

echo
echo "Setup complete. Double-click run.command to start."
```

- [ ] **Step 5: Write `run.command`**

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
exec uv run python app.py
```

- [ ] **Step 6: Write `setup.bat`**

```bat
@echo off
cd /d "%~dp0"

where uv >nul 2>nul
if errorlevel 1 (
  echo Installing uv...
  powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex"
  set "PATH=%USERPROFILE%\.local\bin;%PATH%"
)

echo Installing Python...
uv python install 3.12

echo Installing dependencies...
uv sync

echo Downloading the browser (about 150MB, one time)...
uv run playwright install chromium

echo.
echo Setup complete. Double-click run.bat to start.
pause
```

- [ ] **Step 7: Write `run.bat`**

```bat
@echo off
cd /d "%~dp0"
uv run python app.py
```

- [ ] **Step 8: Make the shell scripts executable**

```bash
chmod +x setup.command run.command
git update-index --chmod=+x setup.command run.command
```

Without the second line the executable bit is not recorded in git, and a colleague's clone arrives unrunnable.

- [ ] **Step 9: Ignore the settings file**

Append to `.gitignore`:

```
data/settings.json
```

- [ ] **Step 10: Add the teammate section to `README.md`**

Insert this immediately after the existing `# Google Maps Club Scraper` title line, before `## Install`:

````markdown
## For teammates: getting started

You need nothing installed beforehand — not even Python.

**macOS**

```bash
git clone git@github.com:rakshithvikramraj/gmap-scraper.git
cd gmap-scraper
./setup.command      # once, takes a few minutes
./run.command        # opens the app
```

**Windows**

```
git clone https://github.com/rakshithvikramraj/gmap-scraper.git
cd gmap-scraper
setup.bat
run.bat
```

Setup downloads roughly 180MB once: Python, the libraries, and the browser the
scraper drives. After that both scripts start instantly.

To get later fixes, run `git pull` and then `./setup.command` (or `setup.bat`)
again.

### Using it

Add your search terms, tick the states you want, press **Start scrape**. A full
50-state run takes a few hours, so it paces itself and saves as it goes — you
can close the window and press Start again later to carry on from where it
stopped.

Results are written to `data/results.csv`, which opens in Excel or imports
straight into Google Sheets.

### If something looks wrong

The panel at the end of a run shows how complete each column is. If a column
that is normally full drops to near zero, Google has changed its page layout
and the app needs updating — tell whoever maintains this.
````

- [ ] **Step 11: Verify the bootstrap actually works**

Prove the scripts work from a clean state rather than assuming:

```bash
mv .venv .venv.bak
./setup.command
```

Expected: it completes without error and creates a working environment.

Run: `uv run pytest -v`
Expected: **122 passed**

Then restore your original venv if you prefer it: `rm -rf .venv && mv .venv.bak .venv`

- [ ] **Step 12: Commit and push**

```bash
git add pyproject.toml uv.lock .gitattributes .gitignore README.md \
        setup.command run.command setup.bat run.bat
git commit -m "feat: one-command setup for macOS and Windows teammates"
git push origin master
```

---

## Verification checklist

- [ ] `pytest -v` reports 122 passed
- [ ] `python scrape.py --help` still lists all eight flags, unchanged
- [ ] `python app.py` opens a window matching `design/Main.dc.html`
- [ ] Tab reaches every button, Space and Return activate them, a focus ring shows
- [ ] A short run updates the grid live and leaves the window responsive
- [ ] Stop halts within a few seconds and Start resumes rather than restarting
- [ ] Closing mid-run asks first and exits without a traceback
- [ ] `./setup.command` works from a clean checkout
- [ ] `git status` shows no `data/`, no `.venv/`, no `settings.json`
