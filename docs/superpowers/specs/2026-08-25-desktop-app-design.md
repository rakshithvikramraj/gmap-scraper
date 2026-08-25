# Club Scraper Desktop App — Design

**Date:** 2026-08-25
**Status:** Design approved; implementation plan pending
**Visual design:** `design/*.dc.html` + `design/canvas.json`
**Depends on:** `docs/superpowers/specs/2026-08-24-gmap-scraper-design.md`

## 1. Purpose

A Tkinter desktop front end for the club scraper, so that several people on
a team can each run their own scrapes without using a terminal, and can watch
a multi-hour run without wondering whether it is still alive.

The scraper itself is finished and tested. This work adds a window in front of
it and a way to hand the whole thing to a colleague.

## 2. Scope

In scope:

- One window with four states: setup, running, finished, interrupted.
- A worker thread that runs the existing scrape, and an event stream the
  window folds into what it shows.
- A stop control that halts cleanly between listings.
- Distribution to macOS and Windows teammates from a private git repository,
  bootstrapped by `uv`.

Out of scope:

- A packaged `.app` or `.exe`. Rejected: bundling Playwright's Chromium costs
  roughly 450 MB per platform, needs a build machine per operating system, and
  needs an Apple Developer account for macOS Gatekeeper. Shipping source costs
  none of those.
- A shared server instance. Rejected on technical grounds: Google blocks by IP
  address, so five people scraping from five laptops is more robust than five
  people queued behind one host.
- Any change to how the scraper finds or parses data. This work adds
  observation and control, nothing else.
- The command line interface stays exactly as it is and keeps working.

## 3. Decisions

| Decision | Choice | Rationale |
| --- | --- | --- |
| How the window observes the scrape | An event hook added to `scrape.py` | The window needs the `complete` and `failed` values that `should_mark_done` already computes. Recovering those by parsing printed English would re-derive data the code holds as booleans, and would break silently whenever a message is reworded. |
| Threading | Tkinter on the main thread, scrape on a worker | Verified experimentally: Playwright's sync API runs correctly in a plain worker thread. Tkinter is not thread-safe, so only the main thread touches widgets. |
| Worker to window | `queue.Queue` drained on a `root.after` tick | The standard Tkinter pattern, and the only one that keeps widget access on one thread. |
| Window to worker | A `threading.Event` checked between listings | Stops within about one listing. Never interrupts a cache write. |
| Where state lives | A pure module, `runstate.py` | Same split that made the scraper testable: fold events into plain data, keep widgets dumb. The reducer is unit-testable with no browser, network or display. |
| Distribution | Source in a private repo, bootstrapped by `uv` | One codebase for both operating systems, no build matrix, no code signing, roughly 1 MB to hand over, and a fix ships as `git pull`. |
| Dependency pinning | `pyproject.toml` + `uv.lock` | `requirements.txt` specifies floors, so two teammates can resolve different Playwright versions and see different selector behaviour. A lockfile removes that. |

## 4. Architecture

```
main thread                         worker thread
-----------                         -------------
Tk mainloop                         run_stage1 / run_stage2
  |                                   |
  |  root.after(100ms)                |  emit(kind, **data)
  v                                   v
drain queue  <----- queue.Queue <---- subscribe()
  |
  v
runstate.fold(event)  ->  RunState (plain data)
  |
  v
repaint widgets

stop: window sets threading.Event -> worker checks it between listings
```

Three units:

| Unit | Responsibility | Depends on |
| --- | --- | --- |
| `app.py` | The window and its widgets. Owns the main thread. Never scrapes. | `runstate`, `scrape` |
| `runstate.py` | Folds an event stream into what the screen shows. Pure. | nothing |
| `scrape.py` | Unchanged scraping, plus an event hook and a stop flag. | as today |

## 5. Changes to `scrape.py`

Additive only. The command line behaviour must not change, and the existing
74 tests must stay green.

```python
_listeners: list = []
_stop = threading.Event()


def subscribe(listener) -> None:
    """Register a callable invoked as listener(kind, data) for each event."""


def unsubscribe(listener) -> None: ...


def emit(kind: str, **data) -> None:
    """Notify every listener. Never raises: a listener error must not stop a run."""


def request_stop() -> None: ...
def stop_requested() -> bool: ...
def clear_stop() -> None: ...
```

Every existing `print()` in the run path becomes an `emit()`. A default
listener, installed by `main()`, prints exactly the strings the CLI prints
today, so terminal output is byte-identical.

`scrape_query` checks `stop_requested()` between listings and `run_stage1`
between queries. On a stop request both return normally, so the pair is left
unmarked and a later run retries it.

### Events

| Kind | Data | Raised by |
| --- | --- | --- |
| `run_start` | `terms`, `states`, `total_queries` | `run_stage1` |
| `query_start` | `term`, `state` | `run_stage1` |
| `query_skipped` | `term`, `state` | `run_stage1`, for a cached pair |
| `listings_found` | `term`, `state`, `count`, `at_cap` | `scrape_query` |
| `listing_saved` | `name`, `city`, `state` | `scrape_query` |
| `listing_failed` | `error` | `scrape_query` |
| `query_done` | `term`, `state`, `scraped`, `failed`, `complete` | `run_stage1` |
| `query_failed` | `term`, `state`, `error` | `run_stage1` |
| `blocked` | `term`, `state`, `consecutive` | `run_stage1` |
| `stage2_start` | `total` | `run_stage2` |
| `enriched` | `index`, `total`, `name`, `error` | `run_stage2` |
| `run_finished` | `reason` (`done`, `stopped`, `blocked`, `crashed`) | worker |

`run_finished` maps to a status: `done` and `stopped` both give `finished`
(a stopped run still has usable results), while `blocked` and `crashed` both
give `blocked`, which is the panel that explains what happened and offers a
way to continue.

## 6. `runstate.py`

Pure functions over plain data. No widgets, no threads, no I/O.

```python
@dataclass
class RunState:
    status: str                      # idle | running | paused | finished | blocked
    coverage: dict[str, str]         # state name -> done | partial | failed | active | pending
    clubs: int
    queries_done: int
    queries_total: int
    started_at: float | None
    current: str                     # human-readable current activity
    log: list[str]                   # most recent lines, capped
    failures: list[tuple[str, str]]  # (state, reason)
    at_cap: list[str]                # states that hit the ~120 result cap


def initial_state(done_pairs, terms, states) -> RunState:
    """Seed coverage from the resume cache before any event arrives."""


def fold(state: RunState, kind: str, data: dict) -> RunState:
    """Return the state after one event. Never mutates its argument."""


def elapsed(state, now) -> str: ...
def remaining(state, now) -> str: ...
def fill_rate_rows(records) -> list[tuple[str, float]]: ...
```

Coverage is seeded from `read_cache()`, which records only completed pairs.
A state that failed in an earlier session therefore shows as `pending` rather
than `failed` on relaunch. That is correct behaviour, since it will be retried
either way, but the red and amber distinctions live only within a session.

## 6a. Settings

The term list, the selected states and the option checkboxes are saved to
`data/settings.json` when a run starts and reloaded at launch. Without this a
user retypes their search terms every session, which for a tool run repeatedly
is the difference between usable and irritating.

When the file is absent or unreadable the window falls back to `SEARCH_TERMS`
and `STATES` from `scrape.py`, so a fresh clone opens with working defaults.
The file holds preferences only; it is not the resume cache and deleting it
loses no scraped data.

## 7. The window

One `Tk` window, four states, matching `design/canvas.json`:

| State | What changes |
| --- | --- |
| Setup | Term list, options, coverage grid seeded from the cache, Start button. |
| Running | Start becomes Stop; current activity, progress bar, counters and activity log appear; the grid updates live. |
| Finished | Results table, data-health panel, buttons to open the file or start again. |
| Interrupted | The blocked banner with its three choices, above the grid showing what survived. |

Widgets are `ttk` throughout, on a forced `clam` theme with an explicit
palette (see section 7a). Every path is a `pathlib.Path`.

The coverage grid is a `tk.Canvas` of coloured rectangles rather than 50
widgets, so a repaint is one redraw.

## 7a. Visual fidelity

The app must look the same on macOS and Windows. Native per-platform theming
is therefore rejected: `ttk` is forced to the `clam` theme, which ships on
macOS, Windows and Linux alike and accepts full restyling. Verified on this
machine: background, foreground, border width, relief and padding are all
honoured under `clam`.

The palette is the design's, converted from oklch to the hex values Tk needs:

```python
PALETTE = {
    "bg":       "#f9f9f6",   "panel":    "#f3f3f0",
    "sunken":   "#ededea",   "line":     "#dad9d5",
    "ink":      "#2c2a25",   "muted":    "#71706b",
    "faint":    "#86857f",   "field":    "#fefdfc",
    "accent":   "#3b6fbc",   "accent_d": "#2559a3",
    "done":     "#50a069",   "partial":  "#dea645",
    "failed":   "#c74f47",
}
```

Fonts are resolved once at startup against `tkinter.font.families()`, taking
the first that exists, because no single face ships on both systems:

```python
UI_FACES   = ("Segoe UI", "Helvetica Neue", "DejaVu Sans")
MONO_FACES = ("Consolas", "Menlo", "DejaVu Sans Mono", "Courier New")
```

**Pinned across platforms:** every colour, all spacing and panel geometry, the
type scale in points, table and border treatment, and the coverage grid, which
is drawn on a `tk.Canvas` and is therefore identical to the pixel.

**Unavoidably different:** the font face itself, and with it small text-metric
differences. Layout must therefore size text containers by measuring rendered
text rather than by hardcoded pixel widths. Font smoothing differs between the
two systems, and the window's own title bar belongs to the OS.

**Not achievable in `ttk`:** rounded corners and drop shadows. Widgets under
`clam` are square. The mockups draw buttons with a small radius; the app uses
square buttons, which suits a native toolkit and costs nothing. Reproducing
the radius would mean drawing every button on a `Canvas` and reimplementing
hover, press, focus and disabled states by hand, which is a poor trade for a
few pixels of corner.

## 8. Error handling

- The worker wraps the whole run. Any exception becomes a `run_finished`
  event with reason `crashed` plus the traceback, shown in the interrupted
  panel. The window never dies with the scrape.
- `emit()` swallows listener exceptions. A GUI bug must not abort a
  three-hour scrape.
- The window stays responsive throughout, because nothing that touches the
  network or a browser runs on the main thread.
- Closing the window during a run asks for confirmation, then requests a stop
  and waits for the worker to finish its current listing.

## 9. Distribution

Teammates clone the private repository and run one script.

```
setup.sh / setup.command      macOS and Linux
setup.bat                     Windows
run.sh / run.command          macOS and Linux
run.bat                       Windows
```

`setup` installs `uv` if it is absent, has `uv` install Python 3.12, creates
the virtual environment, installs the locked dependencies, and downloads
Chromium. `run` launches `app.py`. About 180 MB is downloaded once per
machine; nothing is downloaded again afterwards.

No code signing is involved anywhere: the colleague's machine builds and runs
the code locally, which Gatekeeper and SmartScreen do not gate. Files obtained
by `git clone` are not quarantined; the README carries the one-line `xattr`
command for anyone who takes a zip instead.

`.bat` files are written with CRLF line endings.

## 10. File layout

```
app.py                  the window
runstate.py             pure state folding and formatting
scrape.py               unchanged scraping, plus event hook and stop flag
pyproject.toml          dependencies
uv.lock                 exact resolved versions
setup.sh / setup.bat    one-time bootstrap
run.sh / run.bat        launcher
data/settings.json      remembered terms, states and options (gitignored)
tests/test_runstate.py  reducer and formatter tests
tests/test_parsing.py   unchanged, must stay green
README.md               gains a section for teammates
```

## 11. Testing

`runstate.py` is unit-tested against event sequences: a clean run, a run with
a failed query, a run that hits the result cap, a stop, a block, and a resume
seeded from a cache. Assertions are on the resulting `RunState`, so no
browser, network or display is involved.

`scrape.py` gains tests for `subscribe`/`emit`/`unsubscribe`, for `emit`
swallowing a listener error, and for the stop flag halting a loop. Its
existing 74 tests must pass unchanged.

`app.py` is verified by launching it: the four states, a real short scrape, a
stop mid-run, and a window close during a run.

## 12. Risks

| Risk | Mitigation |
| --- | --- |
| Widget code drifts onto the worker thread and corrupts Tk state | All widget access lives in `app.py` methods called only from the `root.after` tick. Nothing in `runstate.py` imports `tkinter`. |
| A slow repaint blocks the tick during a fast event burst | Events are drained in batches and the grid repaints at most once per tick. |
| Windows appearance differs from the macOS mockups | Addressed by section 7a: forced `clam` theme and an explicit palette pin the look. Residual difference is the font face only, which is why text containers are measured rather than fixed. |
| A teammate's `uv` install is blocked by corporate policy | The README documents the manual path: install Python 3.12, then `pip install -r requirements.txt`. |
| The event hook changes CLI output | The default listener reproduces today's strings, and the existing tests cover the code paths that emit them. |
