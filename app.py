"""Desktop window for the lead scraper.

Owns the main thread. Never scrapes: a worker thread does that and reports
through an event queue this window drains on a timer. Nothing here may be
called from the worker, because Tk is not thread-safe.
"""

import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import geo
import paths
import theme


def _ensure_tcl_paths() -> None:
    """Point Tk at the Tcl/Tk that ships with this interpreter.

    uv's standalone CPython bundles Tcl/Tk but sets neither TCL_LIBRARY nor
    TK_LIBRARY, so tkinter reports "Tcl wasn't installed properly" on a launch
    that does not already carry them. Deriving the paths from sys.base_prefix
    keeps this portable: every uv-installed Python lays them out the same way,
    and an environment that already sets them is left alone.

    The version is globbed rather than hardcoded: pinning tcl8.6/tk8.6 would
    silently no-op on a Tcl 9 build and surface as a TclError at import.

    A frozen bundle is exempt: PyInstaller ships its own Tcl/Tk and sets both
    variables from its runtime hook. There, sys.base_prefix points into the
    unpacked bundle, where this glob would at best waste a stat and at worst
    invent a path that shadows the working one.
    """
    if paths.frozen():
        return
    lib = Path(sys.base_prefix) / "lib"
    for variable, prefix in (("TCL_LIBRARY", "tcl"), ("TK_LIBRARY", "tk")):
        if os.environ.get(variable):
            continue
        exact = lib / f"{prefix}8.6"
        candidates = [exact] if exact.is_dir() else sorted(
            p for p in lib.glob(f"{prefix}[0-9]*") if p.is_dir()
        )
        if candidates:
            os.environ[variable] = str(candidates[-1])


_ensure_tcl_paths()

# Also deliberately before the tkinter import, and for the same class of
# reason: Tk asks the OS for its font families once, when it starts, so a
# font registered afterwards would be invisible and every display face would
# silently fall back to the system sans.
theme.register_fonts()

# Deliberately below _ensure_tcl_paths(): TCL_LIBRARY/TK_LIBRARY must be set
# before tkinter is imported, or this Tk build can't find its own init.tcl.
import tkinter as tk  # noqa: E402
from tkinter import messagebox, ttk  # noqa: E402

import runstate
import scrape
import selection
import settings
from theme import PALETTE
import widgets
from widgets import CoverageGrid, CoverageSummary, RoundedButton

SETTINGS_PATH = paths.data_dir() / "settings.json"

# The visible panel is the single source of truth for what render() paints; the
# pump uses this to follow a status change the user did not ask for (a mid-run
# block), and nothing else moves the panel behind their back.
PANEL_FOR_STATUS = {"idle": "setup", "running": "running",
                    "finished": "results", "blocked": "blocked"}

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Wkey Lead Scraper")
        self.geometry("1100x740")
        self.minsize(960, 660)
        self.configure(bg=PALETTE["bg"])

        self.fonts = theme.build_fonts()
        theme.apply_theme(self)

        self.prefs = settings.load(SETTINGS_PATH)
        # Prune once, at load: a selection saved against an older geodata may
        # name a country or region this build no longer has. Dropping it is
        # reported in the activity log and never blocks startup.
        self.selection, dropped = selection.prune(self.prefs["selection"])
        self._startup_notes = (
            [f"Dropped {len(dropped)} place(s) this version no longer knows: "
             + ", ".join(dropped[:6])] if dropped else [])
        records, done_pairs = scrape.read_cache()
        # Named run_state, not state: tk.Tk already has a public state()
        # method, and shadowing it would quietly remove the ability to call
        # it (e.g. with "zoomed") or to query the window state at all.
        self.run_state = runstate.initial_state(
            done_pairs, self.prefs["terms"], geo.leaf_places(self.selection)
        )
        self.run_state.saved = len(records)
        self.run_state.log.extend(self._startup_notes)

        self.events = queue.Queue()
        self.worker = None
        self.stop_flag = threading.Event()
        self._visible = "setup"
        self._rendered_minute = None

        self._cost_strips = []
        self._build_toolbar()
        # Before the body: pack gives the bottom edge to whoever claims it
        # first, and an expanding body packed ahead of it squeezes it flat.
        # It also has to exist before _paint_cost, which writes to it.
        self._build_statusbar()
        self._body = ttk.Frame(self)
        self._body.pack(fill="both", expand=True)
        self._panels = {}
        self._build_setup()
        self._build_locations()
        self._build_running()
        self._build_results()
        self._build_blocked()
        self.show("setup")
        self.protocol("WM_DELETE_WINDOW", self.on_close)

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

        mark = ttk.Frame(bar, style="Panel.TFrame")
        mark.pack(side="left", expand=True)
        for text, style in ((theme.tracked("Wkey"), "Muted.TLabel"),
                            ("·", "Lime.TLabel"),
                            (theme.tracked("Lead Scraper"), "Muted.TLabel")):
            ttk.Label(mark, text=text, font=self.fonts["wordmark"], style=style,
                      background=PALETTE["panel"]).pack(side="left")

        right = ttk.Frame(bar, style="Panel.TFrame")
        right.pack(side="right")
        ttk.Label(right, text=theme.tracked("Saving To"), style="Faint.TLabel",
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
        panel = self._panels.get(name)
        if panel is None:
            raise KeyError(f"no panel named {name!r}; built: {sorted(self._panels)}")
        for existing in self._panels.values():
            existing.pack_forget()
        panel.pack(fill="both", expand=True)
        self._visible = name

    def _go(self, name: str) -> None:
        """Hand navigation: switch panel and paint it once.

        render() paints whatever panel is visible, and the pump has stopped by
        the time these buttons exist, so the switch has to repaint itself or
        the user lands on the previous frame's contents.
        """
        self.show(name)
        self.render()

    # -- setup panel -------------------------------------------------------
    def _build_setup(self):
        panel = ttk.Frame(self._body, padding=(16, 14))
        self._panels["setup"] = panel

        top = ttk.Frame(panel)
        top.pack(fill="x")

        terms_box = ttk.Frame(top)
        terms_box.pack(side="left", fill="y")
        ttk.Label(terms_box, text=theme.tracked("Search Terms"), style="Faint.TLabel",
                  font=self.fonts["label"]).pack(anchor="w", pady=(0, 6))
        self.terms_list = tk.Listbox(
            terms_box, width=38, height=5, font=self.fonts["ui"],
            bg=PALETTE["panel"], fg=PALETTE["ink"], relief="flat", bd=0,
            highlightthickness=1, highlightbackground=PALETTE["cellline"],
            highlightcolor=PALETTE["cellline"],
            selectbackground=PALETTE["row_sel"],
            selectforeground=PALETTE["bright"], activestyle="none",
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

        places_box = ttk.Frame(top, padding=(14, 0, 0, 0))
        places_box.pack(side="left", fill="y")
        ttk.Label(places_box, text=theme.tracked("Locations"), style="Faint.TLabel",
                  font=self.fonts["label"]).pack(anchor="w", pady=(0, 6))
        self.places_rows = ttk.Frame(places_box, height=104)
        self.places_rows.pack(fill="x")
        self.places_rows.pack_propagate(False)
        places_actions = ttk.Frame(places_box)
        places_actions.pack(fill="x", pady=(6, 0))
        RoundedButton(places_actions, "Choose locations",
                      lambda: self._go("locations"), font=self.fonts["ui"],
                      height=26, pad_x=10).pack(side="left")
        self.places_count = ttk.Label(places_actions, text="", style="Faint.TLabel",
                                      font=self.fonts["small"])
        self.places_count.pack(side="left", padx=(9, 0))

        opts = ttk.Frame(top, padding=(22, 0, 0, 0))
        opts.pack(side="left", fill="both", expand=True)
        ttk.Label(opts, text=theme.tracked("Options"), style="Faint.TLabel",
                  font=self.fonts["label"]).pack(anchor="w", pady=(0, 6))
        self.var_enrich = tk.BooleanVar(value=self.prefs["enrich"])
        self.var_headed = tk.BooleanVar(value=self.prefs["headed"])
        self.var_force = tk.BooleanVar(value=self.prefs["force"])
        for text, var in (
            ("Look up contact details on business websites", self.var_enrich),
            ("Show the browser while it works", self.var_headed),
            ("Re-scrape places already finished", self.var_force),
        ):
            ttk.Checkbutton(opts, text=text, variable=var).pack(anchor="w", pady=2)

        limit_row = ttk.Frame(opts)
        limit_row.pack(anchor="w", pady=2)
        self.var_limited = tk.BooleanVar(value=self.prefs.get("limit") is not None)
        ttk.Checkbutton(limit_row, text="Stop after", variable=self.var_limited,
                        command=self._sync_limit).pack(side="left")
        # readonly, never "normal": a freely typeable Spinbox lets an empty or
        # padded value reach int() and raise ValueError out of on_start, where
        # the only symptom is a Start button that looks dead.
        self.limit_spin = ttk.Spinbox(limit_row, from_=1, to=120, width=4,
                                      font=self.fonts["mono"], state="readonly")
        self.limit_spin.set(self.prefs.get("limit") or 3)
        self.limit_spin.configure(command=self._paint_cost)
        self.limit_spin.pack(side="left", padx=6)
        # Wording only -- the mechanism is unchanged. It follows the most
        # specific level in the selection, because "per state" is a lie when
        # the run is aimed at six cities inside one.
        self.cap_label = ttk.Label(limit_row, text="businesses per place")
        self.cap_label.pack(side="left")

        self.cost_setup = self._build_cost(panel)
        self.cost_setup.pack(fill="x", pady=(14, 0))
        # After the cost strip, not beside the spinbox: _sync_limit repaints
        # the estimate, and the estimate's labels are built just above.
        self._sync_limit()

        grid_head = ttk.Frame(panel)
        grid_head.pack(fill="x", pady=(14, 6))
        self.coverage_head = ttk.Label(grid_head, style="Faint.TLabel",
                                       font=self.fonts["label"])
        self.coverage_head.pack(side="left")
        # Each word in its own colour: a legend that names four colours in
        # one grey tells you the four statuses exist and nothing else.
        legend = ttk.Frame(grid_head)
        legend.pack(side="right")
        for text, colour in (("finished", "lime"), ("partly done", "amber"),
                             ("failed", "coral"), ("not started", "seg")):
            swatch = tk.Canvas(legend, width=8, height=8, highlightthickness=0,
                               bd=0, bg=PALETTE["bg"])
            swatch.create_rectangle(0, 0, 8, 8, fill=PALETTE[colour],
                                    outline=PALETTE[colour])
            swatch.pack(side="left", padx=(14, 5))
            ttk.Label(legend, text=text, style="Faint.TLabel",
                      font=self.fonts["small"]).pack(side="left")

        self.grid_setup, self.summary_setup = self._build_coverage(panel)

    # -- shared pieces -----------------------------------------------------
    def _build_cost(self, parent):
        """A run-cost strip, registered so `_paint_cost` repaints every one.

        Setup and the selector both show this. Binding the labels to `self`
        would mean the second strip built silently stole the first one's
        names, leaving that strip frozen on its construction defaults -- so
        each strip keeps its own labels and joins a list instead.
        """
        strip = ttk.Frame(parent, style="Panel.TFrame", padding=(14, 10))
        queries = ttk.Label(strip, text="0", font=self.fonts["big"],
                            background=PALETTE["panel"])
        queries.pack(side="left")
        ttk.Label(strip, text=theme.tracked("searches"), style="Faint.TLabel",
                  font=self.fonts["label"], background=PALETTE["panel"]
                  ).pack(side="left", padx=(9, 0))
        hours = ttk.Label(strip, text="—", font=self.fonts["big"],
                          style="Lime.TLabel", background=PALETTE["panel"])
        hours.pack(side="left", padx=(20, 0))
        ttk.Label(strip, text=theme.tracked("estimated"), style="Faint.TLabel",
                  font=self.fonts["label"], background=PALETTE["panel"]
                  ).pack(side="left", padx=(9, 0))
        detail = ttk.Label(strip, text="", style="Muted.TLabel",
                           font=self.fonts["small"], background=PALETTE["panel"])
        detail.pack(side="left", padx=(18, 0))
        self._cost_strips.append((queries, hours, detail))
        return strip

    def _build_coverage(self, parent):
        """A grid and a summary. Which one is packed depends on the selection."""
        grid = CoverageGrid(parent, cell_font=self.fonts["cell"],
                            label_font=self.fonts["label"])
        summary = CoverageSummary(parent, font=self.fonts["ui"],
                                  note_font=self.fonts["note"])
        return grid, summary

    def _limit_value(self):
        try:
            return int(self.limit_spin.get()) if self.var_limited.get() else None
        except (TypeError, ValueError):
            return None

    def _terms(self):
        return list(self.terms_list.get(0, "end"))

    def _paint_cost(self, *_args) -> None:
        """The run-cost line, everywhere it appears."""
        terms = self._terms()
        leaves = geo.leaf_count(self.selection)
        cap = self._limit_value()
        queries, seconds = runstate.estimate_run(len(terms), leaves, cap)
        hours = seconds / 3600
        at_least = "" if cap else "\u2265"
        if not queries:
            spent = "—"
        elif hours >= 1:
            spent = f"{at_least}{hours:.0f}h"
        else:
            spent = f"{at_least}{seconds / 60:.0f}m"

        per = f"capped at {cap} businesses each" if cap else "no cap"
        detail = f"{len(terms)} search terms × {leaves:,} places, {per}"
        if cap:
            detail += f" · up to {queries * cap:,} leads"
        if hours > 24:
            detail += "  ·  consider narrowing"

        for queries_label, hours_label, detail_label in self._cost_strips:
            queries_label.configure(text=f"{queries:,}")
            # Past a day the number stops reassuring and starts warning, so it
            # changes colour rather than being buried in the detail line.
            hours_label.configure(
                text=spent, style="Amber.TLabel" if hours > 24 else "Lime.TLabel")
            detail_label.configure(text=detail)

        self.cap_label.configure(
            text=f"businesses per {selection.cap_noun(self.selection)}")
        self.places_count.configure(text=f"{leaves:,} places")
        if self.worker is None:
            self.start_btn.set_enabled(bool(terms and leaves))
            self.status_right.configure(
                text="" if leaves else "Choose at least one location")

    def _paint_locations(self) -> None:
        """The Locations box on the setup panel, rebuilt from the selection."""
        for child in self.places_rows.winfo_children():
            child.destroy()
        lines = selection.summary(self.selection)
        if not lines:
            ttk.Label(self.places_rows, text="Nothing selected yet",
                      style="Faint.TLabel", font=self.fonts["small"]
                      ).pack(anchor="w", pady=2)
        for country, detail in lines[:4]:
            row = ttk.Frame(self.places_rows)
            row.pack(fill="x", pady=1)
            ttk.Label(row, text=country, font=self.fonts["ui"]).pack(side="left")
            ttk.Label(row, text=detail, style="Faint.TLabel",
                      font=self.fonts["small"]).pack(side="right")
        if len(lines) > 4:
            ttk.Label(self.places_rows, text=f"+{len(lines) - 4} more countries",
                      style="Faint.TLabel", font=self.fonts["small"]
                      ).pack(anchor="w", pady=1)

    def _paint_coverage(self, grid, summary, headline=None) -> None:
        """Draw the coverage of whichever panel asked, at whichever size fits."""
        keys = selection.region_keys(self.selection)
        # Counted over the cells actually being drawn, not over run_state's
        # whole coverage dict: that dict was seeded from the selection as it
        # stood when the window opened, so after an edit it reports the old
        # total against the new grid.
        shown = {region: self.run_state.coverage.get(region, "pending")
                 for _, region in keys}
        done, total = runstate.coverage_tally(shown)
        if len(keys) > widgets.LARGE_SELECTION:
            grid.pack_forget()
            summary.pack(fill="x")
            summary.set_rows(runstate.country_tally(keys, self.run_state.coverage))
            text = (f"— {done} of {total} states complete, across "
                    f"{len({c for c, _ in keys})} countries")
        else:
            summary.pack_forget()
            grid.pack(fill="x")
            groups = []
            for country, region in keys:
                label = (geo.abbreviate(country, region) if region != country
                         else country)
                if groups and groups[-1][0] == country:
                    groups[-1][1].append((region, label))
                else:
                    groups.append((country, [(region, label)]))
            grid.set_groups(groups)
            grid.update_coverage(self.run_state.coverage,
                                 self.run_state.term_status, self._terms())
            text = (f"— {done} of {total} states finished" if done
                    else "— nothing started")
        if headline is not None:
            # Only the label is tracked. Threading thin spaces through a whole
            # sentence, which is what the mockup's CSS letter-spacing does at
            # a much smaller ratio, is unreadable at this size.
            headline.configure(text=f"{theme.tracked('Coverage')}   {text}")

    def _sync_limit(self) -> None:
        self.limit_spin.configure(
            state="readonly" if self.var_limited.get() else "disabled")
        self._paint_cost()

    # -- locations panel ---------------------------------------------------
    def _build_locations(self):
        """The country -> state -> city selector.

        Three cascading panes plus a summary, over one `Selection` this panel
        edits through `selection`'s pure functions. The panes never hold a
        selection of their own: they cascade, and a pane's own state is
        destroyed every time it repopulates.
        """
        panel = ttk.Frame(self._body, padding=(16, 12))
        self._panels["locations"] = panel

        self._pick_country = ""
        self._pick_region = ""
        self._search = ""
        self._hits = {}

        head = ttk.Frame(panel)
        head.pack(fill="x", pady=(0, 10))
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", self._on_search)
        entry = ttk.Entry(head, textvariable=self.search_var, font=self.fonts["ui"])
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Escape>", lambda _e: self.search_var.set(""))
        self.search_hint = ttk.Label(
            head, style="Faint.TLabel", font=self.fonts["small"],
            text="Search any country, state or city")
        self.search_hint.pack(side="left", padx=(12, 0))
        RoundedButton(head, "Clear all", self._clear_selection,
                      font=self.fonts["ui"], height=26, pad_x=10
                      ).pack(side="right", padx=(10, 0))
        RoundedButton(head, "Done", lambda: self._go("setup"), kind="primary",
                      font=self.fonts["button"], height=30, pad_x=18
                      ).pack(side="right")

        panes = ttk.Frame(panel, style="Panel.TFrame", padding=1)
        panes.pack(fill="both", expand=True)

        def pane(width, title):
            box = ttk.Frame(panes, style="Panel.TFrame", width=width)
            box.pack(side="left", fill="y")
            box.pack_propagate(False)
            bar = ttk.Frame(box, style="Panel.TFrame", padding=(12, 7))
            bar.pack(fill="x")
            ttk.Label(bar, text=theme.tracked(title), style="Faint.TLabel",
                      font=self.fonts["label"], background=PALETTE["panel"]
                      ).pack(side="left")
            subtitle = ttk.Label(bar, text="", style="Faint.TLabel",
                                 font=self.fonts["small"],
                                 background=PALETTE["panel"])
            subtitle.pack(side="right")
            body = ttk.Frame(box, style="Panel.TFrame")
            body.pack(fill="both", expand=True)
            return body, subtitle

        def picker(parent, on_toggle, on_highlight):
            bar = ttk.Scrollbar(parent, orient="vertical")
            bar.pack(side="right", fill="y")
            widget = widgets.PickList(parent, font=self.fonts["row"],
                                      note_font=self.fonts["note"],
                                      on_toggle=on_toggle,
                                      on_highlight=on_highlight)
            widget.pack(side="left", fill="both", expand=True)
            widget.configure_scrollbar(bar)
            return widget

        body, self.head_country = pane(250, "Country")
        self.pane_country = picker(body, self._toggle_country, self._focus_country)
        body, self.head_region = pane(250, "State")
        self.pane_region = picker(body, self._toggle_region, self._focus_region)
        body, self.head_city = pane(250, "City")
        self.pane_city = picker(body, self._toggle_city, lambda _n: None)

        chosen = ttk.Frame(panes, style="Panel.TFrame", padding=(14, 7))
        chosen.pack(side="left", fill="both", expand=True)
        ttk.Label(chosen, text=theme.tracked("Selected"), style="Faint.TLabel",
                  font=self.fonts["label"], background=PALETTE["panel"]
                  ).pack(anchor="w")
        self.chosen_rows = ttk.Frame(chosen, style="Panel.TFrame")
        self.chosen_rows.pack(fill="both", expand=True, pady=(8, 0))
        ttk.Label(chosen, style="Faint.TLabel", font=self.fonts["small"],
                  background=PALETTE["panel"], wraplength=230,
                  text="An empty level means all of it. No cities picked "
                       "searches the whole state."
                  ).pack(anchor="w", pady=(8, 0))

        self.cost_locations = self._build_cost(panel)
        self.cost_locations.pack(fill="x", pady=(10, 0))

    # -- locations: state -> screen ---------------------------------------
    def _on_search(self, *_args) -> None:
        self._search = self.search_var.get().strip()
        self.search_hint.configure(
            text="Search any country, state or city" if not self._search
            else f"{len(geo.search_places(self._search))} matches")
        self._paint_panes()

    def _clear_selection(self) -> None:
        self.selection = {}
        self._paint_panes()

    def _is_on(self, place) -> bool:
        """Whether `place` is already selected, at whatever level it names."""
        if place.city:
            return selection.is_city_on(self.selection, place.country,
                                        place.region, place.city)
        if place.region:
            return selection.is_region_on(self.selection, place.country,
                                          place.region)
        return selection.is_country_on(self.selection, place.country)

    def _focus_country(self, name) -> None:
        # In search mode the country pane holds flat results at every level,
        # so the row name is a Place path rather than a country -- the same
        # thing _toggle_country resolves through _hits. Storing it verbatim
        # left _pick_country as "Texas, United States", which names no
        # country, so geo.regions found nothing and the State pane stayed
        # empty even after the search was cleared. Point the panes at the
        # place instead, so clearing the search lands on what was just found.
        #
        # No repaint here: the flat list is what is on screen while a search
        # is running, and the toggle that follows every click repaints it.
        if self._search:
            place = self._hits.get(name)
            if place is None:
                return
            self._pick_country = place.country
            self._pick_region = place.region
            self.pane_region.set_current(place.region)
            self.pane_city.set_current(place.city)
            return
        self._pick_country = name
        self._pick_region = ""
        self.pane_region.set_current("")
        self.pane_city.set_current("")
        self._paint_panes()

    def _focus_region(self, name) -> None:
        self._pick_region = name
        self.pane_city.set_current("")
        self._paint_panes()

    def _toggle_country(self, name) -> None:
        # In search mode the country pane holds flat results at every level,
        # so the row name is a Place label rather than a country.
        if self._search:
            place = self._hits.get(name)
            if place is not None:
                self.selection = selection.toggle_place(self.selection, place)
            return self._paint_panes()
        self._pick_country = name
        self.selection = selection.toggle_country(self.selection, name)
        self._paint_panes()

    def _toggle_region(self, name) -> None:
        if not self._pick_country:
            return
        self._pick_region = name
        self.selection = selection.toggle_region(
            self.selection, self._pick_country, name)
        self._paint_panes()

    def _toggle_city(self, name) -> None:
        if not (self._pick_country and self._pick_region):
            return
        self.selection = selection.toggle_city(
            self.selection, self._pick_country, self._pick_region, name)
        self._paint_panes()

    def _paint_panes(self) -> None:
        """Repaint all three panes plus the summary from self.selection.

        One method, not three: the panes cascade, and a partial repaint is how
        the state pane ends up listing one country's regions under another
        country's header.
        """
        if self._search:
            self._paint_search()
            return
        self.pane_country.set_rows([
            widgets.Row(name, selection.country_note(self.selection, name),
                        selection.is_country_on(self.selection, name))
            for name in geo.countries()])
        country = self._pick_country
        self.pane_region.set_rows([
            widgets.Row(name, selection.region_note(self.selection, country, name),
                        selection.is_region_on(self.selection, country, name))
            for name in geo.regions(country)] if country else [])
        region = self._pick_region
        # The city pane's note column is empty on purpose. geodata stores
        # cities in population order but not the populations themselves, so
        # the figure the mockup shows does not exist -- the order carries the
        # ranking, and the pane header says so.
        self.pane_city.set_rows([
            widgets.Row(name, "",
                        selection.is_city_on(self.selection, country, region, name))
            for name in geo.cities(country, region)] if region else [])
        self.head_country.configure(
            text=f"{len(self.selection)} / {len(geo.countries())}")
        self.head_region.configure(text=country or "—")
        self.head_city.configure(
            text=f"{region} · most populous first" if region else "—")
        self._paint_chosen()
        self._paint_cost()

    def _paint_search(self) -> None:
        """Replace the three panes with one flat list of matches.

        Rows are keyed by `Place.query_text()`, so `_hits` can turn the name
        the widget hands back into the Place that produced it. A PickList row
        knows only its own name -- resolving it here keeps the widget free of
        any idea what a place is.

        `query_text()` and not `label()`, because labels collide: the country
        Georgia and the US state Georgia both label as "Georgia", so a
        label-keyed dict kept one of them and clicking either row toggled the
        wrong place. The full path is unique by construction, and in a flat
        result list it is also the more useful thing to read.
        """
        hits = geo.search_places(self._search)
        self._hits = {place.query_text(): place for place in hits}
        self.pane_country.set_rows([
            widgets.Row(place.query_text(), "", self._is_on(place))
            for place in hits])
        self.pane_region.set_rows([])
        self.pane_city.set_rows([])
        self.head_country.configure(text=f"{len(hits)} matches")
        self.head_region.configure(text="—")
        self.head_city.configure(text="—")
        self._paint_chosen()
        self._paint_cost()

    def _paint_chosen(self) -> None:
        for child in self.chosen_rows.winfo_children():
            child.destroy()
        lines = selection.summary(self.selection)
        if not lines:
            ttk.Label(self.chosen_rows, text="Nothing selected yet",
                      style="Faint.TLabel", font=self.fonts["small"],
                      background=PALETTE["panel"]).pack(anchor="w")
        for country, detail in lines[:9]:
            row = ttk.Frame(self.chosen_rows, style="Panel.TFrame")
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=country, font=self.fonts["ui"],
                      background=PALETTE["panel"]).pack(side="left")
            ttk.Label(row, text=detail, style="Faint.TLabel",
                      font=self.fonts["small"], background=PALETTE["panel"]
                      ).pack(side="right")
        if len(lines) > 9:
            ttk.Label(self.chosen_rows, text=f"+{len(lines) - 9} more",
                      style="Faint.TLabel", font=self.fonts["small"],
                      background=PALETTE["panel"]).pack(anchor="w", pady=2)

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

        self.coverage_head_running = ttk.Label(panel, style="Faint.TLabel",
                                               font=self.fonts["label"])
        self.coverage_head_running.pack(anchor="w", pady=(16, 6))
        self.grid_running, self.summary_running = self._build_coverage(panel)

        ttk.Label(panel, text=theme.tracked("Activity"), style="Faint.TLabel",
                  font=self.fonts["label"]).pack(anchor="w", pady=(14, 6))
        self.log_box = tk.Text(panel, height=7, font=self.fonts["mono"],
                               bg=PALETTE["panel"], fg=PALETTE["dim"],
                               relief="flat", bd=0, highlightthickness=1,
                               highlightbackground=PALETTE["cellline"],
                               highlightcolor=PALETTE["cellline"],
                               insertbackground=PALETTE["ink"],
                               selectbackground=PALETTE["row_sel"],
                               selectforeground=PALETTE["bright"],
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
        ttk.Label(head, text="businesses saved", style="Muted.TLabel",
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
            ("name", "Business", 280), ("where", "Where", 150), ("phone", "Phone", 130),
            ("email", "Email", 220), ("rating", "Rating", 90),
        ):
            self.table.heading(col, text=title)
            self.table.column(col, width=width, anchor="w")
        self.table.pack(side="left", fill="both", expand=True)

        health = ttk.Frame(body, padding=(16, 0, 0, 0))
        health.pack(side="right", fill="y")
        ttk.Label(health, text=theme.tracked("How complete the data is"), style="Faint.TLabel",
                  font=self.fonts["label"]).pack(anchor="w", pady=(0, 8))
        self.health_rows = ttk.Frame(health)
        self.health_rows.pack(fill="both", expand=True)

        actions = ttk.Frame(panel)
        actions.pack(fill="x", pady=(12, 0))
        RoundedButton(actions, "Open results file", self.on_open_folder,
                      kind="primary", font=self.fonts["ui_bold"], height=30).pack(side="left")
        RoundedButton(actions, "Start a new run", lambda: self._go("setup"),
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
                  wraplength=700, foreground=PALETTE["lime_ink"],
                  text=("Nothing has been lost. Every business found so far is already "
                        "written to disk, so continuing picks up where it stopped.")
                  ).pack(anchor="w", pady=(10, 0))

        row = ttk.Frame(card, style="Card.TFrame")
        row.pack(anchor="w", pady=(14, 0))
        RoundedButton(row, "Carry on from here", self.on_start, kind="primary",
                      font=self.fonts["ui_bold"], height=30,
                      bg=PALETTE["field"]).pack(side="left")
        RoundedButton(row, "Finish here and keep what I have",
                      lambda: self._go("results"), font=self.fonts["ui"],
                      height=30, bg=PALETTE["field"]).pack(side="left", padx=(9, 0))

        self.coverage_head_blocked = ttk.Label(panel, style="Faint.TLabel",
                                               font=self.fonts["label"])
        self.coverage_head_blocked.pack(anchor="w", pady=(18, 6))
        self.grid_blocked, self.summary_blocked = self._build_coverage(panel)
        self.blocked_detail = ttk.Label(panel, text="", style="Muted.TLabel",
                                        font=self.fonts["small"], wraplength=1040)
        self.blocked_detail.pack(anchor="w", pady=(10, 0))

    # -- actions (wired in Task 9) ----------------------------------------
    def on_add_term(self):
        term = self.term_entry.get().strip()
        if term and term not in self.terms_list.get(0, "end"):
            self.terms_list.insert("end", term)
            self.term_entry.delete(0, "end")
            self._paint_cost()

    def on_remove_term(self):
        for index in reversed(self.terms_list.curselection()):
            self.terms_list.delete(index)
        self._paint_cost()

    def on_open_folder(self):
        folder = Path(scrape.CSV_PATH).parent.resolve()
        folder.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform == "darwin":
                subprocess.run(["open", str(folder)], check=False)
            elif sys.platform.startswith("win"):
                subprocess.run(["explorer", str(folder)], check=False)
            else:
                subprocess.run(["xdg-open", str(folder)], check=False)
        except OSError:
            pass

    def on_start(self):
        if self.worker is not None:
            return self.on_stop()

        prefs = self.current_prefs()
        if not prefs["terms"]:
            messagebox.showwarning("No search terms",
                                   "Add at least one search term before starting.")
            return
        # Start is already disabled with nothing selected, so this only fires
        # if that guard is ever bypassed -- but a run with no places would
        # finish instantly and look like a crash.
        if not geo.leaf_count(self.selection):
            messagebox.showwarning(
                "No locations",
                "Press Choose locations and pick at least one country, "
                "state or city before starting.")
            return
        settings.save(prefs, SETTINGS_PATH)
        self.prefs = prefs

        scrape.clear_stop()
        records, done_pairs = scrape.read_cache()
        self.run_state = runstate.initial_state(
            done_pairs, prefs["terms"], geo.leaf_places(self.selection)
        )
        self.run_state.saved = len(records)
        self._rendered_minute = None

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
            places = geo.leaf_places(prefs["selection"])
            scrape.run_stage1(
                prefs["terms"], places,
                limit=prefs.get("limit"),
                headless=not prefs["headed"],
                force=prefs["force"],
            )
            # A block does not set the stop flag - run_stage1's circuit breaker
            # emits "blocked" and returns normally - so enrichment has to be
            # gated on saw_blocked too, or a blocked run spends hours fetching
            # club websites behind a panel that says it is paused.
            if saw_blocked["hit"]:
                reason = "blocked"
            else:
                if prefs["enrich"] and not scrape.stop_requested():
                    scrape.run_stage2(force=prefs["force"])
                reason = "stopped" if scrape.stop_requested() else "done"
            records, _ = scrape.read_cache()
            scrape.write_csv(records)
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
        try:
            before = self.run_state.status
            drained = 0
            for _ in range(500):
                try:
                    kind, data = self.events.get_nowait()
                except queue.Empty:
                    break
                drained += 1
                self.run_state = runstate.fold(self.run_state, kind, data, now=now)
                if kind == "run_finished":
                    finished = True

            # Only on a *change*: the panel is the source of truth, so following
            # the status here catches a mid-run block, while leaving the two
            # hand-navigation buttons free to show a panel the next tick will
            # not yank back.
            if self.run_state.status != before:
                self.show(PANEL_FOR_STATUS[self.run_state.status])

            # Nothing on screen has finer resolution than a minute, so a tick
            # that drained nothing inside the same minute would repaint 100
            # canvas items and wipe the activity log's selection for nothing.
            minute = self._elapsed_minute(now)
            if drained or minute != self._rendered_minute:
                self._rendered_minute = minute
                self.render(now)
        except Exception as exc:
            # One exception in fold() or render() used to kill the after() chain
            # for good: the worker scraped on for hours behind a window frozen on
            # its last frame, _finish() never ran, the button stayed on "Stop".
            self.run_state.log.append(f"Display problem: {type(exc).__name__}: {exc}")

        if finished:
            self._finish()
        elif (self.worker and self.worker.is_alive()) or not self.events.empty():
            self.after(100, self._pump)

    def _elapsed_minute(self, now: float) -> int | None:
        """Which minute of the run `now` falls in, or None before it starts."""
        if self.run_state.started_at is None:
            return None
        return int((now - self.run_state.started_at) // 60)

    def _finish(self):
        scrape.unsubscribe(self._on_event)
        self.worker = None
        self.start_btn.set_text("Start scrape")
        self.start_btn.set_enabled(True)
        self.show(PANEL_FOR_STATUS[self.run_state.status])
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
            # write_csv() rewrites the whole file, so closing inside that window
            # truncates results.csv. Wait for the current listing to land.
            self.worker.join(timeout=15)
        self.destroy()

    def current_prefs(self) -> dict:
        try:
            limit = int(self.limit_spin.get()) if self.var_limited.get() else None
        except (TypeError, ValueError):
            limit = None
        return {
            "terms": list(self.terms_list.get(0, "end")),
            "selection": self.selection,
            "enrich": self.var_enrich.get(),
            "headed": self.var_headed.get(),
            "force": self.var_force.get(),
            "limit": limit,
        }

    def render(self, now: float | None = None) -> None:
        """Paint the visible panel from self.run_state. Main thread only."""
        now = time.monotonic() if now is None else now
        s = self.run_state

        # "accent" and "done" both became lime, which would make running and
        # finished the same dot. Idle steps back to faint; blocked keeps amber.
        dot = {"idle": PALETTE["faint"], "running": PALETTE["lime"],
               "finished": PALETTE["lime"], "blocked": PALETTE["amber"]}[s.status]
        self.status_dot.delete("all")
        self.status_dot.create_oval(0, 0, 9, 9, fill=dot, outline=dot)
        self.status_text.configure(
            text={"idle": "Ready", "running": "Running",
                  "finished": "Finished", "blocked": "Paused"}[s.status])

        # Dispatch on the visible panel, not the status: they can legitimately
        # disagree (a mid-run block before the pump switches, or either hand
        # navigation button), and painting the panel nobody is looking at
        # freezes the one they are. The dot above stays status-derived - it is
        # genuinely about the run, not about what is on screen.
        if self._visible == "running":
            self.run_current.configure(text=s.current or "Working…")
            self.run_stage.configure(text=s.stage)
            done_pct = (100 * s.queries_done / s.queries_total) if s.queries_total else 0
            self.run_bar.configure(value=done_pct)
            left = runstate.remaining(s, now)
            self.run_counts.configure(
                text=f"{s.queries_done} of {s.queries_total} searches   ·   "
                     f"{s.saved} businesses saved   ·   {runstate.elapsed(s, now)} elapsed"
                     + (f"   ·   about {left} left" if left else ""))
            self._paint_coverage(self.grid_running, self.summary_running,
                                 self.coverage_head_running)
            self.log_box.configure(state="normal")
            self.log_box.delete("1.0", "end")
            self.log_box.insert("1.0", "\n".join(s.log[-7:]))
            self.log_box.configure(state="disabled")
            self.status_detail.configure(text=f"{s.saved} businesses")
            self.status_right.configure(text="Safe to close — picks up where it left off")

        elif self._visible == "results":
            self.res_count.configure(text=f"{s.saved:,}")
            self.res_summary.configure(
                text=f"Finished in {runstate.elapsed(s, now)} · "
                     f"{s.queries_done} of {s.queries_total} searches")
            # "partial" is not evidence of the 120-result cap: a per-place
            # limit makes every query incomplete, which used to announce that
            # all 50 states hit a cap none of them reached. at_cap is the real
            # signal and was already folded.
            partial = [st for st, v in s.coverage.items() if v == "partial"]
            if s.at_cap:
                warning = (f"{len(s.at_cap)} places hit Google's 120-result limit "
                           f"({', '.join(s.at_cap[:4])}) — searching those by city "
                           "would find more.")
            elif partial:
                warning = (f"{len(partial)} places are only partly covered "
                           f"({', '.join(partial[:4])}) — re-run without a per-place "
                           "limit to finish them.")
            else:
                warning = ""
            self.res_warning.configure(text=warning)
            records, _ = scrape.read_cache()
            self._fill_table(records)
            self._fill_health(records)
            self.status_detail.configure(text=str(scrape.CSV_PATH))
            self.status_right.configure(text="")

        elif self._visible == "blocked":
            crashed = s.finish_reason == "crashed"
            self.blocked_title.configure(
                text="Something went wrong" if crashed else "Google has stopped answering")
            self.blocked_body.configure(
                text=(s.failures[-1][1] if crashed and s.failures else
                      "Three searches failed one after another, which usually means "
                      "Google wants someone to prove they are not a robot. The run "
                      "paused itself rather than keep hammering the site."))
            self._paint_coverage(self.grid_blocked, self.summary_blocked,
                                 self.coverage_head_blocked)
            failed = [st for st, v in s.coverage.items() if v == "failed"]
            self.blocked_detail.configure(
                text=(f"{', '.join(failed)} failed and will be tried again next run."
                      if failed else ""))
            self.status_detail.configure(text=f"{s.saved} businesses saved")
            self.status_right.configure(text="Waiting for you")

        elif self._visible == "locations":
            self._paint_panes()
            self.status_detail.configure(
                text=f"{geo.leaf_count(self.selection)} places selected")

        else:
            self._paint_locations()
            self._paint_cost()
            self._paint_coverage(self.grid_setup, self.summary_setup,
                                 self.coverage_head)
            self.status_detail.configure(
                text=f"{s.queries_total} searches queued · {s.saved} businesses cached")

    def _fill_table(self, records):
        for row in self.table.get_children():
            self.table.delete(row)
        for record in records[:200]:
            self.table.insert("", "end", values=(
                record.get("name", ""),
                f"{record.get('city', '')}, {record.get('state', '')}".strip(", "),
                record.get("phone", ""),
                (record.get("emails", "") or "").split(";")[0].strip(),
                f"{record.get('rating', '')} · {record.get('reviews', '')}".strip(" ·"),
            ))

    def _fill_health(self, records):
        for child in self.health_rows.winfo_children():
            child.destroy()
        interesting = ["name", "address", "phone", "website", "emails",
                       "instagram", "owner_name", "owner_phone"]
        for column, rate in runstate.fill_rate_rows(records, interesting):
            row = ttk.Frame(self.health_rows)
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=column, width=13, style="Muted.TLabel",
                      font=self.fonts["small"]).pack(side="left")
            meter = tk.Canvas(row, width=120, height=6, highlightthickness=0,
                              bd=0, bg=PALETTE["hairline"])
            meter.pack(side="left", padx=6)
            colour = PALETTE["lime"] if rate >= 0.4 else PALETTE["amber"]
            meter.create_rectangle(0, 0, max(1, 120 * rate), 6,
                                   fill=colour, outline=colour)
            ttk.Label(row, text=f"{rate:.0%}", width=5, style="Muted.TLabel",
                      font=self.fonts["small"]).pack(side="left")


if __name__ == "__main__":
    App().mainloop()
