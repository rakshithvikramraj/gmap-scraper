"""Google Maps club scraper -> Google Sheets.

Run `python scrape.py --help` for usage.
"""

import json
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus, unquote, urljoin, urlparse

import google.auth
import gspread
import httpx
from bs4 import BeautifulSoup
from google.auth.exceptions import DefaultCredentialsError, RefreshError
from gspread.utils import rowcol_to_a1
from playwright.sync_api import TimeoutError as PWTimeout, sync_playwright

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
        candidate = unquote(match.group(1))
        if EMAIL_RE.fullmatch(candidate):
            found.add(candidate)
    return sorted(
        {e.lower() for e in found if not is_junk_email(e)}
    )


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
    title word sitting directly against the name.

    The whitespace-only gap check stops two survivors pairing across a
    filtered word: "John Smith, Owner Bob Jones" yields ["John Smith", "Bob Jones"],
    never "Smith Bob".
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


def _first_path_segment(url: str) -> str:
    """Lowercased first path segment of `url`, or "" when it has none."""
    return urlparse(url).path.strip("/").split("/")[0].lower()


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
            print(f"  skipped a listing: {type(exc).__name__}: {exc}")
            failed += 1
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
