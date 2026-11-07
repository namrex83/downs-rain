"""
The Bureau of Meteorology's own daily forecasts (the ones in the BOM app) for towns across South East Queensland
and the Darling Downs, for a "BOM" line on the Forecast tab's rain chart.

Run by .github/workflows/forecast.yml. For each town it reads the BOM app's 7-day forecast and keeps, per day
(midnight to midnight, Queensland time): the rain amount BOM gives a 50% chance of reaching ("min"), the amount it
gives a 25% chance ("max") and the chance of any rain. Writes data/bom_fc.json.

Note: this is the BOM app's data feed, which isn't officially licensed for reuse; fine for personal use, but ask
BOM before using it for clients.
"""
import datetime as dt
import json
import os
import time

import requests

OUT = os.path.join("data", "bom_fc.json")
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; seq-weather-desk/1.0; personal weather page)"}
TOWNS = {
    "Dalby": (-27.18, 151.26), "Toowoomba": (-27.56, 151.95), "Oakey": (-27.43, 151.72), "Pittsworth": (-27.72, 151.63),
    "Millmerran": (-27.87, 151.27), "Cecil Plains": (-27.53, 151.19), "Chinchilla": (-26.74, 150.63), "Miles": (-26.66, 150.18),
    "Tara": (-27.28, 150.46), "Jandowae": (-26.78, 151.11), "Bell": (-26.93, 151.45), "Kingaroy": (-26.54, 151.84),
    "Nanango": (-26.67, 152.00), "Crows Nest": (-27.26, 152.05), "Warwick": (-28.22, 152.03), "Stanthorpe": (-28.65, 151.93),
    "Goondiwindi": (-28.55, 150.31), "Inglewood": (-28.42, 151.08), "Texas": (-28.85, 151.17), "St George": (-28.04, 148.58),
    "Roma": (-26.57, 148.79), "Condamine": (-26.93, 150.13), "Wandoan": (-26.12, 149.96), "Taroom": (-25.64, 149.80),
    "Murgon": (-26.24, 151.94), "Gayndah": (-25.63, 151.61), "Gatton": (-27.56, 152.28), "Esk": (-27.24, 152.42),
    "Ipswich": (-27.61, 152.76), "Boonah": (-28.00, 152.68), "Beaudesert": (-27.99, 153.00), "Brisbane": (-27.47, 153.03),
    "Gold Coast": (-28.00, 153.43), "Sunshine Coast": (-26.65, 153.07), "Gympie": (-26.19, 152.67), "Clifton": (-27.93, 151.91),
}
_B32 = "0123456789bcdefghjkmnpqrstuvwxyz"


def geohash(lat, lon, n=6):
    """Standard geohash, which the BOM app uses to name forecast locations."""
    la, lo, out, bits, ch, even = [-90.0, 90.0], [-180.0, 180.0], "", 0, 0, True
    while len(out) < n:
        rng, v = (lo, lon) if even else (la, lat)
        mid = (rng[0] + rng[1]) / 2
        if v > mid:
            ch = ch * 2 + 1
            rng[0] = mid
        else:
            ch = ch * 2
            rng[1] = mid
        even, bits = not even, bits + 1
        if bits == 5:
            out += _B32[ch]
            bits, ch = 0, 0
    return out


def run():
    towns = []
    for name, (lat, lon) in TOWNS.items():
        try:
            r = requests.get(f"https://api.weather.bom.gov.au/v1/locations/{geohash(lat, lon)}/forecasts/daily", headers=HEADERS, timeout=30)
            r.raise_for_status()
            days = []
            for d in r.json().get("data", []):
                rain = d.get("rain") or {}
                amt = rain.get("amount") or {}
                date = (d.get("date") or "")
                if not date:
                    continue
                # BOM gives the date as midnight Queensland time in UTC (e.g. 2026-11-07T14:00:00Z for 8 Nov)
                local = (dt.datetime.fromisoformat(date.replace("Z", "+00:00")) + dt.timedelta(hours=10)).date().isoformat()
                days.append({"d": local, "min": amt.get("min"), "max": amt.get("max"), "chance": rain.get("chance")})
            if days:
                towns.append({"name": name, "lat": lat, "lon": lon, "days": days})
        except Exception as e:
            print(f"  {name}: {e}", flush=True)
        time.sleep(0.5)
    if not towns:
        raise SystemExit("No BOM town forecasts")
    os.makedirs("data", exist_ok=True)
    with open(OUT, "w") as f:
        json.dump({"made": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "towns": towns}, f, separators=(",", ":"))
    print(f"Wrote {OUT}: {len(towns)} of {len(TOWNS)} towns; e.g. {towns[0]['name']}: "
          + ", ".join(f"{x['d'][5:]} {x['min']}-{x['max']} mm {x['chance']}%" for x in towns[0]["days"][:4]))


if __name__ == "__main__":
    run()
