"""Folds a scrape's event stream into the data the window paints.

Pure by design: no tkinter, no files, no clock of its own. That is what makes
the app's behaviour testable without opening a window or a browser.
"""

from dataclasses import dataclass, field, replace

MAX_LOG = 200

# Worst-first, so a later good outcome cannot hide an earlier bad one.
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

    `queries_done` deliberately starts at 0 and is owned entirely by the event
    stream: run_stage1 emits query_skipped for every cached pair, so seeding it
    from the cache too counted those pairs twice and a resumed run finished
    above 100% with a halved ETA. Nothing reads it before run_start.
    """
    coverage = {}
    for state in states:
        cached = sum(1 for term in terms if (term, state) in done_pairs)
        coverage[state] = "done" if cached == len(terms) and terms else "pending"
    return RunState(
        coverage=coverage,
        queries_total=len(terms) * len(states),
        queries_done=0,
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
        s.current = f"Searching \"{data['term']}\" in {data['state']}"
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
        note(f"Saved \"{data['name']}\"{where}")

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
