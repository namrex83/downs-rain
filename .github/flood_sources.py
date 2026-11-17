"""Looks for long river height records for the Condamine catchment's gauges that the flood model doesn't cover yet:
the Queensland Government's water monitoring records (WMIP) and the Bureau's Water Data Online (KiWIS). Prints what
it finds; run by hand from .github/workflows/flood-sources.yml."""
import json, re, sys
import requests

UA = {"User-Agent": "Mozilla/5.0 (weather-desk flood model)"}
WMIP = "https://water-monitoring.information.qld.gov.au/cgi/webservice.pl"
KIWIS = "https://www.bom.gov.au/waterdata/services"
# the gauges still without an estimate: the Bureau's name -> its station number (from its graph links)
fw = json.load(open("data/flood_warn.json"))["rivers"]
est = json.load(open("data/flood_est.json")).get("estimates", {})
names, seen = [], set()
for r in fw["rows"]:
    if r[0] in seen:
        continue
    seen.add(r[0]); names.append(r[0])
    if r[0] == "Undulla Creek at Tara Lagoon":
        break
want = {n: (re.search(r"\.(\d{6})\.plt", fw["plots"].get(n, "")) or [None, None])[1] for n in names if n not in est}
print(f"{len(want)} Condamine gauges without an estimate")

# 1. WMIP: every site in the Condamine-Balonne basin (422...) on the Downs
try:
    r = requests.post(WMIP, data=json.dumps({"function": "get_db_info", "version": "3", "params": {
        "table_name": "site", "return_type": "array", "field_list": ["station", "stname", "latitude", "longitude"],
        "filter_values": {}}}), headers=UA, timeout=300)
    rows = r.json().get("return", {}).get("rows", [])
    print(f"WMIP: {len(rows)} sites in all")
    for s in rows:
        try:
            lat, lon = float(s.get("latitude") or 0), float(s.get("longitude") or 0)
        except ValueError:
            continue
        if str(s.get("station", "")).startswith("422") and -28.8 < lat < -26.0 and 149.5 < lon < 152.6:
            print(f"  WMIP {s['station']:10} {lat:9.4f} {lon:9.4f} {s.get('stname')}")
except Exception as e:
    print(f"WMIP failed: {e}")

# 2. Water Data Online (KiWIS): stations on the Downs, and the time series our station numbers have
def kiwis(**p):
    q = {"service": "kisters", "type": "queryServices", "datasource": "0", "format": "json", **p}
    r = requests.get(KIWIS, params=q, headers=UA, timeout=120)
    return r.json()
try:
    st = kiwis(request="getStationList", returnfields="station_no,station_name,station_latitude,station_longitude", bbox="149.5,-28.8,152.6,-26.0")
    print(f"KiWIS: {len(st) - 1} stations on the Downs")
    for row in st[1:]:
        print("  WDO", " | ".join(str(x) for x in row))
except Exception as e:
    print(f"KiWIS station list failed: {e}")
for n, no in list(want.items()):
    if not no:
        continue
    try:
        ts = kiwis(request="getTimeseriesList", station_no=no, returnfields="station_no,station_name,ts_id,ts_name,parametertype_name,coverage")
        rows = ts[1:] if isinstance(ts, list) else []
        lv = [x for x in rows if "Level" in str(x)]
        print(f"  {no} {n}: {len(rows)} series; levels: " + "; ".join(" | ".join(str(y) for y in x) for x in lv[:4]))
    except Exception as e:
        print(f"  {no} {n}: {e}")

# 3. The flood model's sites that came back with no records when it learned: which datasources and variables
#    each one actually has in WMIP, and (for the 7-digit numbers, which look like the Bureau's) what Water Data Online
#    holds under that number
STUCK = ["422349B", "422340B", "422330A", "422314A", "422332A", "422327A", "4223059", "4223187", "4223112", "4223083",
         "4223089", "422353B", "422358B"]
def wmip(fn, params, version):
    try:
        r = requests.post(WMIP, data=json.dumps({"function": fn, "version": version, "params": params}), headers=UA, timeout=120)
        return r.json()
    except Exception as e:
        return {"error_msg": str(e)}
print("\nSites with no records at the last learning run:")
for s in STUCK:
    j = wmip("get_datasources_by_site", {"site_list": s}, "1")
    ds = [d for t in (j.get("return") or {}).get("sites", []) for d in (t.get("datasources") or [])]
    print(f"  {s}: datasources {ds or '-'} {j.get('error_msg') or ''}")
    for d in ds or ["A"]:
        v = wmip("get_variable_list", {"site_list": s, "datasource": d}, "1")
        for t in (v.get("return") or {}).get("sites", []):
            for x in t.get("variables") or []:
                print(f"      {d}: {x.get('variable')} {x.get('name')} ({x.get('units')}) {x.get('period_start')} - {x.get('period_end')}")
        if v.get("error_msg"):
            print(f"      {d}: {v.get('error_msg')}")
    if len(s) == 7 and s.isdigit():
        try:
            ts = kiwis(request="getTimeseriesList", station_no=s, returnfields="station_no,station_name,ts_id,ts_name,parametertype_name,coverage")
            rows = ts[1:] if isinstance(ts, list) else []
            for x in [x for x in rows if "Level" in str(x)][:4]:
                print("      WDO " + " | ".join(str(y) for y in x))
            if not rows:
                print(f"      WDO: nothing under {s}")
        except Exception as e:
            print(f"      WDO: {e}")
