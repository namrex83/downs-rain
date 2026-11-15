"""Rough positions of the Bureau's Condamine river gauges, and the creek and river lines they sit on, from OpenStreetMap:
data/river_map.json for the Catchments tab's map.

Each gauge is named "<creek or river> at <place>". The place is looked up among OpenStreetMap's named features (towns,
localities, bridges, weirs, roads) and the gauge is put on the named creek or river line nearest that place. Where the
place can't be found, the gauge is left off the map. Run by hand (river-map.yml) when the Bureau adds gauges."""
import json, math, re, sys, time
import requests
from shapely.geometry import LineString, MultiLineString, Point
from shapely.ops import linemerge, nearest_points, unary_union

BOX = (-29.6, 149.4, -24.95, 153.6)   # S, W, N, E: the radar rain map's area
UA = {"User-Agent": "weather-desk-river-map/1.0"}
SERVERS = ["https://overpass-api.de/api/interpreter", "https://overpass.private.coffee/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
SHORT = {r"\bRd\b": "Road", r"\bSt\b": "Street", r"\bCk\b": "Creek", r"\bMt\b": "Mount"}


FAILED = []


def overpass(q):
    """One query, tried on each server up to three times; a query that still fails is skipped (and counted), so
    one bad batch doesn't lose the whole map."""
    for attempt in range(2):
        for url in SERVERS:
            try:
                r = requests.post(url, data={"data": q}, headers=UA, timeout=100)
                if r.ok:
                    time.sleep(2)   # gently: the servers are shared
                    return r.json()["elements"]
                print(f"  {url}: {r.status_code}", flush=True)
            except Exception as e:
                print(f"  {url}: {e}", flush=True)
        time.sleep(15 * (attempt + 1))
    FAILED.append(q[:120])
    print("  a query failed on every server; skipped", flush=True)
    return []


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
names = list(dict.fromkeys(r[iN] for r in rv.get("rows", []) if r and r[iN] and len(r[iN]) < 80))   # in the Bureau's order, down each river
# only the gauges in the page's catchments: the Condamine's table down to Chinchilla Weir, and the coast's rivers
# (the same runs of the tables as the page's GAUGE_RANGE, and its name rules)
RANGES = [(r"^Condamine River at The Head", r"^Condamine River at Chinchilla Weir TW"),
          (r"^Stanley River at Peachester", r"^Brisbane River at Mt Crosby$"), (r"^Bremer River at Adams Bridge", r"^Bremer River at Karalee"),
          (r"^Burnett Creek at Maroon Dam Inflow", r"^Logan River at Waterford$"), (r"^Albert River at Darlington School", r"^Albert River at Beenleigh"),
          (r"^Obi Obi Creek at Gardners Falls", r"^Mary River at Home Park")]
NAMED = (r"^(North|South) Pine River|^Sideling Creek|^Caboolture River|^(Lagoon|King John) Creek at Caboolture|^((North|South) )?Maroochy River|^(Petrie|Paynter|Eudlo) Creek"
         r"|^Dogwood Creek|^Tinana Creek|^Burrum River at (Lenthalls Dam|Howard)|^Barambah Creek|^Stuart River|^Boyne River at (Carters|Boondooma|Cooranga|Derra|Mclaughlins)"
         r"|^Little Nerang Creek|^Nerang River at (Numinbah|Hinze|Clearview|Carrara)|^Canungra Creek|^Coomera River at (Canungra Army|Clagiraba|Oxenford)"
         r"|^Macintyre Brook|^Severn River at (Ballandean|Farnbro)|^Quart Pot Creek|^Weir River|^Kings Creek|^Dalrymple Creek|^Sandy Creek at Leslie Dam|^Swan Creek at (Swanfels|Yangan)")
keep = set(n for n in names if re.search(NAMED, n))
for a, b in RANGES:
    i = next((k for k, n in enumerate(names) if re.search(a, n)), None)
    j = next((k for k, n in enumerate(names) if re.search(b, n)), None)
    if i is not None and j is not None and j >= i:
        keep.update(names[i:j + 1])
names = [n for n in names if n in keep]
print(f"{len(names)} gauges in the page's catchments")
parsed = {n: split(n) for n in names}

# what an earlier run already found is kept: only new gauges, and creeks not drawn yet, are looked up
try:
    old = json.load(open("data/river_map.json"))
except (OSError, ValueError):
    old = {}
old_g = {n: v for n, v in (old.get("gauges") or {}).items() if n in parsed}
old_s = {st["name"]: st for st in (old.get("streams") or [])}
todo = [n for n in names if n not in old_g]
print(f"{len(old_g)} already placed, {len(todo)} to look up")
ways = sorted({parsed[n][0] for n in todo if parsed[n][0]} | ({"Condamine River"} - set(old_s)))
places = sorted({p for n in todo for p in parsed[n][1] if len(p) > 2})

S, W, N, E = BOX
bb = f"({S},{W},{N},{E})"
def chunked(items, n=40):   # several small queries: one big case-blind one is too slow for the servers
    for i in range(0, len(items), n):
        yield items[i:i + n]


ways_q = ways + ([] if "North Branch" in old_s else ["Condamine River North Branch", "North Branch Condamine River"])
lines = []
for part in chunked(ways_q):
    wre = "|".join(esc(w) for w in part)
    lines += overpass(f'[out:json][timeout:170];way["waterway"~"^(river|stream|canal)$"]["name"~"^({wre})$"]{bb};out geom;')
    print(f"  waterways {len(lines)} so far", flush=True)
print(f"{len(lines)} waterway pieces")
geo = {}
for el in lines:
    nm = el["tags"]["name"]
    key = next((w for w in ways if w.lower() == nm.lower()), "North Branch" if "north branch" in nm.lower() else nm)
    geo.setdefault(key, []).append(LineString([(p["lon"], p["lat"]) for p in el["geometry"]]))
geo = {k: linemerge(MultiLineString(v)) for k, v in geo.items()}

feats = []
# only the kinds of thing a gauge is named after: towns and localities, weirs, dams and bridges, and roads for the
# names that are roads (asking for any feature by name is far too slow around Brisbane)
roads = [p for p in places if re.search(r"\b(Road|Street|Lane|Drive|Avenue|Crossing|Highway)$", p)]
for part in chunked([p for p in places if p not in roads], 60):
    pre = "|".join(esc(p) for p in part)
    feats += overpass(f'[out:json][timeout:90];(node["place"]["name"~"^({pre})$"]{bb};nwr["waterway"~"^(weir|dam)$"]["name"~"^({pre})$"]{bb};);out center tags;')
    print(f"  places {len(feats)} so far", flush=True)
for part in chunked(roads, 60):
    pre = "|".join(esc(p) for p in part)
    feats += overpass(f'[out:json][timeout:90];way["highway"]["name"~"^({pre})$"]{bb};out center tags;')
    print(f"  roads: {len(feats)} so far", flush=True)
print(f"{len(feats)} named features")
spots = {}
for el in feats:
    c = el.get("center") or ({"lat": el["lat"], "lon": el["lon"]} if "lat" in el else None)
    if c:
        spots.setdefault(el["tags"]["name"].lower(), []).append(Point(c["lon"], c["lat"]))

km = lambda a, b: math.hypot((a.x - b.x) * 99.0, (a.y - b.y) * 110.6)
out, missing = dict(old_g), []
for n in todo:
    way, ps = parsed[n]
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
# a gauge whose place can't be found, between two placed gauges on the same creek or river: the Bureau lists each
# river's gauges in order downstream, so put it that far between them along the line
for n in list(missing):
    way = parsed[n][0]
    line = geo.get(way) if way else None
    if line is None:
        continue
    same = [m for m in names if parsed[m][0] == way]
    k = same.index(n)
    up = next((same[j] for j in range(k - 1, -1, -1) if same[j] in out), None)
    down = next((same[j] for j in range(k + 1, len(same)) if same[j] in out), None)
    if not up or not down:
        continue
    gap = [m for m in same[same.index(up) + 1:same.index(down)] if m not in out]
    pieces = list(line.geoms) if hasattr(line, "geoms") else [line]
    a, b = Point(out[up][1], out[up][0]), Point(out[down][1], out[down][0])
    piece = min(pieces, key=lambda g: g.distance(a) + g.distance(b))
    da, db = piece.project(a), piece.project(b)
    for i, m in enumerate(gap):
        pt = piece.interpolate(da + (db - da) * (i + 1) / (len(gap) + 1))
        out[m] = [round(pt.y, 4), round(pt.x, 4)]
        missing.remove(m)
        print(f"  {m}: between {up} and {down}")
# still no spot, but its creek is drawn: put it on the creek anyway - below the gauge upstream of it (towards the
# creek's mouth, the end nearer a main river), above the one downstream of it, or halfway along if it's the only one
mains = unary_union([g for k, g in geo.items() if k in ("Condamine River", "North Branch", "Brisbane River", "Logan River", "Mary River", "Weir River")] or
                    [LineString([(p[1], p[0]) for p in ln]) for st in old_s.values() if st.get("main") for ln in st["lines"] if len(ln) > 1])
for n in list(missing):
    way = parsed[n][0]
    line = geo.get(way) if way else None
    if line is None:
        continue
    pieces = sorted(list(line.geoms) if hasattr(line, "geoms") else [line], key=lambda g: -g.length)
    same = [m for m in names if parsed[m][0] == way]
    k = same.index(n)
    up = next((same[j] for j in range(k - 1, -1, -1) if same[j] in out), None)
    down = next((same[j] for j in range(k + 1, len(same)) if same[j] in out), None)
    ref = Point(out[up][1], out[up][0]) if up else Point(out[down][1], out[down][0]) if down else None
    piece = min(pieces, key=lambda g: g.distance(ref)) if ref else pieces[0]
    ends = [Point(piece.coords[0]), Point(piece.coords[-1])]
    mouth = min(ends, key=lambda e: e.distance(mains)) if not mains.is_empty else ends[1]
    head = ends[0] if mouth is ends[1] else ends[1]
    dm, dh = piece.project(mouth), piece.project(head)
    if ref:
        dr = piece.project(ref)
        at = dr + (dm - dr) * 0.6 if up else dr + (dh - dr) * 0.5
    else:   # none of this creek's gauges placed: spread out in the table's order (downstream), head to mouth
        at = dh + (dm - dh) * (k + 1) / (len(same) + 1)
    pt = piece.interpolate(at)
    out[n] = [round(pt.y, 4), round(pt.x, 4)]
    missing.remove(n)
    print(f"  {n}: on {way}, {'below ' + up if up else 'above ' + down if down else 'spread along it'} (rough)")
print(f"placed {len(out)}, not found {len(missing)}: {missing}; queries skipped: {len(FAILED)}")

streams = [st for name, st in old_s.items() if name not in geo]   # creeks drawn before
for name, g in geo.items():
    g = g.simplify(0.0015, preserve_topology=False)
    parts = list(g.geoms) if hasattr(g, "geoms") else [g]
    if name not in ("Condamine River", "North Branch"):   # common names (Sandy, Spring Creek): only the pieces by its gauges
        mine = [Point(out[m][1], out[m][0]) for m in names if parsed[m][0] == name and m in out]
        parts = [q for q in parts if any(q.distance(pt) < 0.1 for pt in mine)]
    streams.append({"name": name, "main": name in ("Condamine River", "North Branch", "Brisbane River", "Logan River", "Mary River", "Weir River"),
                    "lines": [[[round(y, 4), round(x, 4)] for x, y in p.coords] for p in parts if p.length > 0.005]})
json.dump({"source": "OpenStreetMap contributors (ODbL); positions are approximate",
           "gauges": out, "missing": missing, "streams": streams},
          open("data/river_map.json", "w"), separators=(",", ":"))
