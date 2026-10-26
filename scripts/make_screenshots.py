"""Render the README screenshots in docs/ from the example config and made-up sample data.

Usage: python scripts/make_screenshots.py   (run from the repo root)
"""
import datetime as dt
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JELLYFIN_API_KEY", "example")
os.environ.setdefault("PLEX_TOKEN", "example")

from teletextarr import config as cfg  # noqa: E402
from teletextarr.pages import build  # noqa: E402
from teletextarr.render import Renderer  # noqa: E402


class Sample:
    def __init__(self, data):
        self.data = data

    def get(self, number):
        return self.data.get(number)


now = time.time()
utc = dt.timezone.utc
stories = [
    {"title": "Village bakery wins national award for its sourdough", "desc": "Judges praised the loaf's crust and the shop's decision to keep prices unchanged for a fifth year.", "source": "Example News", "time": now - 600},
    {"title": "Heavy rain expected across the north tonight", "desc": "Forecasters say up to 40mm could fall on high ground, with travel disruption likely in the morning.", "source": "Example News", "time": now - 1800},
    {"title": "Library extends opening hours after record visits", "desc": "Weekend opening will continue through the winter following a successful trial.", "source": "Example News", "time": now - 3600},
    {"title": "New cycle path links two market towns", "desc": "The 12-mile route follows an old railway line and opens to the public next month.", "source": "Example News", "time": now - 7200},
]


def forecast(days=5):
    return {"current": {"temperature_2m": 11, "apparent_temperature": 8, "weather_code": 2, "wind_speed_10m": 12, "relative_humidity_2m": 71},
            "daily": {"time": [(dt.date.today() + dt.timedelta(days=i)).isoformat() for i in range(days)],
                      "weather_code": [61, 3, 2, 80, 0][:days], "temperature_2m_max": [14, 13, 15, 12, 16][:days],
                      "temperature_2m_min": [8, 7, 9, 6, 8][:days], "precipitation_probability_max": [80, 20, 10, 60, 0][:days]}}


def show(start, title, sub=""):
    return (dt.datetime.fromtimestamp(start, utc), title, sub)


config = cfg.load(os.path.join(os.path.dirname(__file__), "..", "config.example.yaml"))
places = [(p["name"], p["latitude"], p["longitude"], t, w)
          for p, t, w in zip(config.pages[400].options["map"]["places"], (12, 8, 11, 9), (3, 61, 2, 63))]
data = {
    101: stories,
    400: {"forecasts": [("London", forecast())], "map": places},
    600: [("Comedy Classics", [show(now - 900, "The Office Party", "Pilot"), show(now + 900, "Sitcom Night")]),
          ("Action Movies", [show(now - 3000, "Explosions at Dawn"), show(now + 2400, "Chase Sequence 2")]),
          ("Family Films", [show(now - 1200, "The Brave Little Toaster"), show(now + 4000, "Pony Adventures")])],
    700: {"playing": [("Jellyfin", "A Space Documentary"), ("Plex", "Cooking Show")],
          "latest": [("Film", "The Example (2026)"), ("TV", "Sample Series"), ("Film", "Placeholder Returns (2025)")],
          "disk": (42.5, 120.0)},
}
sources = Sample(data)
renderer = Renderer()
out = os.path.join(os.path.dirname(__file__), "..", "docs")
os.makedirs(out, exist_ok=True)
for name, number, subpage in [("index", 100, 0), ("news", 101, 1), ("weather", 400, 0), ("tv-guide", 600, 0), ("server", 700, 0)]:
    screen = build(config, number, sources)[subpage]
    renderer.with_header(renderer.render(screen), config.brand, number, config.timezone).save(os.path.join(out, f"{name}.png"))
    print("wrote", name)
