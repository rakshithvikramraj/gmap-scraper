"""Places, and the data behind them.

Pure lookups over the generated `geodata` module plus a `Place` value that
carries a country/region/city triple around. No I/O, no tkinter, no clock -
everything here is testable without a display, like `runstate`.

Display names are the identity throughout. Codes appear only where a machine
needs one: `country_code` for the Maps `gl` parameter, `abbreviate` for a
coverage cell too narrow to hold a name.
"""

from dataclasses import dataclass

import geodata

# country -> region -> cities. An empty dict or list means "all of it".
Selection = dict[str, dict[str, list[str]]]


@dataclass(frozen=True)
class Place:
    """Somewhere to search. Empty parts mean the whole of the parent."""

    country: str = ""
    region: str = ""
    city: str = ""

    def __post_init__(self) -> None:
        if not self.country and (self.region or self.city):
            raise ValueError(
                "a region or city requires a country: "
                f"Place(country={self.country!r}, region={self.region!r}, "
                f"city={self.city!r})"
            )

    def parts(self) -> tuple[str, ...]:
        """The non-empty parts, broadest first."""
        return tuple(p for p in (self.country, self.region, self.city) if p)

    def query_text(self) -> str:
        """What follows "in" in a Maps search - most specific first.

        Google resolves "Austin, Texas, United States" reliably; leading with
        the country does not.
        """
        return ", ".join(reversed(self.parts()))

    def key(self) -> tuple[str, str, str]:
        """Fixed-arity identity for done markers and dict keys."""
        return (self.country, self.region, self.city)

    def label(self) -> str:
        """Short human form for a log line or a status header."""
        if self.city:
            return f"{self.city}, {self.region}" if self.region else self.city
        return self.region or self.country

    def coverage_key(self) -> str:
        """Identity of this place's coverage-grid cell.

        The spec wants coverage cells to be regions, not cities: a city-level
        run still paints progress onto its region's cell rather than adding
        one cell per city. This is the single definition of that identity --
        `runstate.initial_state` and every coverage event `scrape.py` emits
        must key off this, not re-derive it, or a city-level place seeds one
        cell and reports progress against a different one.
        """
        return self.region or self.country


def countries() -> list[str]:
    return sorted(geodata.COUNTRIES)


def regions(country: str) -> list[str]:
    return sorted(geodata.REGIONS.get(country, {}))


def cities(country: str, region: str) -> list[str]:
    """Most populous first - the order the generator wrote, not alphabetical.

    The order is the ranking reason, and re-sorting would discard it.
    """
    return list(geodata.CITIES.get((country, region), ()))


def country_code(country: str) -> str:
    """Lowercase ISO2 for the Maps `gl` parameter, or "" if unknown."""
    return geodata.COUNTRIES.get(country, "").lower()


def abbreviate(country: str, region: str) -> str:
    """A short label for a coverage cell.

    Prefers the real admin1 code, but only when it is alphabetic: most of the
    world's are numeric ("16" for Maharashtra), which tells an operator
    nothing. Those fall back to the first three letters of the name.
    """
    code = geodata.REGIONS.get(country, {}).get(region, "")
    if code.isalpha():
        return code.upper()
    return region[:3].upper()


def leaf_places(selection: Selection) -> list[Place]:
    """One Place per search that a selection implies.

    A country with no regions is one search; a region with no cities is one
    search; otherwise one per city.
    """
    places: list[Place] = []
    for country in sorted(selection):
        chosen = selection[country]
        if not chosen:
            places.append(Place(country=country))
            continue
        for region in sorted(chosen):
            names = chosen[region]
            if not names:
                places.append(Place(country=country, region=region))
                continue
            for city in names:
                places.append(Place(country=country, region=region, city=city))
    return places


def leaf_count(selection: Selection) -> int:
    """How many searches per term the selection implies."""
    return len(leaf_places(selection))


MIN_QUERY = 2


def search_places(query: str, limit: int = 200) -> list[Place]:
    """Places whose name contains `query`, broadest and closest match first.

    The selector's three panes cannot answer "where is Austin?" - you have to
    know it is in Texas before you can find it. This scans all three levels at
    once so the operator does not have to.

    A linear scan over roughly 24,000 names, measured at about 15ms - inside
    one frame, so it can run on every keystroke, and far cheaper to maintain
    than the index that avoiding it would cost. If the city cap ever rises
    well above 25 per region, measure again before assuming it still holds.
    """
    needle = query.strip().casefold()
    if len(needle) < MIN_QUERY:
        return []

    hits: list[tuple[int, int, str, Place]] = []

    def consider(name: str, place: Place) -> None:
        folded = name.casefold()
        position = folded.find(needle)
        if position < 0:
            return
        # depth first (country before region before city), then a prefix
        # match before a match buried mid-name, then alphabetically.
        hits.append((len(place.parts()), 0 if position == 0 else 1, folded, place))

    for country in geodata.COUNTRIES:
        consider(country, Place(country=country))
    for country, regions_of in geodata.REGIONS.items():
        for region in regions_of:
            consider(region, Place(country=country, region=region))
    for (country, region), names in geodata.CITIES.items():
        for city in names:
            consider(city, Place(country=country, region=region, city=city))

    hits.sort(key=lambda hit: hit[:3])
    return [place for _, _, _, place in hits[:limit]]
