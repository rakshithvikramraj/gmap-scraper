"""The country -> state -> city cascade, with no Tk.

The pane methods are driven as unbound functions against a stub holding only
the attributes they touch, the way test_worker drives _run_worker. Panes are
recorded rather than drawn, so what a click does to the next pane down is
assertable without a display.

`_click` mirrors PickList._on_click deliberately: it calls the highlight
handler and then the toggle handler, in that order. A click is both, and the
bug this file was written for lived in the half that the App's own callers
never exercised separately.
"""

import app
import geo


class _Pane:
    """Enough of PickList to record what it was handed."""

    def __init__(self):
        self.rows = []
        self.current = ""

    def set_rows(self, rows):
        self.rows = list(rows)

    def set_current(self, name):
        self.current = name

    def names(self):
        return [row.name for row in self.rows]


class _Label:
    def __init__(self):
        self.text = ""

    def configure(self, **kw):
        self.text = kw.get("text", self.text)


class _Stub:
    """Enough of App for the pane cascade, with no Tk involved."""

    _paint_panes = app.App._paint_panes
    _paint_search = app.App._paint_search
    _is_on = app.App._is_on

    def __init__(self):
        self._pick_country = ""
        self._pick_region = ""
        self._search = ""
        self._hits = {}
        self.selection = {}
        self.pane_country = _Pane()
        self.pane_region = _Pane()
        self.pane_city = _Pane()
        self.head_country = _Label()
        self.head_region = _Label()
        self.head_city = _Label()

    def _paint_chosen(self):
        pass

    def _paint_cost(self):
        pass


def _click(stub, pane, name):
    """One click: highlight then toggle, exactly as PickList._on_click does."""
    highlight = {
        "country": app.App._focus_country,
        "region": app.App._focus_region,
    }.get(pane)
    toggle = {
        "country": app.App._toggle_country,
        "region": app.App._toggle_region,
        "city": app.App._toggle_city,
    }[pane]
    if highlight:
        highlight(stub, name)
    toggle(stub, name)


def _fresh():
    stub = _Stub()
    app.App._paint_panes(stub)
    return stub


# -- the plain three-pane flow -------------------------------------------


def test_clicking_a_country_lists_its_states():
    stub = _fresh()
    _click(stub, "country", "United States")
    assert stub._pick_country == "United States"
    assert "Texas" in stub.pane_region.names()
    assert stub.head_region.text == "United States"


def test_clicking_a_state_lists_its_cities():
    stub = _fresh()
    _click(stub, "country", "United States")
    _click(stub, "region", "Texas")
    assert stub._pick_region == "Texas"
    assert "Austin" in stub.pane_city.names()


def test_choosing_another_country_clears_the_state_below_it():
    stub = _fresh()
    _click(stub, "country", "United States")
    _click(stub, "region", "Texas")
    _click(stub, "country", "Canada")
    assert stub._pick_region == "", "a stale state must not survive a new country"
    assert "Texas" not in stub.pane_region.names()
    assert stub.pane_city.rows == []


def test_a_country_with_no_states_lists_none():
    stub = _fresh()
    countries = [c for c in geo.countries() if not geo.regions(c)]
    assert countries, "the fixture assumes at least one region-less country"
    _click(stub, "country", countries[0])
    assert stub.pane_region.rows == []


# -- the search flow -----------------------------------------------------


def test_searching_replaces_the_panes_with_one_flat_list():
    stub = _Stub()
    stub._search = "Texas"
    app.App._paint_panes(stub)
    assert stub.pane_country.names() == ["Texas, United States"]
    assert stub.pane_region.rows == []


def test_clicking_a_state_result_leaves_the_panes_on_a_real_place():
    """The regression: a search row is a Place path, not a country name.

    _focus_country used to store the row name verbatim, so _pick_country
    became "Texas, United States", geo.regions of that is empty, and the
    State pane stayed blank even after the search was cleared.
    """
    stub = _Stub()
    stub._search = "Texas"
    app.App._paint_panes(stub)
    _click(stub, "country", "Texas, United States")

    assert stub._pick_country == "United States", "must be a country, not a path"
    assert stub._pick_region == "Texas"

    stub._search = ""
    app.App._paint_panes(stub)
    assert "Texas" in stub.pane_region.names(), "the State pane must fill in"
    assert stub.head_region.text == "United States"


def test_clicking_a_city_result_points_every_pane_at_it():
    stub = _Stub()
    stub._search = "Austin"
    app.App._paint_panes(stub)
    row = next(n for n in stub.pane_country.names() if n.startswith("Austin,"))
    _click(stub, "country", row)

    stub._search = ""
    app.App._paint_panes(stub)
    assert stub._pick_country == "United States"
    assert stub._pick_region == "Texas"
    assert "Austin" in stub.pane_city.names()


def test_clicking_a_country_result_still_works():
    stub = _Stub()
    stub._search = "India"
    app.App._paint_panes(stub)
    _click(stub, "country", "India")

    stub._search = ""
    app.App._paint_panes(stub)
    assert stub._pick_country == "India"
    assert stub._pick_region == ""
    assert stub.pane_region.rows, "India has states to list"


def test_an_unknown_search_row_leaves_the_panes_alone():
    stub = _fresh()
    _click(stub, "country", "United States")
    stub._search = "Texas"
    app.App._paint_panes(stub)
    _click(stub, "country", "Nowhere, Atlantis")
    assert stub._pick_country == "United States", "an unresolvable row changes nothing"
