"""Google Maps club scraper -> Google Sheets.

Run `python scrape.py --help` for usage.
"""

import argparse
import csv
import json
import random
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus, unquote, urljoin, urlparse

import google.auth
import gspread
import httpx
import phonenumbers
from bs4 import BeautifulSoup
from google.auth.exceptions import DefaultCredentialsError, RefreshError
from gspread.utils import rowcol_to_a1
from playwright.sync_api import TimeoutError as PWTimeout, sync_playwright

import geo
import paths

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
    "country", "phone", "website", "rating", "reviews", "latitude",
    "longitude", "maps_url", "emails", "owner_name", "owner_phone",
    "other_phones", "instagram", "facebook", "linkedin", "search_term",
    "search_country", "search_state", "search_city", "scraped_at",
    "enrich_error",
]

STAGE1_COLUMNS = [
    "place_key", "name", "category", "address", "city", "state", "zip",
    "country", "phone", "website", "rating", "reviews", "latitude",
    "longitude", "maps_url", "search_term", "search_country", "search_state",
    "search_city", "scraped_at",
]

# Resolved rather than hardcoded: a frozen bundle has no working directory
# to be relative to. From a checkout this is still exactly Path("data").
DATA_DIR = paths.data_dir()
CACHE_PATH = DATA_DIR / "cache.jsonl"
CSV_PATH = DATA_DIR / "results.csv"

# ---------------------------------------------------------------------------
# Parsing functions
# ---------------------------------------------------------------------------

# gl is filled per query; hl stays English in every country so the page text
# the SELECTORS block matches on does not change under us.
MAPS_SEARCH_URL = "https://www.google.com/maps/search/{query}?hl=en&gl={gl}"

DEFAULT_GL = "us"

PLACE_KEY_RE = re.compile(r"!1s(0x[0-9a-f]+:0x[0-9a-f]+)", re.I)
LATLNG_RE = re.compile(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)")
ADDR_TAIL_RE = re.compile(
    r",\s*([A-Za-z .'\-]+),\s*([A-Z]{2})\s+(\d{5})(?:-\d{4})?\b"
)


def build_search_url(term: str, place: "geo.Place") -> str:
    """URL for a Maps search of `term` within `place`.

    An empty place searches the bare term, which is what a caller with no
    geography selected means.
    """
    location = place.query_text()
    query = f"{term} in {location}" if location else term
    return MAPS_SEARCH_URL.format(
        query=quote_plus(query),
        gl=geo.country_code(place.country) or DEFAULT_GL,
    )


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


def normalize_phone(text: str, region: str = "US") -> str:
    """`text` as an E.164 number, or "" if it is not a real number there.

    Validation, not pattern matching. The regex this replaced turned a
    product code into +14567890123, because thirteen digits look like a phone
    number to a pattern and like nothing at all to a validator.

    `region` decides what a number without a + prefix means; a number that
    carries its own country code ignores it.
    """
    try:
        parsed = phonenumbers.parse(text, region or "US")
    except phonenumbers.NumberParseException:
        return ""
    if not phonenumbers.is_valid_number(parsed):
        return ""
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def extract_phones(text: str, region: str = "US") -> list[str]:
    """Valid phone numbers in `text`, E.164, deduplicated, in order.

    PhoneNumberMatcher, not a regex. A US-shaped regex never matched
    "022 2822 1234", so pairing it with an international validator would
    report no phones at all for a business outside the US. The matcher
    scans free text for any format valid in `region`, and rejects SKUs and
    order numbers on its own.
    """
    seen: list[str] = []
    for match in phonenumbers.PhoneNumberMatcher(text, region or "US"):
        number = phonenumbers.format_number(
            match.number, phonenumbers.PhoneNumberFormat.E164
        )
        if number not in seen:
            seen.append(number)
    return seen


def _names_in(fragment: str) -> list[str]:
    """Maximal Title Case name runs in `fragment`, in order, skipping titles.

    Scans tokens instead of matching pairs directly. A pair regex is greedy:
    on "Owner Maria Lopez" it matches ("Owner", "Maria"), the stopword filter
    rejects that pair, and the scan resumes past "Maria" -- so the real name is
    never seen. Filtering tokens first and pairing adjacent survivors handles a
    title word sitting directly against the name.

    The whitespace-only gap check stops two survivors pairing across a
    filtered word: "John Smith, Owner Bob Jones" yields ["John Smith", "Bob Jones"],
    never "Smith Bob".

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


def find_owner_contact(text: str, region: str = "US") -> tuple[str, str]:
    """(name, phone) for the phone number closest to an ownership title.

    Scores every phone number by its distance to the nearest title keyword and
    picks the closest. Distance rather than document order matters, because a
    concatenation of several pages can easily put an unrelated front-desk
    number within OWNER_WINDOW characters of an "Owner" heading further down.

    PhoneNumberMatcher, not a regex, finds the candidates -- the same
    international coverage extract_phones uses. A US-only regex here would
    silently lose both the number and the name for a business outside the US:
    the name search is anchored on the phone match's position, so a candidate
    finder that never matches "022 2822 1234" costs the owner's name too, not
    just their number.

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
    for match in phonenumbers.PhoneNumberMatcher(text, region or "US"):
        gap = min(_gap(span, match.start, match.end) for span in spans)
        if gap > OWNER_WINDOW:
            continue
        if best_gap is not None and gap >= best_gap:
            continue
        phone = phonenumbers.format_number(
            match.number, phonenumbers.PhoneNumberFormat.E164
        )

        before = text[max(0, match.start - OWNER_WINDOW):match.start]
        after = text[match.end:match.end + OWNER_WINDOW]
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
MAX_PAGE_BYTES = 3_000_000

CONTACT_LINK_RE = re.compile(r"contact|about|team|staff|coach", re.I)
SOCIAL_RES = {
    "instagram": re.compile(
        r"https?://(?:www\.)?instagram\.com/[A-Za-z0-9_.\-]+/?"
    ),
    "facebook": re.compile(
        r"https?://(?:www\.)?facebook\.com/"
        r"(?:p/|pages/[^/\s\"'<>]+/|people/[^/\s\"'<>]+/)?"
        r"[A-Za-z0-9_.\-]+/?"
    ),
    "linkedin": re.compile(
        r"https?://(?:www\.)?linkedin\.com/(?:company|in)/[A-Za-z0-9_.\-]+/?"
    ),
}
SOCIAL_PREFIX_PATHS = {"p", "pages", "people"}
SOCIAL_JUNK_PATHS = {
    "tr", "sharer", "sharer.php", "share.php", "profile.php",
    "plugins", "intent", "dialog", "login", "home.php",
    "reel", "reels", "explore", "stories", "tv", "share", "watch",
    "groups", "events", "hashtag", "photo", "photos",
}


def _is_usable_social(url: str) -> bool:
    """True when `url` identifies a specific profile rather than a stub.

    A prefix segment such as Facebook's /p/ is only meaningful when a name
    follows it: "facebook.com/p/SLC-Padel-100086" is a real page, while a
    bare "facebook.com/p/" is what a truncated match leaves behind. Because
    the Instagram pattern stays single-segment, an Instagram post link can
    only ever produce that bare form, so it is rejected here.
    """
    segments = [s for s in urlparse(url).path.split("/") if s]
    if not segments:
        return False
    first = segments[0].lower()
    if first in SOCIAL_PREFIX_PATHS:
        return len(segments) >= 2
    return first not in SOCIAL_JUNK_PATHS


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
            if not _is_usable_social(url):
                continue
            result[key] = url
            break
    return result


def empty_enrichment() -> dict[str, str]:
    """All enrichment fields, blank."""
    return {key: "" for key in ENRICH_KEYS}


def make_fetcher(timeout: float = 15.0):
    """A fetch(url) -> html callable backed by a pooled httpx client.

    Streams with a byte cap and a content-type check: a club's "website" can
    be anything, and Stage 2 is a single-threaded loop with no watchdog, so
    one slow or oversized response would otherwise stall the whole stage.
    """
    client = httpx.Client(
        follow_redirects=True,
        timeout=httpx.Timeout(timeout, connect=10.0),
        headers={"User-Agent": USER_AGENT},
    )

    def fetch(url: str) -> str:
        with client.stream("GET", url) as response:
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if not (content_type.startswith("text/") or "html" in content_type):
                raise ValueError(f"not a web page: {content_type or 'unknown type'}")
            body = b""
            for chunk in response.iter_bytes():
                body += chunk
                if len(body) > MAX_PAGE_BYTES:
                    raise ValueError(f"page exceeded {MAX_PAGE_BYTES} bytes")
            return body.decode(response.encoding or "utf-8", errors="replace")

    return fetch


def enrich_website(
    url: str, fetch_fn, listing_phone: str = "", region: str = "US"
) -> dict[str, str]:
    """Contact details scraped from a club's own website.

    Fetches the homepage plus up to three contact-ish pages, then extracts
    emails, phones, an owner name/phone pair, and social links. Never raises:
    fetch failures land in the `enrich_error` field.

    `region` is the ISO2 code every phone lookup in this function -- the
    owner name/phone search and the general phone scan alike -- falls back
    to for a number without its own country code, e.g. "IN" for a search
    that targeted India. Without it, an owner's locally-formatted number is
    found by `extract_phones` for `other_phones` but missed by
    `find_owner_contact`, which loses the owner's name too since the name
    search is anchored on the phone match.
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
    owner_name, owner_phone = find_owner_contact(text, region=region)
    result["owner_name"] = owner_name
    result["owner_phone"] = owner_phone
    result["other_phones"] = "; ".join(
        phone for phone in extract_phones(text, region=region)
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


LEGACY_COUNTRY = "United States"


def pair_key(term: str, place: "geo.Place") -> tuple[str, str, str, str]:
    """Identity of one search, for the resume set."""
    return (term, *place.key())


def mark_pair_done(term: str, place: "geo.Place", path: Path | None = None) -> None:
    """Record that a (term, place) search finished, so re-runs can skip it."""
    _append_line({"type": "pair", "term": term, "country": place.country,
                  "state": place.region, "city": place.city}, path)


def read_cache(path: Path | None = None) -> tuple[list[dict], set[tuple[str, str, str, str]]]:
    """(records, completed_pairs) from the cache file.

    Records are deduplicated on place_key, last write wins. Malformed lines
    are skipped rather than aborting the run.
    """
    target = path or CACHE_PATH
    if not target.exists():
        return ([], set())

    records: dict[str, dict] = {}
    pairs: set[tuple[str, str, str, str]] = set()
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
                key = obj.get("place_key", "")
                records[key] = {**records.get(key, {}), **obj}
            elif kind == "pair":
                # Markers written before worldwide support carry no "country"
                # key at all, and every one of those runs was US-only -- that
                # missing key, not a falsy value, is what means "legacy".
                # `mark_pair_done` always writes the key, even as "" for a
                # malformed place, so a present-but-empty country must NOT
                # hit this fallback: doing so made it indistinguishable from
                # a legacy marker and the pair could never resolve as done.
                pairs.add((
                    obj.get("term", ""),
                    obj["country"] if "country" in obj else LEGACY_COUNTRY,
                    obj.get("state", ""),
                    obj.get("city", ""),
                ))
    return (list(records.values()), pairs)


ADDRESS_PREFIX_RE = re.compile(r"^\s*Address:\s*", re.I)
RATING_VALUE_RE = re.compile(r"\b(\d(?:\.\d)?)\b")
REVIEWS_PAREN_RE = re.compile(r"\(([\d,]+)\)")
REVIEWS_WORD_RE = re.compile(r"([\d,]+)\s*review", re.I)


def clean_address_label(label: str) -> str:
    """Address text from a Maps aria-label, minus the "Address:" prefix."""
    return ADDRESS_PREFIX_RE.sub("", label or "").strip()


def phone_from_item_id(item_id: str, region: str = "US") -> str:
    """Normalized phone number from a data-item-id of "phone:tel:....".

    `region` is the ISO2 fallback normalize_phone falls back to for a number
    with no country code of its own. The caller passes the place being
    searched rather than leaving this at the US default -- otherwise a
    locally-formatted listing phone outside the US normalizes to "" even
    though the same digits are valid there, e.g. an Indian "022 2822 1234"
    or a UK "020 7930 4832".
    """
    if not item_id:
        return ""
    return normalize_phone(item_id.split("phone:tel:")[-1], region)


def parse_rating_block(block: str) -> tuple[float | None, int]:
    """(rating, review_count) from the Maps header text, e.g. "4.8(127)"."""
    if not block:
        return (None, 0)
    rating_match = RATING_VALUE_RE.search(block)
    rating = float(rating_match.group(1)) if rating_match else None
    reviews_match = REVIEWS_PAREN_RE.search(block) or REVIEWS_WORD_RE.search(block)
    reviews = int(reviews_match.group(1).replace(",", "")) if reviews_match else 0
    return (rating, reviews)


def build_record(raw: dict, term: str, place: "geo.Place", now: str) -> dict:
    """A fully shaped record with every STAGE1_COLUMNS key present.

    `place_key` is guaranteed non-empty: when a Maps URL carries no feature id,
    the name and coordinates stand in. `read_cache` deduplicates on this key and
    buckets every falsy key together, so an empty one would silently collapse
    unrelated clubs into a single row. The invariant is enforced here, at the
    only place records are created, rather than guarded again downstream.
    """
    url = raw.get("url", "")
    address = clean_address_label(raw.get("address_label", ""))
    address_city, address_state, postcode = split_address(address)
    latitude, longitude = parse_latlng(url)
    rating, reviews = parse_rating_block(raw.get("rating_block", ""))
    name = (raw.get("name") or "").strip()

    record = {column: "" for column in STAGE1_COLUMNS}
    record.update({
        "place_key": parse_place_key(url) or f"{name}|{latitude},{longitude}",
        "name": name,
        "category": (raw.get("category") or "").strip(),
        "address": address,
        # The query is authoritative: when we searched "in Mumbai", the city
        # is an input, not something to infer from a foreign address format.
        # Only a whole-region or whole-country run falls back to parsing.
        "city": place.city or address_city,
        "state": place.region or address_state,
        "zip": postcode,
        "country": place.country,
        "phone": phone_from_item_id(
            raw.get("phone_item_id", ""),
            geo.country_code(place.country).upper() or "US",
        ),
        "website": raw.get("website", "") or "",
        "rating": rating if rating is not None else "",
        "reviews": reviews,
        "latitude": latitude if latitude is not None else "",
        "longitude": longitude if longitude is not None else "",
        "maps_url": url,
        "search_term": term,
        "search_country": place.country,
        "search_state": place.region,
        "search_city": place.city,
        "scraped_at": now,
    })
    return record


# ---------------------------------------------------------------------------
# Event hook - lets a GUI observe a run without parsing printed output
# ---------------------------------------------------------------------------

_listeners: list = []
_stop = threading.Event()


def subscribe(listener) -> None:
    """Register listener(kind, data), called for every emitted event."""
    if listener not in _listeners:
        _listeners.append(listener)


def unsubscribe(listener) -> None:
    """Remove a previously registered listener. Silent if absent."""
    if listener in _listeners:
        _listeners.remove(listener)


def emit(kind: str, **data) -> None:
    """Notify every listener.

    Never raises. A listener is UI code running on another thread; a bug there
    must not abort a scrape that has been running for hours.
    """
    for listener in list(_listeners):
        try:
            listener(kind, data)
        except Exception:
            pass


def request_stop() -> None:
    """Ask the current run to stop at the next listing boundary."""
    _stop.set()


def stop_requested() -> bool:
    return _stop.is_set()


def clear_stop() -> None:
    """Reset the flag. Call before starting a run."""
    _stop.clear()


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
CONSECUTIVE_FAILURE_LIMIT = 3


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


def scrape_listing(page, url: str, term: str, place: "geo.Place") -> dict:
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
    return build_record(raw, term, place, utc_now())


def should_mark_done(failed: int, complete: bool) -> bool:
    """A pair is done only when it was fully covered and nothing failed.

    Inferring completeness from `failed == 0` is what let a --limit run, a
    feed timeout, and a single-result redirect each mark a state finished
    that had not been scraped.
    """
    return complete and not failed


def scrape_query(
    page, term: str, place: "geo.Place", limit: int | None
) -> tuple[int, int, bool]:
    """Scrape every listing for one (term, place) pair.

    Returns (scraped, failed, complete). `complete` says whether the pair was
    covered end to end: a --limit truncation and a missing results feed both
    make it False. Only a complete pair with no failures may be marked done,
    or the listings it never reached are lost for good.
    """
    location = place.query_text()
    place_state = place.coverage_key()
    page.goto(build_search_url(term, place), wait_until="domcontentloaded", timeout=60000)
    accept_consent(page)
    if is_blocked(page):
        raise RuntimeError(f"blocked on {term} / {location} - rerun with --headed")

    if "/maps/place/" in page.url:
        print(f"  single result for {term} / {location}")
        emit("listings_found", term=term, state=place_state, country=place.country,
             city=place.city, count=1, at_cap=False)
        try:
            record = scrape_listing(page, page.url, term, place)
            append_record(record)
            emit("listing_saved", name=record.get("name", ""),
                 city=record.get("city", ""), state=place_state, country=place.country)
            return 1, 0, True
        except Exception as exc:
            print(f"  skipped the single result: {type(exc).__name__}: {exc}")
            emit("listing_failed", error=f"{type(exc).__name__}: {exc}")
            return 0, 1, True

    try:
        page.wait_for_selector(SELECTORS["feed"], timeout=20000)
    except PWTimeout:
        print(f"  no results feed for {term} / {location}")
        emit("listings_found", term=term, state=place_state, country=place.country,
             city=place.city, count=0, at_cap=False)
        return 0, 0, False

    links = collect_result_links(page)
    complete = limit is None
    if limit:
        links = links[:limit]
    print(f"  {len(links)} listings for {term} / {location}")
    emit("listings_found", term=term, state=place_state, country=place.country,
         city=place.city, count=len(links), at_cap=len(links) >= 118)
    if len(links) >= 118:
        print("  WARNING: at the ~120 result cap; this place is undersampled")

    scraped = 0
    failed = 0
    for link in links:
        if stop_requested():
            break
        try:
            record = scrape_listing(page, link, term, place)
            append_record(record)
            scraped += 1
            emit("listing_saved", name=record.get("name", ""),
                 city=record.get("city", ""), state=place_state, country=place.country)
        except Exception as exc:
            failed += 1
            print(f"  skipped a listing: {type(exc).__name__}: {exc}")
            emit("listing_failed", error=f"{type(exc).__name__}: {exc}")
        _pause(PAUSE_LISTING)
    return scraped, failed, complete


def run_stage1(terms, places, limit=None, headless=True, force=False) -> None:
    """Scrape every (term, place) pair not already in the cache."""
    _, done = read_cache()
    emit("run_start", terms=list(terms), states=[place.query_text() for place in places],
         total_queries=len(terms) * len(places))
    consecutive_failures = 0
    # Must precede sync_playwright(): the driver reads the browser path once,
    # when it starts. A no-op outside a bundle.
    paths.use_bundled_browsers()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        context = browser.new_context(
            user_agent=USER_AGENT, viewport={"width": 1280, "height": 900}
        )
        page = context.new_page()
        for term in terms:
            for place in places:
                if stop_requested():
                    browser.close()
                    return
                location = place.query_text()
                place_state = place.coverage_key()
                if not force and pair_key(term, place) in done:
                    print(f"skip (cached): {term} / {location}")
                    emit("query_skipped", term=term, state=place_state,
                         country=place.country, city=place.city)
                    continue
                print(f"searching: {term} / {location}")
                emit("query_start", term=term, state=place_state,
                     country=place.country, city=place.city)
                try:
                    scraped, failed, complete = scrape_query(page, term, place, limit)
                except Exception as exc:
                    print(f"  FAILED {term} / {location}: {exc}")
                    emit("query_failed", term=term, state=place_state,
                         country=place.country, city=place.city,
                         error=f"{type(exc).__name__}: {exc}")
                    consecutive_failures += 1
                    if consecutive_failures >= CONSECUTIVE_FAILURE_LIMIT:
                        print(
                            f"\nAborting: {consecutive_failures} queries failed in a "
                            "row. Google may be blocking this run, or the browser "
                            "died.\nNothing scraped so far is lost \u2014 re-run to resume, "
                            "or use --headed to solve a CAPTCHA by hand."
                        )
                        emit("blocked", term=term, state=place_state,
                             country=place.country, city=place.city,
                             consecutive=consecutive_failures)
                        browser.close()
                        return
                    _pause(PAUSE_QUERY)
                    continue
                consecutive_failures = 0
                if should_mark_done(failed, complete) and not stop_requested():
                    mark_pair_done(term, place)
                else:
                    reason = (
                        f"{failed} listing(s) failed" if failed
                        else "coverage incomplete"
                    )
                    print(
                        f"  {reason}; leaving {term} / {location} unmarked "
                        "so a re-run retries it"
                    )
                # `complete` was computed before the stop flag broke the
                # listing loop, so a stopped place would paint green "done" in
                # the grid while the cache correctly left it unmarked.
                emit("query_done", term=term, state=place_state,
                     country=place.country, city=place.city, scraped=scraped,
                     failed=failed, complete=complete and not stop_requested())
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
    "    --scopes=https://www.googleapis.com/auth/cloud-platform,"
    "https://www.googleapis.com/auth/spreadsheets,"
    "https://www.googleapis.com/auth/drive.readonly"
)


def record_to_row(record: dict) -> list[str]:
    """A record rendered as a row in COLUMNS order."""
    return [
        "" if record.get(column) is None else str(record.get(column, ""))
        for column in COLUMNS
    ]


def row_range(row_number: int) -> str:
    """A1 range covering every column of one row, e.g. "A2:Y2"."""
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
    # Unconditional, not just when the header looks stale: a sheet created
    # before COLUMNS grew is narrower than COLUMNS but its existing header
    # still compares unequal to the new one, so this must run regardless of
    # that comparison. `values.update` (below) does not auto-expand a grid
    # the way `append` does -- without this, writing a header wider than the
    # sheet fails the whole push with a 400 "exceeds grid limits".
    if worksheet.col_count < len(COLUMNS):
        worksheet.add_cols(len(COLUMNS) - worksheet.col_count)
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


class PlaceArgError(ValueError):
    """A --country/--region/--city value that does not resolve to a place.

    Raised rather than letting a malformed or unrecognised value reach
    `geo.Place` (whose own validation would surface a bare ValueError with no
    hint of which flag or value was at fault) or, worse, letting it construct
    a `Place` silently: an unrecognised country name would search Google Maps
    for a place that does not exist and mark it done forever, and a region or
    city typed without its country would build a marker that can never be
    read back as done on the next run.
    """


def resolve_places(
    countries: list[str], regions: list[str], cities: list[str]
) -> list["geo.Place"]:
    """`Place`s named by --country/--region/--city, validated against `geo`.

    Every country and region name must be one `geo` actually knows, and a
    --region or --city entry must carry a country -- a bare "Texas" with no
    comma is a plausible misreading of --region's own example. City names are
    not checked against `geo.cities`: that list is capped at the 25 most
    populous cities per region, so a smaller real city is expected to be
    absent from it and must still be searchable.
    """
    places: list[geo.Place] = []
    for raw in countries:
        name = raw.strip()
        if name not in geo.countries():
            raise PlaceArgError(f"--country {raw!r}: unrecognised country {name!r}")
        places.append(geo.Place(country=name))
    for raw in regions:
        region, _, country = raw.partition(",")
        region, country = region.strip(), country.strip()
        if not country:
            raise PlaceArgError(
                f'--region {raw!r} needs a country, e.g. --region "Texas,United States"'
            )
        if country not in geo.countries():
            raise PlaceArgError(f"--region {raw!r}: unrecognised country {country!r}")
        if region not in geo.regions(country):
            raise PlaceArgError(
                f"--region {raw!r}: unrecognised region {region!r} for {country!r}"
            )
        places.append(geo.Place(country=country, region=region))
    for raw in cities:
        city, _, rest = raw.partition(",")
        region, _, country = rest.partition(",")
        city, region, country = city.strip(), region.strip(), country.strip()
        if not country:
            raise PlaceArgError(
                f'--city {raw!r} needs a country, e.g. --city "Austin,Texas,United States"'
            )
        if country not in geo.countries():
            raise PlaceArgError(f"--city {raw!r}: unrecognised country {country!r}")
        if region not in geo.regions(country):
            raise PlaceArgError(
                f"--city {raw!r}: unrecognised region {region!r} for {country!r}"
            )
        places.append(geo.Place(country=country, region=region, city=city))
    return places


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
    emit("stage2_start", total=len(targets))
    fetch = make_fetcher()
    for index, record in enumerate(targets, start=1):
        if stop_requested():
            break
        try:
            region = geo.country_code(record.get("search_country", "")).upper() or "US"
            enrichment = enrich_website(
                record["website"], fetch, record.get("phone", ""), region=region
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
        emit("enriched", index=index, total=len(targets),
             name=record.get("name", ""), error=enrichment["enrich_error"])
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
    parser.add_argument("--states", help="comma-separated US states, e.g. Texas,Florida "
                        "(ignored if --country, --region or --city is given)")
    parser.add_argument("--country", action="append", default=[],
                        help="search a whole country, repeatable")
    parser.add_argument("--region", action="append", default=[],
                        metavar="REGION,COUNTRY",
                        help='e.g. --region "Texas,United States", repeatable')
    parser.add_argument("--city", action="append", default=[],
                        metavar="CITY,REGION,COUNTRY",
                        help='e.g. --city "Austin,Texas,United States", repeatable')
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
        places = [geo.Place(country="United States", region=state)
                  for state in parse_list_arg(args.states, STATES)]
        if args.country or args.region or args.city:
            try:
                places = resolve_places(args.country, args.region, args.city)
            except PlaceArgError as exc:
                parser.error(str(exc))
        run_stage1(
            parse_list_arg(args.terms, SEARCH_TERMS),
            places,
            limit=args.limit,
            headless=HEADLESS and not args.headed,
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
