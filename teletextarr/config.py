"""Load and validate the YAML config.

Values written as ${NAME} are read from the environment, so secrets like API keys stay out of the file.
"""
import os
import re
from dataclasses import dataclass, field

import yaml

from . import __version__

COLOURS = {
    "black": (0, 0, 0),
    "red": (255, 0, 0),
    "green": (0, 255, 0),
    "yellow": (255, 255, 0),
    "blue": (0, 0, 255),
    "magenta": (255, 0, 255),
    "cyan": (0, 255, 255),
    "white": (255, 255, 255),
}
PAGE_TYPES = {"index", "rss", "weather", "xmltv", "media_server", "text"}
WEATHER_DETAILS = {"feels_like", "wind", "humidity"}
TEMPERATURE_UNITS = {"celsius", "fahrenheit"}
WIND_UNITS = {"mph", "kmh", "ms", "kn"}
ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class ConfigError(ValueError):
    pass


@dataclass
class Feed:
    name: str
    url: str


@dataclass
class PageConfig:
    number: int
    title: str
    type: str
    banner: str
    channel_name: str
    background: tuple
    text: tuple
    refresh_minutes: float
    feeds: list = field(default_factory=list)
    options: dict = field(default_factory=dict)


@dataclass
class Fastext:
    label: str
    page: int
    colour: tuple


@dataclass
class Config:
    brand: str
    channel_name_format: str
    base_url: str
    port: int
    device_id: str
    tuner_count: int
    fps: int
    timezone: str
    story_seconds: int
    index_seconds: int
    user_agent: str
    fastext: list
    pages: dict  # number -> PageConfig, in config order

    def channel_name(self, number):
        page = self.pages[number]
        return page.channel_name or self.channel_name_format.format(brand=self.brand, title=page.title, number=number)


def _interpolate(value):
    if isinstance(value, str):
        def lookup(match):
            name = match.group(1)
            if name not in os.environ:
                raise ConfigError(f"environment variable {name} is referenced in the config but not set")
            return os.environ[name]
        return ENV_REF.sub(lookup, value)
    if isinstance(value, list):
        return [_interpolate(v) for v in value]
    if isinstance(value, dict):
        return {k: _interpolate(v) for k, v in value.items()}
    return value


def _colour(name, where):
    if name not in COLOURS:
        raise ConfigError(f"{where}: unknown colour {name!r} (use one of {', '.join(COLOURS)})")
    return COLOURS[name]


DEFAULT_REFRESH = {"index": 1, "rss": 10, "weather": 30, "xmltv": 1, "media_server": 1, "text": 60}
PAGE_KEYS = {"number", "title", "type", "banner", "channel_name", "background", "text", "refresh_minutes", "feeds"}


def _weather_options(p, where):
    """Normalise a weather page's options: one or more locations, forecast days, units and details."""
    locations = p.get("locations") or ([p["location"]] if p.get("location") else [])
    if not locations:
        raise ConfigError(f"{where}: weather pages need a location (or a list of locations)")
    for j, loc in enumerate(locations):
        if not isinstance(loc, dict) or not {"latitude", "longitude"} <= loc.keys():
            raise ConfigError(f"{where}.locations[{j}]: needs latitude and longitude")
        loc.setdefault("name", f"{loc['latitude']}, {loc['longitude']}")
    days = int(p.get("days", 5))
    if not 1 <= days <= 7:
        raise ConfigError(f"{where}: days must be 1 to 7")
    units = {"temperature": "celsius", "wind": "mph", **(p.get("units") or {})}
    if units["temperature"] not in TEMPERATURE_UNITS:
        raise ConfigError(f"{where}: units.temperature must be one of {', '.join(sorted(TEMPERATURE_UNITS))}")
    if units["wind"] not in WIND_UNITS:
        raise ConfigError(f"{where}: units.wind must be one of {', '.join(sorted(WIND_UNITS))}")
    details = list(p.get("details", ["feels_like", "wind", "humidity"]))
    if not set(details) <= WEATHER_DETAILS:
        raise ConfigError(f"{where}: details can include {', '.join(sorted(WEATHER_DETAILS))}")
    weather_map = p.get("map")
    if weather_map:
        from .maps import find_country
        try:
            find_country(weather_map.get("country", ""))
        except ValueError as e:
            raise ConfigError(f"{where}.map: {e}") from None
        places = weather_map.get("places") or locations
        for j, place in enumerate(places):
            if not isinstance(place, dict) or not {"latitude", "longitude"} <= place.keys():
                raise ConfigError(f"{where}.map.places[{j}]: needs latitude and longitude")
            place.setdefault("name", f"{place['latitude']}, {place['longitude']}")
        weather_map = {"country": weather_map["country"], "places": places,
                       "neighbours": bool(weather_map.get("neighbours", True)), "title": weather_map.get("title")}
    return {"locations": locations, "days": days, "units": units, "details": details, "map": weather_map}


def parse(raw):
    raw = _interpolate(raw or {})
    server, display = raw.get("server", {}), raw.get("display", {})
    if "base_url" not in server:
        raise ConfigError("server.base_url is required: the address Plex and Jellyfin use to reach Teletextarr")

    pages = {}
    for i, p in enumerate(raw.get("pages") or []):
        where = f"pages[{i}]"
        number, kind = p.get("number"), p.get("type")
        if not isinstance(number, int) or not 100 <= number <= 899:
            raise ConfigError(f"{where}: number must be a page number from 100 to 899")
        if number in pages:
            raise ConfigError(f"{where}: page {number} is defined twice")
        if kind not in PAGE_TYPES:
            raise ConfigError(f"{where}: type must be one of {', '.join(sorted(PAGE_TYPES))}")
        feeds = [Feed(f["name"], f["url"]) for f in p.get("feeds") or []]
        options = {k: v for k, v in p.items() if k not in PAGE_KEYS}
        if kind == "weather":
            options.update(_weather_options(p, where))
            options.pop("location", None)
        if kind == "xmltv" and not p.get("url"):
            raise ConfigError(f"{where}: xmltv pages need a url")
        title = str(p.get("title") or kind.title())
        if kind == "rss" and not feeds:
            raise ConfigError(f"{where}: rss pages need at least one feed")
        pages[number] = PageConfig(
            number=number,
            title=title,
            type=kind,
            banner=str(p.get("banner", title.upper())),
            channel_name=str(p.get("channel_name", "")),
            background=_colour(p.get("background", "blue"), where),
            text=_colour(p.get("text", "white"), where),
            refresh_minutes=float(p.get("refresh_minutes", DEFAULT_REFRESH[kind])),
            feeds=feeds,
            options=options,
        )
    if not pages:
        raise ConfigError("define at least one page")

    fastext = []
    for i, f in enumerate(display.get("fastext") or []):
        if f.get("page") not in pages:
            raise ConfigError(f"display.fastext[{i}]: page {f.get('page')} is not defined")
        fastext.append(Fastext(str(f.get("label", pages[f["page"]].title)), f["page"], _colour(f.get("colour", "white"), f"display.fastext[{i}]")))
    if len(fastext) > 4:
        raise ConfigError("display.fastext: teletext has four coloured buttons, so list at most four")
    if sum(len(f"{f.label} {f.page}") for f in fastext) + max(len(fastext) - 1, 0) > 40:
        raise ConfigError("display.fastext: labels do not fit on one 40-character row; shorten them")

    name_format = str(display.get("channel_name", "{brand} {title}"))
    try:
        name_format.format(brand="", title="", number=0)
    except (KeyError, IndexError, ValueError) as e:
        raise ConfigError(f"display.channel_name: use {{brand}}, {{title}} and {{number}} only ({e})") from None

    fps = int(server.get("fps", 25))
    if not 1 <= fps <= 30:
        raise ConfigError("server.fps must be 1 to 30")

    device_id = str(server.get("device_id", "54545852")).upper()
    if not re.fullmatch(r"[0-9A-F]{8}", device_id):
        raise ConfigError("server.device_id must be 8 hex characters")

    return Config(
        brand=str(display.get("brand", "TELETEXTARR")),
        channel_name_format=name_format,
        base_url=str(server["base_url"]).rstrip("/"),
        port=int(server.get("port", 8098)),
        device_id=device_id,
        tuner_count=int(server.get("tuner_count", 4)),
        fps=fps,
        timezone=str(display.get("timezone", "UTC")),
        story_seconds=int(display.get("story_seconds", 12)),
        index_seconds=int(display.get("index_seconds", 15)),
        user_agent=str(server.get("user_agent", f"Teletextarr/{__version__}")),
        fastext=fastext,
        pages=pages,
    )


def load(path):
    with open(path) as f:
        return parse(yaml.safe_load(f))
