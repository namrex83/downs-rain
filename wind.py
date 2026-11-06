"""
Wind forecast for the "Wind" tab.

Run by .github/workflows/forecast.yml after each ECMWF 00z and 12z run. Uses ECMWF open data (IFS,
0.25 degree, licensed CC BY 4.0) 10 m winds, every 3 hours out to 6 days then every 6 hours to 7 days,
and writes data/wind.json:

  times  forecast times (unix seconds)
  spd    10 m wind speed, km/h (rounded), rows north to south
  dir    direction the wind blows FROM, in 10-degree steps (0 = from the north, 9 = from the east ...)
  gust   strongest 10 m gust since the previous time, km/h, if the open data has it
"""
import datetime as dt
import json
import os
import sys

import numpy as np

from storms_ecmwf import read

LAT_N, LAT_S = -24.5, -30.5       # the Darling Downs and SE Queensland, with some room around
LON_W, LON_E = 148.0, 154.5
STEPS = list(range(0, 145, 3)) + list(range(150, 169, 6))
OUT = os.path.join("data", "wind.json")
KMH = 3.6


def run():
    from ecmwf.opendata import Client
    client = Client(source="ecmwf")
    when = client.latest(type="fc", stream="oper", param="10u", step=168)
    if when.hour % 12:          # the 06z and 18z runs stop at 90 hours; use the 00z or 12z run before
        when -= dt.timedelta(hours=6)
    tag = when.strftime("%Y-%m-%dT%HZ")
    if os.path.exists(OUT) and json.load(open(OUT)).get("run") == tag and json.load(open(OUT)).get("v") == 2 and "--force" not in sys.argv:
        print(f"Already have the {tag} wind run")
        return
    base = dict(date=when.strftime("%Y%m%d"), time=when.hour, type="fc", stream="oper", step=STEPS)
    print(f"Getting ECMWF {tag} 10 m winds", flush=True)
    client.retrieve(**base, param=["10u", "10v"], target="wind10.grib2")
    have_gust = True
    try:
        client.retrieve(**{**base, "step": STEPS[1:]}, param="10fg", target="gust.grib2")   # no gust at step 0
    except Exception as e:
        print(f"  no gusts in the open data ({e})", flush=True)
        have_gust = False
    # between about 90 and 144 hours ECMWF gives gusts as the strongest in each 6 hours ("10fg6"), not "10fg":
    # fetch those too and use them for both 3-hourly times inside each 6 hours
    gust6 = {}
    try:
        client.retrieve(**{**base, "step": list(range(96, 145, 6))}, param="10fg6", target="gust6.grib2")
        gust6 = read("gust6.grib2")[0]
    except Exception as e:
        print(f"  no 6-hourly gusts in the open data ({e})", flush=True)
    wind, (lats, lons) = read("wind10.grib2")       # read() already cuts out the storm area
    gust = read("gust.grib2")[0] if have_gust else {}
    get = lambda d, name, step: next((v for (n, lv, s), v in d.items() if n == name and s == step), None)
    rows = np.where((lats <= LAT_N + 1e-6) & (lats >= LAT_S - 1e-6))[0]
    cols = np.where((lons >= LON_W - 1e-6) & (lons <= LON_E + 1e-6))[0]
    cut = lambda a: a[np.ix_(rows, cols)]
    times, spd, drn, gst = [], [], [], []
    for step in STEPS:
        u, v = get(wind, "10u", step), get(wind, "10v", step)
        if u is None or v is None:
            print(f"  step {step}: wind missing, skipped", flush=True)
            continue
        u, v = cut(u), cut(v)
        times.append(int((when + dt.timedelta(hours=step)).replace(tzinfo=dt.timezone.utc).timestamp()))
        spd.append(np.round(np.hypot(u, v) * KMH).astype(int).flatten().tolist())
        frm = (np.degrees(np.arctan2(-u, -v)) + 360) % 360       # meteorological: where it comes from
        drn.append((np.round(frm / 10).astype(int) % 36).flatten().tolist())
        g = get(gust, "10fg", step) if step else None
        if g is None and step:   # fill from the 6-hourly gust covering this time
            g = next((v for (n, lv, s), v in gust6.items() if s == step + (step % 6)), None)
        gst.append(np.round(cut(g) * KMH).astype(int).flatten().tolist() if g is not None else None)
    os.makedirs("data", exist_ok=True)
    out = {"run": tag, "made": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
           "model": "ECMWF IFS (open data, 0.25°)",
           "lats": [round(float(x), 3) for x in lats[rows]], "lons": [round(float(x), 3) for x in lons[cols]],
           "times": times, "spd": spd, "dir": drn, "v": 2}
    if any(g is not None for g in gst):
        out["gust"] = gst
    with open(OUT, "w") as f:
        json.dump(out, f, separators=(",", ":"))
    for p in ("wind10.grib2", "gust.grib2", "gust6.grib2"):
        if os.path.exists(p):
            os.remove(p)
    print(f"Wrote {OUT}: {len(rows)}x{len(cols)} points x {len(times)} times; "
          f"strongest {max(max(s) for s in spd)} km/h; gusts {'yes' if 'gust' in out else 'no'}")


if __name__ == "__main__":
    run()
