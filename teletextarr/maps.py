"""Teletext mosaic maps: country outlines drawn with 2x3 block graphics, like the old Ceefax weather maps.

Outlines come from Natural Earth (public domain), bundled in data/countries.json.gz.
"""
import functools
import gzip
import json
import math
import os

DATA = os.path.join(os.path.dirname(__file__), "data", "countries.json.gz")

# A mosaic cell is 2 blocks wide and 3 tall. Bits: 1 top-left, 2 top-right, 4 middle-left, 8 middle-right,
# 16 bottom-left, 32 bottom-right. Unicode's "sextant" characters cover every pattern except empty,
# left half, right half and full, which have their own block characters.
SPECIAL = {0: " ", 21: "▌", 42: "▐", 63: "█"}
SPECIAL_BITS = {ch: bits for bits, ch in SPECIAL.items() if bits}
SEXTANT_BASE = 0x1FB00


def mosaic_char(bits):
    if bits in SPECIAL:
        return SPECIAL[bits]
    return chr(SEXTANT_BASE + bits - 1 - (bits > 21) - (bits > 42))


def mosaic_bits(ch):
    """The block pattern of a mosaic character, or None if ch is ordinary text."""
    if ch in SPECIAL_BITS:
        return SPECIAL_BITS[ch]
    code = ord(ch) - SEXTANT_BASE
    if not 0 <= code < 60:
        return None
    bits = code + 1
    if bits >= 21:
        bits += 1
    if bits >= 42:
        bits += 1
    return bits


@functools.cache
def countries():
    with gzip.open(DATA, "rt") as f:
        return json.load(f)["countries"]


def find_country(query):
    q = str(query).strip().lower()
    for c in countries():
        if q in {str(c["iso"]).lower(), str(c["name"]).lower(), str(c["admin"]).lower()}:
            return c
    raise ValueError(f"unknown country {query!r}: use an ISO code like GB or a name like 'United Kingdom'")


def ring_area(ring):
    return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]))) / 2


def bbox(points):
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def country_bbox(country, extra_points=()):
    """Bounds of the country's main landmass plus nearby islands, ignoring far-off territories."""
    rings = sorted(country["rings"], key=ring_area, reverse=True)
    x0, y0, x1, y1 = bbox(rings[0])
    reach = max(x1 - x0, y1 - y0) * 0.5
    near = [r for r in rings if (lambda b: b[2] >= x0 - reach and b[0] <= x1 + reach and b[3] >= y0 - reach and b[1] <= y1 + reach)(bbox(r))]
    return bbox([p for r in near for p in r] + list(extra_points))


def inside(x, y, ring):
    hit = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            hit = not hit
    return hit


class MosaicMap:
    """A country rasterised to teletext mosaic cells, with a lat/lon to cell projection."""

    # Screen pixels per block: cells are 28x27, split 2 across and 3 down.
    BLOCK_W, BLOCK_H = 14, 9

    def __init__(self, country, max_cols, max_rows, places=(), neighbours=True, margin=0.04):
        c = find_country(country)
        lon0, lat0, lon1, lat1 = country_bbox(c, [(p[1], p[0]) for p in places])
        pad_x, pad_y = (lon1 - lon0) * margin, (lat1 - lat0) * margin
        self.lon0, self.lat0, self.lon1, self.lat1 = lon0 - pad_x, lat0 - pad_y, lon1 + pad_x, lat1 + pad_y
        self.kx = math.cos(math.radians((self.lat0 + self.lat1) / 2))  # equirectangular, scaled at mid-latitude
        width, height = (self.lon1 - self.lon0) * self.kx, self.lat1 - self.lat0
        scale = min(max_cols * 2 * self.BLOCK_W / width, max_rows * 3 * self.BLOCK_H / height)
        self.cols = max(1, math.ceil(width * scale / (2 * self.BLOCK_W)))
        self.rows = max(1, math.ceil(height * scale / (3 * self.BLOCK_H)))
        self.scale = scale
        candidates = [c] if not neighbours else countries()
        self.rings = [r for k in candidates for r in k["rings"] if self._overlaps(bbox(r))]
        self.cells = self._rasterise()

    def _overlaps(self, b):
        return b[2] >= self.lon0 and b[0] <= self.lon1 and b[3] >= self.lat0 and b[1] <= self.lat1

    def block_to_lonlat(self, bx, by):
        px, py = (bx + 0.5) * self.BLOCK_W, (by + 0.5) * self.BLOCK_H
        return self.lon0 + px / self.scale / self.kx, self.lat1 - py / self.scale

    def cell_of(self, lat, lon):
        px = (lon - self.lon0) * self.kx * self.scale
        py = (self.lat1 - lat) * self.scale
        return min(self.rows - 1, max(0, int(py // (3 * self.BLOCK_H)))), min(self.cols - 1, max(0, int(px // (2 * self.BLOCK_W))))

    def _rasterise(self):
        rings = [(bbox(r), r) for r in self.rings]
        cells = []
        for row in range(self.rows):
            line = []
            for col in range(self.cols):
                bits = 0
                for i, (dx, dy) in enumerate(((0, 0), (1, 0), (0, 1), (1, 1), (0, 2), (1, 2))):
                    lon, lat = self.block_to_lonlat(col * 2 + dx, row * 3 + dy)
                    if any(b[0] <= lon <= b[2] and b[1] <= lat <= b[3] and inside(lon, lat, r) for b, r in rings):
                        bits |= 1 << i
                line.append(mosaic_char(bits))
            cells.append(line)
        return cells


@functools.lru_cache(maxsize=8)
def cached_map(country, max_cols, max_rows, places, neighbours):
    return MosaicMap(country, max_cols, max_rows, places, neighbours)
