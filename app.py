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
