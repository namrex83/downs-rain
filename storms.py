"""
Storm ingredients for South East Queensland and northern NSW.

Run by .github/workflows/storms.yml four times a day. Downloads the GFS weather model
forecast from Open-Meteo (free, no key) on a half-degree grid and writes data/storms.json
for the "Storms" tab of the web page:

  li     Lifted Index (°C, x10). Below 0 the air rises on its own; -6 or lower is very unstable.
  cape   CAPE (J/kg). How much energy a rising parcel gains. 1000+ is plenty for strong storms.
  shear  Deep-layer wind shear (knots): the difference between the 10 m wind and the
         500 hPa (about 5.5 km) wind. 30+ kt helps storms organise; 40+ kt suits supercells.
  srh    0-3 km storm-relative helicity (m2/s2), from the winds at 950-400 hPa (see helicity.py).
         Positive = helps southern-hemisphere supercells rotate; 100+ supports supercells.

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
CHUNK = 10                        # grid points per request (small: each point asks for a whole wind profile)
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
LEVELS = [950, 900, 850, 800, 700, 600, 500, 400]        # hPa, for the helicity wind profile
OPTIONAL = [["convective_inhibition"],                    # the cap
            [f"{n}_{p}hPa" for p in LEVELS for n in ("wind_speed", "wind_direction", "geopotential_height")
             if f"{n}_{p}hPa" not in VARS]]
n_optional = len(OPTIONAL)
PAUSE = 4         # seconds between requests: the profile winds make each request "heavier" for Open-Meteo


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
            r = requests.get(URL, params=params, timeout=120)
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


def uv(speed, direction):
    """Wind (knots, direction it blows from) as u, v in m/s."""
    s = speed * 0.514444
    return -s * math.sin(math.radians(direction)), -s * math.cos(math.radians(direction))


def profile_srh(h, t, elev):
    """0-3 km helicity at one point and hour from the 10 m and pressure-level winds, or None."""
    z, u, v = [10.0], [], []
    if h["wind_speed_10m"][t] is None or h["wind_direction_10m"][t] is None:
        return None
    a, b = uv(h["wind_speed_10m"][t], h["wind_direction_10m"][t]); u.append(a); v.append(b)
    for p in LEVELS:
        sp = (h.get(f"wind_speed_{p}hPa") or [None] * (t + 1))[t]
        dr = (h.get(f"wind_direction_{p}hPa") or [None] * (t + 1))[t]
        gh = (h.get(f"geopotential_height_{p}hPa") or [None] * (t + 1))[t]
        if None in (sp, dr, gh) or gh - elev < 50:      # missing, or below/at the ground
            continue
        a, b = uv(sp, dr); z.append(gh - elev); u.append(a); v.append(b)
    if len(z) < 4:
        return None
    val = srh(z, u, v)
    return None if val is None else int(round(val))


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
            time.sleep(PAUSE if n_optional > 1 else 1)
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
    hel = [[None] * len(points) for _ in range(nt)]
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
            hel[t][k] = profile_srh(h, t, res.get("elevation") or 0)

    os.makedirs("data", exist_ok=True)
    out = {
        "made": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "model": model, "lats": lats, "lons": lons, "times": times,
        "li": li, "cape": cape, "shear": sh, "cin": cin,
    }
    if any(v is not None for row in hel for v in row):
        out["srh"] = hel
    with open(OUT, "w") as f:
        json.dump(out, f, separators=(",", ":"))
    worst = min((v for row in li for v in row if v is not None), default=None)
    top = max((v for row in hel for v in row if v is not None), default=None)
    print(f"Wrote {OUT}: {len(points)} points x {nt} hours from {model}; lowest LI {worst and worst / 10}; "
          f"highest helicity {top if top is not None else 'not available'}")


if __name__ == "__main__":
    run()
