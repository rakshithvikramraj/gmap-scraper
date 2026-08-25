"""Google Maps club scraper -> Google Sheets.

Run `python scrape.py --help` for usage.
"""

import re
from pathlib import Path
from urllib.parse import quote_plus, unquote

# ---------------------------------------------------------------------------
# CONFIG - the only block you normally need to edit
# ---------------------------------------------------------------------------

SEARCH_TERMS = ["padel club", "padel court", "padel tennis"]

SHEET_URL = "https://docs.google.com/spreadsheets/d/1hq6DPxz2j59HHPj8VMmtH5Vl7TnlZyPG4nCy_0Lonfk/edit"
WORKSHEET = "clubs"

ENRICH_SITES = True
HEADLESS = True

ALL_50 = [
    "Alabama", "Alaska", "Arizona", "Arkansas", "California",
    "Colorado", "Connecticut", "Delaware", "Florida", "Georgia",
    "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa",
    "Kansas", "Kentucky", "Louisiana", "Maine", "Maryland",
    "Massachusetts", "Michigan", "Minnesota", "Mississippi", "Missouri",
    "Montana", "Nebraska", "Nevada", "New Hampshire", "New Jersey",
    "New Mexico", "New York", "North Carolina", "North Dakota", "Ohio",
    "Oklahoma", "Oregon", "Pennsylvania", "Rhode Island", "South Carolina",
    "South Dakota", "Tennessee", "Texas", "Utah", "Vermont",
    "Virginia", "Washington", "West Virginia", "Wisconsin", "Wyoming",
]

STATES = ALL_50

# ---------------------------------------------------------------------------
# Constants - change only when Google changes, or when adding a column
# ---------------------------------------------------------------------------

COLUMNS = [
    "place_key", "name", "category", "address", "city", "state", "zip",
    "phone", "website", "rating", "reviews", "latitude", "longitude",
    "maps_url", "emails", "owner_name", "owner_phone", "other_phones",
    "instagram", "facebook", "linkedin", "search_term", "search_state",
    "scraped_at",
]

DATA_DIR = Path("data")
CACHE_PATH = DATA_DIR / "cache.jsonl"
CSV_PATH = DATA_DIR / "results.csv"

# ---------------------------------------------------------------------------
# Parsing functions
# ---------------------------------------------------------------------------

MAPS_SEARCH_URL = "https://www.google.com/maps/search/{query}?hl=en&gl=us"

PLACE_KEY_RE = re.compile(r"!1s(0x[0-9a-f]+:0x[0-9a-f]+)", re.I)
LATLNG_RE = re.compile(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)")
ADDR_TAIL_RE = re.compile(
    r",\s*([A-Za-z .'\-]+),\s*([A-Z]{2})\s+(\d{5})(?:-\d{4})?\b"
)


def build_search_url(term: str, state: str) -> str:
    """URL for a Maps search of `term` within `state`."""
    return MAPS_SEARCH_URL.format(query=quote_plus(f"{term} in {state}"))


def parse_place_key(url: str) -> str:
    """Stable hex feature id from a Maps place URL, or "" if absent."""
    match = PLACE_KEY_RE.search(url)
    return match.group(1) if match else ""


def parse_latlng(url: str) -> tuple[float | None, float | None]:
    """(lat, lng) from a Maps place URL, or (None, None) if absent."""
    match = LATLNG_RE.search(url)
    if not match:
        return (None, None)
    return (float(match.group(1)), float(match.group(2)))


def split_address(address: str) -> tuple[str, str, str]:
    """Best-effort (city, state, zip) split of a formatted US address."""
    if not address:
        return ("", "", "")
    match = ADDR_TAIL_RE.search(address)
    if not match:
        return ("", "", "")
    return (match.group(1).strip(), match.group(2), match.group(3))


EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
MAILTO_RE = re.compile(r"mailto:([^\"'?>\s]+)", re.I)

JUNK_LOCALS = {"noreply", "no-reply", "donotreply", "do-not-reply"}
JUNK_DOMAINS = {
    "sentry.io", "wixpress.com", "example.com", "example.org",
    "godaddy.com", "squarespace.com", "schema.org", "w3.org",
    "sentry.wixpress.com",
}
IMAGE_EXT_RE = re.compile(r"\.(png|jpe?g|gif|webp|svg|ico)$", re.I)
HEX_LOCAL_RE = re.compile(r"^[0-9a-f]{20,}$", re.I)


def is_junk_email(email: str) -> bool:
    """True for addresses that are platform noise rather than contacts."""
    local, _, domain = email.lower().partition("@")
    if not domain:
        return True
    if local in JUNK_LOCALS:
        return True
    if HEX_LOCAL_RE.match(local):
        return True
    if IMAGE_EXT_RE.search(domain):
        return True
    return any(domain == d or domain.endswith("." + d) for d in JUNK_DOMAINS)


def extract_emails(html: str) -> list[str]:
    """Every usable email address in `html`, sorted and deduplicated."""
    found = set(EMAIL_RE.findall(html))
    for match in MAILTO_RE.finditer(html):
        found.add(unquote(match.group(1)))
    return sorted(
        {e.lower() for e in found if not is_junk_email(e)}
    )
