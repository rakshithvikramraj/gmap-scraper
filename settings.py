"""Remembered window preferences.

Preferences only. This is not the resume cache: deleting this file loses no
scraped data, only the operator's choice of search terms and states.
"""

import json
from pathlib import Path

import scrape

DEFAULTS = {
    "terms": list(scrape.SEARCH_TERMS),
    "states": list(scrape.STATES),
    "enrich": True,
    "headed": False,
    "force": False,
    "limit": None,
}


def load(path: Path) -> dict:
    """Preferences from `path`, falling back to defaults for anything missing.

    Any unreadable or malformed file yields the defaults rather than an error:
    a corrupted preferences file must never stop someone opening the app.
    """
    prefs = {key: (list(value) if isinstance(value, list) else value)
             for key, value in DEFAULTS.items()}
    try:
        stored = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return prefs
    if not isinstance(stored, dict):
        return prefs
    for key in DEFAULTS:
        if key in stored:
            prefs[key] = stored[key]
    return prefs


def save(prefs: dict, path: Path) -> None:
    """Write the known preference keys to `path`, creating its directory."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    keep = {key: prefs.get(key, DEFAULTS[key]) for key in DEFAULTS}
    target.write_text(json.dumps(keep, indent=2) + "\n", encoding="utf-8")
