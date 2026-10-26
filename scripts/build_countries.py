"""Build teletextarr/data/countries.json.gz from Natural Earth admin-0 countries (public domain).

Keeps outer rings only, rounds to 0.01 degrees and drops points closer than `tolerance` degrees:
teletext maps are at most ~80x54 blocks, so this loses nothing visible.
Usage: python build_countries.py ne_50m_admin_0_countries.geojson out.json.gz
"""
import gzip
import json
import sys

src, out = sys.argv[1], sys.argv[2]
tolerance = 0.05
features = []
for f in json.load(open(src))["features"]:
    p, g = f["properties"], f["geometry"]
    polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
    rings = []
    for poly in polys:
        ring, last = [], None
        for lon, lat in poly[0]:
            if last is None or abs(lon - last[0]) + abs(lat - last[1]) >= tolerance:
                last = (round(lon, 2), round(lat, 2))
                ring.append(last)
        if len(ring) >= 4:
            rings.append(ring)
    iso = p.get("ISO_A2_EH") if p.get("ISO_A2_EH") not in (None, "-99") else p.get("ISO_A2")
    features.append({"iso": iso, "name": p.get("NAME"), "admin": p.get("ADMIN"), "rings": rings})
with gzip.open(out, "wt") as fh:
    json.dump({"source": "Natural Earth 1:50m admin-0 countries (public domain)", "countries": features}, fh, separators=(",", ":"))
print(len(features), "countries")
