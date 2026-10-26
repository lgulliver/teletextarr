import datetime as dt
import json
import os
import xml.etree.ElementTree as ET

import pytest
import yaml

from teletextarr import config as cfg
from teletextarr.maps import MosaicMap, find_country, mosaic_bits, mosaic_char
from teletextarr.pages import COLS, FASTEXT_ROW, build
from teletextarr.render import HEIGHT, WIDTH, Carousel, Renderer
from teletextarr.server import discover, ffmpeg_command, lineup, m3u, xmltv
from teletextarr.sources import clean, parse_feed, parse_xmltv_now_next

EXAMPLE = os.path.join(os.path.dirname(__file__), "..", "config.example.yaml")

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Older story</title><description>&lt;p&gt;Body &amp;amp; more&lt;/p&gt;</description>
<pubDate>Sun, 25 Oct 2026 10:00:00 GMT</pubDate></item>
<item><title>Caf\xc3\xa9 \xe2\x80\x9cnews\xe2\x80\x9d \xe2\x80\x94 \xc2\xa35</title><description>Text</description>
<pubDate>Mon, 26 Oct 2026 09:00:00 +0000</pubDate></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Atom story</title><summary>Summary</summary><updated>2026-10-26T08:00:00Z</updated></entry></feed>"""

GUIDE = b"""<?xml version="1.0"?><tv>
<channel id="b"><display-name>2 Films</display-name><display-name>2</display-name><display-name>Films</display-name></channel>
<channel id="a"><display-name>1 Comedy</display-name><display-name>1</display-name><display-name>Comedy</display-name></channel>
<programme channel="a" start="20261026090000 +0000" stop="20261026093000 +0000"><title>Past</title></programme>
<programme channel="a" start="20261026100000 +0000" stop="20261026103000 +0000"><title>Now Show</title><sub-title>Pilot</sub-title></programme>
<programme channel="a" start="20261026103000 +0000" stop="20261026110000 +0000"><title>Next Show</title></programme>
<programme channel="b" start="20261026090000 +0000" stop="20261026120000 +0000"><title>Long Film</title></programme>
</tv>"""


class FakeSources:
    def __init__(self, data):
        self.data = data

    def get(self, number):
        return self.data.get(number)


@pytest.fixture
def config(monkeypatch):
    monkeypatch.setenv("JELLYFIN_API_KEY", "jf-key")
    monkeypatch.setenv("PLEX_TOKEN", "plex-token")
    return cfg.load(EXAMPLE)


def page(raw, number):
    return next(p for p in raw["pages"] if p["number"] == number)


def raw_example():
    with open(EXAMPLE) as f:
        return yaml.safe_load(f)


def test_example_config_loads_with_env_secrets(config):
    assert list(config.pages)[:2] == [100, 101]
    assert config.pages[700].options["jellyfin"]["api_key"] == "jf-key"
    assert config.channel_name(101) == "TELETEXTARR News"


def test_missing_env_var_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("JELLYFIN_API_KEY", raising=False)
    monkeypatch.setenv("PLEX_TOKEN", "x")
    with pytest.raises(cfg.ConfigError, match="JELLYFIN_API_KEY"):
        cfg.parse(raw_example())


@pytest.mark.parametrize("mutate, message", [
    (lambda r: r["pages"].append(dict(r["pages"][0])), "defined twice"),
    (lambda r: r["pages"][0].update(number=99), "100 to 899"),
    (lambda r: r["pages"][0].update(type="teletext"), "type must be"),
    (lambda r: r["pages"][1].update(feeds=[]), "at least one feed"),
    (lambda r: r["pages"][1].update(background="orange"), "unknown colour"),
    (lambda r: r["display"]["fastext"].append({"label": "X", "page": 999}), "not defined"),
    (lambda r: r["display"]["fastext"][0].update(label="An extremely long fastext label"), "40-character"),
    (lambda r: r["server"].update(device_id="nothex!!"), "hex"),
    (lambda r: r["server"].pop("base_url"), "base_url"),
    (lambda r: r["server"].update(fps=60), "fps"),
    (lambda r: r["display"].update(channel_name="{brand} {nope}"), "channel_name"),
    (lambda r: page(r, 400).pop("locations"), "location"),
    (lambda r: page(r, 400).update(days=9), "1 to 7"),
    (lambda r: page(r, 400).update(units={"temperature": "kelvin"}), "units.temperature"),
    (lambda r: page(r, 400).update(details=["pollen"]), "details"),
    (lambda r: page(r, 600).pop("url"), "need a url"),
])
def test_invalid_configs_are_rejected(monkeypatch, mutate, message):
    monkeypatch.setenv("JELLYFIN_API_KEY", "x")
    monkeypatch.setenv("PLEX_TOKEN", "x")
    raw = raw_example()
    mutate(raw)
    with pytest.raises(cfg.ConfigError, match=message):
        cfg.parse(raw)


def test_clean_keeps_text_drawable():
    assert clean("<p>Café “quote” — £5&nbsp;off</p>") == 'Cafe "quote" - £5 off'


def test_parse_rss_and_atom():
    rss = parse_feed("Test", RSS)
    assert [s["title"] for s in rss] == ["Older story", 'Cafe "news" - £5']
    assert rss[0]["desc"] == "Body & more"
    assert rss[1]["time"] > rss[0]["time"] > 0
    atom = parse_feed("Atom", ATOM)
    assert atom[0]["title"] == "Atom story" and atom[0]["time"] > 0


def test_xmltv_now_next_orders_channels_and_skips_finished():
    now = dt.datetime(2026, 10, 26, 10, 10, tzinfo=dt.timezone.utc).timestamp()
    result = parse_xmltv_now_next(GUIDE, now)
    assert [name for name, _ in result] == ["Comedy", "Films"]
    assert [(t, s) for _, t, s in result[0][1]] == [("Now Show", "Pilot"), ("Next Show", "")]
    assert result[1][1][0][1] == "Long Film"


def test_rss_page_has_index_then_one_subpage_per_story(config):
    stories = parse_feed("Test", RSS)
    screens = build(config, 101, FakeSources({101: stories}))
    assert len(screens) == 3
    assert "1/3" in screens[0].text(22)
    assert "Older story" in screens[1].text()
    assert "£5" in screens[2].text()
    assert screens[0].text(FASTEXT_ROW).startswith("News 101")


def test_every_page_type_builds_without_data(config):
    for number in config.pages:
        screens = build(config, number, FakeSources({}))
        assert screens and all(len(row) == COLS for s in screens for row in s.cells)


def test_media_server_page_hides_now_playing_when_disabled(config):
    config.pages[700].options["show_now_playing"] = False
    status = {"playing": None, "latest": [("Film", "Runner (2026)")], "disk": (1.5, 4.0)}
    text = build(config, 700, FakeSources({700: status}))[0].text()
    assert "NOW PLAYING" not in text and "Runner (2026)" in text and "1.5 of 4.0 TB" in text


def test_text_page(config):
    text = build(config, 888, FakeSources({}))[0].text()
    assert "Welcome to Teletextarr." in text


def test_render_produces_a_720p_frame(config):
    renderer = Renderer()
    img = Carousel(config, renderer, lambda n: build(config, n, FakeSources({}))).frame(100)
    assert img.size == (WIDTH, HEIGHT)
    assert img.getbbox() is not None


def test_tuner_endpoints_describe_every_page(config):
    assert discover(config)["DeviceID"] == "54545852"
    assert [c["GuideNumber"] for c in lineup(config)] == [str(n) for n in config.pages]
    playlist = m3u(config)
    assert playlist.startswith("#EXTM3U") and 'tvg-id="teletextarr.101"' in playlist
    guide = ET.fromstring(xmltv(config))
    assert {c.get("id") for c in guide.iter("channel")} == {f"teletextarr.{n}" for n in config.pages}
    assert len(list(guide.iter("programme"))) == len(config.pages) * 50
    json.dumps(discover(config))


def forecast(days):
    return {
        "current": {"temperature_2m": 10.4, "apparent_temperature": 6, "weather_code": 3, "wind_speed_10m": 8,
                    "relative_humidity_2m": 69},
        "daily": {"time": [(dt.date(2026, 10, 26) + dt.timedelta(days=i)).isoformat() for i in range(days)], "weather_code": [61] * days,
                  "temperature_2m_max": [16] * days, "temperature_2m_min": [9] * days,
                  "precipitation_probability_max": [100] * days},
    }


def test_weather_has_a_subpage_per_location(config):
    data = {"forecasts": [("London", forecast(5)), ("Edinburgh", forecast(5))], "map": []}
    screens = build(config, 400, FakeSources({400: data}))
    assert len(screens) == 2
    assert "LONDON" in screens[0].text() and "EDINBURGH" in screens[1].text()
    assert "Feels like 6C" in screens[0].text() and "Wind 8 mph" in screens[0].text()


def test_weather_details_and_seven_days(config):
    config.pages[400].options.update(details=[], days=7)
    text = build(config, 400, FakeSources({400: {"forecasts": [("London", forecast(7))], "map": []}}))[0].text()
    assert "Feels like" not in text and "Humidity" not in text
    assert text.count("Light rain") == 7


def test_single_location_still_accepted(monkeypatch):
    monkeypatch.setenv("JELLYFIN_API_KEY", "x")
    monkeypatch.setenv("PLEX_TOKEN", "x")
    raw = raw_example()
    weather = page(raw, 400)
    weather["location"] = weather.pop("locations")[0]
    assert cfg.parse(raw).pages[400].options["locations"][0]["name"] == "London"


def test_channel_names_and_banners_are_configurable(config):
    assert config.channel_name(101) == "TELETEXTARR News"
    assert config.channel_name(888) == "Teletext Help"
    assert "ABOUT THIS SERVICE" in build(config, 888, FakeSources({}))[0].text(1)
    config.channel_name_format = "{number} {title}"
    assert config.channel_name(300) == "300 Sport"


def test_mosaic_characters_round_trip():
    assert all(mosaic_bits(mosaic_char(bits)) == bits for bits in range(1, 64))
    assert mosaic_char(0) == " "  # an empty cell is just a space
    assert mosaic_bits("A") is None


def test_country_lookup_by_code_or_name():
    assert find_country("gb")["name"] == find_country("United Kingdom")["name"]
    with pytest.raises(ValueError, match="unknown country"):
        find_country("Atlantis")


def test_uk_map_places_cities_in_the_right_order():
    bolton, london, edinburgh = (53.58, -2.43), (51.51, -0.13), (55.95, -3.19)
    m = MosaicMap("GB", 22, 17, places=(bolton, london, edinburgh))
    assert m.cols <= 22 and m.rows <= 17
    (rb, cb), (rl, cl), (re, ce) = m.cell_of(*bolton), m.cell_of(*london), m.cell_of(*edinburgh)
    assert re < rb < rl  # north to south
    assert cl > cb  # London is east of Bolton
    land = sum(bin(mosaic_bits(ch) or 0).count("1") for line in m.cells for ch in line)
    assert land > 100


def test_weather_map_is_the_first_subpage(config):
    places = [("London", 51.51, -0.13, 11.4, 3), ("Edinburgh", 55.95, -3.19, 7.6, 61)]
    data = {"forecasts": [("London", forecast(5)), ("Edinburgh", forecast(5))], "map": places}
    screens = build(config, 400, FakeSources({400: data}))
    assert len(screens) == 3 and "1/3" in screens[0].text(22)
    text = screens[0].text()
    assert "London" in text and "11C" in text and "Light rain" in text
    assert any(mosaic_bits(ch) for row in screens[0].cells for ch, _, _ in row)
    img = Renderer().render(screens[0])
    assert img.size == (WIDTH, HEIGHT)


def test_unknown_map_country_is_rejected(monkeypatch):
    monkeypatch.setenv("JELLYFIN_API_KEY", "x")
    monkeypatch.setenv("PLEX_TOKEN", "x")
    raw = raw_example()
    page(raw, 400)["map"] = {"country": "Atlantis"}
    with pytest.raises(cfg.ConfigError, match="unknown country"):
        cfg.parse(raw)


def test_encoder_converts_before_duplicating_frames(config):
    cmd = ffmpeg_command(config.fps)
    assert cmd[cmd.index("-vf") + 1] == "format=yuv420p,fps=25"
    assert cmd[cmd.index("-g") + 1] == "50"
    assert ffmpeg_command(10)[cmd.index("-vf") + 1] == "format=yuv420p,fps=10"


# --- shared encoders and lazy refresh ------------------------------------------------------------

import sys
import threading
import time as _time

from teletextarr.broadcast import Broadcaster
from teletextarr.sources import Sources

# Stands in for ffmpeg: copies stdin to stdout in TS-packet-sized pieces.
ECHO = [sys.executable, "-c", "import sys\nwhile d := sys.stdin.buffer.read(188):\n    sys.stdout.buffer.write(d); sys.stdout.buffer.flush()"]


def drain(q, n, timeout=5):
    got = []
    deadline = _time.monotonic() + timeout
    while len(got) < n and _time.monotonic() < deadline:
        try:
            item = q.get(timeout=0.5)
        except Exception:
            continue
        got.append(item)
    return got


def test_viewers_of_a_page_share_one_encoder():
    started = []

    def frames_for(number):
        started.append(number)
        return lambda: b"\x47" * (188 * 64)

    b = Broadcaster(ECHO, frames_for, linger=0.5)
    first, q1 = b.join(101)
    second, q2 = b.join(101)
    assert first is second and started == [101]
    assert len(drain(q1, 1)) == 1 and len(drain(q2, 1)) == 1
    assert b.active() == {101: 2}
    first.unsubscribe(q1)
    first.unsubscribe(q2)
    deadline = _time.monotonic() + 5
    while b.active() and _time.monotonic() < deadline:
        _time.sleep(0.1)
    assert b.active() == {} and first.stopped.is_set()
    assert first.proc.poll() is not None  # the encoder process is gone


def test_a_viewer_returning_within_the_linger_reuses_the_encoder():
    b = Broadcaster(ECHO, lambda n: (lambda: b"\x47" * 188 * 64), linger=5)
    first, q = b.join(300)
    first.unsubscribe(q)
    again, q2 = b.join(300)
    assert again is first and not first.stopped.is_set()
    again.unsubscribe(q2)
    first.stop()


def test_idle_pages_are_not_refreshed(config):
    refreshed = []
    sources = Sources(config)
    sources.refresh = lambda page: refreshed.append(page.number)
    threading.Thread(target=sources.run, daemon=True).start()
    _time.sleep(0.3)
    assert refreshed == []  # nobody watching: nothing fetched
    with sources.watching(100):
        _time.sleep(0.3)
    assert sorted(refreshed) == [100, 101]  # the index and the page it takes headlines from


def test_refresh_when_idle_keeps_everything_fresh(config):
    config.refresh_when_idle = True
    refreshed = []
    sources = Sources(config)
    sources.refresh = lambda page: refreshed.append(page.number)
    threading.Thread(target=sources.run, daemon=True).start()
    _time.sleep(0.3)
    assert sorted(refreshed) == sorted(config.pages)
