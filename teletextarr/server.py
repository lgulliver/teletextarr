"""HTTP side: HDHomeRun emulation, M3U and XMLTV for Plex/Jellyfin, and the live MPEG-TS streams."""
import datetime as dt
import io
import json
import queue
import re
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .broadcast import Broadcaster
from .render import HEIGHT, WIDTH, Carousel

VIEWER_TIMEOUT = 30  # seconds a viewer's socket may stall before we give up on it

def ffmpeg_command(fps):
    """1 fps of rendered pages in, H.264 at `fps` plus silent mono AAC out.

    Measured on a Broadwell Xeon, a stream costs about 25% of a core at 25 fps and 13% at 10 fps.
    Converting to YUV before frames are duplicated, and x264's ultrafast preset, make most of that
    difference: pages are static between subpage changes, so a slower preset has little to find.
    NVIDIA's NVENC uses less CPU but wakes the GPU, adding ~13 W, so x264 on the CPU is the frugal choice.
    """
    return ["ffmpeg", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{WIDTH}x{HEIGHT}",
            "-framerate", "1", "-i", "pipe:0", "-re", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
            "-map", "0:v", "-map", "1:a", "-vf", f"format=yuv420p,fps={fps}", "-c:v", "libx264",
            "-preset", "ultrafast", "-tune", "stillimage,zerolatency", "-g", str(fps * 2),
            "-c:a", "aac", "-b:a", "32k", "-f", "mpegts", "pipe:1"]


GUIDE_HOURS = 48


def channel_id(number):
    return f"teletextarr.{number}"


def discover(config):
    return {"FriendlyName": config.brand.title(), "Manufacturer": "Teletextarr - Silicondust", "ModelNumber": "HDTC-2US",
            "FirmwareName": "hdhomeruntc_atsc", "FirmwareVersion": "20200101", "DeviceID": config.device_id,
            "DeviceAuth": "teletextarr", "TunerCount": config.tuner_count, "BaseURL": config.base_url,
            "LineupURL": f"{config.base_url}/lineup.json"}


def lineup(config):
    return [{"GuideNumber": str(n), "GuideName": config.channel_name(n), "URL": f"{config.base_url}/stream/{n}"}
            for n in config.pages]


def m3u(config):
    lines = ["#EXTM3U"]
    for n in config.pages:
        name = config.channel_name(n)
        lines.append(f'#EXTINF:-1 tvg-id="{channel_id(n)}" tvg-chno="{n}" tvg-name="{name}" group-title="{config.brand}",{name}')
        lines.append(f"{config.base_url}/stream/{n}")
    return "\n".join(lines) + "\n"


def xmltv(config, now=None):
    tv = ET.Element("tv", {"generator-info-name": "Teletextarr"})
    for n in config.pages:
        ch = ET.SubElement(tv, "channel", {"id": channel_id(n)})
        ET.SubElement(ch, "display-name").text = config.channel_name(n)
        ET.SubElement(ch, "display-name").text = str(n)
    start = (now or dt.datetime.now(dt.timezone.utc)).replace(minute=0, second=0, microsecond=0) - dt.timedelta(hours=1)
    for n, page in config.pages.items():
        for h in range(GUIDE_HOURS + 2):
            begin = start + dt.timedelta(hours=h)
            p = ET.SubElement(tv, "programme", {"channel": channel_id(n), "start": begin.strftime("%Y%m%d%H%M%S +0000"),
                                                 "stop": (begin + dt.timedelta(hours=1)).strftime("%Y%m%d%H%M%S +0000")})
            ET.SubElement(p, "title").text = config.channel_name(n)
            ET.SubElement(p, "desc").text = f"Teletext page {n}: {page.title}. Change channel to move between pages."
            ET.SubElement(p, "category").text = "News"
    return ET.tostring(tv, encoding="utf-8", xml_declaration=True)


def make_handler(config, renderer, build, sources, broadcaster):
    class Handler(BaseHTTPRequestHandler):
        def send(self, body, ctype):
            if isinstance(body, str):
                body = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?")[0]
            if path in ("/discover.json", "/device.json"):
                return self.send(json.dumps(discover(config)), "application/json")
            if path == "/lineup.json":
                return self.send(json.dumps(lineup(config)), "application/json")
            if path == "/lineup_status.json":
                return self.send(json.dumps({"ScanInProgress": 0, "ScanPossible": 1, "Source": "Cable", "SourceList": ["Cable"]}), "application/json")
            if path == "/channels.m3u":
                return self.send(m3u(config), "audio/x-mpegurl")
            if path == "/xmltv.xml":
                return self.send(xmltv(config), "application/xml")
            if path == "/health":
                return self.send("ok", "text/plain")
            m = re.fullmatch(r"/(stream|preview)/(\d+)(?:\.png)?", path)
            if m and int(m.group(2)) in config.pages:
                number = int(m.group(2))
                if m.group(1) == "preview":
                    with sources.watching(number):
                        sources.wait_for(number)
                        buf = io.BytesIO()
                        Carousel(config, renderer, build).frame(number).save(buf, "PNG")
                    return self.send(buf.getvalue(), "image/png")
                return self.stream(number)
            self.send_error(404)

        def stream(self, number):
            self.send_response(200)
            self.send_header("Content-Type", "video/mp2t")
            self.end_headers()
            self.connection.settimeout(VIEWER_TIMEOUT)
            with sources.watching(number):
                broadcast, chunks = broadcaster.join(number)
                print(f"{self.address_string()} tuned to {number} ({broadcast.viewer_count()} watching)", flush=True)
                try:
                    while (chunk := chunks.get(timeout=VIEWER_TIMEOUT)) is not None:
                        self.wfile.write(chunk)
                except (OSError, queue.Empty):  # viewer left, stalled, or the encoder stopped
                    pass
                finally:
                    broadcast.unsubscribe(chunks)
                    print(f"{self.address_string()} left {number}", flush=True)

        def log_message(self, fmt, *args):
            pass

    return Handler


def frame_source(config, renderer, build):
    """For the broadcaster: a callable per page that renders that page's current frame as raw RGB."""
    def frames_for(number):
        carousel = Carousel(config, renderer, build)
        return lambda: carousel.frame(number).tobytes()
    return frames_for


def serve(config, renderer, build, sources):
    broadcaster = Broadcaster(ffmpeg_command(config.fps), frame_source(config, renderer, build), config.linger_seconds)
    server = ThreadingHTTPServer(("", config.port), make_handler(config, renderer, build, sources, broadcaster))
    print(f"Teletextarr serving {len(config.pages)} pages on port {config.port} ({config.base_url})", flush=True)
    server.serve_forever()
