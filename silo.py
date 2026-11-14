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


CATCH_IN = os.path.join("assets", "catchments.json")
CATCH_OUT = os.path.join(DATA, "catch_wet.json")


def in_polygon(lat, lon, ring):
    """Which (lat, lon) points (arrays) fall inside a ring of [lon, lat] points (ray casting)."""
    inside = np.zeros(lat.shape, bool)
    xs, ys = [p[0] for p in ring], [p[1] for p in ring]
    for i in range(len(ring)):
        x1, y1, x2, y2 = xs[i - 1], ys[i - 1], xs[i], ys[i]
        if y1 == y2:
            continue
        cross = ((y1 > lat) != (y2 > lat)) & (lon < (x2 - x1) * (lat - y1) / (y2 - y1) + x1)
        inside ^= cross
    return inside


FC_FILES = {"gfs": "forecast_gfs.json", "ecmwf": "forecast.json", "aifs": "forecast_aifs.json"}


def catch_forecast(catchments, ndays=3):
    """Each catchment's forecast rain for the next few 10am-to-10am days: the average over the catchment (points 0.05
    degrees apart inside it, read off each model's 0.25 degree map) for each model, and the average of the models."""
    now = dt.datetime.now(QLD)
    first_end = (now.date() if now.hour < 10 else now.date() + dt.timedelta(days=1)).isoformat()
    want = [(dt.date.fromisoformat(first_end) + dt.timedelta(days=i)).isoformat() for i in range(ndays)]
    pts = {}
    for c in catchments:
        xs, ys = [p[0] for p in c["outline"]], [p[1] for p in c["outline"]]
        LON, LAT = np.meshgrid(np.arange(min(xs), max(xs), 0.05) + 0.025, np.arange(min(ys), max(ys), 0.05) + 0.025)
        m = in_polygon(LAT, LON, c["outline"])
        pts[c["id"]] = (LAT[m], LON[m]) if m.any() else (np.array([np.mean(ys)]), np.array([np.mean(xs)]))
    per = {c["id"]: {} for c in catchments}
    runs = {}
    for name, fn in FC_FILES.items():
        try:
            f = json.load(open(os.path.join(DATA, fn)))
        except (OSError, ValueError):
            continue
        lats, lons = np.array(f["lats"]), np.array(f["lons"])
        nr, nc = len(lats), len(lons)
        if not all(d in f["days"] for d in want):
            continue
        runs[name] = f.get("run")
        for cid, (la, lo) in pts.items():
            fy = (lats[0] - la) / (lats[0] - lats[1]); fx = (lo - lons[0]) / (lons[1] - lons[0])
            r0 = np.clip(np.floor(fy).astype(int), 0, nr - 2); c0 = np.clip(np.floor(fx).astype(int), 0, nc - 2)
            dy, dx = fy - r0, fx - c0
            vals = []
            for d in want:
                g = np.array(f["mm10"][f["days"].index(d)], np.float32).reshape(nr, nc) / 10
                v = (g[r0, c0] * (1 - dy) * (1 - dx) + g[r0, c0 + 1] * (1 - dy) * dx + g[r0 + 1, c0] * dy * (1 - dx) + g[r0 + 1, c0 + 1] * dy * dx)
                vals.append(round(float(np.mean(v)), 1))
            per[cid][name] = vals
    out = {"days": want, "runs": runs, "catchments": {}}
    for cid, models in per.items():
        if models:
            avg = [round(float(np.mean([m[i] for m in models.values()])), 1) for i in range(len(want))]
            out["catchments"][cid] = {"avg": avg, **models}
    return out


def catch_wet():
    """Each catchment's average rain for each of the last 30 rain days (radar map where there is one, else SILO's), and
    the running total since 9am today: data/catch_wet.json, for the Rainfall tab's ground wetness."""
    try:
        catchments = json.load(open(CATCH_IN))["catchments"]
    except (OSError, ValueError, KeyError):
        return
    radar = set(radar_days())
    try:
        silo = set(json.load(open(os.path.join(SILO_DIR, "index.json")))["days"])
    except (OSError, ValueError, KeyError):
        silo = set()
    have = sorted(radar | silo)
    if not have:
        return
    last = dt.date.fromisoformat(have[-1])
    days = days_between(last - dt.timedelta(days=29), last)
    masks, series = {}, {c["id"]: [] for c in catchments}
    def grid_of(path):
        m = json.load(open(path))
        return m, np.array(m["grid"], np.float32).reshape(m["grid_h"], m["grid_w"])
    def averages(m, g):
        key = (m["grid_w"], m["grid_h"], tuple(m["bounds"]))
        if key not in masks:
            s_, w_, n_, e_ = m["bounds"]; H, W = g.shape
            merc = lambda a: np.log(np.tan(np.pi / 4 + np.radians(a) / 2))
            ys = merc(n_) - (np.arange(H) + .5) / H * (merc(n_) - merc(s_))
            lats = np.degrees(2 * np.arctan(np.exp(ys)) - np.pi / 2)
            lons = w_ + (np.arange(W) + .5) / W * (e_ - w_)
            LON, LAT = np.meshgrid(lons, lats)
            masks[key] = {c["id"]: in_polygon(LAT, LON, c["outline"]) for c in catchments}
        out = {}
        for cid, mk in masks[key].items():
            v = g[mk]; v = v[v >= 0]
            out[cid] = round(float(v.mean()) / 10, 1) if v.size > 0.5 * mk.sum() else None
        return out
    for d in days:
        p = os.path.join(DATA, f"{d}.json") if d in radar else os.path.join(SILO_DIR, f"{d}.json") if d in silo else None
        avg = {}
        if p:
            try:
                avg = averages(*grid_of(p))
            except (OSError, ValueError, KeyError):
                avg = {}
        for c in catchments:
            series[c["id"]].append(avg.get(c["id"]))
    today = {}
    try:
        m, g = grid_of(os.path.join(DATA, "today.json"))
        if m.get("date") and m["date"] > days[-1]:
            today = {"date": m["date"], "as_of": m.get("as_of"), **{"mm": averages(m, g)}}
    except (OSError, ValueError, KeyError):
        pass
    out = {"to": days[-1], "days": days, "mm": series, "today": today, "fc": catch_forecast(catchments)}
    text = json.dumps(out, separators=(",", ":"))
    if os.path.exists(CATCH_OUT) and open(CATCH_OUT).read() == text:
        return
    with open(CATCH_OUT, "w") as f:
        f.write(text)
    print(f"Wrote {CATCH_OUT}: {len(catchments)} catchments, {days[0]} to {days[-1]}"
          + "; last 5 days " + ", ".join(f"{c['id']} {sum(x or 0 for x in series[c['id']][-5:]):.0f} mm" for c in catchments))


if __name__ == "__main__":
    if "season" not in sys.argv[1:]:
        fill_from_silo()
    season()
    try:
        catch_wet()
    except Exception as e:
        print(f"Catchment wetness skipped: {e}")
