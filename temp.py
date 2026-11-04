"""
Temperature forecast for the "Temp" tab.

Run by .github/workflows/forecast.yml after each ECMWF 00z and 12z run. Uses ECMWF open data (IFS,
0.25 degree, licensed CC BY 4.0) 2 m temperature, every 3 hours out to 6 days then every 6 hours to
7 days, and writes data/temp.json:

  times  forecast times (unix seconds)
  t      2 m temperature, degC x10, rows north to south
"""
import datetime as dt
import json
import os
import sys

import numpy as np

from storms_ecmwf import read
from wind import LAT_N, LAT_S, LON_W, LON_E, STEPS

OUT = os.path.join("data", "temp.json")


def run():
    from ecmwf.opendata import Client
    client = Client(source="ecmwf")
    when = client.latest(type="fc", stream="oper", param="2t", step=168)
    if when.hour % 12:          # the 06z and 18z runs stop at 90 hours; use the 00z or 12z run before
        when -= dt.timedelta(hours=6)
    tag = when.strftime("%Y-%m-%dT%HZ")
    if os.path.exists(OUT) and json.load(open(OUT)).get("run") == tag and "--force" not in sys.argv:
        print(f"Already have the {tag} temperature run")
        return
    print(f"Getting ECMWF {tag} 2 m temperatures", flush=True)
    client.retrieve(date=when.strftime("%Y%m%d"), time=when.hour, type="fc", stream="oper", step=STEPS,
                    param="2t", target="t2m.grib2")
    temps, (lats, lons) = read("t2m.grib2")          # read() cuts out the storm area
    rows = np.where((lats <= LAT_N + 1e-6) & (lats >= LAT_S - 1e-6))[0]
    cols = np.where((lons >= LON_W - 1e-6) & (lons <= LON_E + 1e-6))[0]
    times, t10 = [], []
    for step in STEPS:
        k = next((v for (n, lv, s), v in temps.items() if n == "2t" and s == step), None)
        if k is None:
            print(f"  step {step}: temperature missing, skipped", flush=True)
            continue
        times.append(int((when + dt.timedelta(hours=step)).replace(tzinfo=dt.timezone.utc).timestamp()))
        t10.append(np.round((k[np.ix_(rows, cols)] - 273.15) * 10).astype(int).flatten().tolist())
    os.makedirs("data", exist_ok=True)
    with open(OUT, "w") as f:
        json.dump({"run": tag, "made": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
                   "model": "ECMWF IFS (open data, 0.25°)",
                   "lats": [round(float(x), 3) for x in lats[rows]], "lons": [round(float(x), 3) for x in lons[cols]],
                   "times": times, "t": t10}, f, separators=(",", ":"))
    os.remove("t2m.grib2")
    print(f"Wrote {OUT}: {len(rows)}x{len(cols)} points x {len(times)} times; "
          f"range {min(min(x) for x in t10) / 10} to {max(max(x) for x in t10) / 10} C")


if __name__ == "__main__":
    run()
