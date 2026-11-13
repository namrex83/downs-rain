"""
Adds gauge readings sent from the page's "Add gauge readings" form (as a GitHub issue) to my_gauges.csv.

Run by .github/workflows/gauge-readings.yml, only for issues opened by the repository owner. The issue body is in
the BODY environment variable; each reading is a line "date,name,,,mm" (date = the morning of the 9am reading).
A reading for a gauge and day already in the file replaces the old one. Gauge positions are never taken from the
issue for the private gauges (those stay in the PRIVATE_GAUGES secret). A line "pos,name,lat,lon" (the 📍 button)
saves a new gauge's position in gauge_positions.json; those gauges are public (a dot on the map). Writes a short
report to report.md for the reply on the issue.
"""
import datetime as dt
import json
import os
import re

CSV = "my_gauges.csv"
POS = "gauge_positions.json"
LINE = re.compile(r"^\s*(\d{4}-\d{2}-\d{2})\s*,\s*([^,#\n]{1,60}?)\s*,\s*,\s*,\s*(\d{1,3}(?:\.\d{1,2})?)\s*$")
POS_LINE = re.compile(r"^\s*pos\s*,\s*([^,#\n]{1,60}?)\s*,\s*(-?\d{1,3}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)\s*$")


def site_key(name):   # as in radar.py: case, spacing and a "(DDCGI)" tag don't matter
    return " ".join(name.lower().replace("(ddcgi)", "").split())


def main():
    body = os.environ.get("BODY", "")
    issue = os.environ.get("ISSUE", "?")
    today = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=10)).date()
    new, bad, saved, failed = [], [], [], []
    positions = json.load(open(POS)) if os.path.exists(POS) else {}
    for raw in body.splitlines():
        pm = POS_LINE.match(raw)
        if pm:
            name = " ".join(pm.group(1).split())
            lat, lon = float(pm.group(2)), float(pm.group(3))
            if -45 <= lat <= -9 and 110 <= lon <= 155:
                positions = {k: v for k, v in positions.items() if site_key(k) != site_key(name)}
                positions[name] = [round(lat, 5), round(lon, 5)]
                saved.append(name)
            else:
                failed.append(f"{name} (that position isn't in Australia)")
            continue
        if not raw.strip() or raw.strip().startswith(("#", "<!--", ">")) or "," not in raw:
            continue
        m = LINE.match(raw)
        if not m:
            bad.append(raw.strip()[:80])
            continue
        day, name, mm = m.group(1), " ".join(m.group(2).split()), float(m.group(3))
        try:
            d = dt.date.fromisoformat(day)
        except ValueError:
            bad.append(raw.strip()[:80])
            continue
        if not (today - dt.timedelta(days=60) <= d <= today + dt.timedelta(days=1)) or mm > 600:
            bad.append(raw.strip()[:80])
            continue
        new.append((day, name, mm))
    if saved:
        with open(POS, "w") as f:
            json.dump(positions, f, indent=0, sort_keys=True)
    lines = open(CSV).read().splitlines() if os.path.exists(CSV) else []
    replaced = 0
    if new:
        keys = {(day, site_key(name)) for day, name, _ in new}
        kept = []
        for ln in lines:
            parts = [x.strip() for x in ln.split(",")]
            if len(parts) == 5 and not ln.startswith("#") and (parts[0], site_key(parts[1])) in keys:
                replaced += 1
                continue
            kept.append(ln)
        days = sorted({d for d, _, _ in new})
        kept.append(f"# {', '.join(days)}: entered on the page (issue #{issue})")
        kept += [f"{day},{name},,,{mm:g}" for day, name, mm in new]
        with open(CSV, "w") as f:
            f.write("\n".join(kept) + "\n")
    secret = set()
    for var in ("PRIVATE_GAUGES", "PRIVATE_GAUGES_EXTRA"):
        try:
            secret |= {site_key(k) for k in json.loads(os.environ.get(var) or "{}")}
        except (ValueError, TypeError):
            pass
    secret |= {site_key(k) for k in positions}
    unplaced = sorted({name for _, name, _ in new if site_key(name) not in secret})
    out = []
    if new:
        out.append(f"Added {len(new)} reading{'s' if len(new) != 1 else ''}"
                   + (f" ({replaced} replaced an earlier reading)" if replaced else "") + ":")
        out += [f"- {day}: {name} {mm:g} mm" for day, name, mm in new]
        out.append("")
        out.append("The radar job is re-running now, so the map is re-corrected in a few minutes.")
    elif not saved:
        out.append("No readings found in this issue, so nothing was added.")
    if saved:
        out.append("")
        out.append(f"Saved the position for: {', '.join(saved)} (from the map pin; shown as a dot on the map).")
    if failed:
        out.append("")
        out.append(f"Couldn't save the position for: {', '.join(failed)}.")
    if unplaced:
        out.append("")
        out.append(f"No saved position yet for: {', '.join(unplaced)}. These show in the gauge table but can't correct "
                   "the map until their position is saved: in the form, put the map pin on the gauge and tap 📍 next to its name.")
    if bad:
        out.append("")
        out.append("Couldn't read these lines (left out): " + "; ".join(f"`{b}`" for b in bad[:10]))
    open("report.md", "w").write("\n".join(out) + "\n")
    open(os.environ.get("GITHUB_OUTPUT", "/dev/null"), "a").write(f"added={len(new) + len(saved)}\n")
    print("\n".join(out))


if __name__ == "__main__":
    main()
