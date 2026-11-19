"""Lists the Queensland Government's water monitoring sites on Warrill Creek (and the Bremer below it), with how far back
their river height records go, for adding them to the flood model. Run by hand (.github/workflows/warrill-sources.yml)."""
import json
import requests

URL = "https://water-monitoring.information.qld.gov.au/cgi/webservice.pl"
UA = {"User-Agent": "Mozilla/5.0 (weather-desk flood model)"}


def call(fn, params, version="2"):
    r = requests.post(URL, data=json.dumps({"function": fn, "version": version, "params": params}), headers=UA, timeout=300)
    return r.json()


rows = call("get_db_info", {"table_name": "site", "return_type": "array",
                            "field_list": ["station", "stname", "latitude", "longitude"], "filter_values": {}}, "3").get("return", {}).get("rows", [])
sites = [s for s in rows if str(s.get("station", "")).startswith("143")
         and any(w in str(s.get("stname", "")).upper() for w in ("WARRILL", "BREMER", "KALBAR", "AMBERLEY", "HARRISVILLE", "CHURCHBANK", "WILLOWBANK"))]
print(f"{len(sites)} sites")
for s in sites:
    st = s["station"]
    try:
        v = call("get_variable_list", {"site_list": st, "datasource": "A"})
        vs = [x for x in (v.get("return") or {}).get("sites", [{}])[0].get("variables", []) if str(x.get("variable", "")).startswith("100")]
        per = "; ".join(f"{x.get('variable')} {x.get('period_start', '')[:8]}-{x.get('period_end', '')[:8]}" for x in vs) or "no level record (A)"
    except Exception as e:
        per = f"error {e}"
    print(f"{st:10} {float(s.get('latitude') or 0):9.4f} {float(s.get('longitude') or 0):9.4f} {s.get('stname')} | {per}", flush=True)
