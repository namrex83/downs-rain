"""One-off check: which data sources each Condamine gauge has in the Queensland records, and whether any holds the last
few hours (telemetry), so the model can read the same gauge it learned from."""
import json, time
import requests

URL = "https://water-monitoring.information.qld.gov.au/cgi/webservice.pl"
UA = {"User-Agent": "weather-desk-flood-model/1.0"}


def call(fn, params, version="1"):
    r = requests.post(URL, data=json.dumps({"function": fn, "version": version, "params": params}), headers=UA, timeout=180)
    try:
        return r.json()
    except ValueError:
        return {"http": r.status_code, "text": r.text[:300]}


sites = "422323A,422316A,422333A,422310C"
j = call("get_datasources_by_site", {"site_list": sites})
print("datasources:", json.dumps(j)[:1500])
now = time.strftime("%Y%m%d%H%M%S", time.gmtime(time.time() + 10 * 3600))
start = time.strftime("%Y%m%d%H%M%S", time.gmtime(time.time() + 10 * 3600 - 3 * 86400))
for ds in ("A", "TELEM", "TM", "T", "PROV", "CP"):
    j = call("get_ts_traces", {"site_list": sites, "datasource": ds, "varfrom": "100.00", "varto": "100.00",
                               "start_time": start, "end_time": now, "interval": "hour", "multiplier": "1", "data_type": "point"}, "2")
    out = []
    for tr in (j.get("return") or {}).get("traces", []):
        pts = tr.get("trace") or []
        out.append((tr.get("site"), len(pts), pts[-1] if pts else None, tr.get("error_msg")))
    print(ds, "->", out or json.dumps(j)[:300])
