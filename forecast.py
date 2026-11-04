"""
ECMWF rainfall forecast for the "Forecast" tab.

Run by .github/workflows/forecast.yml after each new ECMWF run (00z and 12z). Downloads total
precipitation from ECMWF's open data (IFS model, 0.25 degree grid, about 25 km; licensed
CC BY 4.0, so it may be reused commercially with credit) and writes data/forecast.json:

  run     the model run used, e.g. "2026-11-02T00Z"
  days    end date of each forecast day; a day runs 10am to 10am Queensland time (00z to 00z,
          the nearest model steps to 9am)
  mm10    rain for each day on the grid, mm x10, rows north to south
  total   rain over all the days, mm x10
"""
import datetime as dt
import json
import os
import sys

import numpy as np

LAT_N, LAT_S = -23.0, -32.0       # same area as the Storms tab
LON_W, LON_E = 146.0, 155.0
TZ = dt.timedelta(hours=10)       # Queensland, no daylight saving
GRIB = "tp.grib2"
# "python forecast.py" is ECMWF's main model (IFS, out to 10 days); "python forecast.py aifs" is ECMWF's
# AI model (AIFS single, out to 15 days, 6-hourly), also open data under CC BY 4.0
AIFS = "aifs" in sys.argv
MAX_STEP = 360 if AIFS else 240
OUT = os.path.join("data", "forecast_aifs.json" if AIFS else "forecast.json")
MODEL = "ECMWF AIFS (AI model, open data, 0.25°)" if AIFS else "ECMWF IFS (open data, 0.25°)"


def latest_run(client):
    # only the 00z and 12z runs ("oper") go out far enough for a week of days
    return client.latest(type="fc", stream="oper", param="tp", step=MAX_STEP - 12)


def read_grib(path):
    """{end step (h): (lats, lons, values[m])} for the area, rows north to south."""
    import eccodes
    out = {}
    with open(path, "rb") as f:
        while True:
            h = eccodes.codes_grib_new_from_file(f)
            if h is None:
                break
            try:
                step = int(eccodes.codes_get(h, "endStep"))
                ni, nj = eccodes.codes_get(h, "Ni"), eccodes.codes_get(h, "Nj")
                lat1 = eccodes.codes_get(h, "latitudeOfFirstGridPointInDegrees")
                lon1 = eccodes.codes_get(h, "longitudeOfFirstGridPointInDegrees")
                dlat = eccodes.codes_get(h, "jDirectionIncrementInDegrees")
                dlon = eccodes.codes_get(h, "iDirectionIncrementInDegrees")
                north_first = eccodes.codes_get(h, "jScansPositively") == 0
                vals = eccodes.codes_get_values(h).reshape(nj, ni)
            finally:
                eccodes.codes_release(h)
            lats = lat1 - np.arange(nj) * dlat if north_first else lat1 + np.arange(nj) * dlat
            lons = (lon1 + np.arange(ni) * dlon + 180) % 360 - 180
            rows = np.where((lats <= LAT_N + 1e-6) & (lats >= LAT_S - 1e-6))[0]
            cols = np.where((lons >= LON_W - 1e-6) & (lons <= LON_E + 1e-6))[0]
            rows = rows[np.argsort(-lats[rows])]
            cols = cols[np.argsort(lons[cols])]
            out[step] = (lats[rows], lons[cols], vals[np.ix_(rows, cols)])
    return out


def run():
    from ecmwf.opendata import Client
    client = Client(source="ecmwf", model="aifs-single") if AIFS else Client(source="ecmwf")
    when = latest_run(client)
    if when.hour % 12:          # the 06z and 18z runs stop at 90 hours; use the 00z or 12z run before
        when -= dt.timedelta(hours=6)
    tag = when.strftime("%Y-%m-%dT%HZ")
    if os.path.exists(OUT) and json.load(open(OUT)).get("run") == tag and json.load(open(OUT)).get("max_step") == MAX_STEP and "--force" not in sys.argv:
        print(f"Already have the {tag} run")
        return
    first = (24 - when.hour) % 24                 # first step that falls on 00z (10am Qld)
    steps = [first + 24 * i for i in range((MAX_STEP - first) // 24 + 1)]   # as many whole days as the run reaches
    want = [s for s in steps if s > 0]
    print(f"Getting ECMWF {tag} total precipitation, steps {want}", flush=True)
    client.retrieve(date=when.strftime("%Y%m%d"), time=when.hour, type="fc", stream="oper",
                    param="tp", step=want, target=GRIB)
    fields = read_grib(GRIB)
    lats, lons, _ = fields[want[0]]
    acc = {s: fields[s][2] for s in want}
    if 0 in steps:
        acc[0] = np.zeros_like(acc[want[0]])
    days, mm10 = [], []
    for a, b in zip(steps, steps[1:]):
        mm = np.clip((acc[b] - acc[a]) * 1000, 0, None)
        mm10.append(np.round(mm * 10).astype(int).flatten().tolist())
        days.append((when + dt.timedelta(hours=b) + TZ).date().isoformat())
    total = np.clip((acc[steps[-1]] - acc[steps[0]]) * 1000, 0, None)
    os.makedirs("data", exist_ok=True)
    with open(OUT, "w") as f:
        json.dump({"run": tag, "made": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
                   "model": MODEL, "max_step": MAX_STEP,
                   "lats": [round(float(x), 3) for x in lats], "lons": [round(float(x), 3) for x in lons],
                   "days": days, "mm10": mm10,
                   "total": np.round(total * 10).astype(int).flatten().tolist()}, f, separators=(",", ":"))
    os.remove(GRIB)
    print(f"Wrote {OUT}: {len(lats)}x{len(lons)} points, days {days[0]} to {days[-1]}, "
          f"most in a day {max(max(d) for d in mm10) / 10} mm")


if __name__ == "__main__":
    run()
