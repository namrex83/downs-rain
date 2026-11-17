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
import json, math, os, re, sys, time
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
    "422349B": "Condamine River at Lemon Tree Weir", "422347B": "Condamine River at Pampas Bridge",
    "422345A": "Condamine River at Lone Pine", "422344A": "Condamine River at Bedarra", "422325A": "Condamine River at Cotswold",
    "422340B": "Condamine River at Talgai Weir HG", "422341A": "Condamine River at Brosnans Barn",
    "422317C": "Glengallan Creek at Rocky Ridge", "422359B": "Oakey Creek at Jondaryan", "422360A": "Oakey Creek at Dalmeny",
    "422361A": "Oakey Creek at Bowenville Reserve",
    # matched later (.github/flood_sources.py): the same gauge, or one beside it, in the government records
    "422329A": "Condamine River at Pratten", "422330A": "Oakey Creek at Oakey (Weir Park)", "422395A": "Sandy Creek at Leslie Dam TW",
    "422315B": "Sandy Creek at Leslie Dam HG", "422314A": "Condamine River at Ranges Bridge", "422332A": "Gowrie Creek at Oakey",
    "422327A": "Thanes Creek at Ellangowan", "4223059": "Condamine River at Millbrook", "4223187": "Condamine River at Condamine",
    "4223112": "Charleys Creek at Greenswamp Rd", "4223083": "Swan Creek at Yangan", "4223089": "Hodgson Creek at Felton Tm",
}
# the same gauge under another station number in the records: tried when the first number has nothing since 1990
# (the flood sources check found these numbers' records ending in 1960-1981, or none at all)
ALT = {"422330A": "422330B", "422332A": "422332B", "422349B": "422349A", "422340B": "422340A", "422353B": "422353A",
       "422358B": "422358A"}
# gauges with no usable record that sit just below one that has: how they follow it is fitted from the Bureau readings
# this site keeps for both (the last 10 days), and the upstream gauge's estimate is passed on through that fit
NEAR = {"Condamine River at Ranges Bridge": "422333A",   # Ranges Bridge (record ends 1960) follows Loudoun Bridge
        "Condamine River at The Head": "422341A", "Spring Creek at Killarney Alert": "422394A", "Condamine River at Killarney": "422394A",
        "Condamine River at Murrays Bridge": "422394A", "Condamine River at Harrisons Road": "422310C", "Swan Creek at Yangan": "422306A",
        "Connolly Dam": "422310C", "Condamine River at Warwick (Scots College)": "422310C", "Campbells Gully": "422310C",
        "Glengallan Creek": "422317C", "Dalrymple Creek at Goomburra": "422319B", "Dalrymple Creek at Victoria Hill": "422319B",
        "Dalrymple Creek at Allora Alert": "422319B", "Condamine River at Millbrook": "422355A", "Canal Creek at Karara": "422338A",
        "Canal Creek at Torridon": "422338A", "Condamine River at Centenary Bridge": "422323A",
        "West Creek at Margaret Street": "422326A", "West Creek at Herries St": "422326A", "West Creek at Alderly": "422326A",
        "East Creek at South St": "422326A", "East Creek at Long St": "422326A", "East Creek at Alderley": "422326A",
        "Doctors Creek at Oakey (Pechey-Maclagan Rd)": "422360A", "Doctors Creek at Oakey (Oakey-Cooyar Rd)": "422360A",
        "Oakey Creek at Oakey (Boodua Muniganeen Road)": "422360A", "Oakey Creek at Oakey (Kingsthorpe Haden Road)": "422360A",
        "Oakey Creek at Oakey (Weir Park)": "422360A", "Condamine River at Loudoun Weir": "422333A",
        "Condamine River at Chinchilla Weir TW": "422336A", "Condamine River at Condamine": "422344A",
        "Charleys Creek at Greenswamp Rd": "422343A", "Charleys Creek at Beruna": "422343A", "Charleys Creek at Seven Oaks": "422343A",
        "Charleys Creek at Burncluith Bridge": "422343A", "Kings Creek at Clifton": "422334A", "Condamine River at Tipton Bridge": "422316A",
        "Condamine River at Warra-Kogan Rd Bridge": "422336A"}
# creeks with no gauge with AI on them: a rise is matched to a similar Downs creek's past rises (by how fast it's rising
# only, as the heights are on another board) - experimental
BORROW = {**{n: "422359B" for n in ["Myall Creek at Quinalow Peranga Rd", "Myall Creek at Clydesdale", "Myall Creek at Moffatt",
                                    "Myall Creek at Blaxland", "Myall Creek at Dalby"]},
          **{n: "422343A" for n in ["Jimbour Creek at Macalister Bell Rd", "Jandowae Creek at Niagara Rd", "Jandowae Creek at Sengs Road",
                                    "Jandowae Creek at Jandowae Dam", "Jandowae Creek at Jandowae", "Cooranga Creek at Jandowae-Macalister Rd",
                                    "Cooranga Creek at Warra", "Undulla Creek at Tara Lagoon", "Undulla Creek at Leichhardt Highway"]},
          **{n: "422319B" for n in ["Hodgson Creek at Cambooya (Rolleston Park)", "Hodgson Creek at Felton", "Hodgson Creek at Felton Tm"]},
          "Thanes Creek at Ellangowan": "422319B", "Bowenville": "422361A"}
# down the main river: each gauge's estimate can come from the one above it
PAIRS = [("422341A", "422394A"), ("422394A", "422310C"), ("422310C", "422329A"), ("422329A", "422355A"), ("422355A", "4223059"),
         ("4223059", "422323A"), ("422310C", "422323A"), ("422326A", "422332A"), ("422330A", "422359B"), ("422323A", "422353B"), ("422323A", "422347B"),
         ("422347B", "422349B"), ("422349B", "422345A"), ("422323A", "422316A"), ("422316A", "422333A"), ("422333A", "422336A"),
         ("422333A", "422314A"), ("422336A", "422344A"), ("422344A", "4223187"), ("422344A", "422325A"), ("422360A", "422359B"), ("422359B", "422361A"), ("422361A", "422350A")]
STATE = "state"       # the radar job's working state (kept between its runs)
BOM_ARCH = os.path.join(STATE, "bom_archive.json")   # every Condamine gauge's readings from the Bureau's table, kept from now on
LOG = os.path.join(STATE, "flood_log.json")          # the estimates given, to check against what the river then did
LEARN = os.path.join(STATE, "flood_learn.json")      # how far off they were, per gauge
CONDAMINE_LAST = "Undulla Creek at Tara Lagoon"      # the end of the Condamine part of the Bureau's river table
# the catchments (assets/catchments.json, rain in data/catch_wet.json) whose rain reaches each gauge
RAIN_CATCH = {"Kings Creek": ["kings"], "Dalrymple Creek": ["dalrymple"], "Swan Creek": ["swan"], "Sandy Creek": ["sandy"],
              "Gowrie Creek": ["northbranch"], "Oakey Creek": ["northbranch"], "Hodgson Creek": ["hodgson"], "Thanes Creek": ["hodgson"],
              "Charleys Creek": ["charleys"], "Emu Creek": ["condamine_tum"], "Glengallan Creek": ["condamine_tum"],
              "Canal Creek": ["condamine_tum"], "Spring Creek": ["condamine_tum"], "Myall Creek": ["myall"], "Jandowae Creek": ["jandowae"], "Jimbour Creek": ["jimbour"]}
CONDAMINE_RAIN = [("Brosnans Barn Elbow Valley Warwick Pratten Talgai Millbrook Tummaville Emu Glengallan Canal", ["condamine_tum"]),
                  ("Yarramalong Pampas Lemon Tree Lone Pine Cecil Plains Centenary", ["condamine_tum"]),
                  ("Loudoun Ranges", ["condamine_tum", "condamine_mid"]),
                  ("Brigalow Bedarra Condamine Cotswold Chinchilla", ["condamine_all"])]
AHEAD_H = 96          # how far ahead a past rise is followed
RATE_BINS = [0.02, 0.05, 0.1, 0.2, 0.4, 99]   # m an hour
UP_RISE = 0.5         # an upstream rise of at least this (m) counts as a flood coming down


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


CACHE = "fm_cache/series.json"   # the downloaded records, kept between runs (actions/cache)


def train():
    series = {}
    try:
        cached = json.load(open(CACHE))
    except (OSError, ValueError):
        cached = {}
    for site, name in SITES.items():
        h = {int(k): v for k, v in (cached.get(site) or {}).items()}
        last = max(h) if h else None
        src = (cached.get("_src") or {}).get(site, site)   # the number this gauge's records came from
        if not last:
            for cand in [site] + ([ALT[site]] if site in ALT else []):
                for y0 in range(1990, dt.datetime.now().year + 1, 6):
                    h.update(hourly(cand, f"{y0}0101000000", f"{min(y0 + 6, 2100)}0101000000"))
                if h:
                    src = cand
                    break
        else:   # only what's been added to the records since
            h.update(hourly(src, f"{str(last)[:8]}000000", dt.datetime.now(QLD).strftime("%Y%m%d%H%M%S")))
        if src != site:
            cached.setdefault("_src", {})[site] = src
            print(f"  {site}: records under {src}", flush=True)
        cached[site] = {str(k): v for k, v in h.items()}
        t0, s = to_list(h)
        if not s:
            print(f"{site} {name}: no records", flush=True)
            continue
        series[site] = (t0, s)
        print(f"{site} {name}: {len([x for x in s if x is not None])} hours from {t0:%Y-%m-%d}", flush=True)
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    json.dump(cached, open(CACHE, "w"), separators=(",", ":"))
    # gauges with no government record: the Bureau's readings this site has kept (state/bom_archive.json), once
    # there are 90 days of them (they're only learned from once floods have been seen)
    try:
        arch = json.load(open(BOM_ARCH))
    except (OSError, ValueError):
        arch = {}
    have = set(SITES.values())
    for name, h in arch.items():
        if name in have or len(h) < 90 * 12:
            continue
        t0, s = to_list({int(k): v for k, v in h.items()})
        if s:
            series["bom:" + name] = (t0, s)
            print(f"bom:{name}: {len([x for x in s if x is not None])} hours of kept readings from {t0:%Y-%m-%d}", flush=True)
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
            if rate < 0.007 or a < base + 0.2:   # slow rises near the top count too
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
        if not table and site.startswith("bom:"):
            continue
        model["sites"][site] = {"name": SITES.get(site) or site[4:], "base": round(base, 2), "max": round(max(vals), 2),
                                "since": t0.strftime("%Y-%m-%d"), "states": len(recs), "table": table}
        print(f"  {site}: {len(recs)} rising hours, {len(table)} filed states", flush=True)
    # peak travel between gauges
    for up, dn in PAIRS:
        if up not in series or dn not in series:
            continue
        (tu, su), (td, sd) = series[up], series[dn]
        off = int((tu - td).total_seconds() // 3600)    # index in dn = index in up + off
        rows, last = [], -10 ** 9
        for i in range(96, len(su) - 48):
            x = su[i]
            if x is None or i - last < 120:
                continue
            win = [y for y in su[i - 48:i + 49] if y is not None]
            if x < max(win):
                continue                                  # not the peak of this rise
            before = [y for y in su[i - 96:i] if y is not None]
            if len(before) < 48:
                continue
            urise = x - min(before)
            if urise < UP_RISE:
                continue                                  # only real rises upstream
            last = i
            j0 = i + off
            if j0 < 0 or j0 + 168 >= len(sd):
                continue
            dnow = sd[j0]
            after = [(y, k) for k, y in enumerate(sd[j0:j0 + 168]) if y is not None]
            if dnow is None or len(after) < 100:
                continue
            pk, k = max(after)
            rows.append((urise, dnow, pk - dnow, k))
        if len(rows) < 8:
            print(f"  {up}->{dn}: only {len(rows)} matched floods", flush=True)
            continue
        # least squares  rise downstream = a + b x rise upstream + c x downstream height then
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
                               "sd": round(sd_res, 2), "lag": [round(q(lags, .1)), round(q(lags, .5)), round(q(lags, .9))]})
        print(f"  {up}->{dn}: {n} floods, rise = {coef[0]:.2f} + {coef[1]:.2f}x upstream rise + {coef[2]:.2f}x height (+/-{sd_res:.2f} m), "
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


def jload(p, default):
    try:
        return json.load(open(p))
    except (OSError, ValueError):
        return default


def bom_readings(t_end):
    """The Condamine gauges' latest readings from the Bureau's river table (data/flood_warn.json): {name: (time, height)}."""
    fw = jload(os.path.join(DATA, "flood_warn.json"), {}).get("rivers") or {}
    hd = fw.get("headers") or []
    col = lambda w: next((i for i, h in enumerate(hd) if w in h.lower()), None)
    iN, iT, iH = col("station") or 0, col("time"), col("height")
    out, days = {}, ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    if iT is None or iH is None:
        return out
    for r in fw.get("rows") or []:
        try:
            name, h = r[iN], float(r[iH])
            m = re.match(r"(\d{1,2})\.(\d{2})\s*([AP]M)\s+(\w{3})", r[iT] or "")
        except (ValueError, IndexError, TypeError):
            m = None
        if m and m.group(4) in days:   # a gauge listed twice (manual and automatic): the newer reading is kept
            hh = int(m.group(1)) % 12 + (12 if m.group(3) == "PM" else 0)
            for back in range(8):
                d = t_end - dt.timedelta(days=back)
                if d.weekday() == days.index(m.group(4)):
                    t = d.replace(hour=hh, minute=int(m.group(2)), second=0, microsecond=0)
                    if t <= t_end + dt.timedelta(minutes=10) and (name not in out or t > out[name][0]):
                        out[name] = (t, h)
                        break
        if r and r[iN] == CONDAMINE_LAST:
            break
    return out


def rain_for(name, wet):
    """Rain over the gauge's catchment: (mm in the last 3 days to now, mm forecast over the next 2 days), area-weighted."""
    ids = next((v for k, v in RAIN_CATCH.items() if name.startswith(k)), None)
    if not ids and name.startswith("Condamine River"):
        ids = next((v for words, v in CONDAMINE_RAIN if any(w in name for w in words.split())), ["condamine_all"])
    if not ids or not wet:
        return None, None
    past, nxt, wsum = 0.0, 0.0, 0.0
    for c in ids:
        ser = (wet.get("mm") or {}).get(c)
        if not ser:
            continue
        w = 1.0
        past += w * (sum(x or 0 for x in ser[-3:]) + (((wet.get("today") or {}).get("mm") or {}).get(c) or 0))
        f = ((wet.get("fc") or {}).get("catchments") or {}).get(c) or {}
        nxt += w * sum((f.get("avg") or [])[:2])
        wsum += w
    return (round(past / wsum, 1), round(nxt / wsum, 1)) if wsum else (None, None)


def now():
    try:
        model = json.load(open(os.path.join(DATA, "flood_model.json")))
    except (OSError, ValueError):
        print("no flood model yet", flush=True)
        return
    t_end = dt.datetime.now(QLD)
    nowt = t_end.replace(minute=0, second=0, microsecond=0)
    hkey = lambda t: int(t.strftime("%Y%m%d%H"))
    # keep every Condamine gauge's Bureau reading (from now on), to learn the gauges with no government record
    bom = bom_readings(t_end)
    arch = jload(BOM_ARCH, {})
    cut = hkey(nowt - dt.timedelta(days=3 * 365))
    for name, (t, h) in bom.items():
        a = arch.setdefault(name, {})
        a[str(hkey(t))] = h
        if len(a) > 30000:
            arch[name] = {k: v for k, v in a.items() if int(k) >= cut}
    os.makedirs(STATE, exist_ok=True)
    json.dump(arch, open(BOM_ARCH, "w"), separators=(",", ":"))
    live = {}
    gov = [s_ for s_ in model["sites"] if not s_.startswith("bom:")]
    back = {ALT[s_]: s_ for s_ in gov if s_ in ALT}   # the other number's live readings stand in for the gauge
    j = call("get_ts_traces", {"site_list": ",".join(gov + list(back)), "datasource": "TE", "varfrom": "100.00", "varto": "100.00",
                               "start_time": (t_end - dt.timedelta(hours=60)).strftime("%Y%m%d%H%M%S"), "end_time": t_end.strftime("%Y%m%d%H%M%S"),
                               "interval": "hour", "multiplier": "1", "data_type": "point"})
    for tr in (j.get("return") or {}).get("traces", []):
        pts = [(dt.datetime.strptime(str(p["t"])[:10], "%Y%m%d%H").replace(tzinfo=QLD), float(p["v"])) for p in tr.get("trace") or []
               if int(p.get("q", 10)) < 150]
        if pts:
            k = tr["site"]
            if k in back:
                if back[k] in live:
                    continue
                k = back[k]
            live[k] = pts
    for s_ in model["sites"]:   # the gauges learned from the site's own kept readings: those readings are their live data too
        if s_.startswith("bom:"):
            a = arch.get(s_[4:]) or {}
            pts = sorted((dt.datetime.strptime(k, "%Y%m%d%H").replace(tzinfo=QLD), v) for k, v in a.items() if int(k) >= hkey(nowt - dt.timedelta(hours=60)))
            if pts:
                live[s_] = pts
    name_of = lambda s_: model["sites"][s_]["name"] if s_ in model["sites"] else s_[5:] if s_.startswith("near:") else SITES.get(s_, s_)
    # gauges with no live government readings (some newer station numbers only have the archive): the Bureau's readings
    # kept by this site stand in, when they sit in the range the records know (the same gauge board; a weir read on a
    # different datum, like Lemon Tree Weir in metres above sea level, can't be used this way)
    for s_ in gov:
        if s_ in live:
            continue
        m_ = model["sites"][s_]
        a = arch.get(m_["name"]) or {}
        pts = sorted((dt.datetime.strptime(k, "%Y%m%d%H").replace(tzinfo=QLD), v) for k, v in a.items() if int(k) >= hkey(nowt - dt.timedelta(hours=60)))
        if pts and m_["base"] - 0.5 <= pts[-1][1] <= m_["max"] + 1.0:
            live[s_] = pts
            print(f"  {s_} {m_['name']}: live from the Bureau's readings ({len(pts)} hours)", flush=True)
    est = {}

    def at(pts, hours_ago):
        t = nowt - dt.timedelta(hours=hours_ago)
        c = [v for tt, v in pts if abs((tt - t).total_seconds()) <= 1800]
        return c[-1] if c else None

    # 1. rising gauges: matched to their past rises (a slow rise near the top counts too)
    for site, m in model["sites"].items():
        pts = live.get(site)
        if not pts or (nowt - pts[-1][0]).total_seconds() > 4 * 3600 or not m.get("table"):
            continue
        h, h3 = pts[-1][1], at(pts, 3)
        if h3 is None:
            continue
        rate = (h - h3) / 3
        if rate < 0.007:
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
        if up in est and est[up]["how"] in ("rise", "from_up"):   # still rising up there, or a wave on its way: where it's heading
            e = est[up]
            low = min(vv for tt, vv in pu) if pu else e["now"]
            if e["mid"] - low >= UP_RISE:
                src = (e["mid"], e["mid"] - low, dt.datetime.fromisoformat(e["t_mid"]), "estimated")
        if not src and pu:
            recent = [(v, t) for t, v in pu if (nowt - t).total_seconds() <= 48 * 3600]
            if recent:
                v, t = max(recent)
                low = min(vv for tt, vv in pu if tt <= t)
                still_rising = (at(pu, 3) or v) < pu[-1][1] - 0.01
                if v - low >= UP_RISE and not still_rising:
                    src = (v, v - low, t, "peaked")
        if not src and up in est:
            e = est[up]
            low = min(vv for tt, vv in pu) if pu else e["now"]
            if e["mid"] - low >= UP_RISE:
                src = (e["mid"], e["mid"] - low, dt.datetime.fromisoformat(e["t_mid"]), "estimated")
        if not src:
            continue
        uv, urise, ut, kind = src
        rise = p["a"] + p["b"] * urise + p["c"] * dnow
        t_mid = ut + dt.timedelta(hours=p["lag"][1])
        if t_mid < nowt or rise <= 0.1:
            continue   # its time has passed (the gauge's own readings say what's happening now), or nothing much to come
        cand = {"name": name_of(dn), "now": dnow, "how": "from_up",
                "up": name_of(up), "up_h": round(uv, 2), "up_site": up, "up_kind": kind, "n": p["n"],
                "lo": round(dnow + max(0, rise - 1.28 * p["sd"]), 2), "mid": round(dnow + rise, 2), "hi": round(dnow + rise + 1.28 * p["sd"], 2),
                "t_lo": (ut + dt.timedelta(hours=p["lag"][0])).isoformat(), "t_mid": t_mid.isoformat(),
                "t_hi": (ut + dt.timedelta(hours=p["lag"][2])).isoformat()}
        if dn not in est or cand["mid"] > est[dn]["mid"]:   # two rivers meeting: the higher of the two
            est[dn] = cand
    # 3. the rest: just peaked (a rise of 0.3 m+ in the last 60 hours, now falling), rising (with no past case to match),
    #    or no rise expected
    def settle(site, pts):
        v, t = max((vv, tt) for tt, vv in pts)
        low = min(vv for tt, vv in pts if tt <= t)
        h = pts[-1][1]
        if v - low >= 0.3 and h < v - 0.02 and (nowt - t).total_seconds() > 2 * 3600:
            return {"name": name_of(site), "now": h, "how": "peaked", "mid": round(v, 2), "t_mid": t.isoformat()}
        h3 = at(pts, 3)
        if h3 is not None and h - h3 >= 0.03:
            return {"name": name_of(site), "now": h, "how": "rising"}
        return {"name": name_of(site), "now": h, "how": "steady"}
    for site, pts in live.items():
        if site in est or site not in model["sites"]:
            continue
        est[site] = settle(site, pts)
    # 4. rain over each gauge's catchment: the last 3 days, and the next 2 days' forecast (the estimates above don't
    #    know about rain still to fall)
    wet = jload(os.path.join(DATA, "catch_wet.json"), None)
    for s_, e in est.items():
        past, nxt = rain_for(e["name"], wet)
        if past is not None:
            e["rain_past"], e["rain_next"] = past, nxt
    # 5. learning from its own misses: every estimate is logged, and once the river has peaked it's checked; each
    #    gauge's estimates are then scaled by how far off they've been (separately for wet and dry outlooks), more so
    #    the more floods it's been checked on
    log = jload(LOG, [])
    learn = jload(LEARN, {})
    obs_now = {s_: pts for s_, pts in live.items()}
    keep = []
    for L in log:
        t_iss = dt.datetime.fromisoformat(L["t"])
        pts = [(t, v) for t, v in obs_now.get(L["site"], []) if t >= t_iss]
        if pts:   # the highest the river has been since the estimate, and when
            tt, vv = max(pts, key=lambda x: x[1])
            if vv > L.get("seen", -99):
                L["seen"], L["seen_t"] = vv, tt.isoformat()
        L.setdefault("seen", -99)
        done = (nowt - t_iss).total_seconds() > 120 * 3600 or (pts and pts[-1][1] < L["seen"] - 0.05 and L["seen"] > L["now"] + 0.05)
        if not done:
            keep.append(L)
            continue
        if L["mid"] - L["now"] < 0.2 or L["seen"] < -50:
            continue
        ratio = max(0.1, min(3.0, (L["seen"] - L["now"]) / (L["mid"] - L["now"])))
        key = f'{L["site"]}|{L["how"]}|{"wet" if (L.get("rain_next") or 0) >= 10 else "dry"}'
        ev = (L.get("seen_t") or L["t"])[:10]
        rows = learn.setdefault(key, [])
        same = next((r for r in rows if r["event"] == ev), None)
        terr = (dt.datetime.fromisoformat(L["seen_t"]) - dt.datetime.fromisoformat(L["t_mid"])).total_seconds() / 3600 if L.get("seen_t") else 0
        if same:
            same["n"] += 1; same["lr"] += math.log(ratio); same["terr"] += terr
        else:
            rows.append({"event": ev, "n": 1, "lr": math.log(ratio), "terr": terr})
        learn[key] = rows[-12:]
    for s_, e in est.items():
        if e["how"] not in ("rise", "from_up"):
            continue
        key = f'{s_}|{e["how"]}|{"wet" if (e.get("rain_next") or 0) >= 10 else "dry"}'
        rows = learn.get(key) or []
        if rows:
            n = len(rows)
            f = math.exp(sum(r["lr"] / r["n"] for r in rows) / n)
            w = n / (n + 2)                                   # trust it more as more floods are checked
            f_use = max(0.5, min(1.5, f ** w))
            shift = max(-48, min(48, w * sum(r["terr"] / r["n"] for r in rows) / n))
            for k in ("lo", "mid", "hi"):
                e[k] = round(e["now"] + (e[k] - e["now"]) * f_use, 2)
            for k in ("t_lo", "t_mid", "t_hi"):
                e[k] = (dt.datetime.fromisoformat(e[k]) + dt.timedelta(hours=shift)).isoformat()
            e["learned"] = {"floods": n, "factor": round(f_use, 2), "shift_h": round(shift)}
        last = next((L for L in reversed(keep) if L["site"] == s_), None)
        if not last or (nowt - dt.datetime.fromisoformat(last["t"])).total_seconds() >= 3 * 3600:
            keep.append({"site": s_, "how": e["how"], "t": nowt.isoformat(), "now": e["now"], "mid": e["mid"], "t_mid": e["t_mid"],
                         "rain_next": e.get("rain_next")})
    json.dump(keep[-3000:], open(LOG, "w"), separators=(",", ":"))
    json.dump(learn, open(LEARN, "w"), separators=(",", ":"))
    # onto the Bureau's scale: where its board reads differently from these records (a different gauge board at the
    # same place, as at Loudoun Bridge), every height is moved by the difference between the two latest readings
    off = {}
    for s_, pts in live.items():
        b = bom.get(name_of(s_))
        if b is not None:
            d = b[1] - pts[-1][1]
            off[s_] = round(d, 2) if abs(d) >= 0.15 else 0.0
    for s_, e in est.items():
        o = off.get(s_, 0.0)
        if o:
            for k in ("now", "lo", "mid", "hi"):
                if k in e:
                    e[k] = round(e[k] + o, 2)
            e["bom_offset"] = o
        if e.get("up_site") and off.get(e["up_site"]):
            e["up_h"] = round(e["up_h"] + off[e["up_site"]], 2)
    # gauges that follow a neighbour (NEAR): fit  height here = a + b x neighbour's height (lag hours before), on the
    # Bureau's own scale, from the readings kept for both; then the neighbour's coming peak gives this gauge's
    learned_names = {m_["name"] for m_ in model["sites"].values()}
    for nm, up in NEAR.items():
        if nm in learned_names or up not in model["sites"]:
            continue   # learned properly by now (from its own kept readings)
        mine, theirs = arch.get(nm) or {}, arch.get(name_of(up)) or {}
        key = "near:" + nm
        cut10 = hkey(nowt - dt.timedelta(days=10))
        tr_ = {dt.datetime.strptime(k, "%Y%m%d%H"): v for k, v in theirs.items() if int(k) >= cut10}
        mn_ = {dt.datetime.strptime(k, "%Y%m%d%H"): v for k, v in mine.items() if int(k) >= cut10}
        pts = sorted((t.replace(tzinfo=QLD), v) for t, v in mn_.items() if t.replace(tzinfo=QLD) >= nowt - dt.timedelta(hours=60))
        if pts:
            live[key] = pts
        fits = []
        for lag in range(-24, 49):   # below the neighbour (later) or above it (sooner)
            xy = [(tr_[t - dt.timedelta(hours=lag)], v) for t, v in mn_.items() if (t - dt.timedelta(hours=lag)) in tr_]
            if len(xy) < 12 or max(x for x, _ in xy) - min(x for x, _ in xy) < 0.5:
                continue
            n = len(xy); mx = sum(x for x, _ in xy) / n; my = sum(y for _, y in xy) / n
            sxx = sum((x - mx) ** 2 for x, _ in xy)
            b = sum((x - mx) * (y - my) for x, y in xy) / sxx
            a = my - b * mx
            sd_ = math.sqrt(sum((y - a - b * x) ** 2 for x, y in xy) / max(1, n - 2))
            if b > 0:
                fits.append((lag, a, b, sd_, n, min(x for x, _ in xy), max(x for x, _ in xy)))
        # on a steady rise several lags fit about as well: the shortest of those that do
        if fits:   # a lag that leaves few hours overlapping can fit well by chance: only those using most of them
            nmax = max(f[4] for f in fits)
            fits = [f for f in fits if f[4] >= 0.8 * nmax]
        best = min((f for f in fits if f[3] <= min(g[3] for g in fits) * 1.1 + 0.01), key=lambda f: abs(f[0])) if fits else None
        e = est.get(up)
        if not pts or not e or e["how"] not in ("rise", "from_up"):
            continue
        if not best:   # a wave is coming down to the neighbour, but the two can't be compared yet: say so, without a height
            est[key] = {**settle(key, pts), "exp": True, "nofit": True, "later": {"up": name_of(up), "t_mid": e["t_mid"]}}
            continue
        lag, a, b, sd_, n, xlo, xhi = best
        sd_ = sd_ + 0.1 * max(0, e["mid"] - xhi)   # less sure the further past the heights seen together
        mid = a + b * e["mid"]
        here = pts[-1][1]
        if mid <= here + 0.05 or dt.datetime.fromisoformat(e["t_mid"]) + dt.timedelta(hours=lag) < nowt:
            continue
        sh = lambda k: (dt.datetime.fromisoformat(e[k]) + dt.timedelta(hours=lag)).isoformat()
        est[key] = {"name": nm, "now": here, "how": "from_near", "up": name_of(up), "up_h": e["mid"], "n": n, "lag": lag,
                    "lo": round(max(here, a + b * e["lo"] - 1.28 * sd_), 2), "mid": round(mid, 2), "hi": round(a + b * e["hi"] + 1.28 * sd_, 2),
                    "t_lo": sh("t_lo"), "t_mid": sh("t_mid"), "t_hi": sh("t_hi"), "exp": True}
        print(f"  {nm}: follows {name_of(up)} {lag} h later, height = {a:.2f} + {b:.2f} x ({n} hours, +/-{sd_:.2f} m) -> {mid:.2f}", flush=True)
    # creeks with nothing to follow: matched by rate of rise to a similar creek's past rises (all heights pooled)
    for nm, donor in BORROW.items():
        if nm in learned_names or donor not in model["sites"]:
            continue
        a_ = arch.get(nm) or {}
        pts = sorted((dt.datetime.strptime(k, "%Y%m%d%H").replace(tzinfo=QLD), v) for k, v in a_.items() if int(k) >= hkey(nowt - dt.timedelta(hours=60)))
        if not pts:
            continue
        key = "near:" + nm
        live[key] = pts
        h, h3 = pts[-1][1], at(pts, 3)
        if h3 is None or (nowt - pts[-1][0]).total_seconds() > 4 * 3600:
            continue
        rate = (h - h3) / 3
        if rate < 0.007:
            continue
        rb = next(j for j, top in enumerate(RATE_BINS) if rate < top)
        rows = [r for k, r in model["sites"][donor]["table"].items() if k.endswith(f"|{rb}")]
        if not rows:
            continue
        tot = sum(r[0] for r in rows)
        w = lambda i: sum(r[0] * r[i] for r in rows) / tot
        est[key] = {"name": nm, "now": h, "rate": round(rate, 3), "how": "borrow", "donor": name_of(donor), "n": tot, "exp": True,
                    "lo": round(h + w(1), 2), "mid": round(h + w(2), 2), "hi": round(h + w(3), 2),
                    "t_lo": (pts[-1][0] + dt.timedelta(hours=w(4))).isoformat(), "t_mid": (pts[-1][0] + dt.timedelta(hours=w(5))).isoformat(),
                    "t_hi": (pts[-1][0] + dt.timedelta(hours=w(6))).isoformat()}
    # the rest of those: what the gauge is doing now (peaked, rising, or no rise), marked experimental
    for key in [k for k in live if k.startswith("near:") and k not in est]:
        est[key] = {**settle(key, live[key]), "exp": True}
    horizon = nowt + dt.timedelta(hours=48)
    far = {s: e for s, e in est.items() if "t_lo" in e and dt.datetime.fromisoformat(e["t_lo"]) > horizon}
    for s_, e in far.items():   # a peak more than 2 days off is too uncertain to show: say what the gauge is doing now
        if s_ in live:
            n = settle(s_, live[s_])
            o = off.get(s_, 0.0)
            for k in ("now", "mid"):
                if k in n:
                    n[k] = round(n[k] + o, 2)
            n["later"] = {"up": e.get("up") or e.get("donor"), "t_mid": e["t_mid"]}
            if e.get("exp"):
                n["exp"] = True
            est[s_] = n
        else:
            del est[s_]
    # gauges with no record to learn from yet: the readings being kept for them
    covered = {e["name"] for e in est.values()} | {m["name"] for m in model["sites"].values()}
    collecting = {n: min(a)[:8] for n, a in arch.items() if n not in covered and a}
    out = {"made": t_end.strftime("%Y-%m-%dT%H:%M+10:00"), "model_made": model.get("made"),
           "now": {name_of(s): round(p[-1][1], 2) for s, p in live.items()},
           "estimates": {e["name"]: e for e in est.values()}, "collecting": collecting,
           "checked": sum(len(v) for v in learn.values())}
    json.dump(out, open(os.path.join(DATA, "flood_est.json"), "w"), separators=(",", ":"))
    print(f"flood estimates: {len(est)} gauge(s) {[e['name'] + ' ' + str(e.get('mid')) for e in est.values()]}; "
          f"{len(keep)} logged, {sum(len(v) for v in learn.values())} floods checked; keeping readings for {len(arch)} gauges", flush=True)


if __name__ == "__main__":
    {"train": train, "now": now}[sys.argv[1] if len(sys.argv) > 1 else "now"]()
