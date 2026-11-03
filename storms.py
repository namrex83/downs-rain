"""
Storm ingredients for South East Queensland and northern NSW.

Run by .github/workflows/storms.yml four times a day. Downloads the GFS weather model
forecast from Open-Meteo (free, no key) on a half-degree grid and writes data/storms.json
for the "Storms" tab of the web page:

  li     Lifted Index (°C, x10). Below 0 the air rises on its own; -6 or lower is very unstable.
  cape   CAPE (J/kg). How much energy a rising parcel gains. 1000+ is plenty for strong storms.
  shear  Deep-layer wind shear (knots): the difference between the 10 m wind and the
         500 hPa (about 5.5 km) wind. 30+ kt helps storms organise; 40+ kt suits supercells.
  srh    0-3 km storm-relative helicity (m2/s2), see helicity.py. Positive = helps southern-hemisphere
         supercells rotate; 100+ supports supercells. Worked out from the GFS wind profile, fetched
         straight from NOAA (NOMADS) because asking Open-Meteo for whole profiles was too slow.

This is the same kind of model output that storm-chaser "risk maps" are drawn from.
"""
import datetime as dt
import json
import math
import os
import sys
import time

import requests

from helicity import srh

# ---------------------------------------------------------------- settings
LAT_N, LAT_S = -23.0, -32.0       # Emerald/Rocky down to Port Macquarie
LON_W, LON_E = 146.0, 155.0       # west of Roma out to the coast
STEP = 0.5                        # degrees between grid points (about 50 km)
HOURS = 54                        # forecast hours to keep (covers tomorrow afternoon from any run)
CHUNK = 100                       # grid points per request
URL = "https://api.open-meteo.com/v1/gfs"
MODELS = ["gfs_global", "gfs_seamless"]   # GFS 0.25°; fall back to the blended set
VARS = ["lifted_index", "cape", "wind_speed_10m", "wind_direction_10m",
        "wind_speed_500hPa", "wind_direction_500hPa"]
OUT = os.path.join("data", "storms.json")


def grid():
    lats = [round(LAT_N - i * STEP, 2) for i in range(int(round((LAT_N - LAT_S) / STEP)) + 1)]
    lons = [round(LON_W + j * STEP, 2) for j in range(int(round((LON_E - LON_W) / STEP)) + 1)]
    return lats, lons


# extra fields, asked for if the model has them; on an error the last group is dropped first
OPTIONAL = [["convective_inhibition"]]                    # the cap
n_optional = len(OPTIONAL)


def fetch(points, model):
    global n_optional
    params = {
        "latitude": ",".join(str(p[0]) for p in points),
        "longitude": ",".join(str(p[1]) for p in points),
        "hourly": ",".join(VARS + sum(OPTIONAL[:n_optional], [])), "models": model, "forecast_hours": HOURS,
        "wind_speed_unit": "kn", "timeformat": "unixtime", "cell_selection": "nearest",
    }
    for attempt in range(4):
        try:
            r = requests.get(URL, params=params, timeout=90)
        except requests.exceptions.RequestException as e:
            print(f"  {model}: {type(e).__name__}, trying again", flush=True)
            time.sleep(15 * (attempt + 1))
            continue
        if r.status_code == 429:              # over Open-Meteo's per-minute limit: wait it out
            print("  rate limited, waiting a minute", flush=True)
            time.sleep(65)
            continue
        if r.status_code == 200:
            data = r.json()
            return data if isinstance(data, list) else [data]
        print(f"  {model}: HTTP {r.status_code} {r.text[:200]}", flush=True)
        if r.status_code == 400:
            if n_optional:                    # maybe an extra field isn't offered: try again without it
                n_optional -= 1
                return fetch(points, model)
            return None
        time.sleep(10 * (attempt + 1))
    return None


def shear(s10, d10, s500, d500):
    """Size of the vector difference between two winds (knots)."""
    if None in (s10, d10, s500, d500):
        return None
    u = lambda s, d: -s * math.sin(math.radians(d))
    v = lambda s, d: -s * math.cos(math.radians(d))
    return math.hypot(u(s500, d500) - u(s10, d10), v(s500, d500) - v(s10, d10))


# ---------------------------------------------------------------- helicity from NOAA's GFS files
NOMADS = "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p50.pl"
PROFILE = [1000, 975, 950, 925, 900, 850, 800, 750, 700, 650, 600, 550, 500, 450, 400]   # hPa


def nomads_file(cycle, fhr):
    """One GFS half-degree file cut down to our area: 10 m wind, ground height, and wind and height
    at each pressure level. Returns {(name, level): {(lat, lon): value}} or None if it isn't out."""
    import eccodes
    params = {"dir": f"/gfs.{cycle:%Y%m%d}/{cycle:%H}/atmos", "file": f"gfs.t{cycle:%H}z.pgrb2full.0p50.f{fhr:03d}",
              "var_UGRD": "on", "var_VGRD": "on", "var_HGT": "on", "lev_10_m_above_ground": "on", "lev_surface": "on",
              "subregion": "", "toplat": LAT_N + 0.5, "bottomlat": LAT_S - 0.5,
              "leftlon": LON_W - 0.5, "rightlon": LON_E + 0.5}
    for p in PROFILE:
        params[f"lev_{p}_mb"] = "on"
    for attempt in range(3):
        try:
            r = requests.get(NOMADS, params=params, timeout=60)
        except requests.exceptions.RequestException:
            time.sleep(10)
            continue
        if r.status_code == 200 and r.content[:4] == b"GRIB":
            break
        if r.status_code in (404, 200):          # not published (yet): NOMADS answers with a message, not GRIB
            return None
        time.sleep(10)
    else:
        return None
    path = "profile.grib2"
    with open(path, "wb") as f:
        f.write(r.content)
    out = {}
    with open(path, "rb") as f:
        while True:
            h = eccodes.codes_grib_new_from_file(f)
            if h is None:
                break
            try:
                name, kind = eccodes.codes_get(h, "shortName"), eccodes.codes_get(h, "typeOfLevel")
                level = int(eccodes.codes_get(h, "level"))
                la, lo = eccodes.codes_get_array(h, "latitudes"), eccodes.codes_get_array(h, "longitudes")
                vals = eccodes.codes_get_values(h)
            finally:
                eccodes.codes_release(h)
            if kind == "surface":                  # ground height (eccodes may call it "orog" or "gh")
                key = ("gh", 0)
            elif kind == "heightAboveGround":      # 10 m wind: "10u"/"10v" (or "u"/"v" in some tables)
                key = (name if name.startswith("10") else "10" + name, 10)
            else:
                key = (name, level)
            out[key] = {(round(float(y) * 2) / 2, round(((float(x) + 180) % 360 - 180) * 2) / 2): float(v)
                        for y, x, v in zip(la, lo, vals)}
    os.remove(path)
    return out


def profile_srh(fields, pt):
    """0-3 km helicity at one grid point from one GFS file's fields, or None."""
    try:
        ground = fields[("gh", 0)][pt]
        z, u, v = [10.0], [fields[("10u", 10)][pt]], [fields[("10v", 10)][pt]]
    except KeyError:
        return None
    for p in reversed(sorted(PROFILE)):            # 1000 hPa first, going up
        try:
            hgt = fields[("gh", p)][pt] - ground
            a, b = fields[("u", p)][pt], fields[("v", p)][pt]
        except KeyError:
            continue
        if hgt >= 50:                              # skip levels at or below the ground
            z.append(hgt); u.append(a); v.append(b)
    return srh(z, u, v) if len(z) >= 4 else None


def gfs_helicity(times, points):
    """Hourly helicity for each point, from the latest GFS run on NOMADS (3-hourly files, filled in
    between). Returns a list per hour of a list per point, or None if NOMADS can't be reached."""
    now = dt.datetime.now(dt.timezone.utc)
    newest = (now - dt.timedelta(hours=4)).replace(minute=0, second=0, microsecond=0)
    newest -= dt.timedelta(hours=newest.hour % 6)
    for back in range(4):                          # this cycle, or up to 18 hours older
        cycle = newest - dt.timedelta(hours=6 * back)
        c0 = cycle.timestamp()
        fhrs = sorted({3 * math.floor((t - c0) / 10800) for t in times} | {3 * math.ceil((t - c0) / 10800) for t in times})
        fhrs = [f for f in fhrs if 0 <= f <= 384]
        if not fhrs or nomads_file(cycle, fhrs[-1]) is None:   # the last file is the last to appear
            print(f"  GFS {cycle:%Y-%m-%d %Hz} not complete on NOMADS, trying an older run", flush=True)
            continue
        print(f"  helicity from GFS {cycle:%Y-%m-%d %Hz} on NOMADS: {len(fhrs)} files", flush=True)
        grids = {}
        for f in fhrs:
            fields = nomads_file(cycle, f)
            if fields is not None:
                grids[f] = [profile_srh(fields, pt) for pt in points]
            time.sleep(1)                          # NOMADS asks for no more than about 2 requests a second
        out = []
        for t in times:
            fh = (t - c0) / 3600
            f0, f1 = 3 * math.floor(fh / 3), 3 * math.ceil(fh / 3)
            g0, g1 = grids.get(f0), grids.get(f1)
            if g0 is None or g1 is None:
                out.append([None] * len(points))
                continue
            w = 0 if f1 == f0 else (fh - f0) / (f1 - f0)
            out.append([None if a is None or b is None else int(round(a + w * (b - a))) for a, b in zip(g0, g1)])
        return out
    return None


def run():
    lats, lons = grid()
    points = [(la, lo) for la in lats for lo in lons]
    for model in MODELS:
        results = []
        for i in range(0, len(points), CHUNK):
            part = fetch(points[i:i + CHUNK], model)
            if part is None:
                results = None
                break
            results += part
            time.sleep(1)
        if results and any(v is not None for v in results[0]["hourly"].get("lifted_index", [])):
            break
        print(f"{model}: no lifted index returned, trying the next model", flush=True)
    else:
        sys.exit("Could not get storm data from any model")

    times = results[0]["hourly"]["time"]
    nt = len(times)
    li = [[None] * len(points) for _ in range(nt)]
    cape = [[None] * len(points) for _ in range(nt)]
    sh = [[None] * len(points) for _ in range(nt)]
    cin = [[None] * len(points) for _ in range(nt)]
    for k, res in enumerate(results):
        h = res["hourly"]
        for t in range(min(nt, len(h["time"]))):
            v = h["lifted_index"][t]
            li[t][k] = None if v is None else int(round(v * 10))
            v = (h.get("convective_inhibition") or [None] * nt)[t]
            cin[t][k] = None if v is None else int(round(abs(v)))
            v = h["cape"][t]
            cape[t][k] = None if v is None else int(round(v))
            v = shear(h["wind_speed_10m"][t], h["wind_direction_10m"][t],
                      h["wind_speed_500hPa"][t], h["wind_direction_500hPa"][t])
            sh[t][k] = None if v is None else int(round(v))

    os.makedirs("data", exist_ok=True)
    out = {
        "made": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "model": model, "lats": lats, "lons": lons, "times": times,
        "li": li, "cape": cape, "shear": sh, "cin": cin,
    }
    try:
        hel = gfs_helicity(times, points)
    except Exception as e:                         # helicity is a bonus: never lose the rest over it
        print(f"  helicity failed: {type(e).__name__}: {e}", flush=True)
        hel = None
    if hel and any(v is not None for row in hel for v in row):
        out["srh"] = hel
    with open(OUT, "w") as f:
        json.dump(out, f, separators=(",", ":"))
    worst = min((v for row in li for v in row if v is not None), default=None)
    top = max((v for row in (hel or []) for v in row if v is not None), default=None)
    print(f"Wrote {OUT}: {len(points)} points x {nt} hours from {model}; lowest LI {worst and worst / 10}; "
          f"highest helicity {top if top is not None else 'not available'}")


if __name__ == "__main__":
    run()
