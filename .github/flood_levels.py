"""Each Queensland flood warning gauge's flood classification heights (minor, moderate, major, in metres on the
gauge) from the Bureau of Meteorology's Service Level Specification for Queensland (a PDF), written to
data/flood_levels.json for the page (Catchments: what an estimated peak would mean)."""
import io, json, os, re, sys
import requests
from pypdf import PdfReader

URLS = ["https://www.bom.gov.au/sites/default/files/2025-11/service-level-specification-flood-forecasting-warning-services-queensland-v3.6.pdf",
        "https://www.bom.gov.au/qld/flood/brochures/QLD_SLS_current.pdf"]
UA = {"User-Agent": "Mozilla/5.0 (SEQ Weather Desk flood levels)"}
NUM = r"(\d{1,3}(?:\.\d{1,2})?)"


def text():
    for u in URLS:
        try:
            r = requests.get(u, headers=UA, timeout=60)
            r.raise_for_status()
            pages = PdfReader(io.BytesIO(r.content)).pages
            print(f"{u}: {len(pages)} pages")
            return "\n".join(p.extract_text() or "" for p in pages)
        except Exception as e:
            print(f"{u}: {e}")
    return ""


def main():
    t = text()
    lines = [re.sub(r"\s+", " ", l).strip() for l in t.splitlines()]
    if "--dump" in sys.argv:   # to see the layout while writing the parser
        for i, l in enumerate(lines):
            if re.search(r"condamine|tummaville|cecil plains|loudoun|pratten|minor|moderate", l, re.I):
                print(i, "|", " || ".join(lines[max(0, i - 1):i + 3]))
        return
    out = {}
    for l in lines:
        m = re.match(rf"^(.*?[A-Za-z)].*?)\s+{NUM}\s+{NUM}\s+{NUM}$", l)
        if m:
            a, b, c = (float(x) for x in m.groups()[1:])
            if a < b < c or a <= b <= c and a < c:
                out[m.group(1).strip()] = [a, b, c]
    print(f"{len(out)} gauges with flood levels")
    for k in list(out)[:40]:
        print(" ", k, out[k])
    if out:
        os.makedirs("data", exist_ok=True)
        json.dump({"source": "Bureau of Meteorology, Service Level Specification for Flood Forecasting and Warning Services for Queensland",
                   "levels": out}, open("data/flood_levels.json", "w"), indent=0)


main()
