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
    # region -> term -> status, for the per-term segments in a coverage cell.
    term_status: dict = field(default_factory=dict)
    # region -> term -> how many leaf places have yet to report. A region with
    # six cities reports six times per term, and the term is only finished
    # when the last one lands.
    term_left: dict = field(default_factory=dict)
    saved: int = 0            # listings written this run, whatever they are
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


def initial_state(done_pairs, terms, places) -> RunState:
    """Seed from the resume cache, before any event arrives.

    A region counts as done only when every term is cached for every leaf
    place inside it. Tallying rather than assigning matters as soon as a
    region holds more than one place: several cities share one coverage key,
    and a plain assignment let whichever city was written last decide the
    whole region.

    `places` holds whatever the caller compares `done_pairs` entries against -
    in practice `geo.Place` values. This module stays free of any geography
    import: it only calls `.coverage_key()` (the coverage cell's key,
    matching the `state=` value every coverage event in `scrape.py` emits)
    and `.key()` (the country/region/city triple that, prefixed with the
    term, is a `done_pairs` entry) on each one, so it never has to know what
    a country or a region is.

    Coverage here is "everything cached, or pending" rather than the derived
    rule `fold` uses. A region with prior progress but no live run is not
    active, and seeding it that way would light cells up before the run
    starts.

    `queries_done` deliberately starts at 0 and is owned entirely by the event
    stream: run_stage1 emits query_skipped for every cached pair, so seeding it
    from the cache too counted those pairs twice and a resumed run finished
    above 100% with a halved ETA. Nothing reads it before run_start.
    """
    outstanding: dict = {}
    for place in places:
        key = place.coverage_key()
        left = outstanding.setdefault(key, {})
        for term in terms:
            left[term] = left.get(term, 0) + (
                0 if (term, *place.key()) in done_pairs else 1)

    coverage, term_status, term_left = {}, {}, {}
    for key, per_term in outstanding.items():
        term_status[key] = {term: ("pending" if left else "done")
                            for term, left in per_term.items()}
        term_left[key] = dict(per_term)
        coverage[key] = "done" if terms and not any(per_term.values()) else "pending"
    return RunState(
        coverage=coverage,
        term_status=term_status,
        term_left=term_left,
        queries_total=len(terms) * len(places),
        queries_done=0,
    )


def _worse(current: str, candidate: str) -> str:
    return candidate if _RANK[candidate] > _RANK.get(current, 0) else current


def _outcome(failed: int, complete: bool) -> str:
    if not complete:
        return "partial"
    return "failed" if failed else "done"


def _mark_term(s: "RunState", region: str, term: str, outcome: str) -> None:
    """Fold one query's outcome into its term's segment, worst-first.

    A completion only reads as "done" once it is the region's last outstanding
    place for that term; until then it reads as "active". Routing an early
    success through `_worse` as "active" is what stops the first city of six
    from painting the whole term finished, and what stops a later success from
    erasing an earlier city's failure.
    """
    if not term:
        return
    left = s.term_left.setdefault(region, {})
    if outcome != "active":
        left[term] = max(0, left.get(term, 1) - 1)
    row = s.term_status.setdefault(region, {})
    settled = outcome == "done" and left.get(term, 0) == 0
    row[term] = _worse(row.get(term, "pending"),
                       outcome if outcome != "done" or settled else "active")


def _region_status(row: dict) -> str:
    """A coverage cell's status, aggregated from its per-term segments.

    Not `_worse` over the row: `_worse` ranks "done" above "pending", so a
    region with one term finished and one untouched would rank as finished.
    Done is the only status that requires unanimity.
    """
    if not row:
        return "pending"
    values = list(row.values())
    if "failed" in values:
        return "failed"
    if "partial" in values:
        return "partial"
    if all(value == "done" for value in values):
        return "done"
    return "active" if any(v in ("done", "active") for v in values) else "pending"


def fold(state: RunState, kind: str, data: dict, now: float | None = None) -> RunState:
    """Return the state after one event. Never mutates `state`."""
    s = replace(
        state,
        coverage=dict(state.coverage),
        # Dicts of dicts, so a shallow copy would let this fold mutate the
        # state it was handed -- the exact bug the copies above prevent.
        term_status={k: dict(v) for k, v in state.term_status.items()},
        term_left={k: dict(v) for k, v in state.term_left.items()},
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
        _mark_term(s, data["state"], data.get("term", ""), "active")
        s.coverage[data["state"]] = _region_status(s.term_status.get(data["state"], {}))
        s.current = f"Searching \"{data['term']}\" in {data['state']}"
        note(s.current)

    elif kind == "query_skipped":
        s.queries_done += 1
        _mark_term(s, data["state"], data.get("term", ""), "done")
        s.coverage[data["state"]] = _region_status(s.term_status.get(data["state"], {}))
        note(f"Skipping {data['state']} — already done")

    elif kind == "listings_found":
        s.current = f"{data['count']} listings in {data['state']}"
        note(s.current)
        if data.get("at_cap") and data["state"] not in s.at_cap:
            s.at_cap.append(data["state"])

    elif kind == "listing_saved":
        s.saved += 1
        where = f" · {data['city']}, {data['state']}" if data.get("city") else ""
        note(f"Saved \"{data['name']}\"{where}")

    elif kind == "listing_failed":
        note(f"Skipped one listing — {data.get('error', 'unknown problem')}")

    elif kind == "query_done":
        s.queries_done += 1
        _mark_term(s, data["state"], data.get("term", ""),
                   _outcome(data["failed"], data["complete"]))
        s.coverage[data["state"]] = _region_status(s.term_status.get(data["state"], {}))
        note(f"Finished {data['state']} — {data['scraped']} clubs")

    elif kind == "query_failed":
        s.queries_done += 1
        _mark_term(s, data["state"], data.get("term", ""), "failed")
        s.coverage[data["state"]] = _region_status(s.term_status.get(data["state"], {}))
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


def estimate_run(term_count: int, leaf_count: int, cap, pacing=None):
    """(queries, seconds) for a run of this shape.

    Only meaningful because runs are capped: uncapped, a query returns
    anywhere between 1 and about 120 results and any figure would be a guess.
    `scrape.UNCAPPED_ASSUMPTION` stands in when there is no cap, and the
    caller is expected to say "at least" when it does.

    `pacing` is `(query_pause, listing_pause, overhead, uncapped)` and defaults
    to `scrape`'s own constants, so the estimate tracks a pacing change instead
    of restating one. Imported inside the function, not at module scope: this
    module is pure and cheap to import, and `scrape` drags in Playwright,
    gspread and google-auth.
    """
    if pacing is None:
        import scrape
        pacing = (scrape.PAUSE_QUERY, scrape.PAUSE_LISTING,
                  scrape.LISTING_OVERHEAD, scrape.UNCAPPED_ASSUMPTION)
    query_pause, listing_pause, overhead, uncapped = pacing

    queries = term_count * leaf_count
    if not queries:
        return 0, 0.0
    listings = cap if cap else uncapped

    def mean(pair):
        return (pair[0] + pair[1]) / 2

    per_query = mean(query_pause) + listings * (mean(listing_pause) + overhead)
    return queries, queries * per_query


def coverage_tally(coverage: dict) -> tuple[int, int]:
    """(finished, total) across every coverage cell."""
    return sum(1 for v in coverage.values() if v == "done"), len(coverage)


def country_tally(region_keys, coverage: dict) -> list:
    """(country, done, partial, failed, total) per country, in key order.

    Feeds the large-selection coverage view, where a grid of cells stops being
    readable and one bar per country takes over.
    """
    order: list = []
    rows: dict = {}
    for country, region in region_keys:
        if country not in rows:
            order.append(country)
            rows[country] = [0, 0, 0, 0]
        status = coverage.get(region, "pending")
        if status == "done":
            rows[country][0] += 1
        elif status == "partial":
            rows[country][1] += 1
        elif status == "failed":
            rows[country][2] += 1
        rows[country][3] += 1
    return [(country, *rows[country]) for country in order]
