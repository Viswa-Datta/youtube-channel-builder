"""Motion graphics: only verified numbers, dates, quotes and places reach the screen; anchors find their sentence."""
from ycb.agents.core import verify_graphics
from ycb.stages.pipeline import _anchor_time

NARR = ("The Empire State Building rose 1,454 feet above Midtown. Work began in 1930 and the tower opened in 1931. "
        "Crews added four and a half floors a week, a pace no one had seen before.")
CLAIMS = ["The building cost 40.9 million dollars.", "The Chrysler Building is 1,046 feet tall."]


def geo(name):
    return {"New York": (40.71, -74.0, "New York"), "Chicago": (41.88, -87.63, "Chicago"),
            "Empire State Building, New York": (40.748, -73.986, "x")}.get(name)


def test_numbers_must_be_sourced():
    out = verify_graphics([
        {"kind": "counter", "value": 1454, "unit": "feet", "title": "Height"},
        {"kind": "counter", "value": 1500, "unit": "feet", "title": "Invented"},
        {"kind": "bars", "title": "Heights", "labels": ["Empire State", "Chrysler"], "values": [1454, 1046]},
        {"kind": "bars", "title": "Bad", "labels": ["A", "B"], "values": [1454, 999]},
    ], NARR, CLAIMS, geocode=geo)
    assert [(g["kind"], g.get("value")) for g in out] == [("counter", 1454.0), ("bars", None)]


def test_timeline_quote_places():
    out = verify_graphics([
        {"kind": "timeline", "items": ["1930: Work began", "1931: Opened", "1945: Plane crash"]},
        {"kind": "timeline", "items": ["1930: Work began", "1931: Opened", "1929: Demolition"]},
        {"kind": "quote", "text": "Crews added four and a half floors a week, a pace no one had seen before."},
        {"kind": "quote", "text": "This sentence was never in the narration at all, not even close to it."},
        {"kind": "place", "place": "Empire State Building, New York"},
        {"kind": "place", "place": "Atlantis"},
        {"kind": "route", "from_place": "New York", "to_place": "Chicago"},
        {"kind": "explosion"},
    ], NARR, CLAIMS, geocode=geo)
    kinds = [g["kind"] for g in out]
    assert kinds == ["quote", "place", "route"]  # both timelines cite a year not in the text (1945, 1929)
    assert out[1]["lat"] == 40.748 and out[1]["place_label"] == "Empire State Building"
    assert out[2]["a"][2] == "New York" and out[2]["b"][2] == "Chicago"


def test_anchor_time():
    sents = [{"text": "The tower rose fast.", "start": 0.0, "end": 2.0},
             {"text": "Crews added four and a half floors a week.", "start": 2.0, "end": 5.0}]
    assert _anchor_time("added four and a half floors a week", sents) == 2.0
    assert _anchor_time("something entirely different here", sents) is None


def test_beats_group_short_and_split_long_sentences():
    from ycb.agents.core import make_beats
    s = [{"start": 0, "end": 2, "text": "A."}, {"start": 2, "end": 5, "text": "B."},
         {"start": 5, "end": 19, "text": "Long C."}, {"start": 19, "end": 25, "text": "D."}]
    b = make_beats(s)
    assert [x["text"] for x in b] == ["A. B.", "Long C.", "Long C. (continued)", "D."]
    assert b[0]["start"] == 0 and b[-1]["end"] == 25 and all(x["end"] > x["start"] for x in b)


def test_genmedia_finds_video_in_any_response_shape():
    from ycb.providers.genmedia import _find_video
    assert _find_video({"outputs": [{"type": "text", "text": "x"}, {"type": "video", "uri": "https://f/v.mp4"}]}) == ("uri", "https://f/v.mp4")
    assert _find_video({"output": {"content": [{"mime_type": "video/mp4", "data": "AAA"}]}}) == ("data", "AAA")
    assert _find_video({"status": "in_progress"}) is None


def test_genmedia_billing_errors_switch_generation_off():
    import requests
    from ycb.providers.genmedia import GenUnavailable, _raise_for
    r = requests.Response()
    r.status_code, r._content = 429, b'{"error": {"message": "Free tier quota is 0 for this model. Enable billing."}}'
    try:
        _raise_for(r)
        raise AssertionError("expected GenUnavailable")
    except GenUnavailable:
        pass
