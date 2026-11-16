"""Each day's worst storm category at every grid point, for both storm models, in data/storm_days.json: a small file
the page reads on opening to put a red dot on the Storms tab (and on the forecast chart's days) when severe storms
are possible, without downloading the full storm files. Categories as in the page's "Combined" view:
0 none, 1 storms possible, 2 organised storms, 3 severe, 4 significant severe, 5 high-end severe."""
import datetime as dt
import json
import math
import os

OUT = os.path.join("data", "storm_days.json")


def category(li, cape, sh, cin):   # li is x10, cape J/kg, sh knots, cin J/kg (the same rule as the page)
    if li is None or cape is None or cape < 250 or li > -10:
        return 0
    if cape >= 1000 and li <= -40 and sh is not None and sh >= 40:
        f_li = min(1, max(0, -li / 20))
        f_cap = 1 if cin is None else min(1, max(0.25, 1 - (cin - 50) / 200))
        sev = math.sqrt(2 * cape) * sh * 0.5144 * f_li * f_cap
        return 5 if sev >= 1700 else 4 if sev >= 1300 else 3
    if cape >= 500 and sh is not None and sh >= 25:
        return 2
    return 1


def update(key, run, lats, lons, times, li, cape, sh, cin):
    """key: "gfs" or "ecmwf"; the arrays are [time][point], points row by row from the north-west corner."""
    days = {}
    for h, t in enumerate(times):
        d = (dt.datetime.fromtimestamp(t, dt.timezone.utc) + dt.timedelta(hours=10)).strftime("%Y-%m-%d")   # Queensland date
        row = days.setdefault(d, [0] * len(li[h]))
        for k in range(len(row)):
            c = category(li[h][k], cape[h][k], sh[h][k], cin[h][k] if cin else None)
            if c > row[k]:
                row[k] = c
    try:
        out = json.load(open(OUT))
    except Exception:
        out = {}
    out[key] = {"run": run, "lats": [float(x) for x in lats], "lons": [float(x) for x in lons],
                "days": {d: "".join(str(c) for c in v) for d, v in sorted(days.items())}}
    os.makedirs("data", exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(out, f, separators=(",", ":"))
    print(f"{OUT}: {key} worst category by day " + ", ".join(f"{d} {max(int(c) for c in s)}" for d, s in out[key]["days"].items()))
