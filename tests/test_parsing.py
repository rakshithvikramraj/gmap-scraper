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
