"""Rough positions of the Bureau's Condamine river gauges, and the creek and river lines they sit on, from OpenStreetMap:
data/river_map.json for the Catchments tab's map.

Each gauge is named "<creek or river> at <place>". The place is looked up among OpenStreetMap's named features (towns,
localities, bridges, weirs, roads) and the gauge is put on the named creek or river line nearest that place. Where the
place can't be found, the gauge is left off the map. Run by hand (river-map.yml) when the Bureau adds gauges."""
import json, math, re, sys, time
import requests
from shapely.geometry import LineString, MultiLineString, Point
from shapely.ops import linemerge, nearest_points

BOX = (-28.6, 150.3, -26.3, 152.5)   # S, W, N, E: the Condamine down to Chinchilla
UA = {"User-Agent": "weather-desk-river-map/1.0"}
SERVERS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
SHORT = {r"\bRd\b": "Road", r"\bSt\b": "Street", r"\bCk\b": "Creek", r"\bMt\b": "Mount"}


def overpass(q):
    for attempt in range(4):
        for url in SERVERS:
            try:
                r = requests.post(url, data={"data": q}, headers=UA, timeout=180)
                if r.ok:
                    return r.json()["elements"]
                print(f"  {url}: {r.status_code}")
            except Exception as e:
                print(f"  {url}: {e}")
        time.sleep(20 * (attempt + 1))
    sys.exit("Overpass not reachable")


def esc(s):
    return re.sub(r"([\\^$.|?*+()\[\]{}])", r"\\\1", s)


def expand(s):
    for a, b in SHORT.items():
        s = re.sub(a, b, s)
    return s


def split(name):
    """'Hodgson Creek at Felton Tm' -> ('Hodgson Creek', ['Felton']); 'Oakey Creek at Oakey (Weir Park)' ->
    ('Oakey Creek', ['Weir Park', 'Oakey']); 'Connolly Dam' -> (None, ['Connolly Dam'])"""
    m = re.match(r"(.+?) at (.+)$", name)
    if not m:
        return None, [re.sub(r" (HG|TW|Tm|Alert)$", "", name)]
    way, place = m.group(1), m.group(2)
    detail = re.findall(r"\(([^)]*)\)", place)
    place = re.sub(r"\s*\(.*?\)", "", place)
    place = re.sub(r" (HG|TW|Tm|Alert)$", "", place, flags=re.I).strip()
    places = []
    for d in detail:   # a road named in brackets: "Pechey-Maclagan Rd" -> both roads
        for part in re.split(r"-(?=[A-Z])|/", d):
            places.append(expand(part.strip()))
    places.append(expand(place))
    if place in ("Weir", "Dam"):
        places.append(f"{way.split()[0]} {place}")
    for p in list(places):   # then each name in it on its own ("Quinalow Peranga Road" -> Quinalow, Peranga)
        places += [w for w in p.split() if len(w) > 4 and w not in ("Road", "Street", "Bridge", "Creek", "River") and w not in places]
    return way, places


flood = json.load(open("data/flood_warn.json"))
rv = flood.get("rivers") or {}
hd = rv.get("headers") or []
iN = max(0, next((i for i, h in enumerate(hd) if re.search("station", h, re.I)), 0))
names = sorted({r[iN] for r in rv.get("rows", []) if r and r[iN]})
print(f"{len(names)} gauges")
parsed = {n: split(n) for n in names}
ways = sorted({w for w, _ in parsed.values() if w} | {"Condamine River"})
places = sorted({p for _, ps in parsed.values() for p in ps if len(p) > 2})

S, W, N, E = BOX
bb = f"({S},{W},{N},{E})"
wre = "|".join(esc(w) for w in ways) + "|Condamine River North Branch|North Branch Condamine River"
lines = overpass(f'[out:json][timeout:170];way["waterway"~"^(river|stream|canal)$"]["name"~"^({wre})$",i]{bb};out geom;')
print(f"{len(lines)} waterway pieces")
geo = {}
for el in lines:
    nm = el["tags"]["name"]
    key = next((w for w in ways if w.lower() == nm.lower()), "North Branch" if "north branch" in nm.lower() else nm)
    geo.setdefault(key, []).append(LineString([(p["lon"], p["lat"]) for p in el["geometry"]]))
geo = {k: linemerge(MultiLineString(v)) for k, v in geo.items()}

pre = "|".join(esc(p) for p in places)
feats = overpass(f'[out:json][timeout:170];nwr["name"~"^({pre})$",i]{bb};out center tags;')
print(f"{len(feats)} named features")
spots = {}
for el in feats:
    c = el.get("center") or ({"lat": el["lat"], "lon": el["lon"]} if "lat" in el else None)
    if c:
        spots.setdefault(el["tags"]["name"].lower(), []).append(Point(c["lon"], c["lat"]))

km = lambda a, b: math.hypot((a.x - b.x) * 99.0, (a.y - b.y) * 110.6)
out, missing = {}, []
for n, (way, ps) in parsed.items():
    line = geo.get(way) if way else None
    if way and line is None and way.lower().startswith("condamine river"):
        line = geo.get("Condamine River")
    best = None
    for p in ps:   # the first place that can be found (a road in brackets before the town)
        cands = spots.get(p.lower(), [])
        if not cands:
            continue
        if line is not None:
            pt = min(cands, key=lambda c: km(c, nearest_points(line, c)[0]))
            on = nearest_points(line, pt)[0]
            d = km(pt, on)
            best = (on, f"on {way}, {d:.1f} km from {p}") if d < 8 else (pt, f"at {p} ({way} not within 8 km)")
        else:
            best = (cands[0], f"at {p}")
        break
    if best:
        out[n] = [round(best[0].y, 4), round(best[0].x, 4)]
        print(f"  {n}: {best[1]}")
    else:
        missing.append(n)
print(f"placed {len(out)}, not found {len(missing)}: {missing}")

streams = []
for name, g in geo.items():
    g = g.simplify(0.0015, preserve_topology=False)
    parts = list(g.geoms) if hasattr(g, "geoms") else [g]
    streams.append({"name": name, "main": name in ("Condamine River", "North Branch"),
                    "lines": [[[round(y, 4), round(x, 4)] for x, y in p.coords] for p in parts if p.length > 0.005]})
json.dump({"source": "OpenStreetMap contributors (ODbL); positions are approximate",
           "gauges": out, "missing": missing, "streams": streams},
          open("data/river_map.json", "w"), separators=(",", ":"))
