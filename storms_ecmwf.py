"""
Storm ingredients from the ECMWF model, for the Storms tab's "ECMWF" option.

Run by .github/workflows/forecast.yml after each ECMWF 00z and 12z run. Uses ECMWF open data
(IFS, 0.25 degree, licensed CC BY 4.0) and writes data/storms_ecmwf.json in the same layout as
storms.py's GFS file, every 3 hours out to 3 days:

  li     surface-based lifted index (degC x10): a parcel with the 2 m temperature and dew point
         is lifted to 500 hPa (dry to its cloud base, then moist) and compared with the air there
  cape   most-unstable CAPE (J/kg) as ECMWF computes it, if the open data has it; else left empty
  shear  difference between the 10 m and 500 hPa winds (knots)
  cin    the cap (J/kg, as a positive number): ECMWF's most-unstable CIN if the open data has it,
         else estimated from the 925, 850 and 700 hPa temperatures
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


RD = 287.04


def parcel_temp(t2, td2, ps, level):
    """Temperature (K) of air lifted from the ground (2 m temperature and dew point) to `level` hPa:
    dry up to its cloud base, then following the moist adiabat."""
    td2 = np.minimum(td2, t2)
    tl = 1 / (1 / (td2 - 56) + np.log(t2 / td2) / 800) + 56          # cloud-base temperature
    pl = ps * (tl / t2) ** (1 / 0.2854)                               # cloud-base pressure
    dry = t2 * (level / ps) ** 0.2854                                 # if the base is above the level
    cp, lv, eps = 1005.7, 2.501e6, 0.622
    t, p = tl.copy(), pl.copy()
    n = 60
    dp = (level - np.maximum(pl, level)) / n                         # negative: going up
    for _ in range(n):
        e = es(t)
        rs = eps * e / np.maximum(p - e, 1)
        dtdp = (RD * t + lv * rs) / (p * (cp + lv * lv * rs * eps / (RD * t * t)))
        t = t + dtdp * dp
        p = p + dp
    return np.where(pl <= level, dry, t)


def lifted_index(t2, td2, ps, t500):
    """Surface-based lifted index (K): t500 minus the lifted parcel's temperature at 500 hPa."""
    return t500 - parcel_temp(t2, td2, ps, 500)


def cap_estimate(t2, td2, ps, temps):
    """Rough convective inhibition (J/kg) below 700 hPa: the energy needed to push air from the
    ground up through layers where it is colder than the air around it (e.g. a night-time
    inversion). temps = {level hPa: air temperature K} for 925, 850 and 700 hPa."""
    levels = [ps] + [lv for lv in (925, 850, 700)]
    env = [t2] + [temps[lv] for lv in (925, 850, 700)]
    par = [t2] + [parcel_temp(t2, td2, ps, lv) for lv in (925, 850, 700)]
    cin = np.zeros_like(t2)
    for i in range(3):
        lo, hi = levels[i], levels[i + 1]
        lo_p = np.maximum(lo, hi + 1e-3) if np.ndim(lo) else max(lo, hi + 1e-3)
        above_ground = ps > hi                                       # skip levels below the ground
        deficit = ((env[i] - par[i]) + (env[i + 1] - par[i + 1])) / 2
        cin += np.where(above_ground, RD * np.maximum(deficit, 0) * np.log(lo_p / hi), 0)
    return cin


def run():
    from ecmwf.opendata import Client
    client = Client(source="ecmwf")
    when = client.latest(type="fc", stream="oper", param="2t", step=72)
    tag = when.strftime("%Y-%m-%dT%HZ")
    old = json.load(open(OUT)) if os.path.exists(OUT) else {}
    if old.get("run") == tag and any(v is not None for v in (old.get("cin") or [[None]])[0]) and "--force" not in sys.argv:   # files made before the cap was added are redone
        print(f"Already have the {tag} storm run")
        return
    base = dict(date=when.strftime("%Y%m%d"), time=when.hour, type="fc", stream="oper", step=STEPS)
    print(f"Getting ECMWF {tag} storm ingredients", flush=True)
    client.retrieve(**base, param=["2t", "2d", "sp", "10u", "10v"], target="sfc.grib2")
    client.retrieve(**base, param=["t", "u", "v"], levtype="pl", levelist=500, target="pl.grib2")
    client.retrieve(**base, param="t", levtype="pl", levelist=[925, 850, 700], target="low.grib2")
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
    low, _ = read("low.grib2")
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
        if c is None:      # not in the open data: estimate the cap from the 925, 850 and 700 hPa temperatures
            temps = {lv: get(low, "t", step, lv) for lv in (925, 850, 700)}
            if all(v is not None for v in temps.values()):
                c = cap_estimate(t2, td2, ps / 100, temps)
        cn.append(np.round(np.abs(np.nan_to_num(c))).astype(int).flatten().tolist() if c is not None else [None] * t2.size)
    os.makedirs("data", exist_ok=True)
    with open(OUT, "w") as f:
        json.dump({"run": tag, "made": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
                   "model": "ECMWF IFS (open data, 0.25°)",
                   "lats": [round(float(x), 3) for x in lats], "lons": [round(float(x), 3) for x in lons],
                   "times": times, "li": li, "cape": cp, "shear": sh, "cin": cn}, f, separators=(",", ":"))
    for p in ("sfc.grib2", "pl.grib2", "low.grib2", "cape.grib2", "cin.grib2"):
        if os.path.exists(p):
            os.remove(p)
    lowest = min(min(x) for x in li) / 10 if li else None
    print(f"Wrote {OUT}: {len(lats)}x{len(lons)} points x {len(times)} times; lowest LI {lowest}; CAPE {'yes' if have_cape else 'no'}; CIN {'yes' if have_cin else 'no'}")


if __name__ == "__main__":
    run()
