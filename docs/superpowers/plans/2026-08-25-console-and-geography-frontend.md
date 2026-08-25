# Console Restyle and Geography Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restyle the whole desktop app to the approved Console direction, then give it a country → state → city selector, a cost preview and a coverage display that works worldwide.

**Architecture:** A new `theme.py` owns every colour, font and ttk style, so the palette has one home instead of being split between `widgets.PALETTE` and `app.apply_theme`. A new `selection.py` owns the pure algebra over the `Selection` dict (`country -> region -> cities`) that the selector screen edits and `geo.leaf_places` consumes. `widgets.py` grows two canvas-drawn controls — a virtualised `PickList` for lists too long to scroll, and a rewritten `CoverageGrid` grouped by country — because ttk can draw neither. `app.py` gains one new panel and loses its US-only seam.

**Tech Stack:** Python 3.11+, tkinter/ttk (clam theme), ctypes for OS font registration, Unbounded (SIL OFL 1.1) shipped under `assets/fonts/`.

**Spec:** `docs/superpowers/specs/2026-08-25-geography-targeting-design.md` — sections 7 through 10 and "Visual direction". Sections 1–6 landed in the backend plan and are done.

## Global Constraints

- **Colours are exact.** Every hex in this plan was converted from the `oklch()` values in `design/geography/*.dc.html`. Never substitute an approximation, and never introduce a colour that is not in `theme.PALETTE`.
- **No new runtime dependency.** The full list stays `playwright`, `httpx`, `beautifulsoup4`, `gspread`, `google-auth`, `phonenumbers`.
- **Font registration must never raise.** A missing, unreadable or corrupt `.ttf`, or an OS that refuses it, falls back to the system face. `tkfont.Font(family=...)` substitutes silently when a family is absent, so resolve the family actually obtained — never assume registration worked.
- **`runstate.py` stays pure** — no tkinter, no filesystem, no clock of its own.
- **Tk is not thread-safe.** Nothing reachable from the worker thread may touch a widget.
- **Tests must run without a display.** Import widget modules freely; never instantiate a widget or a `tk.Tk` in a test.
- **`geo.Place` is the unit of geography.** Never pass a bare state name across a function boundary.
- **User-visible copy is plain English.** New copy says "businesses", not "clubs".

---

## File Structure

| File | Responsibility |
|---|---|
| `theme.py` *(new)* | The Console palette, font registration, font objects, ttk restyle. Every colour in the app resolves here. |
| `selection.py` *(new)* | Pure algebra over `Selection`: toggling, counting, summarising, pruning, legacy migration. No tkinter. |
| `assets/fonts/` *(new)* | `Unbounded-Regular.ttf`, `Unbounded-Bold.ttf`, `OFL.txt`. Already placed on disk. |
| `widgets.py` | Canvas controls. Gains `PickList` and `CoverageSummary`; `CoverageGrid` is rewritten; `PALETTE` and `STATE_ABBR` are removed. |
| `app.py` | Window and panels. Gains the locations panel; loses `_us_places`, `apply_theme`, `build_fonts`, `resolve_face`. |
| `runstate.py` | Gains `estimate_run`, per-term coverage, and a completeness test that handles many cities per region. |
| `geo.py` | Gains `search_places`. |
| `scrape.py` | Gains `LISTING_OVERHEAD` and `UNCAPPED_ASSUMPTION`. Nothing else changes. |
| `settings.py` | `states` becomes `selection`, with migration. |
| `paths.py` | Gains `font_dir` and `bundled_fonts`. |
| `wkey-lead-scraper.spec` | Ships `assets/fonts`. |

---

### Task 1: `theme.py` — palette, fonts and OS font registration

**Files:**
- Create: `theme.py`
- Create: `tests/test_theme.py`
- Modify: `paths.py` (append two functions)

**Interfaces:**
- Consumes: `paths.frozen()`, `paths.bundled_fonts()` (added here).
- Produces:
  - `theme.PALETTE: dict[str, str]` — every colour, hex.
  - `theme.DISPLAY_FACES`, `theme.UI_FACES`, `theme.MONO_FACES` — face preference chains.
  - `theme.tracked(text: str) -> str` — letter-spacing for micro-labels.
  - `theme.register_fonts(directory=None) -> list[str]` — filenames the OS accepted.
  - `theme.resolve_face(candidates) -> str`
  - `theme.build_fonts() -> dict[str, tkfont.Font]`
  - `theme.apply_theme(root) -> None`
  - `paths.font_dir() -> Path`, `paths.bundled_fonts() -> Path | None`

**Why registration is not the `Info.plist` route the spec described:** the spec proposed `ATSApplicationFontsPath` for macOS, which only works inside a `.app` bundle and would leave source runs on the fallback face. `CTFontManagerRegisterFontsForURL` with **process scope** was verified to work from a plain `uv run python app.py`, and Tk sees the family immediately — provided registration happens **before `tk.Tk()` is constructed**. One code path, and dev runs get the real font.

- [ ] **Step 1: Add the font paths to `paths.py`**

Append to `paths.py`, after `use_bundled_browsers`:

```python
FONT_DIR = "assets/fonts"


def bundled_fonts() -> Path | None:
    """The font directory inside a frozen bundle, or None outside one."""
    root = getattr(sys, "_MEIPASS", None)
    if not frozen() or root is None:
        return None
    target = Path(root) / FONT_DIR
    return target if target.is_dir() else None


def font_dir() -> Path:
    """Where the shipped .ttf files live, frozen or from source.

    Anchored on this module's own location rather than the working directory:
    a frozen .app launched from Finder starts with cwd "/", and a relative
    path would silently find nothing and drop the app to the fallback face.
    """
    bundled = bundled_fonts()
    if bundled is not None:
        return bundled
    return Path(__file__).resolve().parent / FONT_DIR
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_theme.py`:

```python
import sys
from pathlib import Path

import pytest

import paths
import theme


def test_every_palette_value_is_a_hex_colour():
    bad = {k: v for k, v in theme.PALETTE.items()
           if not (isinstance(v, str) and len(v) == 7 and v.startswith("#"))}
    assert bad == {}, f"Tk cannot parse these: {bad}"


def test_the_palette_carries_the_names_the_app_paints_with():
    required = {
        "bg", "panel", "sunken", "raised", "line", "hairline", "chrome",
        "ink", "bright", "muted", "dim", "faint", "cellink", "field",
        "lime", "lime_ink", "lime_d", "onlime", "amber", "coral",
        "row_sel", "cell_done", "cell_partial", "cell_failed", "cell_active",
        "done_edge", "partial_edge", "failed_edge", "tickline", "seg",
    }
    assert required <= set(theme.PALETTE), f"missing {sorted(required - set(theme.PALETTE))}"


def test_tracked_puts_a_thin_space_between_letters():
    assert theme.tracked("AB") == "A B"


def test_tracked_uppercases_so_callers_do_not_have_to():
    assert theme.tracked("Coverage").startswith("C O")


def test_tracked_leaves_a_single_character_alone():
    assert theme.tracked("A") == "A"


def test_tracked_survives_an_empty_string():
    assert theme.tracked("") == ""


def test_the_shipped_font_directory_exists_with_both_weights():
    names = {p.name for p in paths.font_dir().glob("*.ttf")}
    assert names == {"Unbounded-Regular.ttf", "Unbounded-Bold.ttf"}, names


def test_the_font_licence_ships_beside_the_fonts():
    assert (paths.font_dir() / "OFL.txt").is_file(), "OFL 1.1 requires the licence to ship"


def test_registering_a_directory_that_does_not_exist_reports_nothing(tmp_path):
    assert theme.register_fonts(tmp_path / "nope") == []


def test_registering_a_file_that_is_not_a_font_does_not_raise(tmp_path):
    (tmp_path / "Broken.ttf").write_bytes(b"not a font")
    assert theme.register_fonts(tmp_path) == [], "a corrupt face must fall back, not crash"


@pytest.mark.skipif(
    sys.platform != "darwin" and not sys.platform.startswith("win"),
    reason="only macOS and Windows have a font-registration API; Linux CI has none",
)
def test_registering_the_real_fonts_reports_both_weights():
    accepted = theme.register_fonts()
    assert set(accepted) == {"Unbounded-Regular.ttf", "Unbounded-Bold.ttf"}, accepted


def test_a_platform_without_a_registration_api_reports_nothing_and_does_not_raise(
        monkeypatch):
    """The Linux test runner takes this path, and so would any future one."""
    monkeypatch.setattr(theme.sys, "platform", "sunos5")
    assert theme.register_fonts() == []
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_theme.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'theme'`

- [ ] **Step 4: Write `theme.py`**

```python
"""The Console look: one palette, one font stack, one ttk restyle.

Every colour the app paints comes from PALETTE, and every hex in it was
converted from the oklch() values in design/geography/*.dc.html. Tk cannot
parse oklch, and it has no notion of alpha, so the translucent fills in the
design are pre-composited here against the surface they sit on -- that is why
names like `cell_done` exist alongside `lime`.

Fonts are registered with the OS before Tk starts, because Tk resolves only
families the OS already knows and silently substitutes when one is missing.
"""

import ctypes
import ctypes.util
import sys
from pathlib import Path

import paths

PALETTE = {
    # surfaces, darkest first
    "bg": "#0a0c0f",
    "chrome": "#181b1f",
    "panel": "#0f1114",
    "sunken": "#121517",
    "raised": "#131518",
    "field": "#121517",
    "kbd": "#202327",
    # lines
    "hairline": "#1c1f23",
    "cellline": "#222528",
    "line": "#24282c",
    "seg": "#2b2e32",
    "tickline": "#373b40",
    # type, dimmest first
    "logtime": "#4e5358",
    "faint": "#6a6f74",
    "cellink": "#82878c",
    "dim": "#9a9fa5",
    "muted": "#b7bbc0",
    "soft": "#dbdee1",
    "ink": "#f1f3f4",
    "bright": "#f5f7f9",
    # the one accent, which also means "finished"
    "lime": "#c0e73f",
    "lime_ink": "#c6e27e",
    "lime_hi": "#cdee77",
    "lime_d": "#a4c900",
    "lime_edge": "#596b26",
    "onlime": "#101700",
    "onlime_d": "#1d2600",
    # statuses. Green is deliberately absent: at cell size it is
    # indistinguishable from lime, which already means finished.
    "amber": "#f5ae39",
    "amber_ink": "#e7b369",
    "coral": "#f5605b",
    "coral_ink": "#f7857d",
    # pre-composited translucent fills (Tk has no alpha)
    "row_sel": "#212618",
    "cell_done": "#262c1c",
    "cell_active": "#2f371e",
    "cell_partial": "#2c261c",
    "cell_failed": "#2e1e20",
    "done_edge": "#4e5c25",
    "partial_edge": "#644c24",
    "failed_edge": "#6d3333",
    # window-chrome dots
    "dot_red": "#c65954",
    "dot_amber": "#b7933f",
    "dot_green": "#5a9f5d",
}

# Unbounded carries the wordmark, the micro-labels, the big numerals and the
# buttons. It is deliberately not the body face: its wide geometric letters
# are hard to read at 13px in a dense list, and dense lists are exactly where
# an operator hunts for a city.
DISPLAY_FACES = ("Unbounded", "Segoe UI", "Helvetica Neue", "DejaVu Sans")
UI_FACES = ("Segoe UI", "Helvetica Neue", "DejaVu Sans")
MONO_FACES = ("SF Mono", "Consolas", "Menlo", "DejaVu Sans Mono", "Courier New")

# Tk has no letter-spacing, so the design's tracked uppercase labels are
# approximated by threading a thin space between the letters. At 9px a thin
# space measures 2px against the design's 0.15em (1.35px) -- close enough to
# read as tracking rather than as words falling apart.
_THIN = " "


def tracked(text: str) -> str:
    """A micro-label with the design's letter-spacing faked in."""
    return _THIN.join(text.upper())


def _register_macos(path: Path) -> bool:
    core_text = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreText"))
    core_foundation = ctypes.cdll.LoadLibrary(
        ctypes.util.find_library("CoreFoundation"))
    core_foundation.CFStringCreateWithCString.restype = ctypes.c_void_p
    core_foundation.CFStringCreateWithCString.argtypes = [
        ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
    core_foundation.CFURLCreateWithFileSystemPath.restype = ctypes.c_void_p
    core_foundation.CFURLCreateWithFileSystemPath.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long, ctypes.c_bool]
    core_text.CTFontManagerRegisterFontsForURL.restype = ctypes.c_bool
    core_text.CTFontManagerRegisterFontsForURL.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p]

    utf8 = 0x08000100
    posix_style = 0
    process_scope = 1
    text = core_foundation.CFStringCreateWithCString(
        None, str(path).encode("utf-8"), utf8)
    url = core_foundation.CFURLCreateWithFileSystemPath(
        None, text, posix_style, False)
    return bool(core_text.CTFontManagerRegisterFontsForURL(
        url, process_scope, None))


def _register_windows(path: Path) -> bool:
    private = 0x10
    added = ctypes.windll.gdi32.AddFontResourceExW(str(path), private, 0)
    return bool(added)


def register_fonts(directory=None) -> list[str]:
    """Register every .ttf in `directory` with the OS, for this process only.

    Returns the filenames the OS accepted, so a caller can tell the difference
    between "the font is available" and "Tk is about to substitute silently".

    Must run before tk.Tk() is constructed: Tk asks the OS for its family list
    once, at startup, and a font registered afterwards is invisible to it.

    Never raises. Every failure mode here -- no font directory, an unreadable
    file, a platform with no registration API, a corrupt face the OS rejects
    -- ends the same way: the family is absent and resolve_face() falls
    through to the next candidate.
    """
    folder = Path(directory) if directory is not None else paths.font_dir()
    if not folder.is_dir():
        return []
    register = {"darwin": _register_macos}.get(sys.platform)
    if register is None and sys.platform.startswith("win"):
        register = _register_windows
    if register is None:
        return []
    accepted = []
    for font in sorted(folder.glob("*.ttf")):
        try:
            if register(font):
                accepted.append(font.name)
        except Exception:
            continue
    return accepted


def resolve_face(candidates) -> str:
    """First installed family from `candidates`, else Tk's own default.

    No single face ships on both macOS and Windows, so the app picks per
    machine rather than naming one and getting a silent substitution.
    """
    import tkinter.font as tkfont

    available = set(tkfont.families())
    for name in candidates:
        if name in available:
            return name
    return tkfont.nametofont("TkDefaultFont").actual("family")


def build_fonts() -> dict:
    """The app's font objects, keyed by the role each one plays."""
    import tkinter.font as tkfont

    display = resolve_face(DISPLAY_FACES)
    ui = resolve_face(UI_FACES)
    mono = resolve_face(MONO_FACES)
    return {
        "ui": tkfont.Font(family=ui, size=12),
        "ui_bold": tkfont.Font(family=ui, size=12, weight="bold"),
        "row": tkfont.Font(family=ui, size=12),
        "small": tkfont.Font(family=ui, size=10),
        "note": tkfont.Font(family=mono, size=10),
        "mono": tkfont.Font(family=mono, size=11),
        # Display roles. Size 8 for the tracked micro-labels, because the thin
        # spaces widen them and Unbounded is wide to start with.
        "label": tkfont.Font(family=display, size=8, weight="bold"),
        "wordmark": tkfont.Font(family=display, size=10, weight="bold"),
        "title": tkfont.Font(family=display, size=15),
        "big": tkfont.Font(family=display, size=21, weight="bold"),
        "huge": tkfont.Font(family=display, size=30, weight="bold"),
        "button": tkfont.Font(family=display, size=11, weight="bold"),
        "cell": tkfont.Font(family=display, size=10, weight="bold"),
    }


def apply_theme(root) -> None:
    """Force clam and restyle it to the Console palette.

    Native themes are deliberately rejected: the app must look the same on
    macOS and Windows, and clam is the one theme present on both that accepts
    a full restyle. Under a dark palette that matters more than it did under a
    light one -- an unstyled native widget on a near-black canvas reads as a
    rendering bug, not as a default.
    """
    from tkinter import ttk

    style = ttk.Style(root)
    style.theme_use("clam")
    p = PALETTE

    style.configure(".", background=p["bg"], foreground=p["ink"],
                    fieldbackground=p["field"], bordercolor=p["line"],
                    lightcolor=p["line"], darkcolor=p["line"],
                    focuscolor=p["lime"], troughcolor=p["sunken"],
                    insertcolor=p["ink"], selectbackground=p["row_sel"],
                    selectforeground=p["bright"])
    style.configure("TFrame", background=p["bg"])
    style.configure("Panel.TFrame", background=p["panel"])
    style.configure("Chrome.TFrame", background=p["chrome"])
    style.configure("Card.TFrame", background=p["panel"], relief="solid",
                    borderwidth=1)
    style.configure("TLabel", background=p["bg"], foreground=p["ink"])
    style.configure("Muted.TLabel", foreground=p["dim"])
    style.configure("Faint.TLabel", foreground=p["faint"])
    style.configure("Lime.TLabel", foreground=p["lime"])
    style.configure("Amber.TLabel", foreground=p["amber"])

    style.configure("TEntry", fieldbackground=p["field"], foreground=p["ink"],
                    bordercolor=p["line"], lightcolor=p["line"],
                    darkcolor=p["line"], insertcolor=p["ink"], padding=5)
    style.map("TEntry", bordercolor=[("focus", p["lime_edge"])])

    # clam draws the tick from indicatorbackground/indicatorforeground, so a
    # lime box with a near-black check is reachable without hand-drawing one.
    style.configure("TCheckbutton", background=p["bg"], foreground=p["ink"],
                    indicatorbackground=p["field"],
                    indicatorforeground=p["onlime"],
                    upperbordercolor=p["tickline"],
                    lowerbordercolor=p["tickline"], focuscolor=p["lime"])
    style.map("TCheckbutton",
              indicatorbackground=[("selected", p["lime"]),
                                   ("disabled", p["sunken"])],
              foreground=[("disabled", p["faint"])],
              upperbordercolor=[("selected", p["lime"])],
              lowerbordercolor=[("selected", p["lime"])])

    style.configure("TSpinbox", fieldbackground=p["field"],
                    foreground=p["ink"], bordercolor=p["line"],
                    arrowcolor=p["dim"], lightcolor=p["line"],
                    darkcolor=p["line"])
    style.map("TSpinbox",
              fieldbackground=[("readonly", p["field"]),
                               ("disabled", p["panel"])],
              foreground=[("disabled", p["faint"])],
              arrowcolor=[("disabled", p["seg"])])

    style.configure("TProgressbar", background=p["lime"],
                    troughcolor=p["hairline"], borderwidth=0, thickness=6,
                    lightcolor=p["lime"], darkcolor=p["lime"])

    style.configure("Treeview", background=p["panel"], foreground=p["muted"],
                    fieldbackground=p["panel"], borderwidth=0, rowheight=26)
    style.configure("Treeview.Heading", background=p["chrome"],
                    foreground=p["faint"], relief="flat", borderwidth=0)
    style.map("Treeview.Heading", background=[("active", p["sunken"])])
    style.map("Treeview", background=[("selected", p["row_sel"])],
              foreground=[("selected", p["bright"])])

    style.configure("TScrollbar", background=p["seg"], troughcolor=p["panel"],
                    bordercolor=p["panel"], arrowcolor=p["faint"],
                    lightcolor=p["panel"], darkcolor=p["panel"])
    style.map("TScrollbar", background=[("active", p["tickline"])])
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_theme.py -v`
Expected: 12 passed (11 on Linux, where the registration test skips).

- [ ] **Step 6: Prove Tk actually gets the family, not a substitution**

This is the failure mode the spec warns about, and it cannot be caught by a
test that never opens a display. Run it once by hand:

```bash
uv run python -c "
import theme, paths
print('registered:', theme.register_fonts())
import app  # sets TCL_LIBRARY before tkinter is imported
import tkinter as tk, tkinter.font as tkfont
root = tk.Tk(); root.withdraw()
print('resolved display face:', theme.resolve_face(theme.DISPLAY_FACES))
f = theme.build_fonts()['big']
print('big ->', f.actual('family'), f.actual('size'), f.actual('weight'))
root.destroy()
"
```

Expected: `registered: ['Unbounded-Bold.ttf', 'Unbounded-Regular.ttf']`, then
`resolved display face: Unbounded`, then `big -> Unbounded 21 bold`.
If the resolved face is anything but `Unbounded`, stop and report it — every
later task assumes the display face is real.

- [ ] **Step 7: Run the whole suite and commit**

```bash
uv run pytest -q
git add theme.py tests/test_theme.py paths.py assets/fonts
git commit -m "feat: add the Console theme, with Unbounded registered at startup"
```

---

### Task 2: Repaint the existing app in Console

**Files:**
- Modify: `widgets.py` (delete `PALETTE`, restyle `_KINDS`/`_DISABLED`/`STATUS_COLORS`)
- Modify: `app.py` (delete the font/theme block, rewire every colour)
- Modify: `tests/test_widgets.py` (the palette moved)

**Interfaces:**
- Consumes: everything Task 1 produced.
- Produces: nothing new. `widgets.PALETTE` is gone; importers use `theme.PALETTE`.

**Scope:** the app must look Console and behave exactly as it does today. No new panel, no geography change, no coverage change. A reviewer should be able to run it, see the dark app, start a run and get the same behaviour as before.

- [ ] **Step 1: Point `widgets.py` at the theme**

Replace the `PALETTE = {...}` literal at the top of `widgets.py` with an import,
keeping `STATE_ABBR` exactly as it is (Task 6 removes it):

```python
import math
import tkinter as tk

from theme import PALETTE
```

- [ ] **Step 2: Restyle the button kinds**

Replace `_KINDS` and `_DISABLED` in `widgets.py`:

```python
_KINDS = {
    "primary": {
        "fill": PALETTE["lime"], "hover": PALETTE["lime_d"],
        "text": PALETTE["onlime"], "border": PALETTE["lime"],
    },
    "secondary": {
        "fill": PALETTE["sunken"], "hover": PALETTE["kbd"],
        "text": PALETTE["soft"], "border": PALETTE["line"],
    },
    "quiet": {
        "fill": PALETTE["panel"], "hover": PALETTE["sunken"],
        "text": PALETTE["dim"], "border": PALETTE["hairline"],
    },
    "danger": {
        "fill": PALETTE["sunken"], "hover": PALETTE["cell_failed"],
        "text": PALETTE["coral"], "border": PALETTE["failed_edge"],
    },
}
_DISABLED = {
    "fill": PALETTE["panel"], "hover": PALETTE["panel"],
    "text": PALETTE["faint"], "border": PALETTE["hairline"],
}
```

In `RoundedButton._draw`, the focus ring colour changes from `PALETTE["accent_d"]`
(gone) to `PALETTE["lime"]`, and the default radius goes from `5` to `8` to match
the design's 8–9px corners. Change the `radius=5` default in `__init__` to
`radius=8`.

- [ ] **Step 3: Restyle the coverage statuses**

Replace `STATUS_COLORS` in `widgets.py`:

```python
# Green is deliberately absent. Lime already means "finished", and at cell
# size a green sits too close to it to tell apart -- so partly-done takes
# amber and failed takes coral.
STATUS_COLORS = {
    "pending": (PALETTE["raised"], PALETTE["cellline"], PALETTE["cellink"]),
    "done":    (PALETTE["cell_done"], PALETTE["done_edge"], PALETTE["lime_ink"]),
    "active":  (PALETTE["cell_active"], PALETTE["lime"], PALETTE["lime_hi"]),
    "partial": (PALETTE["cell_partial"], PALETTE["partial_edge"], PALETTE["amber_ink"]),
    "failed":  (PALETTE["cell_failed"], PALETTE["failed_edge"], PALETTE["coral_ink"]),
}
```

- [ ] **Step 4: Gut the theme block in `app.py`**

Delete these from `app.py` outright — they now live in `theme.py`:
`UI_FACES`, `MONO_FACES`, `resolve_face`, `build_fonts`, `apply_theme`, and the
`import tkinter.font as tkfont` line if nothing else uses it.

Register the fonts immediately after `_ensure_tcl_paths()` and **before** the
`import tkinter` line, then import the theme's helpers:

```python
_ensure_tcl_paths()

# Also deliberately before the tkinter import, and for the same class of
# reason: Tk asks the OS for its font families once, when it starts, so a
# font registered afterwards would be invisible and every display face would
# silently fall back to the system sans.
theme.register_fonts()

import tkinter as tk  # noqa: E402
from tkinter import messagebox, ttk  # noqa: E402

import runstate
import scrape
import settings
from theme import PALETTE
from widgets import CoverageGrid, RoundedButton
```

`import theme` goes up with `import geo` / `import paths` at the top. In
`App.__init__`, `self.fonts = build_fonts()` becomes `theme.build_fonts()` and
`apply_theme(self)` becomes `theme.apply_theme(self)`.

- [ ] **Step 5: Rename every colour `app.py` asks for**

Apply this mapping to all 42 `PALETTE[...]` references. The old palette's
`muted` was secondary text; the new palette's `muted` is brighter than that,
so secondary text moves to `dim` — do not leave `muted` in place.

| Old | New | Why |
|---|---|---|
| `accent` | `lime` | one accent hue |
| `accent_d` | `lime_d` | |
| `done` | `lime` | lime doubles as "finished" |
| `done_ink` | `lime_ink` | |
| `partial` | `amber` | |
| `failed` | `coral` | |
| `selected` | `row_sel` | |
| `muted` | `dim` | the new `muted` is a brighter step |
| `bg`, `panel`, `sunken`, `field`, `ink`, `line`, `faint` | unchanged names | |

- [ ] **Step 6: Fix the four spots that need more than a rename**

**The activity log** (`_build_running`) — a `tk.Text` keeps a light insertion
cursor and selection under any ttk theme:

```python
self.log_box = tk.Text(panel, height=7, font=self.fonts["mono"],
                       bg=PALETTE["panel"], fg=PALETTE["dim"],
                       relief="flat", bd=0, highlightthickness=1,
                       highlightbackground=PALETTE["cellline"],
                       highlightcolor=PALETTE["cellline"],
                       insertbackground=PALETTE["ink"],
                       selectbackground=PALETTE["row_sel"],
                       selectforeground=PALETTE["bright"],
                       wrap="none", state="disabled")
```

**The terms list** (`_build_setup`) — same for `tk.Listbox`:

```python
self.terms_list = tk.Listbox(
    terms_box, width=38, height=5, font=self.fonts["ui"],
    bg=PALETTE["panel"], fg=PALETTE["ink"], relief="flat", bd=0,
    highlightthickness=1, highlightbackground=PALETTE["cellline"],
    highlightcolor=PALETTE["cellline"], selectbackground=PALETTE["row_sel"],
    selectforeground=PALETTE["bright"], activestyle="none",
)
```

**The status dot** (`render`) — `accent` and `done` both became `lime`, which
would make "running" and "finished" identical. Running keeps lime; idle and
blocked separate:

```python
dot = {"idle": PALETTE["faint"], "running": PALETTE["lime"],
       "finished": PALETTE["lime"], "blocked": PALETTE["amber"]}[s.status]
```

**The health meters** (`_fill_health`) — the trough was `sunken`, which is now
nearly invisible against the panel:

```python
meter = tk.Canvas(row, width=120, height=6, highlightthickness=0,
                  bd=0, bg=PALETTE["hairline"])
...
colour = PALETTE["lime"] if rate >= 0.4 else PALETTE["amber"]
```

- [ ] **Step 7: Track the micro-labels**

Every `style="Faint.TLabel"` label whose text is an all-caps section heading
(`SEARCH TERMS`, `OPTIONS`, `COVERAGE`, `ACTIVITY`, `SAVING TO`,
`HOW COMPLETE THE DATA IS`, `WHERE IT GOT TO`) gets its text wrapped in
`theme.tracked(...)` and its font changed to `self.fonts["label"]` — which it
already uses. Write the source text in ordinary case and let `tracked` upper it:

```python
ttk.Label(grid_head, text=theme.tracked("Coverage"), style="Faint.TLabel",
          font=self.fonts["label"]).pack(side="left")
```

The wordmark is new. Add it to `_build_toolbar`, centred, between the buttons
and the "SAVING TO" block:

```python
mark = ttk.Frame(bar, style="Panel.TFrame")
mark.pack(side="left", expand=True)
ttk.Label(mark, text=theme.tracked("Wkey"), font=self.fonts["wordmark"],
          style="Muted.TLabel", background=PALETTE["panel"]).pack(side="left")
ttk.Label(mark, text="·", font=self.fonts["wordmark"],
          style="Lime.TLabel", background=PALETTE["panel"]).pack(side="left")
ttk.Label(mark, text=theme.tracked("Lead Scraper"), font=self.fonts["wordmark"],
          style="Muted.TLabel", background=PALETTE["panel"]).pack(side="left")
```

- [ ] **Step 8: Move the palette test**

In `tests/test_widgets.py`, the abbreviation test imports `widgets.STATE_ABBR`
and still passes. Add one test that the indirection holds:

```python
def test_widgets_paints_from_the_shared_theme_palette():
    import theme
    assert widgets.PALETTE is theme.PALETTE, "one palette, not a copy that can drift"
```

- [ ] **Step 9: Run the suite**

Run: `uv run pytest -q`
Expected: every existing test still passes, plus the new one.

- [ ] **Step 10: Run the app and look at it**

```bash
uv run python app.py
```

Check, in this order, and report anything that does not hold:
1. The window is near-black, not grey and not half-light.
2. The wordmark reads `W k e y · L e a d  S c r a p e r` in Unbounded.
3. `Start scrape` is a lime pill with near-black text.
4. The coverage grid's 50 cells are dark with a faint border; nothing is green.
5. Tab moves focus and the focused button shows a lime ring.
6. The checkboxes under OPTIONS tick lime, not white or grey.
7. The `Stop after [3] clubs per state` spinbox is legible, dark, and greys out
   when its checkbox is cleared.

- [ ] **Step 11: Commit**

```bash
git add widgets.py app.py tests/test_widgets.py
git commit -m "feat: repaint the app in the Console palette"
```

---

### Task 3: `selection.py` and `geo.search_places` — the pure logic

**Files:**
- Create: `selection.py`
- Create: `tests/test_selection.py`
- Modify: `geo.py` (append `search_places`)
- Modify: `settings.py` (`states` becomes `selection`)
- Modify: `tests/test_geo.py`, `tests/test_settings.py`

**Interfaces:**
- Consumes: `geo.Selection`, `geo.countries/regions/cities`, `geo.leaf_places`, `geo.Place`.
- Produces:
  - `geo.search_places(query, limit=200) -> list[Place]`
  - `selection.normalise(raw) -> Selection`
  - `selection.from_states(states) -> Selection`
  - `selection.prune(sel) -> tuple[Selection, list[str]]`
  - `selection.toggle_country/toggle_region/toggle_city(sel, ...) -> Selection`
  - `selection.toggle_place(sel, place) -> Selection`
  - `selection.is_country_on/is_region_on/is_city_on(sel, ...) -> bool`
  - `selection.country_note(sel, country) -> str`, `selection.region_note(sel, country, region) -> str`
  - `selection.summary(sel) -> list[tuple[str, str]]`
  - `selection.cap_noun(sel) -> str`
  - `selection.region_keys(sel) -> list[tuple[str, str]]`

**The shape, restated:** `Selection` is `dict[country, dict[region, list[city]]]`.
An empty city list means the whole region. An empty region dict means the whole
country. A country absent from the dict is not selected at all. Every function
here returns a **new** structure and never mutates its argument, so the app can
hold an undo stack later without redesigning anything.

- [ ] **Step 1: Write the failing tests for `geo.search_places`**

Append to `tests/test_geo.py`:

```python
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
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_geo.py -k search -v`
Expected: FAIL with `AttributeError: module 'geo' has no attribute 'search_places'`

- [ ] **Step 3: Implement `geo.search_places`**

Append to `geo.py`:

```python
MIN_QUERY = 2


def search_places(query: str, limit: int = 200) -> list[Place]:
    """Places whose name contains `query`, broadest and closest match first.

    The selector's three panes cannot answer "where is Austin?" -- you have to
    know it is in Texas before you can find it. This scans all three levels at
    once so the operator does not have to.

    A linear scan over roughly 24,000 names, which measures in single-digit
    milliseconds -- cheap enough to run on every keystroke, and far cheaper
    than the index that avoiding it would cost.
    """
    needle = query.strip().casefold()
    if len(needle) < MIN_QUERY:
        return []

    hits: list[tuple[int, int, str, Place]] = []

    def consider(name: str, place: Place) -> None:
        folded = name.casefold()
        position = folded.find(needle)
        if position < 0:
            return
        # depth first (country before region before city), then a prefix
        # match before a match buried mid-name, then alphabetically.
        hits.append((len(place.parts()), 0 if position == 0 else 1, folded, place))

    for country in geodata.COUNTRIES:
        consider(country, Place(country=country))
    for country, regions_of in geodata.REGIONS.items():
        for region in regions_of:
            consider(region, Place(country=country, region=region))
    for (country, region), names in geodata.CITIES.items():
        for city in names:
            consider(city, Place(country=country, region=region, city=city))

    hits.sort(key=lambda hit: hit[:3])
    return [place for _, _, _, place in hits[:limit]]
```

- [ ] **Step 4: Run them to verify they pass**

Run: `uv run pytest tests/test_geo.py -k search -v`
Expected: 9 passed.

- [ ] **Step 5: Write the failing tests for `selection.py`**

Create `tests/test_selection.py`:

```python
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
    after = selection.toggle_country({}, "Japan")
    assert after == {"Japan": {}}


def test_toggling_never_mutates_the_input():
    before = {US: {"Texas": ["Austin"]}}
    snapshot = {US: {"Texas": ["Austin"]}}
    selection.toggle_city(before, US, "Texas", "Dallas")
    assert before == snapshot


def test_toggling_a_region_on_brings_its_country_with_it():
    after = selection.toggle_region({}, US, "Texas")
    assert after == {US: {"Texas": []}}


def test_toggling_a_region_off_drops_its_cities_too():
    after = selection.toggle_region(SEL, US, "Texas")
    assert "Texas" not in after[US]
    assert after[US]["Utah"] == []


def test_toggling_off_the_last_region_leaves_the_country_selected_whole():
    after = selection.toggle_region({US: {"Texas": []}}, US, "Texas")
    assert after == {}, "no regions left means nothing selected, not the whole country"


def test_toggling_a_city_on_brings_its_region_and_country_with_it():
    after = selection.toggle_city({}, US, "Texas", "Austin")
    assert after == {US: {"Texas": ["Austin"]}}


def test_toggling_off_the_last_city_leaves_the_whole_region_selected():
    after = selection.toggle_city({US: {"Texas": ["Austin"]}}, US, "Texas", "Austin")
    assert after == {US: {"Texas": []}}, "the region stays, now meaning all of it"


def test_cities_stay_in_population_order_however_they_were_picked():
    after = selection.toggle_city({US: {"Texas": ["Dallas"]}}, US, "Texas", "Houston")
    assert after[US]["Texas"] == ["Houston", "Dallas"], "geo.cities order, not click order"


def test_toggle_place_dispatches_on_how_specific_the_place_is():
    assert selection.toggle_place({}, geo.Place(country="Japan")) == {"Japan": {}}
    assert selection.toggle_place({}, geo.Place(country=US, region="Texas")) == {US: {"Texas": []}}
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
    assert selection.is_city_on(SEL, US, "Utah", "Provo") is False, "whole region is not every city"


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
    assert selection.region_keys(SEL) == [("Japan", "Japan"), (US, "Texas"), (US, "Utah")]


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
    assert kept == {US: {"Texas": ["Austin"]}}
    assert dropped == ["Atlantis", "Freedonia"]


def test_prune_keeps_a_real_selection_untouched_and_reports_nothing():
    assert selection.prune(SEL) == (SEL, [])


def test_prune_keeps_a_small_city_that_is_below_the_population_cap():
    """geo.cities is the top 25 only; a real smaller city must survive."""
    kept, dropped = selection.prune({US: {"Texas": ["Kyle"]}})
    assert kept == {US: {"Texas": ["Kyle"]}} and dropped == []
```

- [ ] **Step 6: Run them to verify they fail**

Run: `uv run pytest tests/test_selection.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'selection'`

- [ ] **Step 7: Write `selection.py`**

```python
"""What the operator has picked, and everything derivable from it.

A Selection is `dict[country, dict[region, list[city]]]`. An empty city list
means the whole region; an empty region dict means the whole country; an
absent country is not selected. That "empty means all of it" rule is the
whole design -- it lets one structure express "all of Japan", "all of Utah"
and "these two cities in Texas" without a mode flag.

Pure, like `runstate` and `geo`: no tkinter, no files, no clock. The selector
screen renders from these functions and writes back through them, and never
keeps state of its own -- a Listbox's own selection is destroyed every time it
repopulates, which is exactly what a three-pane cascade does constantly.

Every function returns a new structure. Nothing here mutates its argument.
"""

import geo


def normalise(raw) -> geo.Selection:
    """Coerce whatever came out of settings.json into the Selection shape.

    A hand-edited or truncated preferences file must never stop the app
    opening, so anything unrecognisable degrades to "nothing selected" rather
    than raising.
    """
    if not isinstance(raw, dict):
        return {}
    out: geo.Selection = {}
    for country, regions_of in raw.items():
        if not isinstance(country, str):
            continue
        clean: dict[str, list[str]] = {}
        if isinstance(regions_of, dict):
            for region, cities in regions_of.items():
                if not isinstance(region, str):
                    continue
                clean[region] = [c for c in cities if isinstance(c, str)] \
                    if isinstance(cities, (list, tuple)) else []
        out[country] = clean
    return out


def from_states(states) -> geo.Selection:
    """The pre-geography preference -- a bare list of US state names."""
    names = [s for s in states if isinstance(s, str)]
    return {"United States": {state: [] for state in names}} if names else {}


def prune(selection: geo.Selection) -> tuple[geo.Selection, list[str]]:
    """Drop places this build of `geodata` does not know, and name them.

    Cities are deliberately not checked. `geo.cities` is the 25 most populous
    per region, so a real smaller city an operator typed into the search box
    is expected to be absent from it and must still survive a reload.
    """
    known_countries = set(geo.countries())
    kept: geo.Selection = {}
    dropped: list[str] = []
    for country in sorted(selection):
        if country not in known_countries:
            dropped.append(country)
            continue
        known_regions = set(geo.regions(country))
        clean: dict[str, list[str]] = {}
        for region in sorted(selection[country]):
            if region not in known_regions:
                dropped.append(region)
                continue
            clean[region] = list(selection[country][region])
        kept[country] = clean
    return kept, dropped


def toggle_country(selection: geo.Selection, country: str) -> geo.Selection:
    """Select the whole country, or clear it and everything under it."""
    out = {c: dict(r) for c, r in selection.items()}
    if country in out:
        del out[country]
    else:
        out[country] = {}
    return out


def toggle_region(selection: geo.Selection, country: str,
                  region: str) -> geo.Selection:
    """Select the whole region, or clear it and its cities.

    Clearing the last region of a country deselects the country outright
    rather than leaving `{country: {}}`, which would silently mean "the whole
    country" -- the opposite of what unticking the last box asks for.
    """
    out = {c: dict(r) for c, r in selection.items()}
    regions_of = out.setdefault(country, {})
    if region in regions_of:
        del regions_of[region]
        if not regions_of:
            del out[country]
    else:
        regions_of[region] = []
    return out


def toggle_city(selection: geo.Selection, country: str, region: str,
                city: str) -> geo.Selection:
    """Add or remove one city, keeping the region's population order.

    Removing the last city leaves the region selected -- now meaning all of
    it. That is the level above, which is where unticking a city should land.
    """
    out = {c: {r: list(cities) for r, cities in rs.items()}
           for c, rs in selection.items()}
    regions_of = out.setdefault(country, {})
    chosen = regions_of.setdefault(region, [])
    if city in chosen:
        chosen.remove(city)
    else:
        order = geo.cities(country, region)
        chosen.append(city)
        # Sort by population rank, with anything geo does not rank (a small
        # city found through search) after the ranked ones, alphabetically.
        chosen.sort(key=lambda name: (order.index(name) if name in order
                                      else len(order), name))
    return out


def toggle_place(selection: geo.Selection, place: geo.Place) -> geo.Selection:
    """Toggle whichever level `place` names."""
    if place.city:
        return toggle_city(selection, place.country, place.region, place.city)
    if place.region:
        return toggle_region(selection, place.country, place.region)
    return toggle_country(selection, place.country)


def is_country_on(selection: geo.Selection, country: str) -> bool:
    return country in selection


def is_region_on(selection: geo.Selection, country: str, region: str) -> bool:
    return region in selection.get(country, {})


def is_city_on(selection: geo.Selection, country: str, region: str,
               city: str) -> bool:
    return city in selection.get(country, {}).get(region, [])


def country_note(selection: geo.Selection, country: str) -> str:
    """The right-hand figure on a country row: "2/51", "all", or "51"."""
    total = len(geo.regions(country))
    if country not in selection:
        return str(total)
    chosen = selection[country]
    return f"{len(chosen)}/{total}" if chosen else "all"


def region_note(selection: geo.Selection, country: str, region: str) -> str:
    """The right-hand figure on a region row: "2/25" or "25"."""
    total = len(geo.cities(country, region))
    chosen = selection.get(country, {}).get(region, [])
    return f"{len(chosen)}/{total}" if chosen else str(total)


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def summary(selection: geo.Selection) -> list[tuple[str, str]]:
    """One (country, description) line per selected country, country-sorted."""
    lines = []
    for country in sorted(selection):
        regions_of = selection[country]
        if not regions_of:
            lines.append((country, "whole country"))
            continue
        cities = sum(len(names) for names in regions_of.values())
        tail = _plural(cities, "city").replace("citys", "cities") if cities \
            else "whole state"
        lines.append((country, f"{_plural(len(regions_of), 'state')} · {tail}"))
    return lines


def cap_noun(selection: geo.Selection) -> str:
    """What "stop after N per ___" should say, given the finest level chosen."""
    if any(names for regions_of in selection.values()
           for names in regions_of.values()):
        return "city"
    if any(selection.values()):
        return "state"
    return "country" if selection else "place"


def region_keys(selection: geo.Selection) -> list[tuple[str, str]]:
    """(country, coverage cell) for every cell the coverage display will show.

    Derived through `geo.leaf_places` and `Place.coverage_key` rather than
    walking the dict here, so the cells the grid draws and the cells
    `runstate` folds into can never come from two different definitions.
    """
    seen = []
    for place in geo.leaf_places(selection):
        pair = (place.country, place.coverage_key())
        if pair not in seen:
            seen.append(pair)
    return seen
```

Note on `_plural` and "cities": `_plural(2, "city")` yields `"2 citys"`, which
the `.replace` corrects. Write it that way rather than adding a plural table —
"city" is the only irregular noun this module ever pluralises.

- [ ] **Step 8: Run them to verify they pass**

Run: `uv run pytest tests/test_selection.py -v`
Expected: 31 passed.

- [ ] **Step 9: Move `settings.py` from `states` to `selection`**

Replace `DEFAULTS` and add migration to `load`:

```python
import json
from pathlib import Path

import scrape
import selection as selection_mod

DEFAULTS = {
    "terms": list(scrape.SEARCH_TERMS),
    # Every US state individually, not {"United States": {}}. The whole
    # country as one query would return 120 results for the entire US --
    # the exact cap this feature exists to work around.
    "selection": selection_mod.from_states(scrape.ALL_50),
    "enrich": True,
    "headed": False,
    "force": False,
    "limit": None,
}
```

In `load`, after the existing per-key copy loop, add the migration and the
normalise pass:

```python
    for key in DEFAULTS:
        if key in stored:
            prefs[key] = stored[key]
    # A preferences file written before geography targeting has a "states"
    # list and no "selection". Read it as US regions rather than silently
    # resetting someone's saved choice to the default.
    if "selection" not in stored and isinstance(stored.get("states"), list):
        prefs["selection"] = selection_mod.from_states(stored["states"])
    prefs["selection"] = selection_mod.normalise(prefs["selection"])
    return prefs
```

`save` needs no change: it writes exactly the `DEFAULTS` keys, so `states`
disappears from the file on the next write.

- [ ] **Step 10: Update the settings tests**

In `tests/test_settings.py`, replace any assertion about `prefs["states"]`
with the equivalent on `selection`, and add:

```python
def test_a_preferences_file_from_before_geography_keeps_its_states(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"terms": ["gym"], "states": ["Texas", "Utah"]}')
    prefs = settings.load(path)
    assert prefs["selection"] == {"United States": {"Texas": [], "Utah": []}}


def test_a_newer_selection_wins_over_a_leftover_states_list(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"states": ["Texas"], "selection": {"Japan": {}}}')
    assert settings.load(path)["selection"] == {"Japan": {}}


def test_saving_drops_the_obsolete_states_key(tmp_path):
    path = tmp_path / "settings.json"
    settings.save({"selection": {"Japan": {}}, "states": ["Texas"]}, path)
    assert "states" not in json.loads(path.read_text())
```

- [ ] **Step 11: Run the whole suite and commit**

```bash
uv run pytest -q
git add selection.py tests/test_selection.py geo.py tests/test_geo.py settings.py tests/test_settings.py
git commit -m "feat: add the Selection algebra and a worldwide place search"
```

---

### Task 4: `runstate` — the cost estimate and per-term coverage

**Files:**
- Modify: `scrape.py` (two new constants, nothing else)
- Modify: `runstate.py`
- Modify: `tests/test_runstate.py`

**Interfaces:**
- Consumes: `geo.Place.coverage_key()`, `geo.Place.key()`.
- Produces:
  - `scrape.LISTING_OVERHEAD: float`, `scrape.UNCAPPED_ASSUMPTION: int`
  - `runstate.estimate_run(term_count, leaf_count, cap, pacing=None) -> tuple[int, float]`
  - `runstate.RunState.term_status: dict[str, dict[str, str]]`
  - `runstate.RunState.term_left: dict[str, dict[str, int]]`
  - `runstate.coverage_tally(coverage) -> tuple[int, int]`
  - `runstate.country_tally(region_keys, coverage) -> list[tuple[str, int, int, int, int]]`

**The bug this task fixes.** `initial_state` writes
`coverage[place.coverage_key()]` once per place. Two cities in Texas both key
on `"Texas"`, so the second write overwrites the first and a half-cached
region reports whichever city happened to come last. Statewide runs never hit
it because each region had exactly one place; city-level runs hit it every
time. Completeness must be tallied across every leaf place in the region.

- [ ] **Step 1: Add the pacing constants to `scrape.py`**

Beside `PAUSE_LISTING` / `PAUSE_QUERY`:

```python
# Page load plus extraction for one listing, on top of PAUSE_LISTING. Feeds
# the run-cost estimate only; nothing paces off it.
LISTING_OVERHEAD = 1.5
# What to assume an uncapped query returns. Google caps a search at about 120
# results but a typical one returns far fewer, and an estimate has to pick a
# number. The UI says "at least" when it uses this rather than a real cap.
UNCAPPED_ASSUMPTION = 40
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_runstate.py`:

```python
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
```

- [ ] **Step 3: Run them to verify they fail**

Run: `uv run pytest tests/test_runstate.py -v`
Expected: FAIL — `AttributeError: module 'runstate' has no attribute 'estimate_run'`
and `RunState` has no `term_status`.

- [ ] **Step 4: Add the two fields to `RunState`**

```python
    coverage: dict = field(default_factory=dict)
    # region -> term -> status, for the per-term segments in a coverage cell.
    term_status: dict = field(default_factory=dict)
    # region -> term -> how many leaf places have yet to report. A region with
    # six cities reports six times per term, and the term is only finished
    # when the last one lands.
    term_left: dict = field(default_factory=dict)
```

- [ ] **Step 5: Rewrite `initial_state`**

```python
def initial_state(done_pairs, terms, places) -> RunState:
    """Seed from the resume cache, before any event arrives.

    A region counts as done only when every term is cached for every leaf
    place inside it. Tallying rather than assigning matters as soon as a
    region holds more than one place: several cities share one coverage key,
    and a plain assignment let the last city written decide the whole region.

    `places` holds whatever the caller compares `done_pairs` entries against -
    in practice `geo.Place` values. This module stays free of any geography
    import: it only calls `.coverage_key()` (the coverage cell's key,
    matching the `state=` value every coverage event in `scrape.py` emits)
    and `.key()` (the country/region/city triple that, prefixed with the
    term, is a `done_pairs` entry) on each one, so it never has to know what
    a country or a region is.

    `queries_done` deliberately starts at 0 and is owned entirely by the event
    stream: run_stage1 emits query_skipped for every cached pair, so seeding it
    from the cache too counted those pairs twice and a resumed run finished
    above 100% with a halved ETA. Nothing reads it before run_start.
    """
    outstanding: dict = {}
    total: dict = {}
    for place in places:
        key = place.coverage_key()
        for term in terms:
            total.setdefault(key, {})[term] = total.get(key, {}).get(term, 0) + 1
            left = outstanding.setdefault(key, {})
            left[term] = left.get(term, 0) + (
                0 if (term, *place.key()) in done_pairs else 1)

    coverage, term_status, term_left = {}, {}, {}
    for key, per_term in outstanding.items():
        term_status[key] = {term: "pending" if left else "done"
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
```

- [ ] **Step 6: Carry the nested dicts through `fold`'s copy**

`fold` shallow-copies `coverage`, `log`, `failures` and `at_cap`. The two new
fields are dicts **of dicts**, so a shallow copy would let a fold mutate the
previous state — the bug the existing copy exists to prevent:

```python
    s = replace(
        state,
        coverage=dict(state.coverage),
        term_status={k: dict(v) for k, v in state.term_status.items()},
        term_left={k: dict(v) for k, v in state.term_left.items()},
        log=list(state.log),
        failures=list(state.failures),
        at_cap=list(state.at_cap),
    )
```

- [ ] **Step 7: Fold the per-term status**

Add this helper above `fold`:

```python
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
```

- [ ] **Step 7b: Derive a region's status from its terms, instead of assigning it**

A region's cell is now the aggregate of its own term segments, which is the
only way the two can agree. Add beside `_mark_term`:

```python
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
```

Then wire both into the four events that carry a term. Every
`s.coverage[data["state"]] = _worse(...)` line in `fold` is **deleted** and
replaced by a derivation:

```python
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
```

`_worse` stays — `_mark_term` still uses it. `initial_state` keeps its own
simpler rule (everything cached, or pending): a region with prior progress but
no live run is not "active", and seeding it that way would light up cells
before the run starts.

- [ ] **Step 7c: Correct the one existing test this changes**

`test_a_clean_query_marks_the_state_done` folds a single clean `query_done`
against `TERMS` of length two, then asserts the region is done. Under the
derived rule it is `"active"` — and that is the correction, not a regression:
one of two terms finishing has never actually finished the state. Rename and
retarget it:

```python
def test_a_clean_query_marks_its_own_term_done_but_not_the_whole_state():
    s = runstate.initial_state(set(), TERMS, PLACES)
    s = runstate.fold(s, "query_start", {"term": "padel club", "state": "Texas"})
    assert s.coverage["Texas"] == "active"
    s = runstate.fold(s, "query_done", {"term": "padel club", "state": "Texas",
                                        "scraped": 8, "failed": 0, "complete": True})
    assert s.term_status["Texas"]["padel club"] == "done"
    assert s.coverage["Texas"] == "active", "padel court is still outstanding"
    assert s.queries_done == 1
```

The other four coverage assertions in that file are unaffected: `partial` and
`failed` both outrank an outstanding term, so they still win.

- [ ] **Step 8: Add the estimate and the tallies**

```python
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
    mean = lambda pair: (pair[0] + pair[1]) / 2  # noqa: E731
    per_query = mean(query_pause) + listings * (mean(listing_pause) + overhead)
    return queries, queries * per_query


def coverage_tally(coverage: dict) -> tuple[int, int]:
    """(finished, total) across every coverage cell."""
    return sum(1 for v in coverage.values() if v == "done"), len(coverage)


def country_tally(region_keys, coverage: dict) -> list:
    """(country, done, partial, failed, total) per country, in key order.

    Feeds the large-selection coverage view, where a grid of cells stops being
    readable and a bar per country takes over.
    """
    order: list[str] = []
    rows: dict[str, list[int]] = {}
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
```

- [ ] **Step 9: Run the tests to verify they pass**

Run: `uv run pytest tests/test_runstate.py -v`
Expected: every existing runstate test still passes, plus 22 new ones.

- [ ] **Step 10: Run the whole suite and commit**

```bash
uv run pytest -q
git add scrape.py runstate.py tests/test_runstate.py
git commit -m "feat: estimate run cost and track coverage per term, per region"
```

---

### Task 5: `widgets.PickList` — a list long enough to hold every city

**Files:**
- Modify: `widgets.py` (add `Row`, the layout helpers and `PickList`)
- Modify: `tests/test_widgets.py`

**Interfaces:**
- Consumes: `theme.PALETTE`.
- Produces:
  - `widgets.Row` — `NamedTuple(name: str, note: str = "", checked: bool = False)`
  - `widgets.visible_slice(top, height, row_h, count) -> tuple[int, int]`
  - `widgets.clamp_top(top, height, row_h, count) -> int`
  - `widgets.scroll_fractions(top, height, row_h, count) -> tuple[float, float]`
  - `widgets.PickList(master, *, font, note_font, row_h=30, on_toggle=None, on_highlight=None)`
    with `.set_rows(rows)`, `.set_current(name)`, `.current()`, `.yview(*args)`.

**Why not a `Listbox`.** The spec reached for `tk.Listbox`, and it is the wrong
tool for what the mockup asks: a Listbox row is one string in one font, so it
can carry neither the lime tick nor the right-aligned figure, and proportional
text makes column alignment impossible. `widgets.py` already exists because
"ttk cannot draw this" — `CoverageGrid` and `RoundedButton` are both canvases —
so a canvas list is the house idiom, not a new one.

**Why virtualised.** India alone has 36 regions but the region pane must also
hold the United States' 51 and Japan's 47, and the city panes draw from 19,599
cities. Drawing only the rows in view keeps a repaint at a few dozen canvas
items regardless of how long the list is.

**Interaction contract:**

| Gesture | Effect |
|---|---|
| Click a row | toggles it **and** makes it current, so the next pane cascades |
| `Up` / `Down` | moves current without toggling — browse without selecting |
| `space` / `Return` | toggles the current row |
| Wheel / trackpad | scrolls |
| `Home` / `End` | first / last row |

- [ ] **Step 1: Write the failing tests for the layout maths**

Append to `tests/test_widgets.py`:

```python
def test_visible_slice_covers_the_viewport_and_one_row_of_overscan():
    # 200px tall, 30px rows, scrolled to row 3: rows 3..10 inclusive
    assert widgets.visible_slice(3, 200, 30, 100) == (3, 11)


def test_visible_slice_never_runs_past_the_end():
    assert widgets.visible_slice(95, 200, 30, 100) == (95, 100)


def test_visible_slice_of_an_empty_list_is_empty():
    assert widgets.visible_slice(0, 200, 30, 0) == (0, 0)


def test_clamp_top_refuses_to_scroll_above_the_first_row():
    assert widgets.clamp_top(-4, 200, 30, 100) == 0


def test_clamp_top_stops_with_the_last_row_in_view():
    # 200px shows 6 whole rows, so the furthest top is 100 - 6 = 94
    assert widgets.clamp_top(999, 200, 30, 100) == 94


def test_clamp_top_is_zero_when_everything_already_fits():
    assert widgets.clamp_top(5, 900, 30, 10) == 0


def test_scroll_fractions_span_the_whole_bar_when_everything_fits():
    assert widgets.scroll_fractions(0, 900, 30, 10) == (0.0, 1.0)


def test_scroll_fractions_track_the_scroll_position():
    first, last = widgets.scroll_fractions(50, 300, 30, 100)
    assert first == 0.5 and last == 0.6


def test_scroll_fractions_of_an_empty_list_span_the_whole_bar():
    assert widgets.scroll_fractions(0, 300, 30, 0) == (0.0, 1.0)


def test_a_row_carries_a_name_a_note_and_a_tick():
    row = widgets.Row("Texas", "6/25", True)
    assert (row.name, row.note, row.checked) == ("Texas", "6/25", True)


def test_a_row_needs_only_a_name():
    assert widgets.Row("Texas") == ("Texas", "", False)
```

- [ ] **Step 2: Run them to verify they fail, then implement**

Run: `uv run pytest tests/test_widgets.py -v` — expect `AttributeError`.

Add to `widgets.py`:

`Row` needs `from typing import NamedTuple` at the top of `widgets.py`.

```python
class Row(NamedTuple):
    """One line in a PickList. The name is both the label and the identity."""
    name: str
    note: str = ""
    checked: bool = False


def visible_slice(top, height, row_h, count):
    """[first, stop) row indices to draw for a viewport `height` tall.

    One row of overscan past the bottom edge, so a partially visible row is
    drawn rather than clipping to nothing mid-scroll.
    """
    if count <= 0:
        return 0, 0
    top = max(0, min(top, count))
    return top, min(count, top + int(height // row_h) + 2)


def clamp_top(top, height, row_h, count):
    """A scroll position that keeps at least one row on screen."""
    return max(0, min(int(top), max(0, count - int(height // row_h))))


def scroll_fractions(top, height, row_h, count):
    """(first, last) for a ttk.Scrollbar.set, as fractions of the list."""
    if count <= 0:
        return 0.0, 1.0
    visible = height / row_h
    if visible >= count:
        return 0.0, 1.0
    return top / count, min(1.0, (top + visible) / count)
```

Then `PickList`, following `CoverageGrid`'s shape — a `tk.Canvas` that
`delete("all")` and redraws on `<Configure>` and on every state change. It
must:

- keep `self._rows`, `self._top`, `self._current` and nothing else;
- draw, per visible row: the `row_sel` background and a 2px lime left edge
  when the row is current; a 15×15 rounded tick (`rounded_points`, radius 4)
  filled `lime` with an `onlime` check polygon when `checked`, else outlined
  `tickline`; the name in `font`, ellipsised to fit; the note right-aligned in
  `note_font`, coloured `lime` when the row is current and `faint` otherwise;
- expose `yview(*args)` handling both `("moveto", f)` and `("scroll", n, what)`
  so a `ttk.Scrollbar` can drive it, and call `self._yscroll(*scroll_fractions(...))`
  after every repaint;
- bind `<Button-1>`, `<MouseWheel>`, `<Button-4>`, `<Button-5>`, `<Up>`,
  `<Down>`, `<Home>`, `<End>`, `<space>`, `<Return>`, `<Configure>`, and take
  focus (`takefocus=1`), scrolling the current row into view when the keyboard
  moves it;
- call `on_toggle(name)` and `on_highlight(name)` — never mutate a selection
  itself. The widget renders state; `app.py` owns it.

Ellipsising needs the font's own measurement, the way `RoundedButton._resize`
already uses `self._font.measure`:

```python
def _fit(self, text, width):
    if self._font.measure(text) <= width:
        return text
    while text and self._font.measure(text + "…") > width:
        text = text[:-1]
    return text + "…"
```

- [ ] **Step 3: Run the tests, then exercise the widget by hand**

Run: `uv run pytest tests/test_widgets.py -v` — expect 11 new passes.

Then a throwaway harness, because list scrolling is not something a headless
test can vouch for:

```bash
uv run python -c "
import app, theme, tkinter as tk, geo, widgets
root = tk.Tk(); root.configure(bg=theme.PALETTE['bg']); theme.apply_theme(root)
fonts = theme.build_fonts()
picked = set()
def toggle(name):
    picked.symmetric_difference_update({name}); paint()
pl = widgets.PickList(root, font=fonts['row'], note_font=fonts['note'],
                      on_toggle=toggle, on_highlight=lambda n: None)
pl.pack(fill='both', expand=True)
def paint():
    pl.set_rows([widgets.Row(c, str(len(geo.regions(c))), c in picked)
                 for c in geo.countries()])
paint(); root.geometry('300x420'); root.mainloop()
"
```

Confirm: 252 countries scroll smoothly by wheel and by arrow key, clicking a
row ticks it lime and highlights it, a long name ellipsises rather than
overrunning its note, and the note column stays aligned.

- [ ] **Step 4: Commit**

```bash
git add widgets.py tests/test_widgets.py
git commit -m "feat: add PickList, a virtualised canvas list for long place lists"
```

---

### Task 6: Coverage that works for three regions or three hundred

**Files:**
- Modify: `widgets.py` (rewrite `CoverageGrid`, add `CoverageSummary`, delete `STATE_ABBR`)
- Modify: `tests/test_widgets.py` (the abbreviation test moves to `geo.abbreviate`)

**Interfaces:**
- Consumes: `runstate.country_tally`, `geo.abbreviate`, `theme.PALETTE`, `STATUS_COLORS`.
- Produces:
  - `widgets.LARGE_SELECTION = 60`
  - `widgets.group_layout(counts, cols, cell_h, gap, header_h) -> list[tuple[int, int]]`
  - `widgets.layout_height(counts, cols, cell_h, gap, header_h) -> int`
  - `widgets.segment_fills(terms, row) -> list[str]`
  - `widgets.bar_spans(done, partial, failed, total, width) -> list[tuple[float, float, str]]`
  - `widgets.CoverageGrid(master, *, cell_font, label_font, cols=12, ...)` with
    `.set_groups(groups)` and `.update_coverage(coverage, term_status=None, terms=())`
  - `widgets.CoverageSummary(master, *, font, label_font)` with `.set_rows(rows)`

`groups` is `[(country, [(region_key, cell_label), ...]), ...]` — `app.py`
builds it from `selection.region_keys` and `geo.abbreviate`.

**`STATE_ABBR` is deleted.** `geo.abbreviate(country, region)` supersedes it and
already covers all 50 states plus the rest of the world. Move
`test_every_state_has_an_abbreviation` to `tests/test_geo.py`, retargeted:

```python
def test_every_us_state_abbreviates_to_its_two_letter_code():
    codes = {geo.abbreviate("United States", s) for s in scrape.ALL_50}
    assert len(codes) == 50, "the 50 states must not collide in a coverage cell"
    assert geo.abbreviate("United States", "Texas") == "TX"
```

- [ ] **Step 1: Write the failing tests**

```python
def test_group_layout_stacks_each_country_under_its_own_header():
    # two groups of 3 cells, 12 columns, 46px cells, 6px gap, 22px header
    assert widgets.group_layout([3, 3], 12, 46, 6, 22) == [(0, 22), (74, 96)]


def test_layout_height_grows_when_a_country_wraps_past_the_column_count():
    """25 regions in 12 columns is three rows, not one."""
    one_row = widgets.layout_height([12], 12, 46, 6, 22)
    three_rows = widgets.layout_height([25], 12, 46, 6, 22)
    assert one_row == 68 and three_rows == 172


def test_layout_height_counts_every_row_of_every_group():
    # 3 cells = 1 row (46), + header 22 -> 68; two of them plus a 6px gap
    assert widgets.layout_height([3, 3], 12, 46, 6, 22) == 142


def test_layout_height_of_nothing_is_nothing():
    assert widgets.layout_height([], 12, 46, 6, 22) == 0


def test_segment_fills_give_one_colour_per_term_in_order():
    fills = widgets.segment_fills(["a", "b"], {"a": "done", "b": "pending"})
    assert fills == [widgets.PALETTE["lime"], widgets.PALETTE["seg"]]


def test_segment_fills_treat_an_unreported_term_as_pending():
    assert widgets.segment_fills(["a"], {}) == [widgets.PALETTE["seg"]]


def test_segment_fills_distinguish_every_status():
    row = {"w": "done", "x": "active", "y": "partial", "z": "failed"}
    assert widgets.segment_fills(list("wxyz"), row) == [
        widgets.PALETTE["lime"], widgets.PALETTE["lime_edge"],
        widgets.PALETTE["amber"], widgets.PALETTE["coral"]]


def test_bar_spans_lay_the_statuses_out_left_to_right():
    spans = widgets.bar_spans(5, 2, 1, 10, 100)
    assert spans == [(0.0, 50.0, widgets.PALETTE["lime"]),
                     (50.0, 70.0, widgets.PALETTE["amber"]),
                     (70.0, 80.0, widgets.PALETTE["coral"])]


def test_bar_spans_omit_a_status_with_nothing_in_it():
    assert widgets.bar_spans(5, 0, 0, 10, 100) == [(0.0, 50.0, widgets.PALETTE["lime"])]


def test_bar_spans_of_an_empty_country_are_empty():
    assert widgets.bar_spans(0, 0, 0, 0, 100) == []


def test_a_selection_past_the_threshold_is_called_large():
    assert widgets.LARGE_SELECTION == 60
```

- [ ] **Step 2: Implement**

```python
LARGE_SELECTION = 60  # above this a grid stops being readable; bars take over

SEGMENT_FILLS = {
    "done": PALETTE["lime"],
    "active": PALETTE["lime_edge"],
    "partial": PALETTE["amber"],
    "failed": PALETTE["coral"],
    "pending": PALETTE["seg"],
}


def segment_fills(terms, row):
    """One segment colour per term, in the order the terms are listed."""
    return [SEGMENT_FILLS.get((row or {}).get(term, "pending"), PALETTE["seg"])
            for term in terms]


def group_layout(counts, cols, cell_h, gap, header_h):
    """(header y, first cell y) per group, stacked top to bottom."""
    out, y = [], 0
    for count in counts:
        out.append((y, y + header_h))
        rows = math.ceil(count / cols) if count else 0
        y += header_h + rows * cell_h + max(0, rows - 1) * gap + gap
    return out


def layout_height(counts, cols, cell_h, gap, header_h):
    """Total pixel height for `counts` groups, without the trailing gap."""
    if not counts:
        return 0
    total = 0
    for count in counts:
        rows = math.ceil(count / cols) if count else 0
        total += header_h + rows * cell_h + max(0, rows - 1) * gap + gap
    return total - gap


def bar_spans(done, partial, failed, total, width):
    """(x1, x2, colour) runs for one country's progress bar."""
    if not total:
        return []
    spans, x = [], 0.0
    for count, colour in ((done, PALETTE["lime"]), (partial, PALETTE["amber"]),
                          (failed, PALETTE["coral"])):
        if not count:
            continue
        end = x + width * count / total
        spans.append((x, end, colour))
        x = end
    return spans
```

`CoverageGrid` keeps its "one canvas, not one widget per cell" reason for
existing and gains grouping. Per group it draws the country name in
`label_font` (through `theme.tracked`) with a hairline rule running to the
right edge, then the cells. Each cell is a 7px-radius rounded rectangle in its
`STATUS_COLORS` fill and border, the abbreviation centred in `cell_font`, and —
when `terms` is non-empty and there are at most 8 of them — a row of 7×3px
segments beneath the label from `segment_fills`. Above 8 terms the segments
are dropped: they would be thinner than the gaps between them.

`CoverageSummary` draws one row per `runstate.country_tally` entry: the country
name at a fixed 158px column, a 9px bar filled from `bar_spans`, the
`"18 / 51"` count right-aligned in a 72px column, and a hairline under every
row but the last. Above it, `set_rows` also reports the headline totals so
`app.py` can render "34 of 210 states complete, across 7 countries".

- [ ] **Step 3: Run the tests and commit**

```bash
uv run pytest -q
git add widgets.py tests/test_widgets.py tests/test_geo.py
git commit -m "feat: group coverage by country, with per-term segments and a large-selection view"
```

---

### Task 7: The selector screen, and wiring geography through the window

**Files:**
- Modify: `app.py`
- Modify: `tests/test_worker.py` (the worker now receives a selection)

**Interfaces:**
- Consumes: everything Tasks 1–6 produced, plus `geo.leaf_places`, `geo.search_places`, `geo.abbreviate`, `selection.*`, `runstate.estimate_run`, `runstate.country_tally`.
- Produces: no new module API. `App` gains `self.selection`, the `"locations"` panel, and `_paint_cost`.

**`_us_places` is deleted.** It was documented as a temporary seam from the day
it was written; this is the task that removes it. Every place the app searches
now comes from `geo.leaf_places(self.selection)`.

- [ ] **Step 1: Replace the US-only seam**

In `App.__init__`, `on_start` and `_run_worker`, every `_us_places(prefs["states"])`
becomes `geo.leaf_places(self.selection)`. `current_prefs` returns
`"selection": self.selection` instead of `"states": list(scrape.ALL_50)`.

Load and prune the stored selection once, in `__init__`, and report anything
dropped rather than silently losing it:

```python
        self.prefs = settings.load(SETTINGS_PATH)
        self.selection, dropped = selection.prune(self.prefs["selection"])
        self._startup_notes = (
            [f"Dropped {len(dropped)} place(s) this version no longer knows: "
             + ", ".join(dropped[:6])] if dropped else [])
```

Push `_startup_notes` into `self.run_state.log` right after `initial_state`, so
it surfaces in the activity log exactly once and never blocks startup.

- [ ] **Step 2: Give the setup panel a Locations box and a cost line**

Between the terms box and the options box, a third column matching the mockup's
`Locations` card: one line per `selection.summary(self.selection)` entry, a
`Choose locations` button that calls `self._go("locations")`, and
`"{n} places"` beside it. Rebuild it from `_paint_locations()` so returning
from the selector refreshes it.

Below the three columns, the cost strip — the same widget the selector's footer
uses, so build it once as `_build_cost(parent)` and paint it from `_paint_cost`:

```python
    def _paint_cost(self) -> None:
        """The run-cost line, everywhere it appears."""
        terms = list(self.terms_list.get(0, "end"))
        leaves = geo.leaf_count(self.selection)
        cap = self._limit_value()
        queries, seconds = runstate.estimate_run(len(terms), leaves, cap)
        hours = seconds / 3600
        self.cost_queries.configure(text=f"{queries:,}")
        self.cost_hours.configure(
            text=("—" if not queries else
                  f"{'≥' if cap is None else ''}{hours:.0f}h" if hours >= 1
                  else f"{'≥' if cap is None else ''}{seconds / 60:.0f}m"))
        # Past a day the number stops being reassuring and starts being a
        # warning, so it changes colour rather than being buried in the detail.
        self.cost_hours.configure(
            style="Amber.TLabel" if hours > 24 else "Lime.TLabel")
        noun = selection.cap_noun(self.selection)
        per = f"capped at {cap} businesses each" if cap else "no cap"
        self.cost_detail.configure(
            text=f"{len(terms)} search terms × {leaves:,} places, {per}"
                 + (f" · up to {queries * (cap or 0):,} leads" if cap else "")
                 + ("  ·  consider narrowing" if hours > 24 else ""))
        self.start_btn.set_enabled(bool(terms and leaves) or self.worker is not None)
        self.cap_label.configure(text=f"businesses per {noun}")
```

Call `_paint_cost()` from `__init__`, from `on_add_term`/`on_remove_term`, from
`_sync_limit`, from the spinbox's `command`, and when the selector closes.

**Start is disabled with no location**, per the spec's error table. The label
beside it reads `Choose at least one location` when `leaves == 0`.

- [ ] **Step 3: Build the locations panel**

`_build_locations()` creates a fourth entry in `self._panels`, laid out as the
mockup: a search entry across the top with the selected-country chips beside
it; a bordered row of three `PickList` panes (country / state / city, 248px
each) plus a flexible fourth column summarising the selection; the cost strip
and a `Done` button along the bottom.

State the panel owns:

```python
        self._pick_country = ""   # which country the state pane is showing
        self._pick_region = ""    # which region the city pane is showing
        self._search = ""         # the global search query, "" when browsing
```

Repaint through one method, so the three panes can never disagree:

```python
    def _paint_panes(self) -> None:
        """Repaint all three panes plus the summary from self.selection.

        One method, not three: the panes cascade, and a partial repaint is how
        the state pane ends up listing one country's regions under another
        country's header.
        """
        if self._search:
            return self._paint_search()
        self.pane_country.set_rows([
            widgets.Row(name, selection.country_note(self.selection, name),
                        selection.is_country_on(self.selection, name))
            for name in geo.countries()])
        country = self._pick_country
        self.pane_region.set_rows([
            widgets.Row(name, selection.region_note(self.selection, country, name),
                        selection.is_region_on(self.selection, country, name))
            for name in geo.regions(country)])
        region = self._pick_region
        self.pane_city.set_rows([
            widgets.Row(name, "", selection.is_city_on(self.selection, country, region, name))
            for name in geo.cities(country, region)])
        self.head_region.configure(text=country or "—")
        self.head_city.configure(text=region or "—")
        self._paint_summary()
        self._paint_cost()
```

**The city pane's note column is empty**, and that is a deliberate departure
from the mockup, which shows a population beside each city ("2.3M"). `geodata`
stores city names in population order but not the populations themselves, so
that figure does not exist. Adding it would mean regenerating the committed
480KB `geodata.py` against a live GeoNames download, trading a reviewed,
reproducible artifact for one decorative column. The ordering already carries
the ranking, and the pane header says so: `City · most populous first`.

- [ ] **Step 4: Wire the search box**

Typing runs `geo.search_places` and replaces the three panes' contents with one
flat result list in the country pane, the other two cleared and their headers
reading `—`:

```python
    def _is_on(self, place) -> bool:
        """Whether `place` is already selected, at whatever level it names."""
        if place.city:
            return selection.is_city_on(self.selection, place.country,
                                        place.region, place.city)
        if place.region:
            return selection.is_region_on(self.selection, place.country,
                                          place.region)
        return selection.is_country_on(self.selection, place.country)

    def _paint_search(self) -> None:
        """Replace the three panes with one flat list of matches.

        Rows are keyed by `Place.label()`, so `_hits` can turn the name the
        widget hands back into the Place that produced it. A PickList row
        knows only its own name -- resolving it here keeps the widget free of
        any idea what a place is.
        """
        hits = geo.search_places(self._search)
        self._hits = {place.label(): place for place in hits}
        self.pane_country.set_rows([
            widgets.Row(place.label(), place.parts()[0], self._is_on(place))
            for place in hits])
        self.pane_region.set_rows([])
        self.pane_city.set_rows([])
        self.head_region.configure(text="—")
        self.head_city.configure(text="—")
        self._paint_summary()
        self._paint_cost()
```

A search hit's note is `place.parts()[0]` — its country — so two cities with
the same name are told apart in the list.

Toggling a search hit goes through `selection.toggle_place(self.selection, place)`.
Clearing the box (or `Escape`) sets `self._search = ""` and returns to the panes.

- [ ] **Step 5: Cascade and toggle**

```python
    def _on_country(self, name, toggle):
        self._pick_country = name
        self._pick_region = ""
        if toggle:
            self.selection = selection.toggle_country(self.selection, name)
        self._paint_panes()
```

`_on_region` and `_on_city` follow the same shape. `on_highlight` passes
`toggle=False`; `on_toggle` passes `True`.

- [ ] **Step 6: Rebuild the coverage display from the selection**

`_paint_coverage()` replaces the three hardcoded `CoverageGrid(panel, scrape.ALL_50)`
constructions. Each of the setup, running and blocked panels holds **both** a
`CoverageGrid` and a `CoverageSummary`, and shows whichever suits the size of
the selection:

```python
    def _paint_coverage(self, grid, summary, headline) -> None:
        keys = selection.region_keys(self.selection)
        terms = list(self.terms_list.get(0, "end"))
        done, total = runstate.coverage_tally(self.run_state.coverage)
        if len(keys) > widgets.LARGE_SELECTION:
            grid.pack_forget()
            summary.pack(fill="x")
            summary.set_rows(runstate.country_tally(keys, self.run_state.coverage))
            headline.configure(
                text=f"{done} of {total} states complete, across "
                     f"{len({c for c, _ in keys})} countries")
            return
        summary.pack_forget()
        grid.pack(fill="x")
        groups = []
        for country, region in keys:
            label = geo.abbreviate(country, region) if region != country else country
            if groups and groups[-1][0] == country:
                groups[-1][1].append((region, label))
            else:
                groups.append((country, [(region, label)]))
        grid.set_groups(groups)
        grid.update_coverage(self.run_state.coverage, self.run_state.term_status, terms)
        headline.configure(text=f"{done} of {total} states finished"
                                if done else "nothing started")
```

- [ ] **Step 7: Fix the copy the reframe left behind**

The app still says "clubs" throughout. Every user-visible string changes:

| Was | Now |
|---|---|
| `Look up contact details on club websites` | `Look up contact details on business websites` |
| `Re-scrape states already finished` | `Re-scrape places already finished` |
| `clubs per state` | `businesses per {cap_noun}` — set by `_paint_cost` |
| `clubs saved` (results, running, status bar) | `businesses saved` |
| `Club` (results table heading) | `Business` |
| `Paced to about 3 seconds per club…` | replaced by the cost strip; delete it |
| `{n} states hit Google's 120-result limit` | `{n} places hit Google's 120-result limit` |
| `{n} states are only partly covered` | `{n} places are only partly covered` |
| `Every club found so far is already written…` | `Every business found so far…` |

The results `Where` column already joins `city` and `state`; leave it — the
backend fills both worldwide.

- [ ] **Step 8: Update `tests/test_worker.py`**

The worker test asserts `run_stage1` receives `geo.Place` instances. Keep that
assertion — it is the regression guard from the backend plan — and change the
prefs it builds from `"states": [...]` to `"selection": {...}`:

```python
def test_the_worker_searches_every_place_the_selection_names():
    prefs = {"terms": ["gym"], "selection": {"United States": {"Texas": ["Austin"]}},
             "enrich": False, "headed": False, "force": False, "limit": None}
    ...
    assert seen == [geo.Place(country="United States", region="Texas", city="Austin")]
```

- [ ] **Step 9: Run the suite, then run the app**

```bash
uv run pytest -q
uv run python app.py
```

Walk the whole flow and report anything that does not hold:
1. Setup shows three columns; Locations lists `United States · 51 states · whole state`.
2. The cost strip reads a plausible query count and an hour figure.
3. `Choose locations` opens the selector; the country pane lists 252 countries.
4. Clicking `India` ticks it lime and the state pane fills with 36 Indian regions.
5. Clicking `Maharashtra` fills the city pane; clicking `Mumbai` ticks it.
6. The cost figure moves as you tick, and the fourth pane summarises each country.
7. Typing `austin` in the search box surfaces `Austin, Texas`; ticking it works;
   clearing the box returns to the panes with the tick still on.
8. `Done` returns to setup with the Locations box updated.
9. Unticking everything disables Start and shows `Choose at least one location`.
10. Selecting all 50 US states plus all 36 Indian ones (86 > 60) swaps the
    coverage grid for the per-country bars.
11. The cap label reads `businesses per city` with a city selected, `per state`
    with only states, `per country` with only whole countries.

- [ ] **Step 10: Commit**

```bash
git add app.py tests/test_worker.py
git commit -m "feat: choose countries, states and cities, and see what a run will cost"
```

---

### Task 8: Ship it — packaging, docs and a look at the whole thing

**Files:**
- Modify: `wkey-lead-scraper.spec`, `.gitattributes`, `README.md`

- [ ] **Step 1: Put the fonts in the bundle**

In `wkey-lead-scraper.spec`, add the font directory to the `Analysis` call's
`datas`, which currently reads `datas=list(pw_datas)`:

```python
datas=list(pw_datas) + [("assets/fonts", "assets/fonts")],
```

TTFs are plain data, not Mach-O, so unlike the Chromium problem they collect
without PyInstaller rewriting a signature — the failure that forced browsers
out of the spec and into `package.py`.

- [ ] **Step 2: Keep git's hands off the binaries**

Append to `.gitattributes`:

```
*.ttf binary
```

- [ ] **Step 3: Credit the font**

Add to `README.md`, under a `Credits` heading:

```markdown
## Credits

Location data from [GeoNames](https://www.geonames.org/), CC BY 4.0.

The interface is set in [Unbounded](https://fonts.google.com/specimen/Unbounded)
by the Unbounded Project Authors, used under the SIL Open Font License 1.1.
The licence ships with the font at `assets/fonts/OFL.txt`.
```

Also replace the README's description of state selection with the
country/state/city selector, and drop the manual per-city workaround section —
the selector is that workaround, made first class.

- [ ] **Step 4: Build once and check the bundle still seals**

macOS only, and it is the check that matters — `package.py` already fails loudly
on a broken seal, which is the failure that reads as "damaged" on a download:

```bash
uv run pyinstaller wkey-lead-scraper.spec --noconfirm && uv run python package.py
```

Expected: `codesign --verify --strict` returns 0. Then launch the built app from
Finder — not from the terminal — and confirm the wordmark renders in Unbounded.
A frozen app that falls back to the system face means `paths.font_dir()` is not
finding `sys._MEIPASS`; report it rather than working around it.

- [ ] **Step 5: Look at all four screens against the mockups**

Open `design/geography/Main.dc.html`, `Setup.dc.html` and `Coverage.dc.html`
beside the running app. They will not match pixel for pixel — Tk has no
letter-spacing, no alpha and no CSS grid. What must match:

- near-black canvas, one lime accent, hairline borders, no shadows anywhere;
- Unbounded on the wordmark, the micro-labels, the big numerals and the buttons,
  and **not** on the dense list rows;
- no green anywhere in the status palette — finished is lime, partly done is
  amber, failed is coral;
- numbers in a tabular mono face, right-aligned in their columns.

- [ ] **Step 6: Full suite, then commit**

```bash
uv run pytest -q
git add wkey-lead-scraper.spec .gitattributes README.md
git commit -m "chore: ship the fonts in the bundle and credit their sources"
```

---

## Out of scope

Named so nobody adds them mid-plan:

- **The live results table.** Watching rows arrive during a run is its own change.
- **Tech-signal qualifiers** — has-an-app, site platform, site age. Designed in
  conversation, never specced.
- **A preset vertical list.** Search terms stay free text.
- **City populations in the selector.** See Task 7, Step 3.
- **Non-English Maps locales.** `hl` stays `en`; only `gl` varies.
- **Keyboard shortcuts from the mockup** (`⌘K`, `⌘A`, `⌘↵`). The panes have
  arrow-key and space handling; the global accelerators are not wired.

## Self-review

**Spec coverage.** §7 selector screen → Tasks 5 and 7. §8 cost preview →
Tasks 4 and 7. §9 cap wording → Tasks 3 (`cap_noun`) and 7. §10 coverage
display → Tasks 4 and 6. "Visual direction" → Tasks 1, 2 and 8. Error
handling table: no location selected → Task 7 Step 2; a stored selection
naming an unknown place → Task 3 (`prune`) and Task 7 Step 1; estimate over
24h → Task 7 Step 2.

**Two spec deviations, both deliberate and both argued where they occur:**
`tk.Listbox` → a canvas `PickList` (Task 5), and macOS font registration by
`CTFontManagerRegisterFontsForURL` rather than `ATSApplicationFontsPath`
(Task 1).

**One mockup deviation:** no city populations (Task 7, Step 3).

**One behaviour correction that changes an existing test:** a region's coverage
cell now derives from its terms, so one finished term of two no longer paints
the whole region done (Task 4, Steps 7b–7c).
