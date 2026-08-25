"""Desktop window for the club scraper.

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

# Deliberately below _ensure_tcl_paths(): TCL_LIBRARY/TK_LIBRARY must be set
# before tkinter is imported, or this Tk build can't find its own init.tcl.
import tkinter as tk  # noqa: E402
import tkinter.font as tkfont  # noqa: E402
from tkinter import messagebox, ttk  # noqa: E402

import runstate
import scrape
import settings
from widgets import PALETTE, CoverageGrid, RoundedButton

SETTINGS_PATH = paths.data_dir() / "settings.json"

# The visible panel is the single source of truth for what render() paints; the
# pump uses this to follow a status change the user did not ask for (a mid-run
# block), and nothing else moves the panel behind their back.
PANEL_FOR_STATUS = {"idle": "setup", "running": "running",
                    "finished": "results", "blocked": "blocked"}

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
    style.map("Treeview", background=[("selected", PALETTE["selected"])],
              foreground=[("selected", PALETTE["ink"])])


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Wkey Lead Scraper")
        self.geometry("1100x740")
        self.minsize(960, 660)
        self.configure(bg=PALETTE["bg"])

        self.fonts = build_fonts()
        apply_theme(self)

        self.prefs = settings.load(SETTINGS_PATH)
        records, done_pairs = scrape.read_cache()
        # Named run_state, not state: tk.Tk already has a public state()
        # method, and shadowing it would quietly remove the ability to call
        # it (e.g. with "zoomed") or to query the window state at all.
        self.run_state = runstate.initial_state(
            done_pairs, self.prefs["terms"], self.prefs["states"]
        )
        self.run_state.clubs = len(records)

        self.events = queue.Queue()
        self.worker = None
        self.stop_flag = threading.Event()
        self._visible = "setup"
        self._rendered_minute = None

        self._build_toolbar()
        self._body = ttk.Frame(self)
        self._body.pack(fill="both", expand=True)
        self._panels = {}
        self._build_setup()
        self._build_running()
        self._build_results()
        self._build_blocked()
        self._build_statusbar()
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
        ttk.Label(terms_box, text="SEARCH TERMS", style="Faint.TLabel",
                  font=self.fonts["label"]).pack(anchor="w", pady=(0, 6))
        self.terms_list = tk.Listbox(
            terms_box, width=38, height=5, font=self.fonts["ui"],
            bg=PALETTE["field"], fg=PALETTE["ink"], relief="solid", bd=1,
            highlightthickness=0, selectbackground=PALETTE["selected"],
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
        self.limit_spin.pack(side="left", padx=6)
        ttk.Label(limit_row, text="clubs per state").pack(side="left")
        self._sync_limit()

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
        self.grid_setup.update_coverage(self.run_state.coverage)

    def _sync_limit(self) -> None:
        self.limit_spin.configure(
            state="readonly" if self.var_limited.get() else "disabled")

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
                  wraplength=700, foreground=PALETTE["done_ink"],
                  text=("Nothing has been lost. Every club found so far is already "
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

        ttk.Label(panel, text="WHERE IT GOT TO", style="Faint.TLabel",
                  font=self.fonts["label"]).pack(anchor="w", pady=(18, 6))
        self.grid_blocked = CoverageGrid(panel, scrape.ALL_50, cell_h=40,
                                         font=self.fonts["cell"])
        self.grid_blocked.pack(fill="x")
        self.blocked_detail = ttk.Label(panel, text="", style="Muted.TLabel",
                                        font=self.fonts["small"], wraplength=1040)
        self.blocked_detail.pack(anchor="w", pady=(10, 0))

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
        settings.save(prefs, SETTINGS_PATH)
        self.prefs = prefs

        scrape.clear_stop()
        records, done_pairs = scrape.read_cache()
        self.run_state = runstate.initial_state(done_pairs, prefs["terms"], prefs["states"])
        self.run_state.clubs = len(records)
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
            # The GUI still only lets someone pick US states, so every place
            # it searches is a US region; a later plan gives it a real
            # country/state/city selection and this seam goes away.
            places = [geo.Place(country="United States", region=state)
                      for state in prefs["states"]]
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
            "states": list(scrape.ALL_50),
            "enrich": self.var_enrich.get(),
            "headed": self.var_headed.get(),
            "force": self.var_force.get(),
            "limit": limit,
        }

    def render(self, now: float | None = None) -> None:
        """Paint the visible panel from self.run_state. Main thread only."""
        now = time.monotonic() if now is None else now
        s = self.run_state

        dot = {"idle": PALETTE["partial"], "running": PALETTE["accent"],
               "finished": PALETTE["done"], "blocked": PALETTE["partial"]}[s.status]
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
                     f"{s.clubs} clubs saved   ·   {runstate.elapsed(s, now)} elapsed"
                     + (f"   ·   about {left} left" if left else ""))
            self.grid_running.update_coverage(s.coverage)
            self.log_box.configure(state="normal")
            self.log_box.delete("1.0", "end")
            self.log_box.insert("1.0", "\n".join(s.log[-7:]))
            self.log_box.configure(state="disabled")
            self.status_detail.configure(text=f"{s.clubs} clubs")
            self.status_right.configure(text="Safe to close — picks up where it left off")

        elif self._visible == "results":
            self.res_count.configure(text=f"{s.clubs:,}")
            self.res_summary.configure(
                text=f"Finished in {runstate.elapsed(s, now)} · "
                     f"{s.queries_done} of {s.queries_total} searches")
            # "partial" is not evidence of the 120-result cap: a per-state
            # limit makes every query incomplete, which used to announce that
            # all 50 states hit a cap none of them reached. at_cap is the real
            # signal and was already folded.
            partial = [st for st, v in s.coverage.items() if v == "partial"]
            if s.at_cap:
                warning = (f"{len(s.at_cap)} states hit Google's 120-result limit "
                           f"({', '.join(s.at_cap[:4])}) — searching those by city "
                           "would find more.")
            elif partial:
                warning = (f"{len(partial)} states are only partly covered "
                           f"({', '.join(partial[:4])}) — re-run without a per-state "
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
                              bd=0, bg=PALETTE["sunken"])
            meter.pack(side="left", padx=6)
            colour = PALETTE["done"] if rate >= 0.4 else PALETTE["partial"]
            meter.create_rectangle(0, 0, max(1, 120 * rate), 6,
                                   fill=colour, outline=colour)
            ttk.Label(row, text=f"{rate:.0%}", width=5, style="Muted.TLabel",
                      font=self.fonts["small"]).pack(side="left")


if __name__ == "__main__":
    App().mainloop()
