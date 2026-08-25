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


def test_extract_emails_empty_input():
    assert scrape.extract_emails("") == []


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


def test_find_owner_contact_empty_text():
    assert scrape.find_owner_contact("") == ("", "")


def test_find_owner_contact_handles_title_directly_before_name():
    text = "Owner Maria Lopez - (512) 555-0100"
    assert scrape.find_owner_contact(text) == ("Maria Lopez", "+15125550100")


def test_find_owner_contact_handles_title_before_name_in_prose():
    text = "Founder Dave Kim can be reached at (512) 555-0100"
    assert scrape.find_owner_contact(text) == ("Dave Kim", "+15125550100")


def test_find_owner_contact_keeps_a_three_token_name_whole():
    text = "Mary Jane Watson, Owner - (512) 555-0100"
    assert scrape.find_owner_contact(text) == ("Mary Jane Watson", "+15125550100")


def test_find_owner_contact_rejects_an_over_long_capitalised_run():
    text = "Owner Riverside Grand Athletic Pavilion Trust (512) 555-0100"
    assert scrape.find_owner_contact(text) == ("", "+15125550100")


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
