"""Where the app reads and writes, across every way it can be started.

These tests carry the packaging contract: running from source must resolve
exactly the relative `data/` path the rest of the suite already assumes, and
a frozen bundle must resolve somewhere a person can actually find.
"""

import os
import sys
from pathlib import Path

import pytest

import paths


@pytest.fixture
def frozen(monkeypatch, tmp_path):
    """Pose as a PyInstaller bundle unpacked at tmp_path."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    return tmp_path


@pytest.fixture(autouse=True)
def unfrozen(monkeypatch):
    """Strip any freeze markers so the source-run tests are honest.

    pytest itself is never frozen, but leaving this implicit would make the
    source-run assertions pass for the wrong reason.
    """
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)


# --- data_dir -------------------------------------------------------------

def test_data_dir_from_source_is_the_repo_relative_folder():
    # Every existing test and the CLI depend on this exact value.
    assert paths.data_dir() == Path("data")


def test_data_dir_from_source_is_relative():
    assert not paths.data_dir().is_absolute()


def test_data_dir_frozen_is_absolute(frozen):
    # A frozen app launched from Finder runs with cwd "/". A relative path
    # there resolves to /data and the first write fails.
    assert paths.data_dir().is_absolute()


def test_data_dir_frozen_lands_in_documents(frozen):
    result = paths.data_dir()
    assert result.parent.name == "Documents"
    assert result.name == "Club Scraper"


def test_data_dir_frozen_is_under_the_users_home(frozen):
    assert Path.home() in paths.data_dir().parents


# --- bundled_browsers -----------------------------------------------------

def test_bundled_browsers_is_none_from_source():
    assert paths.bundled_browsers() is None


def test_bundled_browsers_is_none_when_the_bundle_lacks_them(frozen):
    # Nothing was created at tmp_path/ms-playwright.
    assert paths.bundled_browsers() is None


def test_bundled_browsers_finds_the_bundled_folder(frozen):
    (frozen / "ms-playwright").mkdir()
    assert paths.bundled_browsers() == frozen / "ms-playwright"


# --- use_bundled_browsers -------------------------------------------------

def test_use_bundled_browsers_points_playwright_at_the_bundle(frozen, monkeypatch):
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    (frozen / "ms-playwright").mkdir()

    paths.use_bundled_browsers()

    assert os.environ["PLAYWRIGHT_BROWSERS_PATH"] == str(frozen / "ms-playwright")


def test_use_bundled_browsers_does_nothing_from_source(monkeypatch):
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)

    paths.use_bundled_browsers()

    assert "PLAYWRIGHT_BROWSERS_PATH" not in os.environ


def test_use_bundled_browsers_keeps_an_explicit_override(frozen, monkeypatch):
    # Someone debugging a bundle points it at their own browser build.
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "/somewhere/else")
    (frozen / "ms-playwright").mkdir()

    paths.use_bundled_browsers()

    assert os.environ["PLAYWRIGHT_BROWSERS_PATH"] == "/somewhere/else"


def test_use_bundled_browsers_is_silent_when_the_bundle_lacks_them(frozen, monkeypatch):
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)

    paths.use_bundled_browsers()

    assert "PLAYWRIGHT_BROWSERS_PATH" not in os.environ
