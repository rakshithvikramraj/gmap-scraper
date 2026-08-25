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
    return bool(ctypes.windll.gdi32.AddFontResourceExW(str(path), private, 0))


def register_fonts(directory=None) -> list:
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
    if sys.platform == "darwin":
        register = _register_macos
    elif sys.platform.startswith("win"):
        register = _register_windows
    else:
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
