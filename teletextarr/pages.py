"""Build teletext screens (40x25 character grids) for each page type."""
import datetime as dt
import textwrap
from zoneinfo import ZoneInfo

from .config import COLOURS
from .maps import cached_map
from .sources import WMO_CODES

COLS, ROWS = 40, 25
BLACK, RED, GREEN, YELLOW = COLOURS["black"], COLOURS["red"], COLOURS["green"], COLOURS["yellow"]
BLUE, MAGENTA, CYAN, WHITE = COLOURS["blue"], COLOURS["magenta"], COLOURS["cyan"], COLOURS["white"]
BODY_TOP, BODY_BOTTOM, SUBPAGE_ROW, FASTEXT_ROW = 4, 21, 22, 24


class Screen:
    """One teletext screen: a character and colours per cell, plus which rows are double height."""

    def __init__(self):
        self.cells = [[(" ", WHITE, BLACK) for _ in range(COLS)] for _ in range(ROWS)]
        self.double = set()

    def put(self, row, col, text, fg=WHITE, bg=BLACK):
        for i, ch in enumerate(text):
            if 0 <= col + i < COLS and 0 <= row < ROWS:
                self.cells[row][col + i] = (ch, fg, bg)

    def fill(self, row, bg):
        self.cells[row] = [(ch, fg, bg) for ch, fg, _ in self.cells[row]]

    def heading(self, row, text, fg, bg):
        self.fill(row, bg)
        self.fill(row + 1, bg)
        self.put(row, 1, text, fg, bg)
        self.double.add(row)

    def text(self, row=None):
        """Plain-text dump, handy for tests and debugging."""
        rows = range(ROWS) if row is None else [row]
        return "\n".join("".join(ch for ch, _, _ in self.cells[r]).rstrip() for r in rows)


def fit(text, width):
    return text if len(text) <= width else text[: width - 3].rstrip() + "..."


def framed(config, page, subpage=None):
    """A screen with the page's banner, subpage counter and the fastext row."""
    screen = Screen()
    screen.heading(1, fit(page.banner, 33), page.text, page.background)
    screen.put(1, COLS - 4, str(page.number), page.text, page.background)
    if subpage:
        screen.put(SUBPAGE_ROW, COLS - 1 - len(subpage), subpage, WHITE)
    labels = [f"{f.label} {f.page}" for f in config.fastext]
    if labels:
        gap = (COLS - sum(map(len, labels))) // max(len(labels) - 1, 1)
        col = 0
        for f, label in zip(config.fastext, labels):
            screen.put(FASTEXT_ROW, col, label, f.colour)
            col += len(label) + gap
    return screen


def waiting(config, page, message):
    screen = framed(config, page)
    screen.put(6, 2, message, YELLOW)
    return [screen]


def build_index(config, page, sources):
    screen = framed(config, page)
    row = BODY_TOP
    for number, other in config.pages.items():
        if number == page.number or row > BODY_BOTTOM - 5:
            continue
        screen.put(row, 2, fit(other.title, 28) + " " + "." * (30 - len(fit(other.title, 28))), CYAN if row % 2 else WHITE)
        screen.put(row, 34, str(number), YELLOW)
        row += 1
    headlines_from = page.options.get("headlines_from")
    stories = sources.get(headlines_from) if headlines_from else None
    if stories:
        row += 1
        screen.put(row, 2, "LATEST", YELLOW)
        for story in stories[: BODY_BOTTOM - row]:
            row += 1
            screen.put(row, 2, fit(story["title"], 37), WHITE)
    return [screen]


def build_rss(config, page, sources, tz):
    stories = sources.get(page.number)
    if not stories:
        return waiting(config, page, "Waiting for the latest stories...")
    total = len(stories) + 1
    index = framed(config, page, f"1/{total}")
    row = BODY_TOP
    for n, story in enumerate(stories, start=2):
        lines = textwrap.wrap(story["title"], 34)[:2]
        if row + len(lines) > BODY_BOTTOM:
            break
        for i, line in enumerate(lines):
            index.put(row + i, 1, line, WHITE if n % 2 else CYAN)
        index.put(row, 37, f"{n:>2}", YELLOW)
        row += len(lines)
    screens = [index]
    for n, story in enumerate(stories, start=2):
        screen = framed(config, page, f"{n}/{total}")
        row = BODY_TOP
        for line in textwrap.wrap(story["title"], 38)[:3]:
            screen.put(row, 1, line, YELLOW)
            row += 1
        row += 1
        for line in textwrap.wrap(story["desc"], 38)[: BODY_BOTTOM - 1 - row]:
            screen.put(row, 1, line, WHITE)
            row += 1
        when = dt.datetime.fromtimestamp(story["time"], tz).strftime("%H:%M %d %b") if story["time"] else ""
        screen.put(BODY_BOTTOM, 1, fit(f"{story['source']}  {when}", 36), CYAN)
        screens.append(screen)
    return screens


WIND_LABELS = {"mph": "mph", "kmh": "km/h", "ms": "m/s", "kn": "kn"}


def build_weather(config, page, sources):
    """One subpage per location: conditions now, then a 1-7 day forecast table."""
    data = sources.get(page.number)
    if not data:
        return waiting(config, page, "Fetching the forecast...")
    units, details = page.options["units"], page.options["details"]
    deg = "F" if units["temperature"] == "fahrenheit" else "C"
    forecasts, places = data["forecasts"], data["map"]
    total = len(forecasts) + (1 if places else 0)
    screens = [build_weather_map(config, page, places, deg, f"1/{total}" if total > 1 else None)] if places else []
    for name, w in forecasts:
        cur, daily = w["current"], w["daily"]
        n = len(screens) + 1
        screen = framed(config, page, f"{n}/{total}" if total > 1 else None)
        screen.put(4, 1, fit(str(name).upper(), 38), YELLOW)
        screen.put(6, 1, fit(f"Now  {round(cur['temperature_2m'])}{deg}  {WMO_CODES.get(cur['weather_code'], '')}", 38), WHITE)
        extra = []
        if "feels_like" in details:
            extra.append(f"Feels like {round(cur['apparent_temperature'])}{deg}")
        if "wind" in details:
            extra.append(f"Wind {round(cur['wind_speed_10m'])} {WIND_LABELS[units['wind']]}")
        if "humidity" in details:
            extra.append(f"Humidity {cur['relative_humidity_2m']}%")
        for i, line in enumerate(["   ".join(extra[:2]), "   ".join(extra[2:])]):
            if line:
                screen.put(7 + i, 1, fit(line, 38), CYAN)
        days = daily["time"][: page.options["days"]]
        step = 2 if len(days) <= 5 else 1
        screen.put(10, 1, "Day   Weather          Max  Min  Rain", YELLOW)
        for i, day in enumerate(days):
            line = (f"{dt.date.fromisoformat(day):%a}   {fit(WMO_CODES.get(daily['weather_code'][i], ''), 16):<16}"
                    f" {round(daily['temperature_2m_max'][i]):>3}{deg} {round(daily['temperature_2m_min'][i]):>3}{deg}"
                    f" {daily['precipitation_probability_max'][i] or 0:>4}%")
            screen.put(11 + i * step, 1, line, WHITE if i % 2 == 0 else CYAN)
        screen.put(BODY_BOTTOM, 1, "Forecast: Open-Meteo.com", CYAN)
        screens.append(screen)
    return screens


MAP_TOP, MAP_ROWS, MAP_MAX_COLS = 4, 17, 22


def build_weather_map(config, page, places, deg, subpage):
    """Ceefax-style map: the country in green mosaic blocks, temperatures in boxes, and a key alongside."""
    m = page.options["map"]
    mosaic = cached_map(m["country"], MAP_MAX_COLS, MAP_ROWS, tuple((p[1], p[2]) for p in places), m["neighbours"])
    screen = framed(config, page, subpage)
    for r, line in enumerate(mosaic.cells):
        screen.put(MAP_TOP + r, 1, "".join(line), GREEN)
    taken = set()
    for name, lat, lon, temp, code in places:
        r, c = mosaic.cell_of(lat, lon)
        label = f"{round(temp)}"
        row, col = MAP_TOP + r, 1 + c
        # Keep a one-cell gap between labels so neighbouring temperatures don't run together.
        while any((row, col + i) in taken for i in range(-1, len(label) + 1)) and row < MAP_TOP + mosaic.rows:
            row += 1
        screen.put(row, col, label, WHITE, BLUE)
        taken.update((row, col + i) for i in range(len(label)))
    key_col = 1 + mosaic.cols + 2
    width = COLS - key_col - 1
    if m.get("title"):
        screen.put(MAP_TOP, key_col, fit(str(m["title"]).upper(), width), YELLOW)
    row = MAP_TOP + (2 if m.get("title") else 0)
    for name, lat, lon, temp, code in places:
        if row > BODY_BOTTOM:
            break
        screen.put(row, key_col, fit(f"{fit(str(name), width - 5):<{width - 5}}{round(temp):>3}{deg}", width), WHITE)
        screen.put(row + 1, key_col, fit(WMO_CODES.get(code, ""), width), CYAN)
        row += 2
    return screen


def build_xmltv(config, page, sources, tz):
    channels = sources.get(page.number)
    if not channels:
        return waiting(config, page, "Loading the listings...")
    per_screen = int(page.options.get("channels_per_screen", 5))
    screens = []
    chunks = [channels[i:i + per_screen] for i in range(0, len(channels), per_screen)]
    for c, chunk in enumerate(chunks, start=1):
        screen = framed(config, page, f"{c}/{len(chunks)}" if len(chunks) > 1 else None)
        row = BODY_TOP
        for name, shows in chunk:
            screen.put(row, 1, fit(name, 38), YELLOW)
            for label, (start, title, sub), colour in zip(("NOW ", "NEXT"), shows, (WHITE, CYAN)):
                row += 1
                screen.put(row, 1, f"{label} {start.astimezone(tz):%H:%M} {fit(f'{title}: {sub}' if sub else title, 27)}", colour)
            row += 2
        screens.append(screen)
    return screens


def build_media_server(config, page, sources):
    s = sources.get(page.number)
    if not s:
        return waiting(config, page, "Checking the server...")
    screen = framed(config, page)
    row = BODY_TOP
    if s["playing"] is not None:
        screen.put(row, 1, "NOW PLAYING", YELLOW)
        for app, title in s["playing"][:5] or [("", "Nothing playing right now")]:
            row += 1
            screen.put(row, 1, fit(f"{app:<9}{title}", 38), WHITE)
        row += 2
    if s["latest"]:
        screen.put(row, 1, "JUST ADDED", YELLOW)
        for kind, name in s["latest"][: BODY_BOTTOM - row - 2]:
            row += 1
            screen.put(row, 1, fit(f"{kind:<5}{name}", 38), CYAN)
    if s["disk"]:
        free, total = s["disk"]
        screen.put(BODY_BOTTOM - 1, 1, f"Storage free {free:.1f} of {total:.1f} TB", WHITE)
    return [screen]


def build_text(config, page):
    screen = framed(config, page)
    row = BODY_TOP
    for line in page.options.get("lines") or []:
        if isinstance(line, dict):
            text, colour = str(line.get("text", "")), COLOURS.get(line.get("colour", "white"), WHITE)
        else:
            text, colour = str(line), WHITE
        for part in textwrap.wrap(text, 38) or [""]:
            if row > BODY_BOTTOM:
                return [screen]
            screen.put(row, 1, part, colour)
            row += 1
    return [screen]


def build(config, number, sources):
    page = config.pages[number]
    tz = ZoneInfo(config.timezone)
    if page.type == "index":
        return build_index(config, page, sources)
    if page.type == "rss":
        return build_rss(config, page, sources, tz)
    if page.type == "weather":
        return build_weather(config, page, sources)
    if page.type == "xmltv":
        return build_xmltv(config, page, sources, tz)
    if page.type == "media_server":
        return build_media_server(config, page, sources)
    return build_text(config, page)
