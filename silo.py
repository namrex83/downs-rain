"""
Rainfall for days the radar record doesn't cover, and the season total, for the Rainfall tab.

  python silo.py          fill days from SILO, then add up the season (run by .github/workflows/silo.yml)
  python silo.py season   only add up the season (run by the radar job after each update)

SILO (Queensland Government, licensed CC BY 4.0) publishes a daily rainfall map for all of Australia on a
0.05 degree grid (about 5 km), worked out from the Bureau's rain gauges, for the 24 hours to 9am: the same
rain day as the radar maps. For each day from the start of the season with no finished radar map, this puts
SILO's map onto the radar maps' grid and writes data/silo/<day>.json; data/silo/index.json lists them.

The season total (data/season.json) and the last 14 days (data/fortnight.json) add up each day from the start of the season: the radar map where there
is one, otherwise SILO's.
"""
import datetime as dt
import json
import math
import os
import sys

import numpy as np

DATA = "data"
SILO_DIR = os.path.join(DATA, "silo")
SEASON_OUT = os.path.join(DATA, "season.json")
FORTNIGHT_OUT = os.path.join(DATA, "fortnight.json")
SILO_URL = "https://s3-ap-southeast-2.amazonaws.com/silo-open-data/Official/annual/daily_rain/{year}.daily_rain.nc"
REFRESH_DAYS = 10            # SILO revises recent days as late gauge reports come in: fetch these again
QLD = dt.timezone(dt.timedelta(hours=10))


def season_start(today):
    """The summer season runs from 1 October."""
    return dt.date(today.year if today.month >= 10 else today.year - 1, 10, 1)


def days_between(a, b):
    return [(a + dt.timedelta(days=i)).isoformat() for i in range((b - a).days + 1)]


def radar_days():
    try:
        return json.load(open(os.path.join(DATA, "index.json")))["days"]
    except (OSError, ValueError, KeyError):
        return []


def radar_grid():
    """The radar maps' grid (bounds, size) and which cells it covers, from the latest finished day."""
    for d in reversed(radar_days()):
        p = os.path.join(DATA, f"{d}.json")
        if os.path.exists(p):
            m = json.load(open(p))
            g = np.array(m["grid"]).reshape(m["grid_h"], m["grid_w"])
            return m["bounds"], m["grid_w"], m["grid_h"], g >= 0
    raise SystemExit("No radar day to take the grid from")


def cell_centres(bounds, W, H):
    """Latitude of each row and longitude of each column of the radar grid (rows north to south, Mercator)."""
    s, w, n, e = bounds
    merc = lambda lat: math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))
    imerc = lambda y: math.degrees(2 * math.atan(math.exp(y)) - math.pi / 2)
    y0, y1 = merc(n), merc(s)
    lats = np.array([imerc(y0 + (r + .5) / H * (y1 - y0)) for r in range(H)])
    lons = np.array([w + (c + .5) / W * (e - w) for c in range(W)])
    return lats, lons


def fill_from_silo():
    import h5py
    import requests
    today = dt.datetime.now(QLD).date()
    have_radar = set(radar_days())
    os.makedirs(SILO_DIR, exist_ok=True)
    have_silo = {f[:-5] for f in os.listdir(SILO_DIR) if f[:4].isdigit()}
    want = [d for d in days_between(season_start(today), today - dt.timedelta(days=1))
            if d not in have_radar and (d not in have_silo or d >= (today - dt.timedelta(days=REFRESH_DAYS)).isoformat())]
    # a radar map has since been finished for these days: SILO's isn't needed
    for d in have_silo & have_radar:
        os.remove(os.path.join(SILO_DIR, f"{d}.json"))
    if not want:
        print("No days to fill from SILO")
    bounds, W, H, covered = radar_grid()
    lats, lons = cell_centres(bounds, W, H)
    wrote = []
    for year in sorted({d[:4] for d in want}):
        path = f"silo_{year}.nc"
        local = os.environ.get("SILO_LOCAL_DIR")      # for testing: use files already downloaded
        if local and os.path.exists(os.path.join(local, f"{year}.nc")):
            import shutil
            shutil.copy(os.path.join(local, f"{year}.nc"), path)
        else:
            print(f"Getting SILO {year} daily rainfall", flush=True)
            with requests.get(SILO_URL.format(year=year), stream=True, timeout=120) as r:
                r.raise_for_status()
                with open(path, "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
        with h5py.File(path, "r") as f:
            slat, slon = f["lat"][:], f["lon"][:]
            t0 = dt.date(int(year), 1, 1)
            var = f["daily_rain"]
            scale, offset = float(var.attrs.get("scale_factor", [1])[0]), float(var.attrs.get("add_offset", [0])[0])
            fill = int(var.attrs.get("_FillValue", [-32767])[0])
            # fractional SILO grid positions of each radar cell, for bilinear interpolation
            fy = np.interp(lats, slat, np.arange(len(slat)))
            fx = np.interp(lons, slon, np.arange(len(slon)))
            r0, c0 = np.floor(fy).astype(int), np.floor(fx).astype(int)
            r1, c1 = np.minimum(r0 + 1, len(slat) - 1), np.minimum(c0 + 1, len(slon) - 1)
            dy, dx = (fy - r0)[:, None], (fx - c0)[None, :]
            rlo, rhi = r0.min(), r1.max() + 1
            clo, chi = c0.min(), c1.max() + 1
            for d in [d for d in want if d[:4] == year]:
                k = (dt.date.fromisoformat(d) - t0).days
                if k >= var.shape[0]:
                    print(f"  {d}: not in SILO yet", flush=True)
                    continue
                raw = var[k, rlo:rhi, clo:chi]
                a = np.where(raw == fill, np.nan, raw * scale + offset).astype(np.float32)
                q = lambda rr, cc: a[np.ix_(rr - rlo, cc - clo)]
                v = (q(r0, c0) * (1 - dy) * (1 - dx) + q(r0, c1) * (1 - dy) * dx
                     + q(r1, c0) * dy * (1 - dx) + q(r1, c1) * dy * dx)
                v = np.where(covered & ~np.isnan(v), np.maximum(v, 0), np.nan)
                enc = np.where(np.isnan(v), -1, np.round(v * 10)).astype(int)
                with open(os.path.join(SILO_DIR, f"{d}.json"), "w") as out:
                    json.dump({"date": d, "source": "SILO", "bounds": bounds, "grid_w": W, "grid_h": H,
                               "grid": enc.flatten().tolist()}, out, separators=(",", ":"))
                wrote.append(d)
        os.remove(path)
    days = sorted(f[:-5] for f in os.listdir(SILO_DIR) if f[:4].isdigit())
    with open(os.path.join(SILO_DIR, "index.json"), "w") as f:
        json.dump({"days": days}, f, separators=(",", ":"))
    print(f"SILO days written: {', '.join(wrote) or 'none'}; {len(days)} SILO day(s) kept")


def season():
    """The season total, and the last 14 days (data/fortnight.json), from the radar and SILO days."""
    today = dt.datetime.now(QLD).date()
    span_total(season_start(today), SEASON_OUT, "Season")
    radar = set(radar_days())
    try:
        silo = set(json.load(open(os.path.join(SILO_DIR, "index.json")))["days"])
    except (OSError, ValueError, KeyError):
        silo = set()
    if radar | silo:
        last = dt.date.fromisoformat(max(radar | silo))
        span_total(last - dt.timedelta(days=13), FORTNIGHT_OUT, "14-day")


def span_total(start, out_path, label):
    radar = set(radar_days())
    try:
        silo = set(json.load(open(os.path.join(SILO_DIR, "index.json")))["days"])
    except (OSError, ValueError, KeyError):
        silo = set()
    have = sorted(d for d in radar | silo if d >= start.isoformat())
    if not have:
        print(f"No days for the {label} total yet")
        return
    days = days_between(start, dt.date.fromisoformat(have[-1]))
    total, used_radar, used_silo, gauges, meta0 = None, [], [], {}, None
    for d in days:
        src = "radar" if d in radar else "silo" if d in silo else None
        if not src:
            continue
        p = os.path.join(DATA, f"{d}.json") if src == "radar" else os.path.join(SILO_DIR, f"{d}.json")
        try:
            m = json.load(open(p))
        except (OSError, ValueError):
            continue
        g = np.array(m["grid"], np.float32).reshape(m["grid_h"], m["grid_w"])
        if total is not None and g.shape != total.shape:
            print(f"  {label}: skipping {d}, grid size changed")
            continue
        g = np.where(g < 0, np.nan, g / 10)
        total = g if total is None else total + g
        meta0 = meta0 or m
        (used_radar if src == "radar" else used_silo).append(d)
        for x in m.get("gauges", []):
            if x.get("mm") is None or x.get("ignored") or x.get("unplaced"):
                continue
            t = gauges.setdefault(x["name"], {"name": x["name"], "lat": x.get("lat"), "lon": x.get("lon"),
                                              "source": x.get("source"), "mm": 0.0, "days": 0})
            t["mm"] = round(t["mm"] + x["mm"], 1)
            t["days"] += 1
    if total is None:
        return
    out = {"from": days[0], "to": days[-1], "radar_days": used_radar, "silo_days": used_silo,
           "missing": [d for d in days if d not in used_radar and d not in used_silo],
           "bounds": meta0["bounds"], "grid_w": int(total.shape[1]), "grid_h": int(total.shape[0]),
           "gauges": list(gauges.values()),
           "grid": np.where(np.isnan(total), -1, np.round(total * 10)).astype(int).flatten().tolist()}
    text = json.dumps(out, separators=(",", ":"))
    if os.path.exists(out_path) and open(out_path).read() == text:
        print(f"{label} total unchanged")
        return
    with open(out_path, "w") as f:
        f.write(text)
    print(f"Wrote {out_path}: {days[0]} to {days[-1]}, {len(used_radar)} radar day(s), {len(used_silo)} SILO day(s), "
          f"{len(out['missing'])} missing; wettest {np.nanmax(total):.0f} mm")


if __name__ == "__main__":
    if "season" not in sys.argv[1:]:
        fill_from_silo()
    season()
