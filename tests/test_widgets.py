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


def test_widgets_paints_from_the_shared_theme_palette():
    import theme
    assert widgets.PALETTE is theme.PALETTE, "one palette, not a copy that can drift"


def test_visible_slice_covers_the_viewport_and_one_row_of_overscan():
    # 200px tall, 30px rows, scrolled to row 3: rows 3..10 inclusive
    assert widgets.visible_slice(3, 200, 30, 100) == (3, 11)


def test_visible_slice_never_runs_past_the_end():
    assert widgets.visible_slice(95, 200, 30, 100) == (95, 100)


def test_visible_slice_of_an_empty_list_is_empty():
    assert widgets.visible_slice(0, 200, 30, 0) == (0, 0)


def test_clamp_top_refuses_to_scroll_above_the_first_row():
    assert widgets.clamp_top(-4, 200, 30, 100) == 0


def test_clamp_top_stops_with_the_last_row_in_view():
    # 200px shows 6 whole rows, so the furthest top is 100 - 6 = 94
    assert widgets.clamp_top(999, 200, 30, 100) == 94


def test_clamp_top_is_zero_when_everything_already_fits():
    assert widgets.clamp_top(5, 900, 30, 10) == 0


def test_scroll_fractions_span_the_whole_bar_when_everything_fits():
    assert widgets.scroll_fractions(0, 900, 30, 10) == (0.0, 1.0)


def test_scroll_fractions_track_the_scroll_position():
    first, last = widgets.scroll_fractions(50, 300, 30, 100)
    assert first == 0.5 and last == 0.6


def test_scroll_fractions_of_an_empty_list_span_the_whole_bar():
    assert widgets.scroll_fractions(0, 300, 30, 0) == (0.0, 1.0)


def test_a_row_carries_a_name_a_note_and_a_tick():
    row = widgets.Row("Texas", "6/25", True)
    assert (row.name, row.note, row.checked) == ("Texas", "6/25", True)


def test_a_row_needs_only_a_name():
    assert widgets.Row("Texas") == ("Texas", "", False)


def test_group_layout_stacks_each_country_under_its_own_header():
    # two groups of 3 cells, 12 columns, 46px cells, 6px gap, 22px header
    assert widgets.group_layout([3, 3], 12, 46, 6, 22) == [(0, 22), (74, 96)]


def test_layout_height_counts_every_row_of_every_group():
    # 3 cells = 1 row (46) + header 22 -> 68; two of those plus a 6px gap
    assert widgets.layout_height([3, 3], 12, 46, 6, 22) == 142


def test_layout_height_grows_when_a_country_wraps_past_the_column_count():
    """25 regions in 12 columns is three rows, not one."""
    one_row = widgets.layout_height([12], 12, 46, 6, 22)
    three_rows = widgets.layout_height([25], 12, 46, 6, 22)
    assert one_row == 68 and three_rows == 172


def test_layout_height_of_nothing_is_nothing():
    assert widgets.layout_height([], 12, 46, 6, 22) == 0


def test_segment_fills_give_one_colour_per_term_in_order():
    fills = widgets.segment_fills(["a", "b"], {"a": "done", "b": "pending"})
    assert fills == [widgets.PALETTE["lime"], widgets.PALETTE["seg"]]


def test_segment_fills_treat_an_unreported_term_as_pending():
    assert widgets.segment_fills(["a"], {}) == [widgets.PALETTE["seg"]]
    assert widgets.segment_fills(["a"], None) == [widgets.PALETTE["seg"]]


def test_segment_fills_distinguish_every_status():
    row = {"w": "done", "x": "active", "y": "partial", "z": "failed"}
    assert widgets.segment_fills(list("wxyz"), row) == [
        widgets.PALETTE["lime"], widgets.PALETTE["lime_edge"],
        widgets.PALETTE["amber"], widgets.PALETTE["coral"]]


def test_finished_is_the_accent_itself_and_every_other_status_differs():
    """Lime doubles as "finished", which is why green left the status palette.

    Green and lime are indistinguishable in a 7x3px segment, so partly-done
    took amber and failed took coral. The invariant worth guarding is that
    done IS the accent and no other status shares a colour with anything.
    """
    assert widgets.SEGMENT_FILLS["done"] == widgets.PALETTE["lime"]
    fills = list(widgets.SEGMENT_FILLS.values())
    assert len(set(fills)) == len(fills), f"two statuses share a colour: {fills}"


def test_no_status_sits_within_sixty_units_of_another():
    """Anything closer than this is a coin-flip at segment size."""
    import math

    def rgb(value):
        return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))

    fills = sorted(widgets.SEGMENT_FILLS.items())
    close = [(a, b, round(math.dist(rgb(x), rgb(y))))
             for i, (a, x) in enumerate(fills) for b, y in fills[i + 1:]
             if math.dist(rgb(x), rgb(y)) < 60]
    assert close == [], f"too close to tell apart: {close}"


def test_bar_spans_lay_the_statuses_out_left_to_right():
    assert widgets.bar_spans(5, 2, 1, 10, 100) == [
        (0.0, 50.0, widgets.PALETTE["lime"]),
        (50.0, 70.0, widgets.PALETTE["amber"]),
        (70.0, 80.0, widgets.PALETTE["coral"])]


def test_bar_spans_omit_a_status_with_nothing_in_it():
    assert widgets.bar_spans(5, 0, 0, 10, 100) == [(0.0, 50.0, widgets.PALETTE["lime"])]


def test_bar_spans_of_an_empty_country_are_empty():
    assert widgets.bar_spans(0, 0, 0, 0, 100) == []


def test_a_selection_past_the_threshold_is_called_large():
    assert widgets.LARGE_SELECTION == 60
