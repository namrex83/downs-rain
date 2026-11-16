"""One-off check: the Condamine's gauge sites in the Queensland water monitoring records (basin 422, sub-basin 4223),
with their names and years, to match against the BOM's gauge names."""
import json
import requests

URL = "https://water-monitoring.information.qld.gov.au/cgi/webservice.pl"
UA = {"User-Agent": "weather-desk-flood-model/1.0"}

r = requests.post(URL, data=json.dumps({"function": "get_db_info", "version": "3", "params": {
    "table_name": "site", "return_type": "array",
    "complex_filter": [{"fieldname": "station", "operator": "BEGINS", "value": "4223"}]}}), headers=UA, timeout=300)
rows = r.json()["return"]["rows"]
print(len(rows), "rows")
cond = [x for x in rows if str(x.get("station", "")).startswith(("4223", "4224"))]
print(len(cond), "Condamine-basin rows")
for x in sorted(cond, key=lambda x: x["station"]):
    print(x["station"], "|", x.get("stname"), "|", x.get("commence"), "-", x.get("cease"), "| active" if x.get("active") else "", "|", x.get("latitude"), x.get("longitude"))
