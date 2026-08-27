"""The country -> state -> city cascade, with no Tk.

The pane methods are driven as unbound functions against a stub holding only
the attributes they touch, the way test_worker drives _run_worker. Panes are
recorded rather than drawn, so what a click does to the next pane down is
assertable without a display.

`_click` mirrors PickList._on_click deliberately: it calls the highlight
handler and then the toggle handler, in that order. `_arrow` calls only the
highlight handler, the way PickList._move does. A click is both halves and
arrowing is one, and the two must not do the same thing in search mode.

The stub carries a real search box rather than a bare string, because the
jump out of search clears it by writing to the variable and letting its trace
fire -- so a test that set `_search` directly would skip the code path the
app actually takes.
"""

import app
import geo


class _Pane:
    """Enough of PickList to record what it was handed."""

    def __init__(self):
        self.rows = []
        self.current = ""
        self.revealed = None

    def set_rows(self, rows):
        self.rows = list(rows)

    def set_current(self, name):
        self.current = name

    def reveal(self, name):
        self.current = name
        self.revealed = name

    def names(self):
        return [row.name for row in self.rows]

    def ticked(self):
        return [row.name for row in self.rows if row.checked]


class _Label:
    def __init__(self):
        self.text = ""

    def configure(self, **kw):
        self.text = kw.get("text", self.text)


class _Var:
    """A StringVar whose write fires the trace, like the real one."""

    def __init__(self, owner):
        self._value = ""
        self._owner = owner

    def get(self):
        return self._value

    def set(self, value):
        self._value = value
        self._owner._on_search()


class _Stub:
    """Enough of App for the pane cascade, with no Tk involved."""

    _paint_panes = app.App._paint_panes
    _paint_search = app.App._paint_search
    _on_search = app.App._on_search
    _jump_to = app.App._jump_to
    _is_on = app.App._is_on

    def __init__(self):
        self._pick_country = ""
        self._pick_region = ""
        self._search = ""
        self._hits = {}
        self.selection = {}
        self.search_var = _Var(self)
        self.search_hint = _Label()
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
    _arrow(stub, pane, name)
    {
        "country": app.App._toggle_country,
        "region": app.App._toggle_region,
        "city": app.App._toggle_city,
    }[pane](stub, name)


def _arrow(stub, pane, name):
    """Only the highlight half, the way PickList._move reports a keypress."""
    highlight = {
        "country": app.App._focus_country,
        "region": app.App._focus_region,
    }.get(pane)
    if highlight:
        highlight(stub, name)


def _fresh():
    stub = _Stub()
    app.App._paint_panes(stub)
    return stub


def _searching(query):
    stub = _Stub()
    stub.search_var.set(query)
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


# -- search paints one flat list -----------------------------------------


def test_searching_replaces_the_panes_with_one_flat_list():
    stub = _searching("Texas")
    assert stub.pane_country.names() == ["Texas, United States"]
    assert stub.pane_region.rows == []


# -- activating a result jumps out of search -----------------------------


def test_clicking_a_state_result_lands_the_panes_on_it():
    stub = _searching("Texas")
    _click(stub, "country", "Texas, United States")

    assert stub._search == "", "the jump leaves search behind"
    assert stub.search_var.get() == ""
    assert stub._pick_country == "United States", "must be a country, not a path"
    assert stub._pick_region == "Texas"
    assert "Texas" in stub.pane_region.names(), "the State pane must fill in"
    assert "Austin" in stub.pane_city.names(), "and the City pane below it"
    assert stub.head_region.text == "United States"


def test_clicking_a_result_ticks_nothing():
    """Search navigates. Picking happens in the panes it lands you on."""
    stub = _searching("Texas")
    _click(stub, "country", "Texas, United States")
    assert stub.selection == {}
    assert stub.pane_region.ticked() == []


def test_the_jump_scrolls_the_landed_row_into_view():
    """Texas is row 43 of 51 -- highlighting it off screen looks like a no-op."""
    stub = _searching("Texas")
    _click(stub, "country", "Texas, United States")
    assert stub.pane_country.revealed == "United States"
    assert stub.pane_region.revealed == "Texas"


def test_clicking_a_city_result_points_every_pane_at_it():
    stub = _searching("Austin")
    row = next(n for n in stub.pane_country.names() if n.startswith("Austin,"))
    _click(stub, "country", row)

    assert stub._pick_country == "United States"
    assert stub._pick_region == "Texas"
    assert "Austin" in stub.pane_city.names()
    assert stub.pane_city.revealed == "Austin"


def test_clicking_a_country_result_lands_on_that_country():
    stub = _searching("India")
    _click(stub, "country", "India")

    assert stub._search == ""
    assert stub._pick_country == "India"
    assert stub._pick_region == ""
    assert stub.pane_region.rows, "India has states to list"


def test_arrowing_through_results_does_not_move_the_panes():
    """Browsing matches must not teleport the panes under the operator."""
    stub = _fresh()
    _click(stub, "country", "United States")
    stub.search_var.set("Texas")
    _arrow(stub, "country", "Texas, United States")

    assert stub._search == "Texas", "arrowing stays in search"
    assert stub._pick_country == "United States", "and changes nothing beneath"


def test_an_unknown_search_row_leaves_the_panes_alone():
    stub = _fresh()
    _click(stub, "country", "United States")
    stub.search_var.set("Texas")
    _click(stub, "country", "Nowhere, Atlantis")

    assert stub._search == "Texas", "an unresolvable row does not leave search"
    assert stub._pick_country == "United States"
