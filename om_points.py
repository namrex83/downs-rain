"""
The forecast chart's seven models, hour by hour, kept on the site so the chart doesn't wait on Open-Meteo.

Run by .github/workflows/forecast.yml. For a point every 0.25 degrees across the Downs and SEQ (the same spacing as
the ECMWF map) it asks Open-Meteo for each model's hourly rain for the next 16 days, and writes one small file per
point, data/om/<lat>_<lon>.json:

  {"made": unix time, "t0": unix time of the first hour (end of the hour), "m": {model: [first hour index, [mm x 10, ...]]}}

The page reads the file for the point nearest where you've tapped (or Toowoomba), and only asks Open-Meteo itself for
spots outside this area or when a file is missing.
"""
import json
import os
import time

import requests

OUT = os.path.join("data", "om")
LATS = [round(-25.5 - 0.25 * i, 2) for i in range(16)]       # -25.5 to -29.25
LONS = [round(149.5 + 0.25 * j, 2) for j in range(17)]       # 149.5 to 153.5
H_IDS = {"gfs": "gfs_seamless", "ecmwf": "ecmwf_ifs025", "aifs": "ecmwf_aifs025_single"}
OM_MODELS = ["bom_access_global", "ukmo_seamless", "icon_seamless", "jma_seamless"]
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
    for i0 in range(0, len(pts), CHUNK):
        chunk = pts[i0:i0 + CHUNK]
        locs = fetch(chunk, ids)
        if locs is None:   # in case Open-Meteo doesn't know the AI model
            locs = fetch(chunk, [x for x in ids if x != H_IDS["aifs"]])
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
            with open(os.path.join(OUT, f"{la:.2f}_{lo:.2f}.json"), "w") as f:
                json.dump({"made": made, "t0": t[0], "m": {k: [a, v] for k, (a, v) in m.items()}}, f, separators=(",", ":"))
            wrote += 1
        time.sleep(1)
    print(f"Wrote {wrote} of {len(pts)} points to {OUT} ({with_bom} with ACCESS-G)", flush=True)


main()
