"""Canvas-drawn controls that ttk cannot provide.

ttk widgets are square under every theme, so the rounded buttons in the design
are drawn by hand here. A drawn control inherits none of a real button's
behaviour, so RoundedButton reimplements hover, press, focus, keyboard
activation and disabled state explicitly.
"""

import math
import tkinter as tk
from typing import NamedTuple

from theme import PALETTE

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


class RoundedButton(tk.Canvas):
    """A button with rounded corners, drawn because ttk cannot draw one.

    Reimplements what ttk would have given for free: hover, press, keyboard
    focus and activation, and a disabled state. Press tracks the pointer, so
    dragging off the button and releasing cancels the click, which is what
    every real button does and what a naive press/release binding gets wrong.
    """

    def __init__(self, master, text, command=None, *, kind="secondary",
                 font=None, radius=8, pad_x=14, height=28, min_width=0, **kw):
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
                smooth=True, fill="", outline=PALETTE["lime"], width=1,
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


_CHECK = (1.6, 5.2, 3.8, 7.4, 8.4, 2.6)  # tick polyline, in a 10x10 box


class PickList(tk.Canvas):
    """A scrolling list of tickable rows, drawn because ttk cannot draw one.

    A Listbox row is one string in one font, so it can carry neither the lime
    tick nor the right-aligned figure the design asks for, and proportional
    text makes the figure column impossible to align. Drawing it is the same
    trade CoverageGrid and RoundedButton already make.

    Only the rows in view are drawn. The city panes come from 19,599 cities
    and the country pane from 252, so a repaint stays a few dozen canvas items
    however long the list is.

    The widget owns no selection. It renders the rows it is handed and reports
    gestures back through on_toggle/on_highlight, because the selection
    cascades across three panes and a widget-local copy would be one more
    thing that can disagree with the others.
    """

    def __init__(self, master, *, font=None, note_font=None, row_h=30,
                 on_toggle=None, on_highlight=None, **kw):
        super().__init__(master, highlightthickness=0, bd=0, takefocus=1,
                         bg=kw.pop("bg", PALETTE["panel"]), **kw)
        self._rows = []
        self._top = 0
        self._current = ""
        self._font = font
        self._note_font = note_font
        self._row_h = row_h
        self._on_toggle = on_toggle
        self._on_highlight = on_highlight
        self._yscroll = None

        for sequence, handler in (
            ("<Button-1>", self._on_click),
            ("<MouseWheel>", self._on_wheel),
            ("<Button-4>", lambda e: self._scroll_by(-3)),
            ("<Button-5>", lambda e: self._scroll_by(3)),
            ("<Up>", lambda e: self._move(-1)),
            ("<Down>", lambda e: self._move(1)),
            ("<Prior>", lambda e: self._move(-self._page())),
            ("<Next>", lambda e: self._move(self._page())),
            ("<Home>", lambda e: self._move(-len(self._rows))),
            ("<End>", lambda e: self._move(len(self._rows))),
            ("<space>", self._on_activate),
            ("<Return>", self._on_activate),
            ("<Configure>", lambda e: self._draw()),
        ):
            self.bind(sequence, handler)

    # -- public ------------------------------------------------------------
    def set_rows(self, rows) -> None:
        """Replace every row. Keeps the scroll position where it still fits."""
        self._rows = list(rows)
        self._top = clamp_top(self._top, self._height(), self._row_h,
                              len(self._rows))
        self._draw()

    def set_current(self, name: str) -> None:
        self._current = name
        self._draw()

    def current(self) -> str:
        return self._current

    def configure_scrollbar(self, bar) -> None:
        """Wire a ttk.Scrollbar both ways in one call."""
        self._yscroll = bar.set
        bar.configure(command=self.yview)
        self._draw()

    def yview(self, *args):
        """Scrollbar protocol: ("moveto", f) or ("scroll", n, "units"|"pages")."""
        if not args:
            return
        if args[0] == "moveto":
            self._set_top(round(float(args[1]) * len(self._rows)))
        elif args[0] == "scroll":
            step = int(args[1])
            self._set_top(self._top + step * (self._page() if args[2] == "pages" else 1))

    # -- internals ---------------------------------------------------------
    def _height(self) -> int:
        return max(self.winfo_height(), 1)

    def _page(self) -> int:
        return max(1, int(self._height() // self._row_h) - 1)

    def _set_top(self, top) -> None:
        self._top = clamp_top(top, self._height(), self._row_h, len(self._rows))
        self._draw()

    def _scroll_by(self, rows):
        self._set_top(self._top + rows)
        return "break"

    def _on_wheel(self, event):
        # Windows reports delta in multiples of 120; macOS reports small
        # integers. Dividing the Windows value keeps one notch to one step.
        step = -event.delta // 120 if abs(event.delta) >= 120 else -event.delta
        return self._scroll_by(step)

    def _index_at(self, y) -> int:
        return self._top + int(y // self._row_h)

    def _on_click(self, event):
        self.focus_set()
        index = self._index_at(event.y)
        if 0 <= index < len(self._rows):
            name = self._rows[index].name
            self._current = name
            if self._on_highlight:
                self._on_highlight(name)
            if self._on_toggle:
                self._on_toggle(name)
            self._draw()
        return "break"

    def _move(self, step):
        """Move the highlight without toggling, so a list can be browsed."""
        if not self._rows:
            return "break"
        names = [row.name for row in self._rows]
        index = names.index(self._current) if self._current in names else -1
        index = max(0, min(len(names) - 1, index + step))
        self._current = names[index]
        # Keep the highlight in view, or arrowing past the edge looks dead.
        visible = max(1, int(self._height() // self._row_h))
        if index < self._top:
            self._top = index
        elif index >= self._top + visible:
            self._top = index - visible + 1
        if self._on_highlight:
            self._on_highlight(self._current)
        self._draw()
        return "break"

    def _on_activate(self, _event):
        if self._current and self._on_toggle:
            self._on_toggle(self._current)
        return "break"

    def _fit(self, text, width):
        """`text`, ellipsised to `width` pixels using the font's own metrics."""
        if not self._font or self._font.measure(text) <= width:
            return text
        while text and self._font.measure(text + "…") > width:
            text = text[:-1]
        return text + "…"

    def _draw(self) -> None:
        self.delete("all")
        width = max(self.winfo_width(), 1)
        first, stop = visible_slice(self._top, self._height(), self._row_h,
                                    len(self._rows))
        for index in range(first, stop):
            row = self._rows[index]
            y = (index - self._top) * self._row_h
            mid = y + self._row_h / 2
            current = row.name == self._current
            if current:
                self.create_rectangle(0, y, width, y + self._row_h,
                                      fill=PALETTE["row_sel"], outline="")
                self.create_rectangle(0, y, 2, y + self._row_h,
                                      fill=PALETTE["lime"], outline="")
            box = (14, mid - 7.5, 29, mid + 7.5)
            if row.checked:
                self.create_polygon(rounded_points(*box, 4), smooth=True,
                                    fill=PALETTE["lime"], outline=PALETTE["lime"])
                self.create_line(
                    *[box[0] + 1.5 + v * 1.2 if i % 2 == 0 else mid - 6 + v * 1.2
                      for i, v in enumerate(_CHECK)],
                    fill=PALETTE["onlime"], width=2, capstyle="round",
                    joinstyle="round")
            else:
                self.create_polygon(rounded_points(*box, 4), smooth=True,
                                    fill="", outline=PALETTE["tickline"])
            note_w = (self._note_font.measure(row.note) if self._note_font and row.note
                      else 0)
            self.create_text(
                39, mid, anchor="w", font=self._font,
                text=self._fit(row.name, max(10, width - 39 - note_w - 26)),
                fill=PALETTE["bright"] if (current or row.checked) else PALETTE["muted"])
            if row.note:
                self.create_text(width - 14, mid, anchor="e", text=row.note,
                                 font=self._note_font,
                                 fill=PALETTE["lime"] if current else PALETTE["faint"])
        if self._yscroll:
            self._yscroll(*[str(f) for f in scroll_fractions(
                self._top, self._height(), self._row_h, len(self._rows))])
