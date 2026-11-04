"""
GFS rainfall forecast for the Forecast tab's "GFS" option.

Run by .github/workflows/forecast.yml. Downloads total precipitation (APCP) for the area from
NOAA's GFS 0.25 degree run on the NOMADS server (US government data, public domain), every 6
hours out to the end of the 16th day, and writes data/forecast_gfs.json in the same layout as
forecast.py's ECMWF file: rain per 10am-to-10am Queensland day (00z to 00z), mm x10.
"""
import datetime as dt
import json
import os
import sys
import tempfile
import time

import numpy as np
import requests

LAT_N, LAT_S = -23.0, -32.0       # same area as the ECMWF forecast
LON_W, LON_E = 146.0, 155.0
DAYS = 16                         # GFS runs out to 384 hours
TZ = dt.timedelta(hours=10)
OUT = os.path.join("data", "forecast_gfs.json")
NOMADS = "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl"
HEADERS = {"User-Agent": "downs-rain/1.0 (personal rainfall map)"}


def url(run, fhr):
    d, h = run.strftime("%Y%m%d"), run.hour
    return (f"{NOMADS}?dir=%2Fgfs.{d}%2F{h:02d}%2Fatmos&file=gfs.t{h:02d}z.pgrb2.0p25.f{fhr:03d}"
            f"&var_APCP=on&lev_surface=on&subregion=&toplat={LAT_N}&leftlon={LON_W}"
            f"&rightlon={LON_E}&bottomlat={LAT_S}")


def fetch(run, fhr, tries=3):
    for attempt in range(tries):
        try:
            r = requests.get(url(run, fhr), headers=HEADERS, timeout=60)
            if r.status_code == 200 and r.content[:4] == b"GRIB":
                return r.content
            if r.status_code == 404 or b"not present" in r.content[:2000]:
                return None
        except requests.RequestException as e:
            print(f"  f{fhr:03d}: {e}", flush=True)
        time.sleep(5 * (attempt + 1))
    return None


def steps_for(run):
    first = (24 - run.hour) % 24                   # first step on 00z (10am Qld)
    return [first + 24 * i for i in range((DAYS * 24 - first) // 24 + 1)]   # whole days to the end of the run (384 h)


def latest_run(now):
    """Newest GFS run whose files reach its last whole day."""
    t = now.replace(minute=0, second=0, microsecond=0)
    t -= dt.timedelta(hours=t.hour % 6)
    for back in range(6):
        run = t - dt.timedelta(hours=6 * back)
        if fetch(run, steps_for(run)[-1], tries=1):
            return run
    return None


def decode(content):
    """[(start h, end h, lats, lons, values)] from one downloaded GRIB file."""
    import eccodes
    out = []
    with tempfile.NamedTemporaryFile(suffix=".grib2") as tmp:
        tmp.write(content)
        tmp.flush()
        with open(tmp.name, "rb") as f:
            while True:
                h = eccodes.codes_grib_new_from_file(f)
                if h is None:
                    break
                try:
                    start, end = int(eccodes.codes_get(h, "startStep")), int(eccodes.codes_get(h, "endStep"))
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
                if not north_first:
                    lats, vals = lats[::-1], vals[::-1]
                out.append((start, end, lats, lons, vals))
    return out


def run():
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    when = latest_run(now)
    if when is None:
        sys.exit("No complete GFS run found on NOMADS")
    tag = when.strftime("%Y-%m-%dT%HZ")
    if os.path.exists(OUT) and json.load(open(OUT)).get("run") == tag and len(json.load(open(OUT))["days"]) >= DAYS - 1 and "--force" not in sys.argv:
        print(f"Already have the GFS {tag} run")
        return
    steps = steps_for(when)
    print(f"Getting GFS {tag} rain, every 6 h to f{steps[-1]:03d}", flush=True)
    # GFS stores rain in 6-hour buckets (and sometimes totals from the start); add them up
    pieces, lats, lons = {}, None, None
    for fhr in range(6, steps[-1] + 1, 6):
        content = fetch(when, fhr)
        if content is None:
            sys.exit(f"GFS {tag} f{fhr:03d} missing")
        for start, end, la, lo, v in decode(content):
            pieces[(start, end)] = v
            lats, lons = la, lo
        time.sleep(0.5)
    total = {0: np.zeros_like(next(iter(pieces.values())))}
    for t in range(6, steps[-1] + 1, 6):
        if (0, t) in pieces:
            total[t] = pieces[(0, t)]
        else:
            start = next((s for (s, e) in pieces if e == t and s in total), None)
            if start is None:
                sys.exit(f"Can't work out the rain total to f{t:03d} from {sorted(pieces)}")
            total[t] = total[start] + pieces[(start, t)]
    days, mm10 = [], []
    for a, b in zip(steps, steps[1:]):
        mm = np.clip(total[b] - total[a], 0, None)              # kg/m2 is mm
        mm10.append(np.round(mm * 10).astype(int).flatten().tolist())
        days.append((when + dt.timedelta(hours=b) + TZ).date().isoformat())
    week = np.clip(total[steps[-1]] - total[steps[0]], 0, None)
    os.makedirs("data", exist_ok=True)
    with open(OUT, "w") as f:
        json.dump({"run": tag, "made": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
                   "model": "GFS (NOAA, 0.25°)",
                   "lats": [round(float(x), 3) for x in lats], "lons": [round(float(x), 3) for x in lons],
                   "days": days, "mm10": mm10,
                   "total": np.round(week * 10).astype(int).flatten().tolist()}, f, separators=(",", ":"))
    print(f"Wrote {OUT}: {len(lats)}x{len(lons)} points, days {days[0]} to {days[-1]}, "
          f"most in a day {max(max(d) for d in mm10) / 10} mm")


if __name__ == "__main__":
    run()
