import scrape
import widgets


def test_rounded_points_traces_the_corners_in_order():
    pts = widgets.rounded_points(0, 0, 100, 40, 5)
    assert len(pts) == 24, "12 x,y pairs: two per corner plus the corner itself"
    assert pts[0] == 5 and pts[1] == 0, "starts just right of the top-left corner"


def test_rounded_points_stays_inside_the_rectangle():
    pts = widgets.rounded_points(10, 20, 110, 60, 6)
    xs, ys = pts[0::2], pts[1::2]
    assert min(xs) == 10 and max(xs) == 110
    assert min(ys) == 20 and max(ys) == 60


def test_radius_is_clamped_to_the_shorter_side():
    pts = widgets.rounded_points(0, 0, 100, 10, 40)
    ys = pts[1::2]
    assert min(ys) == 0 and max(ys) == 10, "an oversized radius must not bulge past the box"


def test_a_zero_radius_gives_square_corners():
    pts = widgets.rounded_points(0, 0, 10, 10, 0)
    assert pts[0] == 0 and pts[1] == 0


def test_button_width_pads_both_sides_of_the_label():
    assert widgets.button_width(60, 14) == 88


def test_button_width_respects_a_minimum():
    assert widgets.button_width(10, 14, min_width=120) == 120


def test_every_state_has_an_abbreviation():
    missing = [s for s in scrape.ALL_50 if s not in widgets.STATE_ABBR]
    assert missing == [], f"no abbreviation for {missing}"
    assert len(set(widgets.STATE_ABBR.values())) == 50, "abbreviations must be unique"


def test_cell_rects_lays_out_row_by_row():
    rects = widgets.cell_rects(3, cols=2, cell_w=100, cell_h=40, gap=10)
    assert rects[0] == (0, 0, 100, 40)
    assert rects[1] == (110, 0, 210, 40), "second cell sits one gap to the right"
    assert rects[2] == (0, 50, 100, 90), "third cell wraps to the next row"


def test_cell_rects_honours_an_origin():
    rects = widgets.cell_rects(1, cols=5, cell_w=20, cell_h=10, gap=4, x0=7, y0=9)
    assert rects[0] == (7, 9, 27, 19)


def test_cell_rects_of_nothing_is_empty():
    assert widgets.cell_rects(0, cols=10, cell_w=10, cell_h=10, gap=2) == []


def test_grid_height_counts_partial_rows():
    assert widgets.grid_height(50, cols=10, cell_h=44, gap=6) == 44 * 5 + 6 * 4
    assert widgets.grid_height(51, cols=10, cell_h=44, gap=6) == 44 * 6 + 6 * 5
    assert widgets.grid_height(0, cols=10, cell_h=44, gap=6) == 0


def test_status_colour_covers_every_state_the_reducer_can_produce():
    for status in ("pending", "done", "active", "partial", "failed"):
        assert status in widgets.STATUS_COLORS
