"""
When in each forecast day the rain falls, for all of Australia: the share of each 10am-to-10am day's rain in each
3 hours, from GFS's 3-hourly rain (NOMADS, the same files forecast_gfs.py reads). The page multiplies a day's rain
map by these to play the day on the map in 3-hour steps outside the Downs/SEQ box (inside it, om_frac.json's
seven-model timing is used).

Writes data/frac_aus.json:
  {"run": "...", "days": [...], "lat0": -9.0, "lon0": 112.0, "step": 0.5, "nlat": 71, "nlon": 97,
   "f": ["<8 base-36 digits per point, point by point, rows north to south>", ...one string per day]}
Each point's 8 digits are the 3-hour blocks from 10am, scaled so the wettest block is "z" (35); the page divides by
their sum. A dry point is "11111111" (spread evenly).

Run by .github/workflows/forecast.yml after forecast_gfs.py.
"""
import datetime as dt
import json
import os
import sys

import numpy as np

import forecast_gfs as G

DAYS = 7                     # the Daily view's days
STEP = 0.5                   # every second 0.25-degree point
OUT = os.path.join("data", "frac_aus.json")
DIG = "0123456789abcdefghijklmnopqrstuvwxyz"


def main():
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    run = G.latest_run(now)
    if run is None:
        print("No complete GFS run found; keeping the last timing file")
        return
    tag = run.strftime("%Y-%m-%dT%HZ")
    if os.path.exists(OUT) and json.load(open(OUT)).get("run") == tag and "--force" not in sys.argv:
        print(f"Already have the GFS {tag} timing")
        return
    first = (24 - run.hour) % 24                   # the first 10am (00 UTC) after the run starts
    hours = list(range(first, first + DAYS * 24 + 1, 3))
    cum, grid = {0: 0.0}, None                     # run-total rain (mm) at each forecast hour
    for fhr in [h for h in range(3, hours[-1] + 1, 3)]:
        content = G.fetch(run, fhr)
        if content is None:
            print(f"  f{fhr:03d} missing; stopping there")
            break
        msgs = G.decode(content)
        if grid is None:
            lats, lons = msgs[0][2], msgs[0][3]
            ri = [i for i, la in enumerate(lats) if abs((la * 2) - round(la * 2)) < 1e-6]
            ci = [j for j, lo in enumerate(lons) if abs((lo * 2) - round(lo * 2)) < 1e-6]
            grid = (np.array(ri), np.array(ci), [round(float(lats[i]), 2) for i in ri], [round(float(lons[j]), 2) for j in ci])
        sub = {(s, e): v[np.ix_(grid[0], grid[1])] for s, e, _, _, v in msgs}
        total = next((v for (s, e), v in sub.items() if s == 0 and e == fhr), None)
        if total is None:                              # only a bucket since the last 6-hour reset
            (s, e), v = next(((k, v) for k, v in sub.items() if k[1] == fhr), (None, None))
            if v is None or s not in cum:
                print(f"  f{fhr:03d}: no rain field I can use; stopping there")
                break
            total = cum[s] + v
        cum[fhr] = total
    if grid is None:
        return
    days, f = [], []
    for d in range(DAYS):
        t0 = first + 24 * d
        blocks = [t0 + 3 * b for b in range(9)]
        if any(h not in cum for h in blocks):
            break
        amt = np.stack([np.maximum(0, cum[blocks[b + 1]] - (cum[blocks[b]] if isinstance(cum[blocks[b]], np.ndarray) else 0)) for b in range(8)], -1)
        top = amt.max(-1, keepdims=True)
        q = np.where(top > 0.05, np.rint(amt / np.maximum(top, 1e-9) * 35), 1).astype(int)
        q = np.where((top > 0.05) & (q.sum(-1, keepdims=True) == 0), 1, q)
        f.append("".join(DIG[x] for x in q.reshape(-1)))
        days.append((run + dt.timedelta(hours=t0 + 24)).strftime("%Y-%m-%d"))
    out = {"run": tag, "days": days, "lat0": grid[2][0], "lon0": grid[3][0], "step": STEP,
           "nlat": len(grid[2]), "nlon": len(grid[3]), "lats_desc": grid[2][0] > grid[2][-1], "f": f}
    with open(OUT, "w") as fh:
        json.dump(out, fh, separators=(",", ":"))
    print(f"Wrote {OUT}: {len(days)} days, {out['nlat']} x {out['nlon']} points, {os.path.getsize(OUT) // 1024} KB")


if __name__ == "__main__":
    main()
