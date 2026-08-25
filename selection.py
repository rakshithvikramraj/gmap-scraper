"""What the operator has picked, and everything derivable from it.

A Selection is `dict[country, dict[region, list[city]]]`. An empty city list
means the whole region; an empty region dict means the whole country; an
absent country is not selected. That "empty means all of it" rule is the
whole design -- it lets one structure express "all of Japan", "all of Utah"
and "these two cities in Texas" without a mode flag.

Pure, like `runstate` and `geo`: no tkinter, no files, no clock. The selector
screen renders from these functions and writes back through them, and never
keeps state of its own -- a Listbox's own selection is destroyed every time it
repopulates, which is exactly what a three-pane cascade does constantly.

Every function returns a new structure. Nothing here mutates its argument.
"""

import geo


def normalise(raw) -> geo.Selection:
    """Coerce whatever came out of settings.json into the Selection shape.

    A hand-edited or truncated preferences file must never stop the app
    opening, so anything unrecognisable degrades to "nothing selected" rather
    than raising.
    """
    if not isinstance(raw, dict):
        return {}
    out: geo.Selection = {}
    for country, regions_of in raw.items():
        if not isinstance(country, str):
            continue
        clean: dict[str, list[str]] = {}
        if isinstance(regions_of, dict):
            for region, cities in regions_of.items():
                if not isinstance(region, str):
                    continue
                clean[region] = (
                    [c for c in cities if isinstance(c, str)]
                    if isinstance(cities, (list, tuple)) else []
                )
        out[country] = clean
    return out


def from_states(states) -> geo.Selection:
    """The pre-geography preference -- a bare list of US state names."""
    names = [s for s in states if isinstance(s, str)]
    return {"United States": {state: [] for state in names}} if names else {}


def prune(selection: geo.Selection) -> tuple[geo.Selection, list[str]]:
    """Drop places this build of `geodata` does not know, and name them.

    Cities are deliberately not checked. `geo.cities` is the 25 most populous
    per region, so a real smaller city an operator found through the search
    box is expected to be absent from it and must still survive a reload.
    """
    known_countries = set(geo.countries())
    kept: geo.Selection = {}
    dropped: list[str] = []
    for country in sorted(selection):
        if country not in known_countries:
            dropped.append(country)
            continue
        known_regions = set(geo.regions(country))
        clean: dict[str, list[str]] = {}
        for region in sorted(selection[country]):
            if region not in known_regions:
                dropped.append(region)
                continue
            clean[region] = list(selection[country][region])
        kept[country] = clean
    return kept, dropped


def toggle_country(selection: geo.Selection, country: str) -> geo.Selection:
    """Select the whole country, or clear it and everything under it."""
    out = {c: dict(r) for c, r in selection.items()}
    if country in out:
        del out[country]
    else:
        out[country] = {}
    return out


def toggle_region(selection: geo.Selection, country: str,
                  region: str) -> geo.Selection:
    """Select the whole region, or clear it and its cities.

    Clearing the last region of a country deselects the country outright
    rather than leaving `{country: {}}`, which would silently mean "the whole
    country" -- the opposite of what unticking the last box asks for.
    """
    out = {c: dict(r) for c, r in selection.items()}
    regions_of = out.setdefault(country, {})
    if region in regions_of:
        del regions_of[region]
        if not regions_of:
            del out[country]
    else:
        regions_of[region] = []
    return out


def toggle_city(selection: geo.Selection, country: str, region: str,
                city: str) -> geo.Selection:
    """Add or remove one city, keeping the region's population order.

    Removing the last city leaves the region selected -- now meaning all of
    it. That is the level above, which is where unticking a city should land.
    """
    out = {c: {r: list(cities) for r, cities in rs.items()}
           for c, rs in selection.items()}
    regions_of = out.setdefault(country, {})
    chosen = regions_of.setdefault(region, [])
    if city in chosen:
        chosen.remove(city)
    else:
        order = geo.cities(country, region)
        chosen.append(city)
        # Sort by population rank, with anything geo does not rank (a small
        # city found through search) after the ranked ones, alphabetically.
        chosen.sort(key=lambda name: (order.index(name) if name in order
                                      else len(order), name))
    return out


def toggle_place(selection: geo.Selection, place: geo.Place) -> geo.Selection:
    """Toggle whichever level `place` names."""
    if place.city:
        return toggle_city(selection, place.country, place.region, place.city)
    if place.region:
        return toggle_region(selection, place.country, place.region)
    return toggle_country(selection, place.country)


def is_country_on(selection: geo.Selection, country: str) -> bool:
    return country in selection


def is_region_on(selection: geo.Selection, country: str, region: str) -> bool:
    return region in selection.get(country, {})


def is_city_on(selection: geo.Selection, country: str, region: str,
               city: str) -> bool:
    return city in selection.get(country, {}).get(region, [])


def country_note(selection: geo.Selection, country: str) -> str:
    """The right-hand figure on a country row: "2/51", "all", or "51"."""
    total = len(geo.regions(country))
    if country not in selection:
        return str(total)
    chosen = selection[country]
    return f"{len(chosen)}/{total}" if chosen else "all"


def region_note(selection: geo.Selection, country: str, region: str) -> str:
    """The right-hand figure on a region row: "2/25" or "25"."""
    total = len(geo.cities(country, region))
    chosen = selection.get(country, {}).get(region, [])
    return f"{len(chosen)}/{total}" if chosen else str(total)


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def summary(selection: geo.Selection) -> list[tuple[str, str]]:
    """One (country, description) line per selected country, country-sorted."""
    lines = []
    for country in sorted(selection):
        regions_of = selection[country]
        if not regions_of:
            lines.append((country, "whole country"))
            continue
        cities = sum(len(names) for names in regions_of.values())
        # "city" is the only irregular noun this module pluralises, so it is
        # corrected in place rather than through a table of one entry.
        tail = (_plural(cities, "city").replace("citys", "cities") if cities
                else "whole state")
        lines.append((country, f"{_plural(len(regions_of), 'state')} · {tail}"))
    return lines


def cap_noun(selection: geo.Selection) -> str:
    """What "stop after N per ___" should say, given the finest level chosen."""
    if any(names for regions_of in selection.values()
           for names in regions_of.values()):
        return "city"
    if any(selection.values()):
        return "state"
    return "country" if selection else "place"


def region_keys(selection: geo.Selection) -> list[tuple[str, str]]:
    """(country, coverage cell) for every cell the coverage display will show.

    Derived through `geo.leaf_places` and `Place.coverage_key` rather than
    walking the dict here, so the cells the grid draws and the cells
    `runstate` folds into can never come from two different definitions.
    """
    seen = []
    for place in geo.leaf_places(selection):
        pair = (place.country, place.coverage_key())
        if pair not in seen:
            seen.append(pair)
    return seen
