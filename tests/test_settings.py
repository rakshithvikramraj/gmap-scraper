import json
import settings


def test_load_returns_defaults_when_the_file_is_absent(tmp_path):
    prefs = settings.load(tmp_path / "nope.json")
    assert prefs == settings.DEFAULTS
    assert prefs is not settings.DEFAULTS, "callers must not be able to edit the defaults"


def test_save_then_load_round_trips(tmp_path):
    path = tmp_path / "settings.json"
    settings.save({"terms": ["padel club"], "states": ["Utah"], "enrich": False}, path)
    assert settings.load(path)["terms"] == ["padel club"]
    assert settings.load(path)["enrich"] is False


def test_a_corrupt_file_falls_back_instead_of_crashing(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{not json")
    assert settings.load(path) == settings.DEFAULTS


def test_unknown_keys_are_dropped_and_missing_keys_are_filled(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"terms": ["x"], "nonsense": 1}))
    prefs = settings.load(path)
    assert prefs["terms"] == ["x"]
    assert "nonsense" not in prefs
    assert prefs["enrich"] == settings.DEFAULTS["enrich"]


def test_save_creates_the_parent_directory(tmp_path):
    path = tmp_path / "data" / "settings.json"
    settings.save({"terms": ["x"]}, path)
    assert path.exists()


def test_a_preferences_file_from_before_geography_keeps_its_states(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"terms": ["gym"], "states": ["Texas", "Utah"]}')
    prefs = settings.load(path)
    assert prefs["selection"] == {"United States": {"Texas": [], "Utah": []}}


def test_a_newer_selection_wins_over_a_leftover_states_list(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"states": ["Texas"], "selection": {"Japan": {}}}')
    assert settings.load(path)["selection"] == {"Japan": {}}


def test_saving_drops_the_obsolete_states_key(tmp_path):
    path = tmp_path / "settings.json"
    settings.save({"selection": {"Japan": {}}, "states": ["Texas"]}, path)
    assert "states" not in json.loads(path.read_text())


def test_a_malformed_selection_degrades_instead_of_raising(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"selection": "not a dict"}')
    assert settings.load(path)["selection"] == {}


def test_the_default_selection_is_every_state_separately_not_the_whole_country(tmp_path):
    """One query for the whole US would return 120 results for the whole US."""
    prefs = settings.load(tmp_path / "absent.json")
    assert prefs["selection"]["United States"], "must name states, not be empty"
    assert len(prefs["selection"]["United States"]) == 50


def test_loading_defaults_does_not_hand_out_the_shared_default_dict(tmp_path):
    first = settings.load(tmp_path / "absent.json")
    first["selection"]["United States"]["Texas"].append("Austin")
    second = settings.load(tmp_path / "absent.json")
    assert second["selection"]["United States"]["Texas"] == []
