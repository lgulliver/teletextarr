"""Fetch and cache the data each page shows: RSS stories, weather, TV listings, media server status."""
import datetime as dt
import html
import json
import os
import re
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

ATOM = "{http://www.w3.org/2005/Atom}"
DATE_FORMATS = ("%a, %d %b %Y %H:%M:%S %z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z")
PUNCTUATION = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", "…": "...", "\xa0": " "})

WMO_CODES = {
    0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Overcast", 45: "Fog", 48: "Freezing fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle", 56: "Freezing drizzle", 57: "Freezing drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain", 66: "Freezing rain", 67: "Freezing rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains", 80: "Showers", 81: "Heavy showers",
    82: "Violent showers", 85: "Snow showers", 86: "Snow showers", 95: "Thunderstorms", 96: "Thunder & hail",
    99: "Thunder & hail",
}


def clean(text):
    """Reduce feed text to plain characters the teletext font can draw (ASCII plus the pound sign)."""
    text = html.unescape(re.sub(r"<[^>]+>", " ", text or "")).translate(PUNCTUATION)
    text = "".join(c if c == "£" else unicodedata.normalize("NFKD", c).encode("ascii", "ignore").decode() for c in text)
    return re.sub(r"\s+", " ", text).strip()


def parse_date(value):
    value = (value or "").strip().replace("GMT", "+0000").replace("UTC", "+0000").replace("Z", "+0000")
    for fmt in DATE_FORMATS:
        try:
            return dt.datetime.strptime(value, fmt).timestamp()
        except ValueError:
            continue
    return 0


def parse_feed(source, raw):
    """RSS 2.0 or Atom bytes to a list of story dicts."""
    root = ET.fromstring(raw)
    entries = [(i.findtext("title"), i.findtext("description"), i.findtext("pubDate")) for i in root.iter("item")]
    entries += [(e.findtext(f"{ATOM}title"), e.findtext(f"{ATOM}summary") or e.findtext(f"{ATOM}content"),
                 e.findtext(f"{ATOM}updated") or e.findtext(f"{ATOM}published")) for e in root.iter(f"{ATOM}entry")]
    return [{"title": clean(t), "desc": clean(d)[:600], "source": source, "time": parse_date(w)}
            for t, d, w in entries if t]


def parse_xmltv_now_next(raw, now=None):
    """XMLTV bytes to [(channel name, [(start, title, subtitle), ...up to 2])], ordered by channel number."""
    root = ET.fromstring(raw)
    now = now or time.time()
    names = {}
    for c in root.iter("channel"):
        shown = [d.text.strip() for d in c.findall("display-name") if d.text]
        # Guides often list "1 Comedy", "1" and "Comedy"; keep the last plain name and the bare number.
        name = next((n for n in reversed(shown) if not n.isdigit()), c.get("id"))
        number = next((int(n) for n in shown if n.isdigit()), 10**6)
        names[c.get("id")] = (number, clean(name))
    shows = {cid: [] for cid in names}
    for p in root.iter("programme"):
        cid = p.get("channel")
        start = dt.datetime.strptime(p.get("start"), "%Y%m%d%H%M%S %z")
        stop = dt.datetime.strptime(p.get("stop"), "%Y%m%d%H%M%S %z")
        if cid in shows and stop.timestamp() > now:
            shows[cid].append((start, clean(p.findtext("title")), clean(p.findtext("sub-title") or "")))
    ordered = sorted(names, key=lambda cid: names[cid])
    return [(names[cid][1], sorted(shows[cid])[:2]) for cid in ordered]


class Sources:
    """Background refresher that keeps the latest data for every page, keyed by page number."""

    def __init__(self, config):
        self.config = config
        self.data = {}
        self.lock = threading.Lock()

    def get(self, number):
        with self.lock:
            return self.data.get(number)

    def fetch(self, url, headers=None, timeout=20):
        req = urllib.request.Request(url, headers={"User-Agent": self.config.user_agent, **(headers or {})})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()

    def load_rss(self, page):
        stories = []
        for feed in page.feeds:
            try:
                stories += parse_feed(feed.name, self.fetch(feed.url))[: int(page.options.get("max_stories", 8))]
            except Exception as e:
                print(f"page {page.number}: feed {feed.url} failed: {e}", flush=True)
        stories.sort(key=lambda s: s["time"], reverse=True)
        return stories[: int(page.options.get("max_stories", 8))] or None

    def load_weather(self, page):
        """One forecast per configured location, in config order."""
        units = page.options["units"]
        forecasts = []
        for loc in page.options["locations"]:
            query = urllib.parse.urlencode({
                "latitude": loc["latitude"], "longitude": loc["longitude"], "timezone": self.config.timezone,
                "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,relative_humidity_2m",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                "temperature_unit": units["temperature"], "wind_speed_unit": units["wind"],
                "forecast_days": page.options["days"],
            })
            forecasts.append((loc["name"], json.loads(self.fetch(f"https://api.open-meteo.com/v1/forecast?{query}"))))
        places = []
        weather_map = page.options.get("map")
        if weather_map:
            pts = weather_map["places"]
            query = urllib.parse.urlencode({
                "latitude": ",".join(str(p["latitude"]) for p in pts),
                "longitude": ",".join(str(p["longitude"]) for p in pts),
                "current": "temperature_2m,weather_code", "temperature_unit": units["temperature"],
                "timezone": self.config.timezone,
            })
            result = json.loads(self.fetch(f"https://api.open-meteo.com/v1/forecast?{query}"))
            result = result if isinstance(result, list) else [result]  # one place comes back as a bare object
            places = [(p["name"], p["latitude"], p["longitude"], r["current"]["temperature_2m"], r["current"]["weather_code"])
                      for p, r in zip(pts, result)]
        return {"forecasts": forecasts, "map": places}

    def load_xmltv(self, page):
        return parse_xmltv_now_next(self.fetch(page.options["url"]))

    def load_media_server(self, page):
        status = {"playing": [], "latest": [], "disk": None}
        jf = page.options.get("jellyfin")
        if jf:
            base, auth = jf["url"].rstrip("/"), {"Authorization": f'MediaBrowser Token="{jf["api_key"]}"'}
            for s in json.loads(self.fetch(f"{base}/Sessions?activeWithinSeconds=600", auth)):
                item = s.get("NowPlayingItem")
                if item:
                    status["playing"].append(("Jellyfin", clean(item.get("SeriesName") or item["Name"])))
            items = json.loads(self.fetch(f"{base}/Items?Recursive=true&IncludeItemTypes=Movie,Series"
                                          "&SortBy=DateCreated&SortOrder=Descending&Limit=30", auth))["Items"]
            for i in [i for i in items if i["Type"] in ("Movie", "Series")][:7]:  # collections can slip in
                name, year = clean(i["Name"]), i.get("ProductionYear")
                status["latest"].append(("Film" if i["Type"] == "Movie" else "TV", name if not year or f"({year})" in name else f"{name} ({year})"))
        plex = page.options.get("plex")
        if plex:
            root = ET.fromstring(self.fetch(f"{plex['url'].rstrip('/')}/status/sessions", {"X-Plex-Token": plex["token"]}))
            status["playing"] += [("Plex", clean(v.get("grandparentTitle") or v.get("title"))) for v in root]
        if not page.options.get("show_now_playing", True):
            status["playing"] = None
        if page.options.get("disk_path"):
            d = os.statvfs(page.options["disk_path"])
            status["disk"] = (d.f_bavail * d.f_frsize / 1e12, d.f_blocks * d.f_frsize / 1e12)
        return status

    def refresh(self, page):
        loader = {"rss": self.load_rss, "weather": self.load_weather, "xmltv": self.load_xmltv,
                  "media_server": self.load_media_server}.get(page.type)
        if not loader:
            return
        try:
            value = loader(page)
        except Exception as e:
            print(f"page {page.number} ({page.type}) refresh failed: {e}", flush=True)
            return
        if value is not None:
            with self.lock:
                self.data[page.number] = value

    def run(self):
        due = {n: 0.0 for n in self.config.pages}
        while True:
            for number, page in self.config.pages.items():
                if time.time() >= due[number]:
                    self.refresh(page)
                    due[number] = time.time() + page.refresh_minutes * 60
            time.sleep(5)

    def start(self):
        threading.Thread(target=self.run, daemon=True, name="sources").start()
