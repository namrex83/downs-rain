"""
Soil temperature forecast at 10 cm for the "Soil" tab.

Run by .github/workflows/forecast.yml twice a day. Gets model soil temperatures from Open-Meteo (free,
no key) on a quarter-degree grid, every 3 hours for 7 days (Qld 2am, 5am, 8am ... so 8am, the cotton
planting reading, is one of them), and works out 10 cm from the two layers the model has either side:

  ECMWF IFS    0-7 cm and 7-28 cm layers (taken as their middles, 3.5 and 17.5 cm)
  fallback     Open-Meteo's best model for the spot, 6 cm and 18 cm

Writes data/soil.json: times (unix seconds), t (degC x10 at 10 cm, rows north to south), model.
Later, CSD soil probe readings can be used to correct the map, the way gauges correct the radar.
"""
import datetime as dt
import json
import os
import sys
import time

import requests

from wind import LAT_N, LAT_S, LON_W, LON_E

STEP = 0.25
CHUNK = 100                       # grid points per request
DAYS = 7
OUT = os.path.join("data", "soil.json")
# (name, url, extra params, shallow variable, its depth cm, deeper variable, its depth cm)
SOURCES = [
    ("ECMWF IFS (via Open-Meteo)", "https://api.open-meteo.com/v1/ecmwf", {},
     "soil_temperature_0_to_7cm", 3.5, "soil_temperature_7_to_28cm", 17.5),
    ("Open-Meteo best match", "https://api.open-meteo.com/v1/forecast", {},
     "soil_temperature_6cm", 6.0, "soil_temperature_18cm", 18.0),
]
HEADERS = {"User-Agent": "downs-rain/1.0 (personal weather page)"}


def grid():
    lats = [round(LAT_N - i * STEP, 3) for i in range(int(round((LAT_N - LAT_S) / STEP)) + 1)]
    lons = [round(LON_W + j * STEP, 3) for j in range(int(round((LON_E - LON_W) / STEP)) + 1)]
    return lats, lons


def fetch(url, extra, vars_, points):
    params = {"latitude": ",".join(str(p[0]) for p in points), "longitude": ",".join(str(p[1]) for p in points),
              "hourly": ",".join(vars_), "forecast_days": DAYS + 1, "timeformat": "unixtime",
              "cell_selection": "land", **extra}
    for attempt in range(4):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=90)
            if r.status_code == 200:
                d = r.json()
                return d if isinstance(d, list) else [d]
            if r.status_code == 400:
                raise ValueError(r.text[:300])
            print(f"  HTTP {r.status_code}, retrying", flush=True)
        except requests.RequestException as e:
            print(f"  {e}, retrying", flush=True)
        time.sleep(10 * (attempt + 1))
    raise RuntimeError("Open-Meteo didn't answer")


def at_10cm(a, za, b, zb):
    if a is None or b is None:
        return a if b is None else b
    return a + (b - a) * (10 - za) / (zb - za)


def run():
    lats, lons = grid()
    points = [(la, lo) for la in lats for lo in lons]
    for name, url, extra, va, za, vb, zb in SOURCES:
        print(f"Trying {name}: {va} and {vb}", flush=True)
        try:
            rows = []
            for i in range(0, len(points), CHUNK):
                rows += fetch(url, extra, [va, vb], points[i:i + CHUNK])
                time.sleep(1)
        except Exception as e:
            print(f"  {name} failed: {e}", flush=True)
            continue
        h = rows[0]["hourly"]
        if all(x is None for x in h.get(va) or [None]):
            print(f"  {name} has no soil temperatures here", flush=True)
            continue
        # every 3 hours on Qld 2am, 5am, 8am ...; keep times from now on
        now = time.time() - 3 * 3600
        keep = [k for k, t in enumerate(h["time"]) if ((t + 36000) // 3600) % 3 == 2 and t >= now]
        times = [h["time"][k] for k in keep]
        t10 = []
        for k in keep:
            col = []
            for r in rows:
                v = at_10cm(r["hourly"][va][k], za, r["hourly"][vb][k], zb)
                col.append(None if v is None else int(round(v * 10)))
            t10.append(col)
        os.makedirs("data", exist_ok=True)
        with open(OUT, "w") as f:
            json.dump({"made": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "model": name,
                       "depth": f"10 cm, worked out from the model's {za:g} cm and {zb:g} cm readings",
                       "lats": lats, "lons": lons, "times": times, "t": t10}, f, separators=(",", ":"))
        vals = [v for c in t10 for v in c if v is not None]
        print(f"Wrote {OUT} from {name}: {len(lats)}x{len(lons)} points x {len(times)} times; "
              f"{min(vals) / 10} to {max(vals) / 10} C")
        return
    sys.exit("No soil temperatures from any source")


if __name__ == "__main__":
    run()
