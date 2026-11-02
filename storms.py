"""
Storm ingredients for South East Queensland and northern NSW.

Run by .github/workflows/storms.yml four times a day. Downloads the GFS weather model
forecast from Open-Meteo (free, no key) on a half-degree grid and writes data/storms.json
for the "Storms" tab of the web page:

  li     Lifted Index (°C, x10). Below 0 the air rises on its own; -6 or lower is very unstable.
  cape   CAPE (J/kg). How much energy a rising parcel gains. 1000+ is plenty for strong storms.
  shear  Deep-layer wind shear (knots): the difference between the 10 m wind and the
         500 hPa (about 5.5 km) wind. 30+ kt helps storms organise; 40+ kt suits supercells.

This is the same kind of model output that storm-chaser "risk maps" are drawn from.
"""
import datetime as dt
import json
import math
import os
import sys
import time

import requests

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


def fetch(points, model):
    params = {
        "latitude": ",".join(str(p[0]) for p in points),
        "longitude": ",".join(str(p[1]) for p in points),
        "hourly": ",".join(VARS), "models": model, "forecast_hours": HOURS,
        "wind_speed_unit": "kn", "timeformat": "unixtime", "cell_selection": "nearest",
    }
    for attempt in range(4):
        r = requests.get(URL, params=params, timeout=90)
        if r.status_code == 200:
            data = r.json()
            return data if isinstance(data, list) else [data]
        print(f"  {model}: HTTP {r.status_code} {r.text[:200]}", flush=True)
        if r.status_code == 400:
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
    for k, res in enumerate(results):
        h = res["hourly"]
        for t in range(min(nt, len(h["time"]))):
            v = h["lifted_index"][t]
            li[t][k] = None if v is None else int(round(v * 10))
            v = h["cape"][t]
            cape[t][k] = None if v is None else int(round(v))
            v = shear(h["wind_speed_10m"][t], h["wind_direction_10m"][t],
                      h["wind_speed_500hPa"][t], h["wind_direction_500hPa"][t])
            sh[t][k] = None if v is None else int(round(v))

    os.makedirs("data", exist_ok=True)
    out = {
        "made": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "model": model, "lats": lats, "lons": lons, "times": times,
        "li": li, "cape": cape, "shear": sh,
    }
    with open(OUT, "w") as f:
        json.dump(out, f, separators=(",", ":"))
    worst = min((v for row in li for v in row if v is not None), default=None)
    print(f"Wrote {OUT}: {len(points)} points x {nt} hours from {model}; lowest LI {worst and worst / 10}")


if __name__ == "__main__":
    run()
