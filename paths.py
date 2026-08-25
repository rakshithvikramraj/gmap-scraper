"""Where the app reads and writes, in every way it can be started.

Three entry points share these paths: `python scrape.py` from a checkout,
`python app.py` from a checkout, and a PyInstaller bundle a teammate
double-clicks. Only the last one has no meaningful working directory --
launched from Finder it runs with cwd "/", so the repo-relative data/ path
the other two rely on would resolve to /data and fail on the first write.

Nothing here creates a directory. Callers already mkdir at write time, and a
module that reads a path should not have a side effect on disk.
"""

import os
import sys
from pathlib import Path

# The folder a teammate sees. Results are the deliverable, so they go
# somewhere findable rather than into ~/Library/Application Support.
APP_FOLDER = "Club Scraper"

# PyInstaller unpacks the bundle here and stores the location on sys.
BUNDLED_BROWSERS = "ms-playwright"


def frozen() -> bool:
    """True inside a PyInstaller bundle, false when running from source."""
    return bool(getattr(sys, "frozen", False))


def data_dir() -> Path:
    """The cache, the results CSV and the preferences file live here.

    From source this is the repo-relative `data/` the CLI and the whole test
    suite already assume -- returning anything else would move every existing
    checkout's data out from under it.
    """
    if frozen():
        return Path.home() / "Documents" / APP_FOLDER
    return Path("data")


def bundled_browsers() -> Path | None:
    """The Chromium shipped inside this bundle, or None if there isn't one.

    Absent both when running from source and in a bundle built without the
    browser step -- the caller treats those the same way.
    """
    root = getattr(sys, "_MEIPASS", None)
    if not frozen() or root is None:
        return None
    target = Path(root) / BUNDLED_BROWSERS
    return target if target.is_dir() else None


def use_bundled_browsers() -> None:
    """Point Playwright at the bundled Chromium. Call before sync_playwright().

    Pins an absolute path rather than Playwright's PLAYWRIGHT_BROWSERS_PATH=0
    convention: `0` resolves relative to the installed playwright package, and
    where PyInstaller places that package is not ours to depend on.

    setdefault, not assignment -- an explicit override belongs to whoever set
    it, usually someone debugging a bundle against their own browser build.
    """
    target = bundled_browsers()
    if target is not None:
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(target))
