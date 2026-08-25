"""Google Maps club scraper -> Google Sheets.

Run `python scrape.py --help` for usage.
"""

from pathlib import Path

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
