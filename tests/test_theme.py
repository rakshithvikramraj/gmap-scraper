import sys
from pathlib import Path

import pytest

import paths
import theme


def test_every_palette_value_is_a_hex_colour():
    bad = {k: v for k, v in theme.PALETTE.items()
           if not (isinstance(v, str) and len(v) == 7 and v.startswith("#"))}
    assert bad == {}, f"Tk cannot parse these: {bad}"


def test_the_palette_carries_the_names_the_app_paints_with():
    required = {
        "bg", "panel", "sunken", "raised", "line", "hairline", "chrome",
        "ink", "bright", "muted", "dim", "faint", "cellink", "field",
        "lime", "lime_ink", "lime_d", "onlime", "amber", "coral",
        "row_sel", "cell_done", "cell_partial", "cell_failed", "cell_active",
        "done_edge", "partial_edge", "failed_edge", "tickline", "seg",
    }
    assert required <= set(theme.PALETTE), f"missing {sorted(required - set(theme.PALETTE))}"


def test_tracked_puts_a_thin_space_between_letters():
    assert theme.tracked("AB") == "A\u2009B"


def test_tracked_uppercases_so_callers_do_not_have_to():
    assert theme.tracked("Coverage") == "C\u2009O\u2009V\u2009E\u2009R\u2009A\u2009G\u2009E"


def test_tracked_leaves_a_single_character_alone():
    assert theme.tracked("A") == "A"


def test_tracked_survives_an_empty_string():
    assert theme.tracked("") == ""


def test_the_shipped_font_directory_exists_with_both_weights():
    names = {p.name for p in paths.font_dir().glob("*.ttf")}
    assert names == {"Unbounded-Regular.ttf", "Unbounded-Bold.ttf"}, names


def test_the_font_licence_ships_beside_the_fonts():
    assert (paths.font_dir() / "OFL.txt").is_file(), "OFL 1.1 requires the licence to ship"


def test_registering_a_directory_that_does_not_exist_reports_nothing(tmp_path):
    assert theme.register_fonts(tmp_path / "nope") == []


def test_registering_a_file_that_is_not_a_font_does_not_raise(tmp_path):
    (tmp_path / "Broken.ttf").write_bytes(b"not a font")
    assert theme.register_fonts(tmp_path) == [], "a corrupt face must fall back, not crash"


@pytest.mark.skipif(
    sys.platform != "darwin" and not sys.platform.startswith("win"),
    reason="only macOS and Windows have a font-registration API; Linux CI has none",
)
def test_registering_the_real_fonts_reports_both_weights():
    accepted = theme.register_fonts()
    assert set(accepted) == {"Unbounded-Regular.ttf", "Unbounded-Bold.ttf"}, accepted


def test_a_platform_without_a_registration_api_reports_nothing_and_does_not_raise(
        monkeypatch):
    """The Linux test runner takes this path, and so would any future one."""
    monkeypatch.setattr(theme.sys, "platform", "sunos5")
    assert theme.register_fonts() == []
