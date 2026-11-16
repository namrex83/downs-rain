"""The site's own flood peak estimates for the Condamine's gauges, learned from their past floods.

Data: the Queensland Government's water monitoring records (water-monitoring.information.qld.gov.au, CC BY 4.0) -
hourly river heights back to 1990 ("A", the checked archive) and the last few days by telemetry ("TE"), on the same
gauge scale.

Two methods, both ordinary flood-forecasting practice, learned per gauge:
  1. Rising-limb analogues: every past hour when the gauge was rising, filed by its height and how fast it was rising,
     with how much higher it went over the next 4 days and how many hours that took. A gauge rising now is matched to
     those past moments: the spread of what happened then gives a range (10%, middle, 90%).
  2. Peak travel down the river: for each pair of gauges (upstream -> downstream), past flood peaks upstream matched
     with the downstream peak that followed, fitted as  downstream peak = a + b x upstream peak + c x downstream height
     at that time, with the travel time's range. When an upstream gauge has peaked (or is estimated to), the gauge
     below gets an estimate from it.

    python flood_model.py train   # (by hand, every few months) -> data/flood_model.json
    python flood_model.py now     # (each data run)             -> data/flood_est.json
"""
import json, math, os, sys, time
import datetime as dt
import requests

URL = "https://water-monitoring.information.qld.gov.au/cgi/webservice.pl"
UA = {"User-Agent": "weather-desk-flood-model/1.0"}
DATA = "data"
QLD = dt.timezone(dt.timedelta(hours=10))
# station -> the Bureau's name for the same gauge (as in its river height table)
SITES = {
    "422394A": "Condamine River at Elbow Valley", "422310C": "Condamine River at Warwick",
    "422355A": "Condamine River at Talgai Weir TW", "422323A": "Condamine River at Tummaville",
    "422353B": "Condamine River at Yarramalong", "422316A": "Condamine River at Cecil Plains",
    "422333A": "Condamine River at Loudoun Bridge", "422336A": "Condamine River at Brigalow Bridge",
    "422358B": "Condamine River at Chinchilla Weir HG",
    "422334A": "Kings Creek at Aides Bridge", "422319B": "Dalrymple Creek at Allora", "422306A": "Swan Creek at Swanfels",
    "422313B": "Emu Creek at Emu Vale", "422338A": "Canal Creek at Leyburn", "422350A": "Oakey Creek at Fairview",
    "422326A": "Gowrie Creek at Cranley", "422343A": "Charleys Creek at Chinchilla",
}
# down the main river: each gauge's estimate can come from the one above it
PAIRS = [("422394A", "422310C"), ("422310C", "422323A"), ("422323A", "422353B"), ("422323A", "422316A"),
         ("422316A", "422333A"), ("422333A", "422336A"), ("422336A", "422358B")]
AHEAD_H = 96          # how far ahead a past rise is followed
RATE_BINS = [0.02, 0.05, 0.1, 0.2, 0.4, 99]   # m an hour


def call(fn, params, version="2", tries=3):
    for k in range(tries):
        try:
            r = requests.post(URL, data=json.dumps({"function": fn, "version": version, "params": params}), headers=UA, timeout=300)
            j = r.json()
            if j.get("error_num") in (0, None) or j.get("return"):
                return j
            print(f"  {fn}: {j.get('error_msg')}", flush=True)
            return j
        except Exception as e:
            print(f"  {fn}: {e}", flush=True)
            time.sleep(10 * (k + 1))
    return {}


def hourly(site, start, end, ds="A"):
    """{hour (Qld, as YYYYMMDDHH int): height} for one site."""
    out = {}
    j = call("get_ts_traces", {"site_list": site, "datasource": ds, "varfrom": "100.00", "varto": "100.00",
                               "start_time": start, "end_time": end, "interval": "hour", "multiplier": "1", "data_type": "max"})
    for tr in (j.get("return") or {}).get("traces", []):
        for p in tr.get("trace") or []:
            try:
                q = int(p.get("q", 10))
                if q >= 150:          # the records' own "missing/unusable" codes
                    continue
                out[int(str(p["t"])[:10])] = float(p["v"])
            except (KeyError, ValueError):
                pass
    return out


def to_list(h):
    """dict of hourly heights -> (start datetime, list with None for gaps)."""
    if not h:
        return None, []
    keys = sorted(h)
    t0 = dt.datetime.strptime(str(keys[0]), "%Y%m%d%H")
    t1 = dt.datetime.strptime(str(keys[-1]), "%Y%m%d%H")
    n = int((t1 - t0).total_seconds() // 3600) + 1
    out = [None] * n
    for k, v in h.items():
        i = int((dt.datetime.strptime(str(k), "%Y%m%d%H") - t0).total_seconds() // 3600)
        out[i] = v
    return t0, out


def q(vals, p):
    v = sorted(vals)
    if not v:
        return None
    k = (len(v) - 1) * p
    lo, hi = int(math.floor(k)), int(math.ceil(k))
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def train():
    end = dt.datetime.now(QLD).strftime("%Y%m%d%H%M%S")
    series = {}
    for site, name in SITES.items():
        h = {}
        for y0 in range(1990, dt.datetime.now().year + 1, 6):
            h.update(hourly(site, f"{y0}0101000000", f"{min(y0 + 6, 2100)}0101000000"))
        t0, s = to_list(h)
        if not s:
            print(f"{site} {name}: no records", flush=True)
            continue
        series[site] = (t0, s)
        print(f"{site} {name}: {len([x for x in s if x is not None])} hours from {t0:%Y-%m-%d}", flush=True)
    model = {"made": dt.datetime.now(QLD).strftime("%Y-%m-%d %H:%M"), "source": "Queensland Government water monitoring records (CC BY 4.0)",
             "sites": {}, "pairs": []}
    for site, (t0, s) in series.items():
        vals = [x for x in s if x is not None]
        base = q(vals, .5)
        recs = []   # (height, rate, rise to peak, hours to peak)
        for i in range(3, len(s) - AHEAD_H):
            a, b = s[i], s[i - 3]
            if a is None or b is None:
                continue
            rate = (a - b) / 3
            if rate < 0.02 or a < base + 0.2:
                continue
            ahead = [(x, k) for k, x in enumerate(s[i:i + AHEAD_H]) if x is not None]
            if len(ahead) < AHEAD_H * .7:
                continue
            pk, k = max(ahead)
            recs.append((a, rate, pk - a, k))
        # filed by height (0.5 m steps) and rate class
        bins = {}
        for a, rate, rise, k in recs:
            rb = next(j for j, top in enumerate(RATE_BINS) if rate < top)
            key = f"{math.floor(a * 2) / 2:.1f}|{rb}"
            bins.setdefault(key, []).append((rise, k))
        table = {}
        for key, v in bins.items():
            if len(v) < 6:
                continue
            rises, hrs = [x[0] for x in v], [x[1] for x in v]
            table[key] = [len(v), round(q(rises, .1), 2), round(q(rises, .5), 2), round(q(rises, .9), 2),
                          round(q(hrs, .1)), round(q(hrs, .5)), round(q(hrs, .9))]
        model["sites"][site] = {"name": SITES[site], "base": round(base, 2), "max": round(max(vals), 2),
                                "since": t0.strftime("%Y-%m-%d"), "states": len(recs), "table": table}
        print(f"  {site}: {len(recs)} rising hours, {len(table)} filed states", flush=True)
    # peak travel between gauges
    for up, dn in PAIRS:
        if up not in series or dn not in series:
            continue
        (tu, su), (td, sd) = series[up], series[dn]
        off = int((tu - td).total_seconds() // 3600)    # index in dn = index in up + off
        vals = [x for x in su if x is not None]
        thr = q(vals, .97)                               # the upstream gauge's top 3% of heights: a flood peak
        rows, last = [], -10 ** 9
        for i in range(48, len(su) - 48):
            x = su[i]
            if x is None or x < thr or i - last < 120:
                continue
            win = [y for y in su[i - 48:i + 49] if y is not None]
            if x < max(win):
                continue                                  # not the peak of this rise
            last = i
            j0 = i + off
            if j0 < 0 or j0 + 168 >= len(sd):
                continue
            dnow = sd[j0]
            after = [(y, k) for k, y in enumerate(sd[j0:j0 + 168]) if y is not None]
            if dnow is None or len(after) < 100:
                continue
            pk, k = max(after)
            if pk <= dnow + 0.05:
                continue
            rows.append((x, dnow, pk, k))
        if len(rows) < 6:
            print(f"  {up}->{dn}: only {len(rows)} matched floods", flush=True)
            continue
        # least squares  pk = a + b*x + c*dnow
        n = len(rows)
        X = [(1.0, r[0], r[1]) for r in rows]
        Y = [r[2] for r in rows]
        XtX = [[sum(X[k][i] * X[k][j] for k in range(n)) for j in range(3)] for i in range(3)]
        XtY = [sum(X[k][i] * Y[k] for k in range(n)) for i in range(3)]
        try:
            coef = solve3(XtX, XtY)
        except ZeroDivisionError:
            continue
        res = [Y[k] - sum(coef[i] * X[k][i] for i in range(3)) for k in range(n)]
        sd_res = math.sqrt(sum(r * r for r in res) / max(1, n - 3))
        lags = [r[3] for r in rows]
        model["pairs"].append({"up": up, "dn": dn, "n": n, "a": round(coef[0], 3), "b": round(coef[1], 3), "c": round(coef[2], 3),
                               "sd": round(sd_res, 2), "lag": [round(q(lags, .1)), round(q(lags, .5)), round(q(lags, .9))],
                               "up_thr": round(thr, 2)})
        print(f"  {up}->{dn}: {n} floods, peak = {coef[0]:.2f} + {coef[1]:.2f}x up + {coef[2]:.2f}x now (+/-{sd_res:.2f} m), "
              f"lag {q(lags, .5):.0f} h", flush=True)
    os.makedirs(DATA, exist_ok=True)
    json.dump(model, open(os.path.join(DATA, "flood_model.json"), "w"), separators=(",", ":"))
    print("model written", flush=True)


def solve3(A, b):
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for c in range(3):
        p = max(range(c, 3), key=lambda r: abs(M[r][c]))
        if abs(M[p][c]) < 1e-12:
            raise ZeroDivisionError
        M[c], M[p] = M[p], M[c]
        for r in range(3):
            if r != c:
                f = M[r][c] / M[c][c]
                M[r] = [M[r][k] - f * M[c][k] for k in range(4)]
    return [M[i][3] / M[i][i] for i in range(3)]


def now():
    try:
        model = json.load(open(os.path.join(DATA, "flood_model.json")))
    except (OSError, ValueError):
        print("no flood model yet", flush=True)
        return
    t_end = dt.datetime.now(QLD)
    live = {}
    j = call("get_ts_traces", {"site_list": ",".join(model["sites"]), "datasource": "TE", "varfrom": "100.00", "varto": "100.00",
                               "start_time": (t_end - dt.timedelta(hours=60)).strftime("%Y%m%d%H%M%S"), "end_time": t_end.strftime("%Y%m%d%H%M%S"),
                               "interval": "hour", "multiplier": "1", "data_type": "point"})
    for tr in (j.get("return") or {}).get("traces", []):
        pts = [(dt.datetime.strptime(str(p["t"])[:10], "%Y%m%d%H").replace(tzinfo=QLD), float(p["v"])) for p in tr.get("trace") or []
               if int(p.get("q", 10)) < 150]
        if pts:
            live[tr["site"]] = pts
    est = {}
    nowt = t_end.replace(minute=0, second=0, microsecond=0)

    def at(pts, hours_ago):
        t = nowt - dt.timedelta(hours=hours_ago)
        c = [v for tt, v in pts if abs((tt - t).total_seconds()) <= 1800]
        return c[-1] if c else None

    # 1. rising gauges: matched to their past rises
    for site, m in model["sites"].items():
        pts = live.get(site)
        if not pts or (nowt - pts[-1][0]).total_seconds() > 4 * 3600:
            continue
        h, h3 = pts[-1][1], at(pts, 3)
        if h3 is None:
            continue
        rate = (h - h3) / 3
        if rate < 0.02:
            continue
        rb = next(j for j, top in enumerate(RATE_BINS) if rate < top)
        row = None
        for dh in (0, -0.5, 0.5, -1, 1):     # the nearest height filed with enough past cases
            row = m["table"].get(f"{math.floor((h + dh) * 2) / 2:.1f}|{rb}")
            if row:
                break
        if not row:
            continue
        n, r10, r50, r90, k10, k50, k90 = row
        est[site] = {"name": m["name"], "now": h, "rate": round(rate, 3), "how": "rise", "n": n,
                     "lo": round(h + r10, 2), "mid": round(h + r50, 2), "hi": round(h + r90, 2),
                     "t_lo": (pts[-1][0] + dt.timedelta(hours=k10)).isoformat(), "t_mid": (pts[-1][0] + dt.timedelta(hours=k50)).isoformat(),
                     "t_hi": (pts[-1][0] + dt.timedelta(hours=k90)).isoformat()}
    # 2. peaks coming down the river (in order, so an estimate can pass on down)
    for p in model["pairs"]:
        up, dn = p["up"], p["dn"]
        pu, pd = live.get(up), live.get(dn)
        if not pd:
            continue
        dnow = pd[-1][1]
        src = None
        if pu:
            recent = [(v, t) for t, v in pu if (nowt - t).total_seconds() <= 48 * 3600]
            if recent:
                v, t = max(recent)
                still_rising = pu[-1][1] >= v - 0.01 and (at(pu, 3) or v) < pu[-1][1]
                if v >= p["up_thr"] and not still_rising:
                    src = (v, t, "peaked")
        if not src and up in est and est[up]["mid"] >= p["up_thr"]:
            e = est[up]
            src = (e["mid"], dt.datetime.fromisoformat(e["t_mid"]), "estimated")
        if not src:
            continue
        uv, ut, kind = src
        mid = p["a"] + p["b"] * uv + p["c"] * dnow
        t_mid = ut + dt.timedelta(hours=p["lag"][1])
        if t_mid < nowt - dt.timedelta(hours=3) or mid <= dnow + 0.05:
            continue   # it's been and gone, or nothing much to come
        est[dn] = {"name": model["sites"][dn]["name"] if dn in model["sites"] else SITES.get(dn), "now": dnow, "how": "from_up",
                   "up": SITES.get(up), "up_h": round(uv, 2), "up_kind": kind, "n": p["n"],
                   "lo": round(mid - 1.28 * p["sd"], 2), "mid": round(mid, 2), "hi": round(mid + 1.28 * p["sd"], 2),
                   "t_lo": (ut + dt.timedelta(hours=p["lag"][0])).isoformat(), "t_mid": t_mid.isoformat(),
                   "t_hi": (ut + dt.timedelta(hours=p["lag"][2])).isoformat()}
    horizon = nowt + dt.timedelta(hours=48)
    est = {s: e for s, e in est.items() if dt.datetime.fromisoformat(e["t_lo"]) <= horizon}
    out = {"made": t_end.strftime("%Y-%m-%dT%H:%M+10:00"), "model_made": model.get("made"),
           "now": {SITES[s]: round(p[-1][1], 2) for s, p in live.items() if s in SITES},
           "estimates": {e["name"]: e for e in est.values()}}
    json.dump(out, open(os.path.join(DATA, "flood_est.json"), "w"), separators=(",", ":"))
    print(f"flood estimates: {len(est)} gauge(s) {[e['name'] + ' ' + str(e['mid']) for e in est.values()]}", flush=True)


if __name__ == "__main__":
    {"train": train, "now": now}[sys.argv[1] if len(sys.argv) > 1 else "now"]()
