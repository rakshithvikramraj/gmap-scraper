import geo
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
    url = scrape.build_search_url("padel club", geo.Place("United States", "New York"))
    assert url.startswith("https://www.google.com/maps/search/")
    assert "padel+club+in+New+York%2C+United+States" in url
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


def test_extract_phones_ignores_long_digit_runs():
    assert scrape.extract_phones("SKU 1234567890123") == []
    assert scrape.extract_phones("Call (512) 555-0100") == ["+15125550100"]


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


def test_find_owner_contact_finds_an_indian_number():
    # PHONE_RE never matched this shape, and the name search is anchored on
    # the phone match's position -- a US-only candidate finder would lose
    # both the number and the name for a business outside the US.
    text = "Owner Priya Sharma - 022 2822 1234"
    assert scrape.find_owner_contact(text, region="IN") == (
        "Priya Sharma", "+912228221234"
    )


def test_find_owner_contact_finds_a_uk_number():
    text = "Owner James Hall - 020 7930 4832"
    assert scrape.find_owner_contact(text, region="GB") == (
        "James Hall", "+442079304832"
    )


def test_find_owner_contact_us_cases_are_unaffected_by_the_region_default():
    # Regression guard: switching the candidate finder to PhoneNumberMatcher
    # must not change behaviour for the plain US case that was already
    # covered above.
    text = "Owner Maria Lopez - (512) 555-0100"
    assert scrape.find_owner_contact(text) == ("Maria Lopez", "+15125550100")


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


def test_enrich_website_threads_region_to_owner_contact():
    # The wiring bug this guards: `region` reached extract_phones but not
    # find_owner_contact, so a non-US owner's name and phone came back
    # empty while other_phones still found the same number.
    def fetch(url):
        return "<html><body>Owner Priya Sharma - 022 2822 1234</body></html>"

    result = scrape.enrich_website("https://x.example", fetch, region="IN")
    assert result["owner_name"] == "Priya Sharma"
    assert result["owner_phone"] == "+912228221234"


def test_enrich_website_records_fetch_failure():
    def boom(url):
        raise TimeoutError("timed out")

    result = scrape.enrich_website("https://dead.example", boom)
    assert "TimeoutError" in result["enrich_error"]
    assert result["emails"] == ""


def test_enrich_website_survives_a_failing_contact_page():
    pages = {
        "https://austinpadel.com": fixture("club_site.html"),
        "https://austinpadel.com/about": fixture("club_about.html"),
    }

    def fetch(url):
        if url not in pages:
            raise TimeoutError("contact page timed out")
        return pages[url]

    result = scrape.enrich_website("https://austinpadel.com", fetch)
    assert result["emails"] == "info@austinpadel.com"
    assert result["enrich_error"] == ""


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


def test_read_cache_merges_enrichment_over_a_rescrape(tmp_path):
    cache = tmp_path / "cache.jsonl"
    scrape.append_record(
        {"place_key": "k", "name": "X", "emails": "a@b.c",
         "enriched_at": "2026-01-01T00:00:00+00:00"},
        cache,
    )
    scrape.append_record({"place_key": "k", "name": "X", "phone": "+15125550100"}, cache)
    records, _ = scrape.read_cache(cache)
    assert len(records) == 1
    assert records[0]["emails"] == "a@b.c"
    assert records[0]["enriched_at"]
    assert records[0]["phone"] == "+15125550100"


def test_mark_pair_done_round_trips(tmp_path):
    cache = tmp_path / "cache.jsonl"
    scrape.mark_pair_done("padel club", geo.Place("United States", "Texas"), cache)
    _, pairs = scrape.read_cache(cache)
    assert pairs == {("padel club", "United States", "Texas", "")}


def test_a_done_marker_round_trips(tmp_path):
    cache = tmp_path / "cache.jsonl"
    place = geo.Place("United States", "Texas", "Austin")
    scrape.mark_pair_done("gym", place, cache)
    _, done = scrape.read_cache(cache)
    assert scrape.pair_key("gym", place) in done


def test_a_statewide_marker_does_not_satisfy_a_city(tmp_path):
    cache = tmp_path / "cache.jsonl"
    scrape.mark_pair_done("gym", geo.Place("United States", "Texas"), cache)
    _, done = scrape.read_cache(cache)
    assert scrape.pair_key("gym", geo.Place("United States", "Texas", "Austin")) not in done


def test_a_legacy_two_part_marker_still_reads_as_done(tmp_path):
    # Written by every run before this change, when the scraper was US-only.
    cache = tmp_path / "cache.jsonl"
    cache.write_text('{"type": "pair", "term": "padel club", "state": "Texas"}\n',
                     encoding="utf-8")
    _, done = scrape.read_cache(cache)
    assert ("padel club", "United States", "Texas", "") in done


def test_legacy_and_new_markers_coexist(tmp_path):
    cache = tmp_path / "cache.jsonl"
    cache.write_text('{"type": "pair", "term": "gym", "state": "Texas"}\n', encoding="utf-8")
    scrape.mark_pair_done("gym", geo.Place("India", "Maharashtra", "Mumbai"), cache)
    _, done = scrape.read_cache(cache)
    assert ("gym", "United States", "Texas", "") in done
    assert ("gym", "India", "Maharashtra", "Mumbai") in done


def test_records_are_unaffected_by_the_marker_change(tmp_path):
    cache = tmp_path / "cache.jsonl"
    scrape.append_record({"place_key": "abc", "name": "A Gym"}, cache)
    scrape.mark_pair_done("gym", geo.Place(country="India"), cache)
    records, done = scrape.read_cache(cache)
    assert [r["name"] for r in records] == ["A Gym"]
    assert len(done) == 1


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
    place = geo.Place(country="United States", region="Texas")
    record = scrape.build_record(raw, "padel club", place, "2026-08-24T00:00:00+00:00")

    assert set(record) == set(scrape.STAGE1_COLUMNS)
    assert record["place_key"] == "0x864b1a:0x9fe1"
    assert record["name"] == "Austin Padel Club"
    assert record["city"] == "Austin"
    # The query's region name wins over the address's two-letter code.
    assert record["state"] == "Texas"
    assert record["zip"] == "78701"
    assert record["phone"] == "+15125550100"
    assert record["rating"] == 4.8
    assert record["reviews"] == 127
    assert record["latitude"] == 30.2672
    assert record["search_state"] == "Texas"


def test_build_record_never_yields_an_empty_place_key():
    place = geo.Place(country="United States", region="Ohio")
    record = scrape.build_record({}, "padel club", place, "2026-08-24T00:00:00+00:00")
    assert record["place_key"]


def test_build_record_falls_back_when_no_place_key():
    raw = {"url": "https://www.google.com/maps", "name": "Nameless Club"}
    place = geo.Place(country="United States", region="Utah")
    record = scrape.build_record(raw, "padel club", place, "2026-08-24T00:00:00+00:00")
    assert record["place_key"] == "Nameless Club|None,None"
    assert record["rating"] == ""
    assert record["reviews"] == 0


def test_extract_emails_rejects_escaped_mailto_artifacts():
    html = r'<a href="mailto:info@club.com\">E</a><a href="mailto:real@club.com">R</a>'
    assert scrape.extract_emails(html) == ["info@club.com", "real@club.com"]


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


def test_extract_socials_prefers_a_profile_over_an_earlier_post_link():
    html = (
        '<a href="https://www.instagram.com/p/CabcDEF/">post</a>'
        '<a href="https://www.instagram.com/slcpadelclub/">profile</a>'
    )
    assert scrape.extract_socials(html)["instagram"] == (
        "https://www.instagram.com/slcpadelclub/"
    )


def test_extract_socials_keeps_a_named_facebook_page_prefix():
    html = '<a href="https://www.facebook.com/p/SLC-Padel-100086">fb</a>'
    assert scrape.extract_socials(html)["facebook"] == (
        "https://www.facebook.com/p/SLC-Padel-100086"
    )


def test_extract_socials_rejects_a_bare_prefix_stub():
    html = '<a href="https://www.facebook.com/p/">fb</a>'
    assert scrape.extract_socials(html)["facebook"] == ""


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
    assert scrape.row_range(2) == "A2:AB2"


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


class FakeWorksheet:
    """Just enough of gspread's Worksheet for open_worksheet to run offline."""

    def __init__(self, col_count, header):
        self.col_count = col_count
        self._header = header
        self.add_cols_calls: list[int] = []
        self.update_calls: list[tuple] = []

    def row_values(self, row):
        return self._header if row == 1 else []

    def add_cols(self, n):
        self.add_cols_calls.append(n)
        self.col_count += n

    def update(self, range_name, values):
        self.update_calls.append((range_name, values))
        self._header = values[0]


class FakeSpreadsheet:
    def __init__(self, worksheet):
        self.title = "Fake Sheet"
        self._worksheet = worksheet

    def worksheet(self, name):
        return self._worksheet


class FakeSheetsClient:
    def __init__(self, spreadsheet):
        self._spreadsheet = spreadsheet

    def open_by_url(self, url):
        return self._spreadsheet


def _patch_sheets_client(monkeypatch, spreadsheet):
    monkeypatch.setattr(scrape.google.auth, "default", lambda scopes: (None, None))
    monkeypatch.setattr(scrape.gspread, "authorize",
                        lambda creds: FakeSheetsClient(spreadsheet))


def test_open_worksheet_widens_a_sheet_narrower_than_columns(monkeypatch):
    # COLUMNS grew from 25 to len(scrape.COLUMNS); a worksheet created before
    # that grows still reports the old, narrower col_count. write_records
    # then batch-updates an A2:AB2-shaped range against a 25-wide grid, which
    # the Sheets API rejects with a 400 "exceeds grid limits" -- every push
    # and --check-auth would fail on the first run after upgrade, for every
    # existing install, unless the sheet is widened first.
    worksheet = FakeWorksheet(col_count=25, header=scrape.COLUMNS[:25])
    _patch_sheets_client(monkeypatch, FakeSpreadsheet(worksheet))

    result = scrape.open_worksheet()

    assert worksheet.add_cols_calls == [len(scrape.COLUMNS) - 25]
    assert result.col_count == len(scrape.COLUMNS)
    assert result.update_calls[-1][1] == [scrape.COLUMNS]


def test_open_worksheet_does_not_widen_an_already_wide_enough_sheet(monkeypatch):
    worksheet = FakeWorksheet(col_count=len(scrape.COLUMNS), header=scrape.COLUMNS)
    _patch_sheets_client(monkeypatch, FakeSpreadsheet(worksheet))

    scrape.open_worksheet()

    assert worksheet.add_cols_calls == []


def test_check_auth_explains_insufficient_scopes(monkeypatch, capsys):
    def raise_permission_error():
        raise PermissionError()

    monkeypatch.setattr(scrape, "open_worksheet", raise_permission_error)
    assert scrape.check_auth() is False
    printed = capsys.readouterr().out
    assert "scopes" in printed
    assert "gcloud auth application-default login" in printed


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


def test_write_csv_round_trips(tmp_path):
    import csv

    target = tmp_path / "out.csv"
    scrape.write_csv([{"place_key": "k", "name": "Padel X"}], target)
    with target.open() as handle:
        rows = list(csv.reader(handle))
    assert rows[0] == scrape.COLUMNS
    assert rows[1][scrape.COLUMNS.index("name")] == "Padel X"


def test_should_mark_done_requires_completeness_and_no_failures():
    assert scrape.should_mark_done(0, True) is True
    assert scrape.should_mark_done(0, False) is False
    assert scrape.should_mark_done(2, True) is False
    assert scrape.should_mark_done(2, False) is False


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


def test_search_url_puts_the_city_first():
    url = scrape.build_search_url("dental clinic", geo.Place("United States", "Texas", "Austin"))
    assert "dental+clinic+in+Austin%2C+Texas%2C+United+States" in url


def test_search_url_for_a_whole_country():
    url = scrape.build_search_url("gym", geo.Place(country="India"))
    assert "gym+in+India" in url


def test_search_url_sets_gl_from_the_country():
    url = scrape.build_search_url("gym", geo.Place(country="India"))
    assert "gl=in" in url


def test_search_url_keeps_hl_english_everywhere():
    # Localised page chrome would break every selector in SELECTORS.
    url = scrape.build_search_url("gym", geo.Place(country="Japan", region="Kanagawa"))
    assert "hl=en" in url
    assert "hl=ja" not in url


def test_an_unknown_country_falls_back_to_us():
    url = scrape.build_search_url("gym", geo.Place(country="Atlantis"))
    assert "gl=us" in url


def test_search_url_of_an_empty_place_searches_the_bare_term():
    url = scrape.build_search_url("gym", geo.Place())
    assert "gym" in url
    assert "+in+" not in url


def test_city_and_state_come_from_the_query_not_the_address():
    # Parsing addresses correctly in every country is a large, low-value
    # problem, and unnecessary: the query already said where we searched.
    raw = {"url": "https://maps.google.com/?cid=1", "name": "A Gym",
           "address_label": "Address: 12 Some Road, Whoknows"}
    record = scrape.build_record(raw, "gym", geo.Place("India", "Maharashtra", "Mumbai"), "now")
    assert record["city"] == "Mumbai"
    assert record["state"] == "Maharashtra"
    assert record["country"] == "India"


def test_the_full_address_is_kept_verbatim():
    raw = {"url": "https://maps.google.com/?cid=1", "name": "A Gym",
           "address_label": "Address: 12 Some Road, Whoknows"}
    record = scrape.build_record(raw, "gym", geo.Place("India", "Maharashtra", "Mumbai"), "now")
    assert record["address"] == "12 Some Road, Whoknows"


def test_a_whole_country_run_falls_back_to_the_address_for_city():
    # No city was searched for, so the address is the only source there is.
    raw = {"url": "https://maps.google.com/?cid=1", "name": "A Gym",
           "address_label": "Address: 1 Main St, Austin, TX 78701"}
    record = scrape.build_record(raw, "gym", geo.Place(country="United States"), "now")
    assert record["city"] == "Austin"
    assert record["country"] == "United States"


def test_search_columns_record_what_was_asked_for():
    record = scrape.build_record({"url": "", "name": "A"}, "gym",
                                 geo.Place("India", "Maharashtra", "Mumbai"), "now")
    assert record["search_term"] == "gym"
    assert record["search_country"] == "India"
    assert record["search_state"] == "Maharashtra"
    assert record["search_city"] == "Mumbai"


def test_search_city_is_empty_on_a_whole_region_run():
    record = scrape.build_record({"url": "", "name": "A"}, "gym",
                                 geo.Place("India", "Maharashtra"), "now")
    assert record["search_city"] == ""


def test_new_columns_are_in_both_column_lists():
    # A column missing from STAGE1_COLUMNS is blanked by every re-scrape.
    for column in ("country", "search_country", "search_city"):
        assert column in scrape.COLUMNS
        assert column in scrape.STAGE1_COLUMNS


def test_a_us_number_normalises_to_e164():
    assert scrape.normalize_phone("(512) 555-0142") == "+15125550142"


def test_an_indian_number_needs_its_region():
    assert scrape.normalize_phone("022 2822 1234", region="IN") == "+912228221234"


def test_the_same_digits_are_invalid_in_another_region():
    # Region is not decoration: it decides validity.
    assert scrape.normalize_phone("022 2822 1234", region="US") == ""


def test_a_product_code_is_rejected():
    # The regex predecessor manufactured +14567890123 out of a SKU. A
    # validator rejects what a pattern would have accepted.
    assert scrape.normalize_phone("SKU 1234567890123") == ""


def test_a_too_short_run_of_digits_is_rejected():
    assert scrape.normalize_phone("call 12345") == ""


def test_an_international_prefix_is_honoured_over_the_region():
    assert scrape.normalize_phone("+44 20 7946 0958", region="US") == "+442079460958"


def test_extract_phones_deduplicates_in_order():
    text = "Call (512) 555-0142 or 512-555-0142 or (512) 555-0143"
    assert scrape.extract_phones(text) == ["+15125550142", "+15125550143"]


def test_extract_phones_drops_invalid_candidates():
    assert scrape.extract_phones("order SKU 1234567890123 today") == []


def test_extract_phones_finds_a_non_us_format():
    # The whole point of the region parameter. PHONE_RE never matched this
    # shape, so a US-only candidate finder would report no phones at all
    # for an Indian business.
    assert scrape.extract_phones("Reception: 022 2822 1234", region="IN") == ["+912228221234"]


def test_extract_phones_finds_a_uk_format():
    assert scrape.extract_phones("Ring us on 020 7930 4832 any time", region="GB") == ["+442079304832"]


def test_extract_phones_ignores_an_order_number():
    assert scrape.extract_phones("Order #4567890123 shipped") == []
