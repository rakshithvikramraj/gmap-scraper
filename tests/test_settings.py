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
