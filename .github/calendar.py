"""
The rain calendar's data: every finished rain day's gauge-corrected total, cut into small tiles so the page can
read one spot's whole history from a single small file.

Run by .github/workflows/radar.yml after the day maps are made. The day maps (data/<day>.json, and
data/silo/<day>.json for days before the radar record) share one 256 x 258 grid of mm x 10. This cuts that
grid into 16 x 16 tiles and writes, for each tile,

  data/cal/<row>_<col>.json  {"days": [day, ...], "v": [[256 values for the first day], ...]}

(values mm x 10, -1 where there is no data), plus data/cal/index.json {"tile": 16, "w": 256, "h": 258,
"bounds": [...], "days": [...]}. A tile is only rewritten when its values change, so a normal 10-minute run
publishes nothing new.
"""
import json
import os

DATA = "data"
OUT = os.path.join(DATA, "cal")
T = 16


def load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def main():
    idx = load(os.path.join(DATA, "index.json")) or {}
    radar = set(idx.get("days") or [])
    silo = set((load(os.path.join(DATA, "silo", "index.json")) or {}).get("days") or [])
    days, grids, W, H, bounds = [], [], None, None, None
    for d in sorted(radar | silo):
        j = load(os.path.join(DATA, f"{d}.json")) if d in radar else None
        if not j or not j.get("grid"):
            j = load(os.path.join(DATA, "silo", f"{d}.json"))
        if not j or not j.get("grid"):
            continue
        if W is None:
            W, H, bounds = j["grid_w"], j["grid_h"], j["bounds"]
        if (j["grid_w"], j["grid_h"]) != (W, H):
            continue
        days.append(d)
        grids.append(j["grid"])
    if not days:
        print("Calendar: no days")
        return
    os.makedirs(OUT, exist_ok=True)
    wrote = 0
    for ty in range((H + T - 1) // T):
        for tx in range((W + T - 1) // T):
            cells = [y * W + x for y in range(ty * T, min(H, ty * T + T)) for x in range(tx * T, min(W, tx * T + T))]
            v = [[g[c] for c in cells] for g in grids]
            if all(x < 0 for row in v for x in row):
                continue
            body = json.dumps({"days": days, "v": v}, separators=(",", ":"))
            p = os.path.join(OUT, f"{ty}_{tx}.json")
            old = None
            if os.path.exists(p):
                with open(p) as f:
                    old = f.read()
            if old != body:
                with open(p, "w") as f:
                    f.write(body)
                wrote += 1
    ix = json.dumps({"tile": T, "w": W, "h": H, "bounds": bounds, "days": days}, separators=(",", ":"))
    p = os.path.join(OUT, "index.json")
    if not os.path.exists(p) or open(p).read() != ix:
        with open(p, "w") as f:
            f.write(ix)
    print(f"Calendar: {len(days)} days ({days[0]} to {days[-1]}), {wrote} tile(s) rewritten")


main()
