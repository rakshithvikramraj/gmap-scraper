# Google Maps Club Scraper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a single Python script that scrapes padel and sports club listings from Google Maps across all 50 US states, enriches each with contact details from the club's own website, and upserts the results into a Google Sheet.

**Architecture:** Three sequential stages in one file. Stage 1 drives headless Chromium through Playwright to read Maps listings; Stage 2 fetches each club's own site with httpx to extract emails, phones and socials; Stage 3 upserts into Sheets with gspread. All three communicate through a single append-only `data/cache.jsonl`, which also provides crash resume. Every non-trivial piece of logic is a pure function so it can be tested without a browser or a network.

**Tech Stack:** Python 3.13, Playwright (sync API, Chromium), httpx, BeautifulSoup4, gspread, google-auth, pytest.

**Spec:** `docs/superpowers/specs/2026-08-24-gmap-scraper-design.md`

## Global Constraints

- Python >= 3.11. Development machine has 3.13.2.
- All runtime code lives in one file, `scrape.py`. Tests live in `tests/`. This is a deliberate spec decision (spec section 3), not an oversight. Do not split `scrape.py` into modules.
- Playwright uses the **sync** API (`sync_playwright`). Do not introduce asyncio.
- No test may make a network request or launch a browser. Tests that need HTML use fixtures; functions that fetch take an injectable `fetch_fn`.
- Element selection prefers `data-item-id` attributes over CSS class names (spec section 3). Every selector string lives in the `SELECTORS` block near the top of `scrape.py` so a future break is a one-line fix.
- Pacing: 1-3s randomized between listings, 5-10s between queries (spec section 8). Never remove these.
- `credentials.json`, `data/`, and `.venv/` are never committed.
- Google Sheets auth is Application Default Credentials via `google.auth.default()`.
  No service-account key file is downloaded or read (spec section 3).
- Sheet URL is fixed: `https://docs.google.com/spreadsheets/d/1hq6DPxz2j59HHPj8VMmtH5Vl7TnlZyPG4nCy_0Lonfk/edit`
- Column order is defined once in `COLUMNS` and never duplicated elsewhere.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `scrape.py` | Everything at runtime: CONFIG block, selector constants, pure parsing helpers, three stages, CLI. |
| `tests/test_parsing.py` | Unit tests for every pure function. No network, no browser. |
| `tests/fixtures/club_site.html` | Synthetic club homepage used by enrichment tests. |
| `tests/fixtures/club_contact.html` | Synthetic contact page with an owner name and phone. |
| `requirements.txt` | Pinned dependency floors. |
| `README.md` | Install, Playwright browser download, gcloud auth setup, usage. |
| `.gitignore` | Excludes `credentials.json`, `data/`, `.venv/`, `__pycache__/`. |
| `data/cache.jsonl` | Append-only record and progress log. Gitignored, created at runtime. |
| `data/results.csv` | Local backup of the final rows. Gitignored, created at runtime. |

---

### Task 1: Project scaffolding and CONFIG block

**Files:**
- Create: `scrape.py`
- Create: `requirements.txt`
- Create: `.gitignore`
- Test: `tests/test_parsing.py`

**Interfaces:**
- Consumes: nothing.
- Produces: module-level constants `SEARCH_TERMS: list[str]`, `ALL_50: list[str]`, `STATES: list[str]`, `SHEET_URL: str`, `WORKSHEET: str`, `ENRICH_SITES: bool`, `HEADLESS: bool`, `COLUMNS: list[str]`, `CACHE_PATH: Path`, `CSV_PATH: Path`.

- [ ] **Step 1: Initialise the repo and virtualenv**

```bash
cd /Users/rakshithvikramraj/dev/gmap-scraper
git init
uv venv
source .venv/bin/activate
```

- [ ] **Step 2: Write `requirements.txt`**

```
playwright>=1.48
httpx>=0.27
beautifulsoup4>=4.12
gspread>=6.1
google-auth>=2.35
pytest>=8.0
```

- [ ] **Step 3: Install dependencies and the Chromium build**

```bash
uv pip install -r requirements.txt
playwright install chromium
```

Expected: `playwright install` downloads a Chromium build (~150MB) and prints its install path.

- [ ] **Step 4: Write `.gitignore`**

```
.venv/
__pycache__/
*.pyc
credentials.json
data/
.pytest_cache/
```

- [ ] **Step 5: Write the failing test**

Create `tests/test_parsing.py`:

```python
import scrape


def test_all_50_states_present():
    assert len(scrape.ALL_50) == 50
    assert "Texas" in scrape.ALL_50
    assert "Wyoming" in scrape.ALL_50


def test_search_terms_non_empty():
    assert scrape.SEARCH_TERMS
    assert all(isinstance(t, str) and t.strip() for t in scrape.SEARCH_TERMS)


def test_columns_are_unique_and_start_with_place_key():
    assert scrape.COLUMNS[0] == "place_key"
    assert len(scrape.COLUMNS) == len(set(scrape.COLUMNS))
```

- [ ] **Step 6: Run test to verify it fails**

Run: `pytest tests/test_parsing.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scrape'`

- [ ] **Step 7: Write `scrape.py` with the CONFIG block**

```python
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
```

- [ ] **Step 8: Run test to verify it passes**

Run: `pytest tests/test_parsing.py -v`
Expected: 3 passed

- [ ] **Step 9: Commit**

```bash
git add .gitignore requirements.txt scrape.py tests/test_parsing.py
git commit -m "feat: project scaffolding and CONFIG block"
```

---

### Task 2: Maps URL and address parsing

**Files:**
- Modify: `scrape.py` (append below the constants block)
- Test: `tests/test_parsing.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `build_search_url(term: str, state: str) -> str`
  - `parse_place_key(url: str) -> str`
  - `parse_latlng(url: str) -> tuple[float | None, float | None]`
  - `split_address(address: str) -> tuple[str, str, str]` returning `(city, state, zip)`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_parsing.py`:

```python
def test_build_search_url_encodes_term_and_state():
    url = scrape.build_search_url("padel club", "New York")
    assert url.startswith("https://www.google.com/maps/search/")
    assert "padel+club+in+New+York" in url
    assert "hl=en" in url


def test_parse_place_key_extracts_feature_id():
    url = "https://www.google.com/maps/place/Padel+X/data=!4m6!1s0x864c3b1a:0x9fe1!3d30.26!4d-97.74"
    assert scrape.parse_place_key(url) == "0x864c3b1a:0x9fe1"


def test_parse_place_key_returns_empty_when_absent():
    assert scrape.parse_place_key("https://www.google.com/maps") == ""


def test_parse_latlng_extracts_coordinates():
    url = "https://www.google.com/maps/place/X/data=!3d30.2672!4d-97.7431"
    assert scrape.parse_latlng(url) == (30.2672, -97.7431)


def test_parse_latlng_handles_negative_and_missing():
    assert scrape.parse_latlng("https://example.com") == (None, None)


def test_split_address_full_us_form():
    addr = "1234 Main St, Austin, TX 78701, United States"
    assert scrape.split_address(addr) == ("Austin", "TX", "78701")


def test_split_address_multiword_city_and_zip_plus_four():
    addr = "500 Padel Way, Salt Lake City, UT 84101-1234"
    assert scrape.split_address(addr) == ("Salt Lake City", "UT", "84101")


def test_split_address_returns_blanks_when_unparseable():
    assert scrape.split_address("") == ("", "", "")
    assert scrape.split_address("Unit 5, Somewhere") == ("", "", "")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_parsing.py -v`
Expected: 8 failures with `AttributeError: module 'scrape' has no attribute 'build_search_url'`

- [ ] **Step 3: Write the implementation**

Append to `scrape.py`:

```python
import re
from urllib.parse import quote_plus

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_parsing.py -v`
Expected: 11 passed

- [ ] **Step 5: Commit**

```bash
git add scrape.py tests/test_parsing.py
git commit -m "feat: Maps URL and address parsing helpers"
```

---

### Task 3: Email extraction and junk filtering

**Files:**
- Modify: `scrape.py`
- Test: `tests/test_parsing.py`

**Interfaces:**
- Consumes: `re` already imported in Task 2.
- Produces:
  - `is_junk_email(email: str) -> bool`
  - `extract_emails(html: str) -> list[str]` returning sorted, lowercased, deduplicated addresses.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_parsing.py`:

```python
def test_extract_emails_finds_plain_and_mailto():
    html = """
    <p>Reach us at info@padelclub.com</p>
    <a href="mailto:bookings@padelclub.com?subject=Hi">Book</a>
    """
    assert scrape.extract_emails(html) == [
        "bookings@padelclub.com",
        "info@padelclub.com",
    ]


def test_extract_emails_deduplicates_and_lowercases():
    html = "Info@Padel.com and info@padel.com"
    assert scrape.extract_emails(html) == ["info@padel.com"]


def test_extract_emails_drops_noreply_addresses():
    html = "noreply@padel.com no-reply@padel.com real@padel.com"
    assert scrape.extract_emails(html) == ["real@padel.com"]


def test_extract_emails_drops_platform_artifacts():
    html = 'x@sentry.io y@sentry-next.wixpress.com z@example.com ok@padel.com'
    assert scrape.extract_emails(html) == ["ok@padel.com"]


def test_extract_emails_drops_image_filenames():
    html = '<img src="logo@2x.png"> real@padel.com'
    assert scrape.extract_emails(html) == ["real@padel.com"]


def test_extract_emails_drops_long_hex_locals():
    html = "a1b2c3d4e5f60718293a4b5c@tracking.io good@padel.com"
    assert scrape.extract_emails(html) == ["good@padel.com"]


def test_extract_emails_rejects_escaped_mailto_artifacts():
    html = r'<a href="mailto:info@club.com\">E</a><a href="mailto:real@club.com">R</a>'
    assert scrape.extract_emails(html) == ["info@club.com", "real@club.com"]


def test_extract_emails_empty_input():
    assert scrape.extract_emails("") == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_parsing.py -k email -v`
Expected: 7 failures with `AttributeError: module 'scrape' has no attribute 'extract_emails'`

- [ ] **Step 3: Write the implementation**

Append to `scrape.py`:

```python
from urllib.parse import unquote

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
        candidate = unquote(match.group(1))
        if EMAIL_RE.fullmatch(candidate):
            found.add(candidate)
    return sorted(
        {e.lower() for e in found if not is_junk_email(e)}
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_parsing.py -v`
Expected: 18 passed

- [ ] **Step 5: Commit**

```bash
git add scrape.py tests/test_parsing.py
git commit -m "feat: email extraction with junk filtering"
```

---

### Task 4: Phone normalization and the owner heuristic

**Files:**
- Modify: `scrape.py`
- Test: `tests/test_parsing.py`

**Interfaces:**
- Consumes: `re` from Task 2.
- Produces:
  - `normalize_phone(raw: str) -> str` returning `+1XXXXXXXXXX` or `""`
  - `extract_phones(text: str) -> list[str]` returning normalized, deduplicated, in document order
  - `find_owner_contact(text: str) -> tuple[str, str]` returning `(owner_name, owner_phone)`

This is the subtlest logic in the project. The heuristic is deliberately
conservative: it would rather return `("", "")` than attach a wrong name to a
phone number.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_parsing.py`:

```python
def test_normalize_phone_strips_formatting():
    assert scrape.normalize_phone("(512) 555-0100") == "+15125550100"
    assert scrape.normalize_phone("+1 512.555.0100") == "+15125550100"
    assert scrape.normalize_phone("512-555-0100") == "+15125550100"


def test_normalize_phone_rejects_wrong_length():
    assert scrape.normalize_phone("555-0100") == ""
    assert scrape.normalize_phone("") == ""
    assert scrape.normalize_phone("12345678901234") == ""


def test_extract_phones_dedupes_across_formats():
    text = "Call (512) 555-0100 or 512-555-0100 or 512.555.0199"
    assert scrape.extract_phones(text) == ["+15125550100", "+15125550199"]


def test_find_owner_contact_matches_name_before_title():
    text = "John Smith, Owner - (512) 555-0100"
    assert scrape.find_owner_contact(text) == ("John Smith", "+15125550100")


def test_find_owner_contact_matches_name_after_phone():
    text = "Founder: (512) 555-0100 Maria Lopez"
    assert scrape.find_owner_contact(text) == ("Maria Lopez", "+15125550100")


def test_find_owner_contact_ignores_phones_without_a_title_keyword():
    text = "Call the front desk on (512) 555-0100 to book a court."
    assert scrape.find_owner_contact(text) == ("", "")


def test_find_owner_contact_returns_phone_when_name_is_only_a_title():
    text = "General Manager: (512) 555-0100"
    assert scrape.find_owner_contact(text) == ("", "+15125550100")


def test_find_owner_contact_ignores_distant_keywords():
    text = "Owner" + (" filler" * 60) + " call (512) 555-0100"
    assert scrape.find_owner_contact(text) == ("", "")


def test_find_owner_contact_prefers_the_nearest_title_keyword():
    text = "Front desk (512) 555-0100. Our team: John Smith, Owner - (512) 555-0142"
    assert scrape.find_owner_contact(text) == ("John Smith", "+15125550142")


def test_find_owner_contact_keeps_a_three_token_name_whole():
    text = "Mary Jane Watson, Owner - (512) 555-0100"
    assert scrape.find_owner_contact(text) == ("Mary Jane Watson", "+15125550100")


def test_find_owner_contact_rejects_an_over_long_capitalised_run():
    text = "Owner Riverside Grand Athletic Pavilion Trust (512) 555-0100"
    assert scrape.find_owner_contact(text) == ("", "+15125550100")


def test_find_owner_contact_handles_title_directly_before_name():
    text = "Owner Maria Lopez - (512) 555-0100"
    assert scrape.find_owner_contact(text) == ("Maria Lopez", "+15125550100")


def test_find_owner_contact_handles_title_before_name_in_prose():
    text = "Founder Dave Kim can be reached at (512) 555-0100"
    assert scrape.find_owner_contact(text) == ("Dave Kim", "+15125550100")


def test_find_owner_contact_empty_text():
    assert scrape.find_owner_contact("") == ("", "")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_parsing.py -k "phone or owner" -v`
Expected: 14 failures with `AttributeError: module 'scrape' has no attribute 'normalize_phone'`

- [ ] **Step 3: Write the implementation**

Append to `scrape.py`:

```python
PHONE_RE = re.compile(
    r"(?:\+?1[\s.\-]?)?\(?([2-9]\d{2})\)?[\s.\-]?(\d{3})[\s.\-]?(\d{4})(?!\d)"
)
TITLE_TOKEN_RE = re.compile(r"\b[A-Z][a-z]{1,15}\b")

OWNER_KEYWORDS = (
    "owner", "founder", "co-founder", "cofounder", "proprietor",
    "general manager", "club manager", "managing director", "director",
    "president", "principal", "ceo",
)
NAME_STOPWORDS = {
    "general", "manager", "managing", "director", "club", "padel", "tennis",
    "contact", "phone", "email", "office", "front", "desk", "head", "coach",
    "the", "our", "call", "text", "united", "states", "founder", "owner",
    "president", "principal", "monday", "friday", "saturday", "sunday",
    "book", "now", "court", "courts", "sports", "center", "centre", "academy",
}
OWNER_WINDOW = 120
MAX_NAME_TOKENS = 3


def normalize_phone(raw: str) -> str:
    """US phone number as +1XXXXXXXXXX, or "" if it is not one."""
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return ""
    return "+1" + digits


def extract_phones(text: str) -> list[str]:
    """Normalized US phone numbers in `text`, deduplicated, in order."""
    seen: list[str] = []
    for match in PHONE_RE.finditer(text):
        number = normalize_phone(match.group(0))
        if number and number not in seen:
            seen.append(number)
    return seen


def _names_in(fragment: str) -> list[str]:
    """Title Case name pairs in `fragment`, in order, skipping title words.

    Scans tokens instead of matching pairs directly. A pair regex is greedy:
    on "Owner Maria Lopez" it matches ("Owner", "Maria"), the stopword filter
    rejects that pair, and the scan resumes past "Maria" -- so the real name is
    never seen. Filtering tokens first and pairing adjacent survivors handles a
    title word sitting directly against the name. The whitespace-only gap check
    stops two survivors pairing across a filtered word: "John Smith, Owner Bob
    Jones" yields ["John Smith", "Bob Jones"], never "Smith Bob".

    Runs are maximal, so a three-token name survives whole. A run longer than
    MAX_NAME_TOKENS is rejected rather than truncated: four or more capitalised
    words in a row is far more likely a heading or business name than a person,
    and truncating would manufacture a plausible-looking wrong name.
    """
    tokens = [
        (match.group(0), match.start(), match.end())
        for match in TITLE_TOKEN_RE.finditer(fragment)
        if match.group(0).lower() not in NAME_STOPWORDS
    ]
    names = []
    run: list[str] = []
    previous_end = None
    for word, start, end in tokens:
        if previous_end is not None and not fragment[previous_end:start].strip():
            run.append(word)
        else:
            if 2 <= len(run) <= MAX_NAME_TOKENS:
                names.append(" ".join(run))
            run = [word]
        previous_end = end
    if 2 <= len(run) <= MAX_NAME_TOKENS:
        names.append(" ".join(run))
    return names


def _keyword_spans(lowered: str) -> list[tuple[int, int]]:
    """Character spans of every ownership keyword occurrence."""
    spans = []
    for keyword in OWNER_KEYWORDS:
        start = lowered.find(keyword)
        while start != -1:
            spans.append((start, start + len(keyword)))
            start = lowered.find(keyword, start + 1)
    return spans


def _gap(span: tuple[int, int], start: int, end: int) -> int:
    """Character distance between a keyword span and a phone span."""
    keyword_start, keyword_end = span
    if keyword_end <= start:
        return start - keyword_end
    if keyword_start >= end:
        return keyword_start - end
    return 0


def find_owner_contact(text: str) -> tuple[str, str]:
    """(name, phone) for the phone number closest to an ownership title.

    Scores every phone number by its distance to the nearest title keyword and
    picks the closest. Distance rather than document order matters, because a
    concatenation of several pages can easily put an unrelated front-desk
    number within OWNER_WINDOW characters of an "Owner" heading further down.

    Returns ("", "") when no phone sits within OWNER_WINDOW characters of a
    keyword, and ("", phone) when one does but no usable personal name is near.
    """
    if not text:
        return ("", "")
    spans = _keyword_spans(text.lower())
    if not spans:
        return ("", "")

    best_gap = None
    best = ("", "")
    for match in PHONE_RE.finditer(text):
        gap = min(_gap(span, match.start(), match.end()) for span in spans)
        if gap > OWNER_WINDOW:
            continue
        if best_gap is not None and gap >= best_gap:
            continue
        phone = normalize_phone(match.group(0))
        if not phone:
            continue

        before = text[max(0, match.start() - OWNER_WINDOW):match.start()]
        after = text[match.end():match.end() + OWNER_WINDOW]
        names_before = _names_in(before)
        name = names_before[-1] if names_before else ""
        if not name:
            names_after = _names_in(after)
            name = names_after[0] if names_after else ""

        best_gap, best = gap, (name, phone)
    return best
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_parsing.py -v`
Expected: 32 passed

- [ ] **Step 5: Commit**

```bash
git add scrape.py tests/test_parsing.py
git commit -m "feat: phone normalization and owner-contact heuristic"
```

---

### Task 5: Website enrichment (Stage 2)

**Files:**
- Modify: `scrape.py`
- Create: `tests/fixtures/club_site.html`
- Create: `tests/fixtures/club_about.html`
- Create: `tests/fixtures/club_contact.html`
- Test: `tests/test_parsing.py`

**Interfaces:**
- Consumes: `extract_emails` (Task 3), `extract_phones` and `find_owner_contact` (Task 4).
- Produces:
  - `html_to_text(html: str) -> str`
  - `find_contact_links(base_url: str, html: str, limit: int = 3) -> list[str]`
  - `extract_socials(html: str) -> dict[str, str]` with keys `instagram`, `facebook`, `linkedin`
  - `empty_enrichment() -> dict[str, str]`
  - `enrich_website(url: str, fetch_fn, listing_phone: str = "") -> dict[str, str]`
  - `make_fetcher(timeout: float = 15.0)` returning a `fetch(url) -> str` callable

`enrich_website` takes `fetch_fn` as a parameter rather than calling httpx
directly. That is what makes it testable offline: tests pass a dictionary-backed
fake, production passes `make_fetcher()`.

- [ ] **Step 1: Create the fixtures**

`tests/fixtures/club_site.html`:

```html
<html><body>
  <h1>Austin Padel Club</h1>
  <p>Email us at info@austinpadel.com</p>
  <nav>
    <a href="/about">About us</a>
    <a href="/contact-us">Contact</a>
    <a href="https://instagram.com/austinpadel">Instagram</a>
    <a href="https://www.facebook.com/austinpadel">Facebook</a>
    <a href="https://partner.example.com/contact">Partner site</a>
  </nav>
  <footer>Front desk: (512) 555-0100</footer>
</body></html>
```

`tests/fixtures/club_about.html`:

```html
<html><body><p>Established in 2019 on the east side.</p></body></html>
```

`tests/fixtures/club_contact.html`:

```html
<html><body>
  <h2>Contact</h2>
  <p>John Smith, Owner - (512) 555-0142</p>
  <p>Bookings: bookings@austinpadel.com</p>
  <a href="https://www.linkedin.com/company/austin-padel">LinkedIn</a>
</body></html>
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_parsing.py`:

```python
import pathlib

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def fixture(name):
    return (FIXTURES / name).read_text()


def fake_fetcher():
    pages = {
        "https://austinpadel.com": fixture("club_site.html"),
        "https://austinpadel.com/about": fixture("club_about.html"),
        "https://austinpadel.com/contact-us": fixture("club_contact.html"),
    }

    def fetch(url):
        if url not in pages:
            raise RuntimeError(f"unexpected fetch: {url}")
        return pages[url]

    return fetch


def test_find_contact_links_same_domain_only():
    links = scrape.find_contact_links(
        "https://austinpadel.com", fixture("club_site.html")
    )
    assert links == [
        "https://austinpadel.com/about",
        "https://austinpadel.com/contact-us",
    ]


def test_extract_socials_picks_first_of_each():
    html = fixture("club_site.html") + fixture("club_contact.html")
    assert scrape.extract_socials(html) == {
        "instagram": "https://instagram.com/austinpadel",
        "facebook": "https://www.facebook.com/austinpadel",
        "linkedin": "https://www.linkedin.com/company/austin-padel",
    }


def test_extract_socials_rejects_tracking_and_stub_urls():
    html = (
        '<a href="https://www.facebook.com/tr">a</a>'
        '<a href="https://www.facebook.com/profile.php">b</a>'
        '<a href="https://www.facebook.com/realclub">c</a>'
    )
    assert scrape.extract_socials(html)["facebook"] == "https://www.facebook.com/realclub"


def test_extract_socials_keeps_paths_that_only_start_like_junk():
    html = '<a href="https://www.facebook.com/trainers">t</a>'
    assert scrape.extract_socials(html)["facebook"] == "https://www.facebook.com/trainers"


def test_extract_socials_blank_when_absent():
    assert scrape.extract_socials("<html></html>") == {
        "instagram": "",
        "facebook": "",
        "linkedin": "",
    }


def test_enrich_website_gathers_everything():
    result = scrape.enrich_website("https://austinpadel.com", fake_fetcher())
    assert result["emails"] == "bookings@austinpadel.com; info@austinpadel.com"
    assert result["owner_name"] == "John Smith"
    assert result["owner_phone"] == "+15125550142"
    assert result["other_phones"] == "+15125550100"
    assert result["instagram"] == "https://instagram.com/austinpadel"
    assert result["enrich_error"] == ""


def test_enrich_website_excludes_the_listing_phone():
    result = scrape.enrich_website(
        "https://austinpadel.com", fake_fetcher(), listing_phone="+15125550100"
    )
    assert result["other_phones"] == ""


def test_enrich_website_records_fetch_failure():
    def boom(url):
        raise TimeoutError("timed out")

    result = scrape.enrich_website("https://dead.example", boom)
    assert "TimeoutError" in result["enrich_error"]
    assert result["emails"] == ""


def test_enrich_website_blank_url_returns_empty():
    assert scrape.enrich_website("", fake_fetcher()) == scrape.empty_enrichment()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_parsing.py -k "contact_links or socials or enrich" -v`
Expected: 9 failures with `AttributeError: module 'scrape' has no attribute 'find_contact_links'`

- [ ] **Step 4: Write the implementation**

Append to `scrape.py`:

```python
import httpx
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

CONTACT_LINK_RE = re.compile(r"contact|about|team|staff|coach", re.I)
SOCIAL_RES = {
    "instagram": re.compile(
        r"https?://(?:www\.)?instagram\.com/[A-Za-z0-9_.\-]+/?"
    ),
    "facebook": re.compile(
        r"https?://(?:www\.)?facebook\.com/[A-Za-z0-9_.\-]+/?"
    ),
    "linkedin": re.compile(
        r"https?://(?:www\.)?linkedin\.com/(?:company|in)/[A-Za-z0-9_.\-]+/?"
    ),
}
SOCIAL_JUNK_PATHS = {
    "tr", "sharer", "sharer.php", "share.php", "profile.php",
    "plugins", "intent", "dialog", "login", "home.php",
}

ENRICH_KEYS = (
    "emails", "owner_name", "owner_phone", "other_phones",
    "instagram", "facebook", "linkedin", "enrich_error",
)


def html_to_text(html: str) -> str:
    """Visible text of `html`, whitespace-collapsed."""
    return BeautifulSoup(html, "html.parser").get_text(" ", strip=True)


def find_contact_links(base_url: str, html: str, limit: int = 3) -> list[str]:
    """Up to `limit` same-domain contact-ish URLs linked from `html`."""
    soup = BeautifulSoup(html, "html.parser")
    base_host = urlparse(base_url).netloc.lower().removeprefix("www.")
    found: list[str] = []
    for anchor in soup.find_all("a", href=True):
        label = f"{anchor['href']} {anchor.get_text(' ', strip=True)}"
        if not CONTACT_LINK_RE.search(label):
            continue
        full = urljoin(base_url, anchor["href"]).split("#")[0]
        parsed = urlparse(full)
        if parsed.scheme not in ("http", "https"):
            continue
        if parsed.netloc.lower().removeprefix("www.") != base_host:
            continue
        if full.rstrip("/") == base_url.rstrip("/"):
            continue
        if full not in found:
            found.append(full)
        if len(found) >= limit:
            break
    return found


def _first_path_segment(url: str) -> str:
    """Lowercased first path segment of `url`, or "" when it has none."""
    return urlparse(url).path.strip("/").split("/")[0].lower()


def extract_socials(html: str) -> dict[str, str]:
    """First real instagram, facebook and linkedin profile URL in `html`.

    Rejects share widgets, tracking endpoints and stub URLs by first path
    segment rather than by substring: "facebook.com/tr" is a tracking pixel and
    must go, while "facebook.com/trainers" is a real page and must stay, so a
    substring test on "tr" would throw away good links.
    """
    result = {}
    for key, pattern in SOCIAL_RES.items():
        result[key] = ""
        for match in pattern.finditer(html):
            url = match.group(0)
            if _first_path_segment(url) in SOCIAL_JUNK_PATHS:
                continue
            result[key] = url
            break
    return result


def empty_enrichment() -> dict[str, str]:
    """All enrichment fields, blank."""
    return {key: "" for key in ENRICH_KEYS}


def make_fetcher(timeout: float = 15.0):
    """A fetch(url) -> html callable backed by a pooled httpx client."""
    client = httpx.Client(
        follow_redirects=True,
        timeout=timeout,
        headers={"User-Agent": USER_AGENT},
    )

    def fetch(url: str) -> str:
        response = client.get(url)
        response.raise_for_status()
        return response.text

    return fetch


def enrich_website(url: str, fetch_fn, listing_phone: str = "") -> dict[str, str]:
    """Contact details scraped from a club's own website.

    Fetches the homepage plus up to three contact-ish pages, then extracts
    emails, phones, an owner name/phone pair, and social links. Never raises:
    fetch failures land in the `enrich_error` field.
    """
    result = empty_enrichment()
    if not url:
        return result

    try:
        homepage = fetch_fn(url)
    except Exception as exc:
        result["enrich_error"] = f"{type(exc).__name__}: {exc}"[:200]
        return result

    pages = [homepage]
    for link in find_contact_links(url, homepage):
        try:
            pages.append(fetch_fn(link))
        except Exception:
            continue

    html = "\n".join(pages)
    text = html_to_text(html)

    result["emails"] = "; ".join(extract_emails(html))
    owner_name, owner_phone = find_owner_contact(text)
    result["owner_name"] = owner_name
    result["owner_phone"] = owner_phone
    result["other_phones"] = "; ".join(
        phone for phone in extract_phones(text)
        if phone not in (owner_phone, listing_phone)
    )
    result.update(extract_socials(html))
    return result
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_parsing.py -v`
Expected: 42 passed

- [ ] **Step 6: Commit**

```bash
git add scrape.py tests/
git commit -m "feat: website enrichment for emails, phones and socials"
```

---

### Task 6: Record cache and resume

**Files:**
- Modify: `scrape.py`
- Test: `tests/test_parsing.py`

**Interfaces:**
- Consumes: `CACHE_PATH`, `COLUMNS` (Task 1).
- Produces:
  - `append_record(record: dict, path: Path | None = None) -> None`
  - `mark_pair_done(term: str, state: str, path: Path | None = None) -> None`
  - `read_cache(path: Path | None = None) -> tuple[list[dict], set[tuple[str, str]]]`
  - `utc_now() -> str` returning an ISO 8601 UTC timestamp

The cache is one JSONL file holding two line types: `{"type": "record", ...}`
and `{"type": "pair", "term": ..., "state": ...}`. Records are deduplicated on
`place_key` with last-write-wins, which is what lets Stage 2 rewrite a record
in place by simply appending an enriched copy.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_parsing.py`:

```python
def test_append_and_read_record(tmp_path):
    cache = tmp_path / "cache.jsonl"
    scrape.append_record({"place_key": "0x1:0x2", "name": "Padel X"}, cache)
    records, pairs = scrape.read_cache(cache)
    assert records == [{"place_key": "0x1:0x2", "name": "Padel X"}]
    assert pairs == set()


def test_read_cache_dedupes_on_place_key_last_wins(tmp_path):
    cache = tmp_path / "cache.jsonl"
    scrape.append_record({"place_key": "k", "name": "Old", "emails": ""}, cache)
    scrape.append_record({"place_key": "k", "name": "Old", "emails": "a@b.c"}, cache)
    records, _ = scrape.read_cache(cache)
    assert len(records) == 1
    assert records[0]["emails"] == "a@b.c"


def test_mark_pair_done_round_trips(tmp_path):
    cache = tmp_path / "cache.jsonl"
    scrape.mark_pair_done("padel club", "Texas", cache)
    _, pairs = scrape.read_cache(cache)
    assert pairs == {("padel club", "Texas")}


def test_read_cache_skips_corrupt_lines(tmp_path):
    cache = tmp_path / "cache.jsonl"
    scrape.append_record({"place_key": "k", "name": "Good"}, cache)
    with cache.open("a") as fh:
        fh.write("{not json\n\n")
    records, _ = scrape.read_cache(cache)
    assert len(records) == 1


def test_read_cache_missing_file(tmp_path):
    assert scrape.read_cache(tmp_path / "nope.jsonl") == ([], set())


def test_utc_now_is_iso_8601():
    from datetime import datetime

    datetime.fromisoformat(scrape.utc_now())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_parsing.py -k "cache or pair or utc" -v`
Expected: 6 failures with `AttributeError: module 'scrape' has no attribute 'append_record'`

- [ ] **Step 3: Write the implementation**

Append to `scrape.py`:

```python
import json
from datetime import datetime, timezone


def utc_now() -> str:
    """Current UTC time as an ISO 8601 string, seconds precision."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _append_line(obj: dict, path: Path | None) -> None:
    target = path or CACHE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(obj, ensure_ascii=False) + "\n")


def append_record(record: dict, path: Path | None = None) -> None:
    """Append one club record to the cache."""
    _append_line({"type": "record", **record}, path)


def mark_pair_done(term: str, state: str, path: Path | None = None) -> None:
    """Record that a (term, state) query finished, so re-runs can skip it."""
    _append_line({"type": "pair", "term": term, "state": state}, path)


def read_cache(path: Path | None = None) -> tuple[list[dict], set[tuple[str, str]]]:
    """(records, completed_pairs) from the cache file.

    Records are deduplicated on place_key, last write wins. Malformed lines
    are skipped rather than aborting the run.
    """
    target = path or CACHE_PATH
    if not target.exists():
        return ([], set())

    records: dict[str, dict] = {}
    pairs: set[tuple[str, str]] = set()
    with target.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = obj.pop("type", None)
            if kind == "record":
                records[obj.get("place_key", "")] = obj
            elif kind == "pair":
                pairs.add((obj.get("term", ""), obj.get("state", "")))
    return (list(records.values()), pairs)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_parsing.py -v`
Expected: 48 passed

- [ ] **Step 5: Commit**

```bash
git add scrape.py tests/test_parsing.py
git commit -m "feat: JSONL record cache with resume support"
```

---

### Task 7: Stage 1, the Maps scraper

**Files:**
- Modify: `scrape.py`
- Test: `tests/test_parsing.py`

**Interfaces:**
- Consumes: `build_search_url`, `parse_place_key`, `parse_latlng`, `split_address` (Task 2), `normalize_phone` (Task 4), `append_record`, `mark_pair_done`, `read_cache`, `utc_now` (Task 6).
- Produces:
  - `clean_address_label(label: str) -> str`
  - `phone_from_item_id(item_id: str) -> str`
  - `parse_rating_block(block: str) -> tuple[float | None, int]`
  - `build_record(raw: dict, term: str, state: str, now: str) -> dict`
  - `run_stage1(terms: list[str], states: list[str], limit: int | None, headless: bool, force: bool) -> None`

The browser-touching code gathers raw strings and hands them to
`build_record`, which does all the shaping. That split is what makes Stage 1
testable: `build_record` is a pure function with full test coverage, and the
Playwright wrapper stays thin enough to verify by eye.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_parsing.py`:

```python
def test_clean_address_label_strips_prefix():
    label = "Address: 1234 Main St, Austin, TX 78701, United States"
    assert scrape.clean_address_label(label) == (
        "1234 Main St, Austin, TX 78701, United States"
    )


def test_clean_address_label_passthrough_and_empty():
    assert scrape.clean_address_label("500 Padel Way") == "500 Padel Way"
    assert scrape.clean_address_label("") == ""


def test_phone_from_item_id():
    assert scrape.phone_from_item_id("phone:tel:+1 512-555-0100") == "+15125550100"
    assert scrape.phone_from_item_id("") == ""


def test_parse_rating_block_variants():
    assert scrape.parse_rating_block("4.8(127)") == (4.8, 127)
    assert scrape.parse_rating_block("4.8\n(1,204)") == (4.8, 1204)
    assert scrape.parse_rating_block("5.0 stars 3 reviews") == (5.0, 3)
    assert scrape.parse_rating_block("") == (None, 0)
    assert scrape.parse_rating_block("No reviews") == (None, 0)


def test_build_record_shapes_every_column():
    raw = {
        "url": (
            "https://www.google.com/maps/place/Austin+Padel/"
            "data=!4m6!1s0x864b1a:0x9fe1!3d30.2672!4d-97.7431"
        ),
        "name": "Austin Padel Club",
        "category": "Padel club",
        "address_label": "Address: 1234 Main St, Austin, TX 78701, United States",
        "phone_item_id": "phone:tel:+1 512-555-0100",
        "website": "https://austinpadel.com",
        "rating_block": "4.8(127)",
    }
    record = scrape.build_record(raw, "padel club", "Texas", "2026-08-24T00:00:00+00:00")

    assert set(record) == set(scrape.COLUMNS)
    assert record["place_key"] == "0x864b1a:0x9fe1"
    assert record["name"] == "Austin Padel Club"
    assert record["city"] == "Austin"
    assert record["state"] == "TX"
    assert record["zip"] == "78701"
    assert record["phone"] == "+15125550100"
    assert record["rating"] == 4.8
    assert record["reviews"] == 127
    assert record["latitude"] == 30.2672
    assert record["search_state"] == "Texas"


def test_build_record_never_yields_an_empty_place_key():
    record = scrape.build_record({}, "padel club", "Ohio", "2026-08-24T00:00:00+00:00")
    assert record["place_key"]


def test_build_record_falls_back_when_no_place_key():
    raw = {"url": "https://www.google.com/maps", "name": "Nameless Club"}
    record = scrape.build_record(raw, "padel club", "Utah", "2026-08-24T00:00:00+00:00")
    assert record["place_key"] == "Nameless Club|None,None"
    assert record["rating"] == ""
    assert record["reviews"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_parsing.py -k "address_label or item_id or rating_block or build_record" -v`
Expected: 7 failures with `AttributeError: module 'scrape' has no attribute 'clean_address_label'`

- [ ] **Step 3: Write the pure helpers**

Append to `scrape.py`:

```python
ADDRESS_PREFIX_RE = re.compile(r"^\s*Address:\s*", re.I)
RATING_VALUE_RE = re.compile(r"\b(\d(?:\.\d)?)\b")
REVIEWS_PAREN_RE = re.compile(r"\(([\d,]+)\)")
REVIEWS_WORD_RE = re.compile(r"([\d,]+)\s*review", re.I)


def clean_address_label(label: str) -> str:
    """Address text from a Maps aria-label, minus the "Address:" prefix."""
    return ADDRESS_PREFIX_RE.sub("", label or "").strip()


def phone_from_item_id(item_id: str) -> str:
    """Normalized phone number from a data-item-id of "phone:tel:...."."""
    if not item_id:
        return ""
    return normalize_phone(item_id.split("phone:tel:")[-1])


def parse_rating_block(block: str) -> tuple[float | None, int]:
    """(rating, review_count) from the Maps header text, e.g. "4.8(127)"."""
    if not block:
        return (None, 0)
    rating_match = RATING_VALUE_RE.search(block)
    rating = float(rating_match.group(1)) if rating_match else None
    reviews_match = REVIEWS_PAREN_RE.search(block) or REVIEWS_WORD_RE.search(block)
    reviews = int(reviews_match.group(1).replace(",", "")) if reviews_match else 0
    return (rating, reviews)


def build_record(raw: dict, term: str, state: str, now: str) -> dict:
    """A fully shaped record with every COLUMNS key present.

    `place_key` is guaranteed non-empty: when a Maps URL carries no feature id,
    the name and coordinates stand in. `read_cache` deduplicates on this key and
    buckets every falsy key together, so an empty one would silently collapse
    unrelated clubs into a single row. The invariant is enforced here, at the
    only place records are created, rather than guarded again downstream.
    """
    url = raw.get("url", "")
    address = clean_address_label(raw.get("address_label", ""))
    city, state_code, postcode = split_address(address)
    latitude, longitude = parse_latlng(url)
    rating, reviews = parse_rating_block(raw.get("rating_block", ""))
    name = (raw.get("name") or "").strip()

    record = {column: "" for column in COLUMNS}
    record.update({
        "place_key": parse_place_key(url) or f"{name}|{latitude},{longitude}",
        "name": name,
        "category": (raw.get("category") or "").strip(),
        "address": address,
        "city": city,
        "state": state_code,
        "zip": postcode,
        "phone": phone_from_item_id(raw.get("phone_item_id", "")),
        "website": raw.get("website", "") or "",
        "rating": rating if rating is not None else "",
        "reviews": reviews,
        "latitude": latitude if latitude is not None else "",
        "longitude": longitude if longitude is not None else "",
        "maps_url": url,
        "search_term": term,
        "search_state": state,
        "scraped_at": now,
    })
    return record
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_parsing.py -v`
Expected: 55 passed

- [ ] **Step 5: Commit the pure helpers**

```bash
git add scrape.py tests/test_parsing.py
git commit -m "feat: listing record shaping helpers"
```

- [ ] **Step 6: Write the browser layer**

Append to `scrape.py`. Note the `SELECTORS` block: every Google-dependent
string lives here and nowhere else.

```python
import random
import time
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

SELECTORS = {
    "feed": 'div[role="feed"]',
    "result_link": 'a[href*="/maps/place/"]',
    "consent": 'button[aria-label*="Accept all"], form[action*="consent"] button',
    "name": "h1",
    "address": 'button[data-item-id="address"]',
    "phone": 'button[data-item-id^="phone:tel:"]',
    "website": 'a[data-item-id="authority"]',
    "category": 'button[jsaction*="category"]',
    "rating_block": "div.F7nice",
}
END_OF_LIST = "You've reached the end of the list"
BLOCK_MARKERS = ("unusual traffic", "not a robot", "/sorry/", "recaptcha")

PAUSE_LISTING = (1.0, 3.0)
PAUSE_QUERY = (5.0, 10.0)
SCROLL_ROUNDS = 40
STAGNANT_LIMIT = 3


def _pause(bounds: tuple[float, float]) -> None:
    time.sleep(random.uniform(*bounds))


def _text(page, selector: str) -> str:
    element = page.query_selector(selector)
    return (element.inner_text() or "").strip() if element else ""


def _attr(page, selector: str, name: str) -> str:
    element = page.query_selector(selector)
    if not element:
        return ""
    return element.get_attribute(name) or ""


def is_blocked(page) -> bool:
    """True when Google has served a CAPTCHA or unusual-traffic page."""
    haystack = (page.url + " " + page.content()[:4000]).lower()
    return any(marker in haystack for marker in BLOCK_MARKERS)


def accept_consent(page) -> None:
    """Dismiss the cookie interstitial if one is showing."""
    button = page.query_selector(SELECTORS["consent"])
    if button:
        button.click()
        page.wait_for_timeout(2000)


def collect_result_links(page) -> list[str]:
    """Scroll the results feed to the end and return every listing URL."""
    seen = 0
    stagnant = 0
    for _ in range(SCROLL_ROUNDS):
        feed = page.query_selector(SELECTORS["feed"])
        if not feed:
            break
        feed.evaluate("el => el.scrollTo(0, el.scrollHeight)")
        page.wait_for_timeout(1500)
        if END_OF_LIST in (feed.inner_text() or ""):
            break
        count = len(page.query_selector_all(SELECTORS["result_link"]))
        if count == seen:
            stagnant += 1
            if stagnant >= STAGNANT_LIMIT:
                break
        else:
            stagnant = 0
            seen = count

    links: list[str] = []
    for anchor in page.query_selector_all(SELECTORS["result_link"]):
        href = anchor.get_attribute("href") or ""
        if href and href not in links:
            links.append(href)
    return links


def scrape_listing(page, url: str, term: str, state: str) -> dict:
    """Open one listing and return its shaped record."""
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_selector(SELECTORS["name"], timeout=20000)
    raw = {
        "url": page.url,
        "name": _text(page, SELECTORS["name"]),
        "category": _text(page, SELECTORS["category"]),
        "address_label": _attr(page, SELECTORS["address"], "aria-label"),
        "phone_item_id": _attr(page, SELECTORS["phone"], "data-item-id"),
        "website": _attr(page, SELECTORS["website"], "href"),
        "rating_block": _text(page, SELECTORS["rating_block"]),
    }
    return build_record(raw, term, state, utc_now())


def scrape_query(page, term: str, state: str, limit: int | None) -> tuple[int, int]:
    """Scrape every listing for one (term, state) pair.

    Returns (scraped, failed). A non-zero failed count means the pair is
    incomplete and must not be marked done, or those listings are lost for good.
    """
    page.goto(build_search_url(term, state), wait_until="domcontentloaded", timeout=60000)
    accept_consent(page)
    if is_blocked(page):
        raise RuntimeError(f"blocked on {term} / {state} - rerun with --headed")

    try:
        page.wait_for_selector(SELECTORS["feed"], timeout=20000)
    except PWTimeout:
        print(f"  no results feed for {term} / {state}")
        return 0, 0

    links = collect_result_links(page)
    if limit:
        links = links[:limit]
    print(f"  {len(links)} listings for {term} / {state}")
    if len(links) >= 118:
        print("  WARNING: at the ~120 result cap; this state is undersampled")

    scraped = 0
    failed = 0
    for link in links:
        try:
            append_record(scrape_listing(page, link, term, state))
            scraped += 1
        except Exception as exc:
            failed += 1
            print(f"  skipped a listing: {type(exc).__name__}: {exc}")
        _pause(PAUSE_LISTING)
    return scraped, failed


def run_stage1(terms, states, limit=None, headless=True, force=False) -> None:
    """Scrape every (term, state) pair not already in the cache."""
    _, done = read_cache()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        context = browser.new_context(
            user_agent=USER_AGENT, viewport={"width": 1280, "height": 900}
        )
        page = context.new_page()
        for term in terms:
            for state in states:
                if not force and (term, state) in done:
                    print(f"skip (cached): {term} / {state}")
                    continue
                print(f"searching: {term} / {state}")
                try:
                    scraped, failed = scrape_query(page, term, state, limit)
                    if failed:
                        print(
                            f"  {failed} listing(s) failed; leaving "
                            f"{term} / {state} unmarked so a re-run retries it"
                        )
                    else:
                        mark_pair_done(term, state)
                except Exception as exc:
                    print(f"  FAILED {term} / {state}: {exc}")
                _pause(PAUSE_QUERY)
        browser.close()
```

- [ ] **Step 7: Verify the browser layer by hand**

Run: `python -c "import scrape; scrape.run_stage1(['padel club'], ['Texas'], limit=3, headless=False)"`

Expected: a browser opens, searches, and `data/cache.jsonl` gains three record
lines plus one pair line. Inspect them:

```bash
python -c "import scrape, json; r,p = scrape.read_cache(); print(json.dumps(r, indent=2)); print(p)"
```

Confirm `name`, `address`, `phone`, `website` and `maps_url` are populated. If
any is blank for every record, the corresponding entry in `SELECTORS` needs
updating against the live page — that is the expected maintenance point.

- [ ] **Step 8: Commit**

```bash
git add scrape.py
git commit -m "feat: Stage 1 Playwright Maps scraper"
```

---

### Task 8: Stage 3, the Google Sheets writer

**Files:**
- Modify: `scrape.py`
- Test: `tests/test_parsing.py`

**Interfaces:**
- Consumes: `COLUMNS`, `SHEET_URL`, `WORKSHEET` (Task 1).
- Produces:
  - `record_to_row(record: dict) -> list[str]`
  - `row_range(row_number: int) -> str`
  - `plan_upserts(existing: dict[str, int], records: list[dict]) -> tuple[list[tuple[int, list[str]]], list[list[str]]]`
  - `open_worksheet()` returning a `gspread.Worksheet`
  - `existing_keys(worksheet) -> dict[str, int]`
  - `write_records(records: list[dict]) -> None`
  - `check_auth() -> bool`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_parsing.py`:

```python
def test_record_to_row_follows_column_order():
    record = {"place_key": "k", "name": "Padel X", "reviews": 12}
    row = scrape.record_to_row(record)
    assert len(row) == len(scrape.COLUMNS)
    assert row[scrape.COLUMNS.index("place_key")] == "k"
    assert row[scrape.COLUMNS.index("name")] == "Padel X"
    assert row[scrape.COLUMNS.index("reviews")] == "12"
    assert row[scrape.COLUMNS.index("emails")] == ""


def test_record_to_row_renders_none_as_blank():
    assert scrape.record_to_row({"name": None})[scrape.COLUMNS.index("name")] == ""


def test_row_range_spans_every_column():
    assert scrape.row_range(2) == "A2:X2"


def test_check_auth_explains_insufficient_scopes(monkeypatch, capsys):
    def raise_permission_error():
        raise PermissionError()

    monkeypatch.setattr(scrape, "open_worksheet", raise_permission_error)
    assert scrape.check_auth() is False
    printed = capsys.readouterr().out
    assert "scopes" in printed
    assert "gcloud auth application-default login" in printed


def test_plan_upserts_appends_unknown_keys():
    updates, appends = scrape.plan_upserts({}, [{"place_key": "new"}])
    assert updates == []
    assert len(appends) == 1


def test_plan_upserts_updates_known_keys():
    updates, appends = scrape.plan_upserts(
        {"known": 7}, [{"place_key": "known", "name": "Padel X"}]
    )
    assert appends == []
    assert updates[0][0] == 7
    assert updates[0][1][scrape.COLUMNS.index("name")] == "Padel X"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_parsing.py -k "record_to_row or row_range or upserts" -v`
Expected: 6 failures with `AttributeError: module 'scrape' has no attribute 'record_to_row'`

- [ ] **Step 3: Write the implementation**

Append to `scrape.py`:

```python
import google.auth
import gspread
from google.auth.exceptions import DefaultCredentialsError, RefreshError
from gspread.utils import rowcol_to_a1

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]
SHEET_CHUNK = 500

ADC_HINT = (
    "Authenticate once with:\n"
    "  gcloud auth application-default login \\\n"
    "    --scopes=https://www.googleapis.com/auth/spreadsheets,"
    "https://www.googleapis.com/auth/drive.readonly"
)


def record_to_row(record: dict) -> list[str]:
    """A record rendered as a row in COLUMNS order."""
    return [
        "" if record.get(column) is None else str(record.get(column, ""))
        for column in COLUMNS
    ]


def row_range(row_number: int) -> str:
    """A1 range covering every column of one row, e.g. "A2:X2"."""
    start = rowcol_to_a1(row_number, 1)
    end = rowcol_to_a1(row_number, len(COLUMNS))
    return f"{start}:{end}"


def plan_upserts(existing, records):
    """Split records into (row_number, row) updates and new-row appends."""
    updates, appends = [], []
    for record in records:
        row = record_to_row(record)
        row_number = existing.get(record.get("place_key", ""))
        if row_number:
            updates.append((row_number, row))
        else:
            appends.append(row)
    return updates, appends


def open_worksheet():
    """The target worksheet, created with a header row if it is missing.

    Uses Application Default Credentials, so the script acts as the Google
    account that ran `gcloud auth application-default login`. That account
    already owns the sheet, so no sharing step is needed.
    """
    credentials, _ = google.auth.default(scopes=SCOPES)
    client = gspread.authorize(credentials)
    spreadsheet = client.open_by_url(SHEET_URL)
    try:
        worksheet = spreadsheet.worksheet(WORKSHEET)
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(
            title=WORKSHEET, rows=2000, cols=len(COLUMNS)
        )
    if worksheet.row_values(1) != COLUMNS:
        worksheet.update(range_name="A1", values=[COLUMNS])
    return worksheet


def existing_keys(worksheet) -> dict[str, int]:
    """Map of place_key to its 1-based sheet row number."""
    column = worksheet.col_values(1)
    return {
        key: number
        for number, key in enumerate(column, start=1)
        if number > 1 and key
    }


def write_records(records: list[dict]) -> None:
    """Upsert every record into the sheet, batched to respect quotas."""
    if not records:
        return
    worksheet = open_worksheet()
    updates, appends = plan_upserts(existing_keys(worksheet), records)

    for start in range(0, len(updates), SHEET_CHUNK):
        worksheet.batch_update([
            {"range": row_range(number), "values": [row]}
            for number, row in updates[start:start + SHEET_CHUNK]
        ])
    for start in range(0, len(appends), SHEET_CHUNK):
        worksheet.append_rows(
            appends[start:start + SHEET_CHUNK], value_input_option="RAW"
        )
    print(f"  sheet: {len(updates)} updated, {len(appends)} added")


def check_auth() -> bool:
    """Verify credentials and sheet access, explaining any failure."""
    try:
        worksheet = open_worksheet()
    except DefaultCredentialsError:
        print("No Application Default Credentials found.")
        print(ADC_HINT)
        return False
    except RefreshError:
        print("Stored credentials have expired or been revoked.")
        print(ADC_HINT)
        return False
    except PermissionError:
        print("Authenticated, but these credentials lack the required scopes.")
        print("This is what `gcloud auth application-default login` produces when")
        print("run without --scopes: user credentials cannot be re-scoped after")
        print("the fact, so the scopes must be granted at login time.")
        print(ADC_HINT)
        return False
    except gspread.SpreadsheetNotFound:
        print("Sheet not found. Check SHEET_URL, and confirm you authenticated")
        print("as the Google account that can open it.")
        return False
    except gspread.exceptions.APIError as exc:
        print(f"Google API error: {exc}")
        print("Enable both APIs and set a quota project:")
        print("  gcloud services enable sheets.googleapis.com drive.googleapis.com")
        print("  gcloud auth application-default set-quota-project YOUR_PROJECT_ID")
        return False
    print(f"OK: '{worksheet.spreadsheet.title}' / worksheet '{worksheet.title}'")
    return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_parsing.py -v`
Expected: 61 passed

- [ ] **Step 5: Verify auth against the real sheet**

Run: `python -c "import scrape; scrape.check_auth()"`

Expected: `OK: '<sheet title>' / worksheet 'clubs'`. If it reports missing
credentials, run the `gcloud auth application-default login` command from the
README first.

- [ ] **Step 6: Commit**

```bash
git add scrape.py tests/test_parsing.py
git commit -m "feat: Google Sheets upsert writer"
```

---

### Task 9: CLI, orchestration and the fill-rate report

**Files:**
- Modify: `scrape.py`
- Test: `tests/test_parsing.py`

**Interfaces:**
- Consumes: everything from Tasks 1-8.
- Produces:
  - `parse_list_arg(value: str | None, default: list[str]) -> list[str]`
  - `fill_rate(records: list[dict]) -> dict[str, float]`
  - `write_csv(records: list[dict], path: Path | None = None) -> None`
  - `run_stage2(force: bool = False) -> None`
  - `main(argv: list[str] | None = None) -> int`

The fill-rate report is the spec's mitigation for silent selector rot (spec
section 14). A scraper whose selectors have broken does not crash; it returns
blanks. Printing the percentage of non-empty values per column at the end of
every run turns that silent failure into something you notice immediately.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_parsing.py`:

```python
def test_parse_list_arg_returns_default_when_blank():
    assert scrape.parse_list_arg(None, ["a", "b"]) == ["a", "b"]
    assert scrape.parse_list_arg("", ["a"]) == ["a"]


def test_parse_list_arg_splits_and_strips():
    assert scrape.parse_list_arg("Texas, New York ,", ["x"]) == ["Texas", "New York"]


def test_fill_rate_counts_non_empty_values():
    records = [
        {"place_key": "a", "phone": "+15125550100"},
        {"place_key": "b", "phone": ""},
    ]
    rates = scrape.fill_rate(records)
    assert rates["place_key"] == 1.0
    assert rates["phone"] == 0.5
    assert rates["emails"] == 0.0


def test_fill_rate_empty_input():
    assert scrape.fill_rate([]) == {}


def test_run_stage2_records_a_crash_instead_of_aborting(tmp_path, monkeypatch, capsys):
    cache = tmp_path / "cache.jsonl"
    monkeypatch.setattr(scrape, "CACHE_PATH", cache)
    scrape.append_record(
        {"place_key": "k", "name": "Boom Club", "website": "https://boom.example"},
        cache,
    )

    def exploding(*args, **kwargs):
        raise ValueError("parser blew up")

    monkeypatch.setattr(scrape, "make_fetcher", lambda *a, **k: (lambda url: ""))
    monkeypatch.setattr(scrape, "enrich_website", exploding)

    scrape.run_stage2()

    records, _ = scrape.read_cache(cache)
    assert records[0]["enriched_at"]
    assert "ValueError" in records[0]["enrich_error"]


def test_write_csv_round_trips(tmp_path):
    import csv

    target = tmp_path / "out.csv"
    scrape.write_csv([{"place_key": "k", "name": "Padel X"}], target)
    with target.open() as handle:
        rows = list(csv.reader(handle))
    assert rows[0] == scrape.COLUMNS
    assert rows[1][scrape.COLUMNS.index("name")] == "Padel X"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_parsing.py -k "list_arg or fill_rate or write_csv" -v`
Expected: 6 failures with `AttributeError: module 'scrape' has no attribute 'parse_list_arg'`

- [ ] **Step 3: Write the implementation**

Append to `scrape.py`:

```python
import argparse
import csv


def parse_list_arg(value: str | None, default: list[str]) -> list[str]:
    """A comma-separated CLI value, or `default` when it is blank."""
    if not value:
        return list(default)
    return [item.strip() for item in value.split(",") if item.strip()]


def fill_rate(records: list[dict]) -> dict[str, float]:
    """Fraction of records with a non-empty value, per column."""
    if not records:
        return {}
    total = len(records)
    return {
        column: sum(
            1 for record in records if str(record.get(column, "")).strip()
        ) / total
        for column in COLUMNS
    }


def write_csv(records: list[dict], path: Path | None = None) -> None:
    """Write every record to CSV as a local backup."""
    target = path or CSV_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        writer.writerows(record_to_row(record) for record in records)


def run_stage2(force: bool = False) -> None:
    """Enrich every cached record that has a website and is not yet enriched."""
    records, _ = read_cache()
    targets = [
        record for record in records
        if record.get("website") and (force or not record.get("enriched_at"))
    ]
    if not targets:
        print("nothing to enrich")
        return

    print(f"enriching {len(targets)} club websites")
    fetch = make_fetcher()
    for index, record in enumerate(targets, start=1):
        try:
            enrichment = enrich_website(
                record["website"], fetch, record.get("phone", "")
            )
        except Exception as exc:
            enrichment = empty_enrichment()
            enrichment["enrich_error"] = f"{type(exc).__name__}: {exc}"[:200]
            print(f"  enrichment crashed for {record.get('name', '?')}: {exc}")
        updated = dict(record)
        updated.update(
            {k: v for k, v in enrichment.items() if k in COLUMNS}
        )
        updated["enrich_error"] = enrichment["enrich_error"]
        updated["enriched_at"] = utc_now()
        append_record(updated)
        if index % 25 == 0:
            print(f"  {index}/{len(targets)}")


def print_fill_rate(records: list[dict]) -> None:
    """Print per-column fill rates; near-zero values mean a broken selector."""
    rates = fill_rate(records)
    if not rates:
        return
    print("\nfill rate by column:")
    for column in COLUMNS:
        print(f"  {column:<14} {rates[column]:>6.1%}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scrape club listings from Google Maps into a Google Sheet."
    )
    parser.add_argument("--terms", help="comma-separated search terms")
    parser.add_argument("--states", help="comma-separated states, e.g. Texas,Florida")
    parser.add_argument("--limit", type=int, help="max listings per query")
    parser.add_argument("--no-enrich", action="store_true", help="skip Stage 2")
    parser.add_argument("--sheets-only", action="store_true", help="push cache only")
    parser.add_argument("--headed", action="store_true", help="visible browser")
    parser.add_argument("--force", action="store_true", help="ignore the cache")
    parser.add_argument("--check-auth", action="store_true", help="test Sheets access")
    args = parser.parse_args(argv)

    if args.check_auth:
        return 0 if check_auth() else 1

    if not args.sheets_only:
        run_stage1(
            parse_list_arg(args.terms, SEARCH_TERMS),
            parse_list_arg(args.states, STATES),
            limit=args.limit,
            headless=not args.headed,
            force=args.force,
        )
        if ENRICH_SITES and not args.no_enrich:
            run_stage2(force=args.force)

    records, _ = read_cache()
    write_csv(records)
    print(f"\n{len(records)} records -> {CSV_PATH}")
    print_fill_rate(records)
    try:
        write_records(records)
    except Exception as exc:
        print(f"\nSheets write failed: {type(exc).__name__}: {exc}")
        print(f"Every record is safe in {CSV_PATH}. Diagnose with:")
        print("  python scrape.py --check-auth")
        print("then re-push with:  python scrape.py --sheets-only")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_parsing.py -v`
Expected: 67 passed

- [ ] **Step 5: Verify the CLI**

Run: `python scrape.py --help`
Expected: usage text listing all eight flags.

- [ ] **Step 6: Commit**

```bash
git add scrape.py tests/test_parsing.py
git commit -m "feat: CLI, stage orchestration and fill-rate report"
```

---

### Task 10: README and end-to-end verification

**Files:**
- Create: `README.md`
- Test: a live smoke run

**Interfaces:**
- Consumes: the finished `scrape.py`.
- Produces: no code.

- [ ] **Step 1: Write `README.md`**

````markdown
# Google Maps Club Scraper

Scrapes club listings from Google Maps across US states, enriches them with
contact details from each club's own website, and upserts the results into a
Google Sheet.

## Install

```bash
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt
playwright install chromium
```

## Google Sheets access

The script authenticates as *you* through Application Default Credentials. No
key file is ever downloaded into the project, and because you already own the
sheet there is no sharing step.

1. Install the gcloud CLI:

   ```bash
   brew install --cask google-cloud-sdk
   ```

2. Create a project at <https://console.cloud.google.com/projectcreate>, then
   log in and select it:

   ```bash
   gcloud auth login
   gcloud config set project YOUR_PROJECT_ID
   ```

3. Enable both APIs. Enabling only Sheets is the most common mistake, because
   opening a sheet by URL goes through Drive:

   ```bash
   gcloud services enable sheets.googleapis.com drive.googleapis.com
   ```

4. Authorize the scopes the script needs. **The scopes must be granted at
   login time** - user credentials cannot be re-scoped afterwards, so omitting
   them here produces a permission error at write time, not at login:

   ```bash
   gcloud auth application-default login \
     --scopes=https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/spreadsheets,https://www.googleapis.com/auth/drive.readonly
   gcloud auth application-default set-quota-project YOUR_PROJECT_ID
   ```

   A browser opens once; approve with the account that owns the sheet. The
   token is stored under `~/.config/gcloud/`, outside this project. The
   `set-quota-project` line is optional - if it errors, skip it.

5. Verify:

   ```bash
   python scrape.py --check-auth
   ```

If the credentials are later revoked or expire, re-run step 4.

## Configure

Edit the `CONFIG` block at the top of `scrape.py`:

- `SEARCH_TERMS` - the phrases to search. Each one is run against every state.
- `STATES` - defaults to all 50. Narrow it for a faster run.
- `SHEET_URL`, `WORKSHEET` - where results land.
- `ENRICH_SITES` - set `False` to skip website enrichment.

## Run

```bash
python scrape.py                      # everything
python scrape.py --states Texas --limit 5   # smoke test
python scrape.py --no-enrich          # Maps only
python scrape.py --sheets-only        # push the existing cache
python scrape.py --headed             # visible browser, to solve a CAPTCHA
python scrape.py --force              # re-scrape cached queries
```

Progress is written to `data/cache.jsonl` as it happens, so an interrupted run
resumes where it stopped. `data/results.csv` is always written as a backup.

## Reading the output

Each run ends with a fill-rate table. If a column that is normally populated
drops to near 0%, Google has changed its markup and the matching entry in the
`SELECTORS` block in `scrape.py` needs updating. That is the expected
maintenance point.

`owner_name` and `owner_phone` are populated only when a club publishes a name
next to a phone number under an ownership title. Expect 20-35% coverage; the
rest of the rows rely on `phone`, `emails` and `other_phones`.

A per-state count of 118 or more means that state hit Google's roughly
120-result cap and is undersampled. Re-run it with narrower searches, for
example `--terms "padel club Dallas","padel club Houston" --states Texas`.

## Tests

```bash
pytest -v
```

Tests cover every parsing function and never touch the network or a browser.

## Caveats

Scraping google.com/maps is against Google's Terms of Service. The script
paces itself conservatively and makes no attempt to defeat anti-bot measures,
but Google may still serve a CAPTCHA. Re-run with `--headed` to solve it by
hand; the cache means nothing already scraped is lost.
````

- [ ] **Step 2: Run the full test suite**

Run: `pytest -v`
Expected: 67 passed

- [ ] **Step 3: Run an end-to-end smoke test**

```bash
rm -f data/cache.jsonl
python scrape.py --terms "padel club" --states Utah --limit 3
```

Expected:
- three listings scraped and printed
- enrichment runs for any with a website
- `data/results.csv` contains three rows plus a header
- the fill-rate table shows `name`, `address` and `maps_url` at 100%
- the sheet gains three rows under the header

- [ ] **Step 4: Verify the upsert is idempotent**

Run the same command again.

Expected: the query is skipped as cached, and the sheet still has three rows
rather than six. This confirms `plan_upserts` matched on `place_key`.

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: setup, usage and maintenance notes"
```

---

## Verification checklist

- [ ] `pytest -v` reports 67 passed
- [ ] `python scrape.py --check-auth` reports OK
- [ ] A smoke run writes rows to the sheet
- [ ] A repeat run updates rather than duplicates those rows
- [ ] `git status` shows `data/` untracked and no credential file in the repo
