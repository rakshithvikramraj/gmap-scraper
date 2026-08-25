"""Canvas-drawn controls that ttk cannot provide.

ttk widgets are square under every theme, so the rounded buttons in the design
are drawn by hand here. A drawn control inherits none of a real button's
behaviour, so RoundedButton reimplements hover, press, focus, keyboard
activation and disabled state explicitly.
"""

import math
import tkinter as tk

PALETTE = {
    "bg": "#f9f9f6", "panel": "#f3f3f0", "sunken": "#ededea", "line": "#dad9d5",
    "ink": "#2c2a25", "muted": "#71706b", "faint": "#86857f", "field": "#fefdfc",
    "accent": "#3b6fbc", "accent_d": "#2559a3", "selected": "#dfe8f6",
    "done": "#50a069", "done_ink": "#2f6b45",
    "partial": "#dea645", "failed": "#c74f47",
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


STATUS_COLORS = {
    "pending": (PALETTE["sunken"], PALETTE["line"], PALETTE["faint"]),
    "done":    ("#e4f1e8", PALETTE["done"], PALETTE["done_ink"]),
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
