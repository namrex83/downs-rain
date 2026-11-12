"""
Adds gauge readings sent from the page's "Add gauge readings" form (as a GitHub issue) to my_gauges.csv.

Run by .github/workflows/gauge-readings.yml, only for issues opened by the repository owner. The issue body is in
the BODY environment variable; each reading is a line "date,name,,,mm" (date = the morning of the 9am reading).
A reading for a gauge and day already in the file replaces the old one. Gauge positions are never taken from the
issue: they stay in the PRIVATE_GAUGES secret. Writes a short report to report.md for the reply on the issue.
"""
import datetime as dt
import json
import os
import re

CSV = "my_gauges.csv"
LINE = re.compile(r"^\s*(\d{4}-\d{2}-\d{2})\s*,\s*([^,#\n]{1,60}?)\s*,\s*,\s*,\s*(\d{1,3}(?:\.\d{1,2})?)\s*$")


def site_key(name):   # as in radar.py: case, spacing and a "(DDCGI)" tag don't matter
    return " ".join(name.lower().replace("(ddcgi)", "").split())


def main():
    body = os.environ.get("BODY", "")
    issue = os.environ.get("ISSUE", "?")
    today = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=10)).date()
    new, bad = [], []
    for raw in body.splitlines():
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
    try:
        secret = {site_key(k) for k in json.loads(os.environ.get("PRIVATE_GAUGES") or "{}")}
    except ValueError:
        secret = set()
    unplaced = sorted({name for _, name, _ in new if site_key(name) not in secret})
    out = []
    if new:
        out.append(f"Added {len(new)} reading{'s' if len(new) != 1 else ''}"
                   + (f" ({replaced} replaced an earlier reading)" if replaced else "") + ":")
        out += [f"- {day}: {name} {mm:g} mm" for day, name, mm in new]
        out.append("")
        out.append("The radar job is re-running now, so the map is re-corrected in a few minutes.")
    else:
        out.append("No readings found in this issue, so nothing was added.")
    if unplaced:
        out.append("")
        out.append(f"No saved position yet for: {', '.join(unplaced)}. These show in the gauge table but can't correct "
                   "the map until their position is added to the private gauge list (ask Claude to add it).")
    if bad:
        out.append("")
        out.append("Couldn't read these lines (left out): " + "; ".join(f"`{b}`" for b in bad[:10]))
    open("report.md", "w").write("\n".join(out) + "\n")
    open(os.environ.get("GITHUB_OUTPUT", "/dev/null"), "a").write(f"added={len(new)}\n")
    print("\n".join(out))


if __name__ == "__main__":
    main()
