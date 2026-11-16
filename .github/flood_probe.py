"""One-off check: can the Queensland government's water monitoring records (Hydstra web service behind
water-monitoring.information.qld.gov.au) give the Condamine's gauge sites and their hourly river heights?"""
import json, sys
import requests

URL = "https://water-monitoring.information.qld.gov.au/cgi/webservice.pl"
UA = {"User-Agent": "weather-desk-flood-model/1.0"}


def call(fn, params, version="1"):
    r = requests.post(URL, data=json.dumps({"function": fn, "version": version, "params": params}), headers=UA, timeout=120)
    print(f"{fn} v{version}: HTTP {r.status_code}, {len(r.content)} bytes")
    try:
        j = r.json()
    except ValueError:
        print(r.text[:500])
        return None
    return j


# 1. sites in the Condamine (basin 422)
for attempt in (
        ("get_db_info", {"table_name": "site", "return_type": "array", "complex_filter": [{"fieldname": "station", "operator": "BEGINS", "value": "4223"}]}, "3"),
        ("get_db_info", {"table_name": "site", "return_type": "array", "filter_values": {}, "field_list": ["station", "stname", "latitude", "longitude"]}, "3"),
        ("get_site_list", {"site_list": "MERGE(4223*)"}, "1")):
    j = call(*attempt)
    if j:
        print(json.dumps(j)[:1500])
    print("----")

# 2. a known site: Condamine River at Warwick (422310C) - what variables, and an hourly trace for a week in Feb 2022
j = call("get_variable_list", {"site_list": "422310C", "datasource": "A"})
if j:
    print(json.dumps(j)[:1500])
for site in ("422310C", "422323A", "422316A"):
    j = call("get_ts_traces", {"site_list": site, "datasource": "A", "varfrom": "100.00", "varto": "100.00",
                               "start_time": "20220221000000", "end_time": "20220301000000", "interval": "hour",
                               "multiplier": "1", "data_type": "max"}, "2")
    if j:
        s = json.dumps(j)
        print(s[:800], "...", s[-300:])
    print("----")
j = call("get_ts_traces", {"site_list": "422310C", "datasource": "A", "varfrom": "100.00", "varto": "100.00",
                           "start_time": "19900101000000", "end_time": "20260101000000", "interval": "day",
                           "multiplier": "1", "data_type": "max"}, "2")
if j:
    try:
        tr = j["return"]["traces"][0]
        pts = tr["trace"]
        print("daily max since 1990:", len(pts), "points, from", pts[0]["t"], "to", pts[-1]["t"], "max", max(float(p["v"]) for p in pts))
    except Exception as e:
        print("couldn't read:", e, json.dumps(j)[:500])
