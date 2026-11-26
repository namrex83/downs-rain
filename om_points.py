"""
The forecast chart's seven models, hour by hour, kept on the site so the chart doesn't wait on Open-Meteo.

Run by .github/workflows/forecast.yml. For a point every 0.25 degrees across the Downs and SEQ (the same spacing as
the ECMWF map) it asks Open-Meteo for each model's hourly rain for the next 16 days, and writes one small file per
point, data/om/<lat>_<lon>.json:

  {"made": unix time, "t0": unix time of the first hour (end of the hour), "m": {model: [first hour index, [mm x 10, ...]]}}

The page reads the file for the point nearest where you've tapped (or Toowoomba), and only asks Open-Meteo itself for
spots outside this area or when a file is missing.

It also writes data/om_frac.json, when in each forecast day (10am to 10am Queensland time) the rain falls at every
point: {"days": [...], "lats": [...], "lons": [...], "f": [[8 numbers per point, point by point] per day]}, the share
(in %) of the day's rain in each 3 hours from 10am, from the average of all the models. The page multiplies a day's
rain map by these to play the day on the map in 3-hour steps.
"""
import json
import os
import time

import requests

OUT = os.path.join("data", "om")
LATS = [round(-25.5 - 0.25 * i, 2) for i in range(16)]       # -25.5 to -29.25
LONS = [round(149.5 + 0.25 * j, 2) for j in range(17)]       # 149.5 to 153.5
H_IDS = {"gfs": "gfs_seamless", "ecmwf": "ecmwf_ifs025", "aifs": "ecmwf_aifs025_single",
         "wn3": "google_weathernext3_ensemble_mean"}
OM_MODELS = ["bom_access_global", "ukmo_seamless", "icon_seamless", "jma_seamless"]
NEW = ["ecmwf_aifs025_single", "google_weathernext3_ensemble_mean"]   # left out if Open-Meteo refuses them
CHUNK = 12
URL = "https://api.open-meteo.com/v1/forecast"


def fetch(points, models, url=URL, single=False):
    params = {"latitude": ",".join(f"{a:.2f}" for a, _ in points), "longitude": ",".join(f"{b:.2f}" for _, b in points),
              "hourly": "precipitation", "forecast_days": 16, "past_days": 1, "timezone": "GMT", "timeformat": "unixtime"}
    if not single:
        params["models"] = ",".join(models)
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, timeout=120)
            if r.status_code == 400:
                print(f"  refused: {r.text[:150]}", flush=True)
                return None
            r.raise_for_status()
            j = r.json()
            return j if isinstance(j, list) else [j]
        except Exception as e:
            print(f"  {e}", flush=True)
            time.sleep(5 * (attempt + 1))
    return None


def trim(times, vals):
    have = [i for i, v in enumerate(vals or []) if v is not None]
    if not have:
        return None
    a, b = have[0], have[-1]
    return a, [round((v or 0) * 10) for v in vals[a:b + 1]]


def main():
    os.makedirs(OUT, exist_ok=True)
    pts = [(la, lo) for la in LATS for lo in LONS]
    ids = list(H_IDS.values()) + OM_MODELS
    made, wrote, with_bom = int(time.time()), 0, 0
    hourly = {}   # (lat, lon) -> {unix time (end of hour): [mm from each model]}
    for i0 in range(0, len(pts), CHUNK):
        chunk = pts[i0:i0 + CHUNK]
        locs = fetch(chunk, ids)
        if locs is None:   # in case Open-Meteo doesn't know one of the AI models
            locs = fetch(chunk, [x for x in ids if x != NEW[1]])
        if locs is None:
            locs = fetch(chunk, [x for x in ids if x not in NEW])
        if not locs:
            continue
        bom = None
        for (la, lo), loc in zip(chunk, locs):
            h = loc.get("hourly") or {}
            t = h.get("time") or []
            if not t:
                continue
            m = {}
            for k, mid in H_IDS.items():
                x = trim(t, h.get(f"precipitation_{mid}"))
                if x:
                    m[k] = x
            for k in OM_MODELS:
                x = trim(t, h.get(f"precipitation_{k}"))
                if x:
                    m[k] = x
            if "bom_access_global" not in m:   # the Bureau's model sometimes only comes from Open-Meteo's own BOM address
                if bom is None:
                    bom = fetch(chunk, [], url="https://api.open-meteo.com/v1/bom", single=True) or []
                k = chunk.index((la, lo))
                if k < len(bom):
                    hb = bom[k].get("hourly") or {}
                    tb = hb.get("time") or []
                    x = trim(tb, hb.get("precipitation"))
                    if x and tb:
                        off = (tb[0] - t[0]) // 3600
                        m["bom_access_global"] = (x[0] + off, x[1])
            if not m:
                continue
            with_bom += "bom_access_global" in m
            hs = hourly.setdefault((la, lo), {})
            for k, (a, v) in m.items():
                for i, x in enumerate(v):
                    hs.setdefault(t[0] + (a + i) * 3600, []).append(x / 10)
            with open(os.path.join(OUT, f"{la:.2f}_{lo:.2f}.json"), "w") as f:
                json.dump({"made": made, "t0": t[0], "m": {k: [a, v] for k, (a, v) in m.items()}}, f, separators=(",", ":"))
            wrote += 1
        time.sleep(1)
    print(f"Wrote {wrote} of {len(pts)} points to {OUT} ({with_bom} with ACCESS-G)", flush=True)
    write_frac(pts, hourly, made)


def write_frac(pts, hourly, made, ndays=10):
    """The share of each forecast day's rain in each 3 hours, at every point (see the top of this file)."""
    if not hourly:
        return
    first = (made // 86400 + 1) * 86400   # the end of the forecast day running now (10am Queensland = 00 UTC)
    ends = [first + 86400 * i for i in range(ndays)]
    days = [time.strftime("%Y-%m-%d", time.gmtime(e)) for e in ends]
    f = []
    for e in ends:
        row = []
        for p in pts:
            hs = hourly.get(p, {})
            blocks = []
            for b in range(8):
                s0 = e - 86400 + b * 10800
                vals = [sum(hs[t]) / len(hs[t]) for t in range(s0 + 3600, s0 + 10801, 3600) if hs.get(t)]
                blocks.append(sum(vals) if len(vals) == 3 else None)
            if None in blocks or sum(blocks) < 0.05:   # no hourly figures, or dry: spread evenly
                row += [13, 12, 13, 12, 13, 12, 13, 12]
                continue
            tot = sum(blocks)
            row += [round(100 * x / tot) for x in blocks]
        f.append(row)
    with open(os.path.join("data", "om_frac.json"), "w") as fh:
        json.dump({"made": made, "days": days, "lats": LATS, "lons": LONS, "f": f}, fh, separators=(",", ":"))
    print(f"Wrote data/om_frac.json: {len(days)} days", flush=True)


main()
