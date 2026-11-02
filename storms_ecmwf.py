"""
Storm ingredients from the ECMWF model, for the Storms tab's "ECMWF" option.

Run by .github/workflows/forecast.yml after each ECMWF 00z and 12z run. Uses ECMWF open data
(IFS, 0.25 degree, licensed CC BY 4.0) and writes data/storms_ecmwf.json in the same layout as
storms.py's GFS file, every 3 hours out to 3 days:

  li     surface-based lifted index (degC x10): a parcel with the 2 m temperature and dew point
         is lifted to 500 hPa (dry to its cloud base, then moist) and compared with the air there
  cape   most-unstable CAPE (J/kg) as ECMWF computes it, if the open data has it; else left empty
  shear  difference between the 10 m and 500 hPa winds (knots)
  cin    most-unstable convective inhibition, the cap (J/kg, as a positive number), if available
"""
import datetime as dt
import json
import os
import sys

import numpy as np

LAT_N, LAT_S = -23.0, -32.0       # same area as the GFS storm maps
LON_W, LON_E = 146.0, 155.0
STEPS = list(range(0, 73, 3))     # every 3 hours out to 3 days
OUT = os.path.join("data", "storms_ecmwf.json")
KT = 1.94384                      # m/s to knots


def read(path):
    """{(shortName, level, step): values} for the area, plus the area's lats and lons."""
    import eccodes
    out, lats_lons = {}, None
    with open(path, "rb") as f:
        while True:
            h = eccodes.codes_grib_new_from_file(f)
            if h is None:
                break
            try:
                name = eccodes.codes_get(h, "shortName")
                level = int(eccodes.codes_get(h, "level"))
                step = int(eccodes.codes_get(h, "endStep"))
                ni, nj = eccodes.codes_get(h, "Ni"), eccodes.codes_get(h, "Nj")
                lat1 = eccodes.codes_get(h, "latitudeOfFirstGridPointInDegrees")
                lon1 = eccodes.codes_get(h, "longitudeOfFirstGridPointInDegrees")
                dlat = eccodes.codes_get(h, "jDirectionIncrementInDegrees")
                dlon = eccodes.codes_get(h, "iDirectionIncrementInDegrees")
                north_first = eccodes.codes_get(h, "jScansPositively") == 0
                vals = eccodes.codes_get_values(h).reshape(nj, ni)
            finally:
                eccodes.codes_release(h)
            lats = lat1 - np.arange(nj) * dlat if north_first else lat1 + np.arange(nj) * dlat
            lons = (lon1 + np.arange(ni) * dlon + 180) % 360 - 180
            rows = np.where((lats <= LAT_N + 1e-6) & (lats >= LAT_S - 1e-6))[0]
            cols = np.where((lons >= LON_W - 1e-6) & (lons <= LON_E + 1e-6))[0]
            rows, cols = rows[np.argsort(-lats[rows])], cols[np.argsort(lons[cols])]
            out[(name, level, step)] = vals[np.ix_(rows, cols)]
            lats_lons = (lats[rows], lons[cols])
    return out, lats_lons


def es(t):
    """Saturation vapour pressure (hPa) at temperature t (K), Bolton 1980."""
    return 6.112 * np.exp(17.67 * (t - 273.15) / (t - 29.65))


def lifted_index(t2, td2, ps, t500):
    """Surface-based lifted index (K): t500 minus the lifted parcel's temperature at 500 hPa."""
    td2 = np.minimum(td2, t2)
    tl = 1 / (1 / (td2 - 56) + np.log(t2 / td2) / 800) + 56          # cloud-base temperature
    pl = ps * (tl / t2) ** (1 / 0.2854)                               # cloud-base pressure
    dry = t2 * (500 / ps) ** 0.2854                                   # if the base is above 500 hPa
    # follow the moist adiabat from cloud base up to 500 hPa in small steps
    rd, cp, lv, eps = 287.04, 1005.7, 2.501e6, 0.622
    t, p = tl.copy(), pl.copy()
    n = 60
    dp = (500 - np.maximum(pl, 500)) / n                             # negative: going up
    for _ in range(n):
        e = es(t)
        rs = eps * e / np.maximum(p - e, 1)
        dtdp = (rd * t + lv * rs) / (p * (cp + lv * lv * rs * eps / (rd * t * t)))
        t = t + dtdp * dp
        p = p + dp
    parcel = np.where(pl <= 500, dry, t)
    return t500 - parcel


def run():
    from ecmwf.opendata import Client
    client = Client(source="ecmwf")
    when = client.latest(type="fc", stream="oper", param="2t", step=72)
    tag = when.strftime("%Y-%m-%dT%HZ")
    old = json.load(open(OUT)) if os.path.exists(OUT) else {}
    if old.get("run") == tag and "cin" in old and "--force" not in sys.argv:   # files made before the cap was added are redone
        print(f"Already have the {tag} storm run")
        return
    base = dict(date=when.strftime("%Y%m%d"), time=when.hour, type="fc", stream="oper", step=STEPS)
    print(f"Getting ECMWF {tag} storm ingredients", flush=True)
    client.retrieve(**base, param=["2t", "2d", "sp", "10u", "10v"], target="sfc.grib2")
    client.retrieve(**base, param=["t", "u", "v"], levtype="pl", levelist=500, target="pl.grib2")
    have_cape = have_cin = True
    try:
        client.retrieve(**base, param="mucape", target="cape.grib2")
    except Exception as e:
        print(f"  no CAPE in the open data ({e}); CAPE maps will be empty", flush=True)
        have_cape = False
    try:
        client.retrieve(**base, param="mucin", target="cin.grib2")
    except Exception as e:
        print(f"  no CIN (cap) in the open data ({e}); severity won't allow for the cap", flush=True)
        have_cin = False
    sfc, (lats, lons) = read("sfc.grib2")
    pl, _ = read("pl.grib2")
    cape = read("cape.grib2")[0] if have_cape else {}
    cins = read("cin.grib2")[0] if have_cin else {}
    get = lambda d, name, step, level=None: next((v for (n, lv, s), v in d.items()
                                                 if n == name and s == step and (level is None or lv == level)), None)
    times, li, cp, sh, cn = [], [], [], [], []
    for step in STEPS:
        t2, td2, ps = get(sfc, "2t", step), get(sfc, "2d", step), get(sfc, "sp", step)
        u10, v10 = get(sfc, "10u", step), get(sfc, "10v", step)
        t5, u5, v5 = get(pl, "t", step, 500), get(pl, "u", step, 500), get(pl, "v", step, 500)
        if any(x is None for x in (t2, td2, ps, u10, v10, t5, u5, v5)):
            print(f"  step {step}: fields missing, skipped", flush=True)
            continue
        times.append(int((when + dt.timedelta(hours=step)).replace(tzinfo=dt.timezone.utc).timestamp()))
        li.append(np.round(lifted_index(t2, td2, ps / 100, t5) * 10).astype(int).flatten().tolist())
        sh.append(np.round(np.hypot(u5 - u10, v5 - v10) * KT).astype(int).flatten().tolist())
        c = get(cape, "mucape", step)
        cp.append(np.round(c).astype(int).flatten().tolist() if c is not None else [None] * t2.size)
        c = get(cins, "mucin", step)
        cn.append(np.round(np.abs(np.nan_to_num(c))).astype(int).flatten().tolist() if c is not None else [None] * t2.size)
    os.makedirs("data", exist_ok=True)
    with open(OUT, "w") as f:
        json.dump({"run": tag, "made": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
                   "model": "ECMWF IFS (open data, 0.25°)",
                   "lats": [round(float(x), 3) for x in lats], "lons": [round(float(x), 3) for x in lons],
                   "times": times, "li": li, "cape": cp, "shear": sh, "cin": cn}, f, separators=(",", ":"))
    for p in ("sfc.grib2", "pl.grib2", "cape.grib2", "cin.grib2"):
        if os.path.exists(p):
            os.remove(p)
    lowest = min(min(x) for x in li) / 10 if li else None
    print(f"Wrote {OUT}: {len(lats)}x{len(lons)} points x {len(times)} times; lowest LI {lowest}; CAPE {'yes' if have_cape else 'no'}; CIN {'yes' if have_cin else 'no'}")


if __name__ == "__main__":
    run()
