import scrape


def test_subscribe_receives_emitted_events():
    seen = []
    listener = lambda kind, data: seen.append((kind, data))
    scrape.subscribe(listener)
    try:
        scrape.emit("query_start", term="padel club", state="Utah")
    finally:
        scrape.unsubscribe(listener)
    assert seen == [("query_start", {"term": "padel club", "state": "Utah"})]


def test_unsubscribe_stops_delivery():
    seen = []
    listener = lambda kind, data: seen.append(kind)
    scrape.subscribe(listener)
    scrape.unsubscribe(listener)
    scrape.emit("query_start", term="x", state="y")
    assert seen == []


def test_emit_survives_a_listener_that_raises():
    seen = []

    def boom(kind, data):
        raise RuntimeError("listener bug")

    scrape.subscribe(boom)
    scrape.subscribe(lambda kind, data: seen.append(kind))
    try:
        scrape.emit("query_start", term="x", state="y")
    finally:
        scrape.unsubscribe(boom)
        scrape._listeners.clear()
    assert seen == ["query_start"], "a broken listener must not stop the run or block others"


def test_emit_with_no_listeners_is_harmless():
    scrape._listeners.clear()
    scrape.emit("query_start", term="x", state="y")


def test_stop_flag_round_trip():
    scrape.clear_stop()
    assert scrape.stop_requested() is False
    scrape.request_stop()
    assert scrape.stop_requested() is True
    scrape.clear_stop()
    assert scrape.stop_requested() is False
