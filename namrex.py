"""
Namrex forecast: how well each rain model has been doing lately over the Downs, and the weights that follow from it.

Run by .github/workflows/forecast.yml after the model forecasts are fetched:

  1. archive  Each model run's daily rain (GFS, ECMWF, ECMWF AI) over the radar's area is kept in
              data/skill/archive.json, by the day it's for and how far ahead it was made (lead):
              A = the day ends within 2 days of the run, B = 2 to 4 days, C = further out.
  2. score    Once a day is over and its gauge-corrected rain map is in (data/<day>.json, or SILO's map for days
              the radar missed), each model's forecast for it is compared with what fell, square by square.
  3. history  Once a day, Open-Meteo's archive of past runs ("previous runs") for all seven chart models, at 30
              spots across the Downs and SEQ, is scored against the same rain maps for the season so far, so the
              weights don't have to wait months for the site's own archive to build up.
  4. weights  data/skill.json: each model's recent error, for each lead, and its weight in the Namrex blend.

Error: the average difference between forecast and observed rain on a square-root scale (so a 5 mm miss on a dry
day counts for more than on a 60 mm day, and big storms don't swamp everything), over the days with rain or rain
forecast. Weight: in proportion to 1 / error (gentle: rain scores are noisy), shrunk towards equal weights until there are enough rain days
(K_SHRINK), and never under MIN_W.
"""
import datetime as dt
import json
import math
import os
import sys
import time

import numpy as np
import requests

DATA = "data"
SKILL_DIR = os.path.join(DATA, "skill")
ARCHIVE = os.path.join(SKILL_DIR, "archive.json")
SCORES = os.path.join(SKILL_DIR, "scores.json")          # the site's own gridded forecasts, scored
POINTS = os.path.join(SKILL_DIR, "points.json")          # Open-Meteo past runs at spots, scored
OUT = os.path.join(DATA, "skill.json")
QLD = dt.timezone(dt.timedelta(hours=10))
BOX = (-29.5, -25.0, 149.5, 153.5)                         # south, north, west, east: inside the radar maps
GRID_MODELS = {"gfs": "forecast_gfs.json", "ecmwf": "forecast.json", "aifs": "forecast_aifs.json"}
OM_IDS = {"gfs": "gfs_seamless", "ecmwf": "ecmwf_ifs025", "aifs": "ecmwf_aifs025_single", "bom_access_global": "bom_access_global",
          "ukmo_seamless": "ukmo_seamless", "icon_seamless": "icon_seamless", "jma_seamless": "jma_seamless"}
OM_LEAD = {"A": 1, "B": 2, "C": 4}                        # previous_dayN for each lead bucket
KEEP_DAYS = 75
WINDOW = 45                                               # days of scores the weights are worked out from
K_SHRINK = 8                                              # rain days before the scores count as much as equal weights
MIN_W = 0.08
SEASON_START = "2026-10-01"


def jload(p, default):
    try:
        return json.load(open(p))
    except (OSError, ValueError):
        return default


def jsave(p, obj):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        json.dump(obj, f, separators=(",", ":"))


def today_qld():
    return dt.datetime.now(QLD).date()


def bucket(hours):
    return "A" if hours <= 48 else "B" if hours <= 96 else "C"


# ------------------------------------------------------------------ observed rain
_obs_cache = {}


def observed(day):
    """The finished rain map for a day (to 9am that day): the gauge-corrected radar map, else SILO's. None if neither."""
    if day in _obs_cache:
        return _obs_cache[day]
    m = None
    for p in (os.path.join(DATA, f"{day}.json"), os.path.join(DATA, "silo", f"{day}.json")):
        if os.path.exists(p):
            m = jload(p, None)
            if m and m.get("grid"):
                break
            m = None
    if m:
        s, w, n, e = m["bounds"]
        W, H = m["grid_w"], m["grid_h"]
        merc = lambda a: math.log(math.tan(math.pi / 4 + a * math.pi / 360))
        rows = np.array([360 / math.pi * math.atan(math.exp(merc(n) - (r + .5) / H * (merc(n) - merc(s)))) - 90 for r in range(H)])
        cols = np.array([w + (c + .5) / W * (e - w) for c in range(W)])
        g = np.array(m["grid"], dtype=float).reshape(H, W)
        g[g < 0] = np.nan
        m = {"rows": rows, "cols": cols, "g": g / 10}
    _obs_cache[day] = m
    return m


def obs_at(o, lat, lon, half=0.125):
    """Average observed rain over a model square (about 25 km) around a point; None where the map has nothing."""
    r = np.where(np.abs(o["rows"] - lat) <= half)[0]
    c = np.where(np.abs(o["cols"] - lon) <= half)[0]
    if not len(r) or not len(c):
        return None
    v = o["g"][np.ix_(r, c)]
    if np.isnan(v).mean() > .5:
        return None
    return float(np.nanmean(v))


def err(f, o):
    return abs(math.sqrt(max(f, 0)) - math.sqrt(max(o, 0)))


# ------------------------------------------------------------------ 1. archive the gridded forecasts
def archive():
    arc = jload(ARCHIVE, {"points": None, "days": {}})
    for m, fn in GRID_MODELS.items():
        f = jload(os.path.join(DATA, fn), None)
        if not f or not f.get("mm10"):
            continue
        lats, lons = f["lats"], f["lons"]
        ri = [i for i, la in enumerate(lats) if BOX[0] <= la <= BOX[1]]
        ci = [j for j, lo in enumerate(lons) if BOX[2] <= lo <= BOX[3]]
        pts = [[lats[i], lons[j]] for i in ri for j in ci]
        if arc["points"] is None:
            arc["points"] = pts
        if pts != arc["points"]:
            print(f"  {m}: grid differs from the archive's, skipped", flush=True)
            continue
        run = dt.datetime.strptime(f["run"], "%Y-%m-%dT%HZ").replace(tzinfo=dt.timezone.utc)
        nl = len(lons)
        for k, d in enumerate(f["days"]):
            end = dt.datetime.fromisoformat(d + "T00:00:00+00:00")   # forecast days end at 00 UTC (10am Qld)
            hours = (end - run).total_seconds() / 3600
            if hours < 12:
                continue
            b = bucket(hours)
            vals = [f["mm10"][k][i * nl + j] for i in ri for j in ci]
            slot = arc["days"].setdefault(d, {}).setdefault(m, {})
            # the latest run that's still in this lead bucket stands for it
            if b not in slot or slot[b]["run"] <= f["run"]:
                slot[b] = {"run": f["run"], "v": vals}
    cut = (today_qld() - dt.timedelta(days=KEEP_DAYS)).isoformat()
    arc["days"] = {d: v for d, v in arc["days"].items() if d >= cut}
    jsave(ARCHIVE, arc)
    print(f"archive: {len(arc['days'])} days kept", flush=True)


# ------------------------------------------------------------------ 2. score the gridded forecasts
def score():
    arc = jload(ARCHIVE, {"points": None, "days": {}})
    sc = jload(SCORES, {})
    done_by = today_qld().isoformat()
    for d, models in arc["days"].items():
        if d >= done_by or d in sc:
            continue
        o = observed(d)
        if not o:
            continue
        ob = [obs_at(o, la, lo) for la, lo in arc["points"]]
        keep = [i for i, v in enumerate(ob) if v is not None]
        if len(keep) < 20:
            continue
        day = {"obs": round(sum(ob[i] for i in keep) / len(keep), 2), "m": {}, "fc": {}}
        for m, slots in models.items():
            for b, s in slots.items():
                f = [s["v"][i] / 10 for i in keep]
                day["m"].setdefault(m, {})[b] = round(sum(err(f[k], ob[i]) for k, i in enumerate(keep)) / len(keep), 3)
                day["fc"].setdefault(m, {})[b] = round(sum(f) / len(f), 2)
        sc[d] = day
        print(f"  scored {d}: observed {day['obs']} mm average, errors {day['m']}", flush=True)
    jsave(SCORES, {d: v for d, v in sc.items() if d >= (today_qld() - dt.timedelta(days=KEEP_DAYS)).isoformat()})


# ------------------------------------------------------------------ 3. Open-Meteo past runs at spots
def history(force=False):
    pts = jload(POINTS, {"made": None, "days": {}})
    if pts.get("made") == today_qld().isoformat() and not force:
        print("history: already done today", flush=True)
        return
    spots = [(round(la, 2), round(lo, 2)) for la in np.arange(-29.25, -25.4, 0.75) for lo in np.arange(150.0, 153.1, 0.75)]
    start = max(dt.date.fromisoformat(SEASON_START), today_qld() - dt.timedelta(days=WINDOW + 5))
    end = today_qld() - dt.timedelta(days=1)
    days = [(start + dt.timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
    days = [d for d in days if observed(d)]
    if not days:
        print("history: no observed days", flush=True)
        return
    q0 = (dt.date.fromisoformat(days[0]) - dt.timedelta(days=1)).isoformat()
    vars_ = [f"precipitation_previous_day{n}" for n in sorted(set(OM_LEAD.values()))]
    # day d (to 10am Qld) = the hours stamped after 00 UTC the day before, up to 00 UTC on d
    fc = {}   # (model, bucket, day) -> [forecast at each spot or None]
    for mk, mid in OM_IDS.items():
        for i0 in range(0, len(spots), 10):
            chunk = spots[i0:i0 + 10]
            params = {"latitude": ",".join(str(a) for a, _ in chunk), "longitude": ",".join(str(b) for _, b in chunk),
                      "hourly": ",".join(vars_), "models": mid, "start_date": q0, "end_date": days[-1], "timezone": "GMT"}
            j = None
            for attempt in range(3):
                try:
                    r = requests.get("https://previous-runs-api.open-meteo.com/v1/forecast", params=params, timeout=90)
                    if r.status_code == 400:
                        print(f"  {mk}: not available ({r.text[:120]})", flush=True)
                        break
                    r.raise_for_status()
                    j = r.json()
                    break
                except Exception as e:
                    print(f"  {mk}: {e}", flush=True)
                    time.sleep(5 * (attempt + 1))
            if j is None:
                break
            locs = j if isinstance(j, list) else [j]
            for si, loc in enumerate(locs):
                h = loc.get("hourly") or {}
                times = h.get("time") or []
                for b, n in OM_LEAD.items():
                    vals = h.get(f"precipitation_previous_day{n}")
                    if vals is None:
                        continue
                    acc = {}
                    for t, v in zip(times, vals):
                        if v is None:
                            continue
                        tt = dt.datetime.fromisoformat(t)
                        d = (tt - dt.timedelta(minutes=1)).date() + dt.timedelta(days=1)   # hour ending 00:00 belongs to that day
                        acc.setdefault(d.isoformat(), []).append(v)
                    for d in days:
                        a = acc.get(d)
                        lst = fc.setdefault((mk, b, d), [None] * len(spots))
                        if a and len(a) >= 20:
                            lst[i0 + si] = sum(a)
            time.sleep(1)
    out = {"made": today_qld().isoformat(), "spots": spots, "days": {}}
    for d in days:
        o = observed(d)
        ob = [obs_at(o, la, lo) for la, lo in spots]
        day = {"obs": None, "m": {}, "fc": {}}
        have = [i for i, v in enumerate(ob) if v is not None]
        if len(have) < 10:
            continue
        day["obs"] = round(sum(ob[i] for i in have) / len(have), 2)
        for (mk, b, dd), lst in fc.items():
            if dd != d:
                continue
            use = [i for i in have if lst[i] is not None]
            if len(use) < 10:
                continue
            day["m"].setdefault(mk, {})[b] = round(sum(err(lst[i], ob[i]) for i in use) / len(use), 3)
            day["fc"].setdefault(mk, {})[b] = round(sum(lst[i] for i in use) / len(use), 2)
        if day["m"]:
            out["days"][d] = day
    jsave(POINTS, out)
    print(f"history: {len(out['days'])} days scored at {len(spots)} spots for "
          f"{sorted({m for v in out['days'].values() for m in v['m']})}", flush=True)


# ------------------------------------------------------------------ 4. weights
def weights_from(days, models, b):
    """Each model's average error over the recent rain days they all have a score for, and the weights."""
    use = []
    for d in sorted(days)[-WINDOW:]:
        v = days[d]
        if not all(m in v["m"] and b in v["m"][m] for m in models):
            continue
        wet = (v.get("obs") or 0) >= 0.5 or any((v["fc"].get(m) or {}).get(b, 0) >= 1 for m in models)
        if wet:
            use.append(v)
    n = len(use)
    if not n:
        return None
    mae = {m: sum(v["m"][m][b] for v in use) / n for m in models}
    raw = {m: 1 / max(mae[m], .05) for m in models}
    tot = sum(raw.values())
    eq = 1 / len(models)
    w = {m: (n * raw[m] / tot + K_SHRINK * eq) / (n + K_SHRINK) for m in models}
    w = {m: max(MIN_W, x) for m, x in w.items()}
    t2 = sum(w.values())
    return {"n": n, "mae": {m: round(x, 3) for m, x in mae.items()}, "w": {m: round(x / t2, 3) for m, x in w.items()}}


def weights():
    sc = jload(SCORES, {})
    pts = jload(POINTS, {"days": {}}).get("days", {})
    out = {"made": dt.datetime.now(QLD).strftime("%Y-%m-%dT%H:%M+10:00"), "window": WINDOW, "map": {}, "chart": {},
           "recent": []}
    grid_models = list(GRID_MODELS)
    for b in ("A", "B", "C"):
        # map: the site's own gridded forecasts once there are enough of them, otherwise the spot scores for the same models
        g = weights_from(sc, grid_models, b)
        p = weights_from(pts, grid_models, b)
        pick = g if g and g["n"] >= 10 else (p if p else g)
        if g and p and g["n"] < 10:   # both: the spot scores carry the weights until the site's own archive builds up
            n = g["n"] + p["n"]
            pick = {"n": n, "mae": {m: round((g["mae"][m] * g["n"] + p["mae"][m] * p["n"]) / n, 3) for m in grid_models}}
            raw = {m: 1 / max(pick["mae"][m], .05) for m in grid_models}
            tot = sum(raw.values()); eq = 1 / len(grid_models)
            w = {m: max(MIN_W, (n * raw[m] / tot + K_SHRINK * eq) / (n + K_SHRINK)) for m in grid_models}
            pick["w"] = {m: round(x / sum(w.values()), 3) for m, x in w.items()}
        if pick:
            out["map"][b] = {**pick, "source": "site" if pick is g else "spots" if pick is p else "both"}
        # chart: every model with spot scores for this lead
        have = sorted({m for v in pts.values() for m in v["m"] if b in v["m"][m]})
        counts = {m: sum(1 for v in pts.values() if b in (v["m"].get(m) or {})) for m in have}
        have = [m for m in have if counts[m] >= 0.6 * max(counts.values() or [0])]
        c = weights_from(pts, have, b) if len(have) >= 2 else None
        if c:
            out["chart"][b] = c
    # the last few rain days, for the track record on the page (lead A: the day before)
    src = {**pts, **sc}
    for d in sorted(src)[-14:]:
        v = src[d]
        out["recent"].append({"day": d, "obs": v.get("obs"), "fc": {m: x.get("A") for m, x in v["fc"].items() if "A" in x}})
    jsave(OUT, out)
    for b, v in out["map"].items():
        print(f"weights {b} ({v['n']} rain days, {v['source']}): {v['w']}  errors {v['mae']}", flush=True)
    for b, v in out["chart"].items():
        print(f"chart weights {b} ({v['n']} rain days): {v['w']}", flush=True)


if __name__ == "__main__":
    steps = sys.argv[1:] or ["archive", "score", "history", "weights"]
    for s in steps:
        try:
            {"archive": archive, "score": score, "history": lambda: history("--force" in sys.argv), "weights": weights}[s]()
        except KeyError:
            pass
        except Exception as e:   # one step failing shouldn't stop the rest (or the forecast job)
            print(f"{s} failed: {type(e).__name__}: {e}", flush=True)
