"""
Google DeepMind's WeatherNext 3 rain forecast map, from Open-Meteo (its ensemble mean, hourly, about 10 km, 15 days).

Run by .github/workflows/forecast.yml. Asks Open-Meteo for each 10am-to-10am Queensland day's rain (00z to 00z) on a
half-degree grid over Queensland, NSW and the sea off the coast, and writes data/forecast_wn3.json in the same layout as
the ECMWF map (forecast.py): {"run", "made", "model", "lats", "lons", "days", "mm10": [[mm x 10 per point] per day],
"total"}. Only fetched about twice a day (each fetch is a couple of thousand points of Open-Meteo's free allowance).
"""
import datetime as dt
import json
import os
import sys
import time

import requests

OUT = os.path.join("data", "forecast_wn3.json")
MODEL_ID = "google_weathernext3_ensemble_mean"
LATS = [round(-10.0 - 0.5 * i, 2) for i in range(57)]      # -10 to -38
LONS = [round(140.0 + 0.5 * j, 2) for j in range(37)]      # 140 to 158 (out to sea past the coast)
CHUNK = 100
URLS = ["https://api.open-meteo.com/v1/forecast", "https://ensemble-api.open-meteo.com/v1/ensemble"]
EVERY_H = 11


def fetch(url, points):
    params = {"latitude": ",".join(f"{a:.2f}" for a, _ in points), "longitude": ",".join(f"{b:.2f}" for _, b in points),
              "daily": "precipitation_sum", "models": MODEL_ID, "forecast_days": 16, "timezone": "GMT"}
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, timeout=120)
            if r.status_code == 400:
                print(f"  {url} refused: {r.text[:200]}", flush=True)
                return None
            r.raise_for_status()
            j = r.json()
            return j if isinstance(j, list) else [j]
        except Exception as e:
            print(f"  {e}", flush=True)
            time.sleep(10 * (attempt + 1))
    return None


def main():
    now = dt.datetime.now(dt.timezone.utc)
    if os.path.exists(OUT) and "--force" not in sys.argv:
        try:
            made = dt.datetime.strptime(json.load(open(OUT))["made"], "%Y-%m-%dT%H:%MZ").replace(tzinfo=dt.timezone.utc)
            if now - made < dt.timedelta(hours=EVERY_H):
                print(f"WeatherNext 3 map made {made:%d %b %H:%MZ}, not due yet")
                return
        except Exception:
            pass
    pts = [(la, lo) for la in LATS for lo in LONS]
    url = None
    for u in URLS:   # whichever Open-Meteo address knows the model
        if fetch(u, pts[:2]):
            url = u
            break
    if not url:
        print("Open-Meteo doesn't have WeatherNext 3 just now")
        return
    print(f"Using {url}", flush=True)
    vals, dates = {}, None
    for i0 in range(0, len(pts), CHUNK):
        chunk = pts[i0:i0 + CHUNK]
        locs = fetch(url, chunk)
        if not locs:
            print(f"  points {i0}+ missing", flush=True)
            continue
        for p, loc in zip(chunk, locs):
            d = loc.get("daily") or {}
            if not d.get("time"):
                continue
            dates = dates or d["time"]
            vals[p] = d.get(f"precipitation_sum_{MODEL_ID}") or d.get("precipitation_sum") or []
        time.sleep(2)
    if not dates or len(vals) < len(pts) * 0.8:
        print(f"Only {len(vals)} of {len(pts)} points came; keeping the old map")
        return
    today = now.date().isoformat()
    days, mm10 = [], []
    for k, d in enumerate(dates):
        if d < today:   # GMT day d is 10am d to 10am d+1 in Queensland; the one running now is kept
            continue
        col = [vals.get(p, [])[k] if k < len(vals.get(p, [])) else None for p in pts]
        if sum(v is None for v in col) > len(col) * 0.2:   # the model's run doesn't cover this day
            continue
        days.append((dt.date.fromisoformat(d) + dt.timedelta(days=1)).isoformat())
        mm10.append([round(max(v or 0, 0) * 10) for v in col])
    if not days:
        print("No whole days in the WeatherNext 3 data")
        return
    total = [sum(x) for x in zip(*mm10)]
    os.makedirs("data", exist_ok=True)
    with open(OUT, "w") as f:
        json.dump({"run": now.strftime("%Y-%m-%dT%HZ"), "made": now.strftime("%Y-%m-%dT%H:%MZ"),
                   "model": "Google WeatherNext 3 (AI)", "lats": LATS, "lons": LONS, "days": days, "mm10": mm10,
                   "total": total}, f, separators=(",", ":"))
    print(f"Wrote {OUT}: {len(LATS)}x{len(LONS)} points, days {days[0]} to {days[-1]}, "
          f"most in a day {max(max(d) for d in mm10) / 10} mm")


if __name__ == "__main__":
    main()
