"""Google Maps club scraper -> Google Sheets.

Run `python scrape.py --help` for usage.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus, unquote, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

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
SOCIAL_JUNK = ("sharer", "share.php", "/tr?", "/plugins", "/intent/")

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
    """First non-junk instagram, facebook and linkedin URL in `html`."""
    result = {}
    for key, pattern in SOCIAL_RES.items():
        result[key] = ""
        for match in pattern.finditer(html):
            url = match.group(0)
            if any(junk in url for junk in SOCIAL_JUNK):
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
