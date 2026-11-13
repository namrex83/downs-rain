"""
Adds gauge readings sent from the page's "Add gauge readings" form (as a GitHub issue) to my_gauges.csv.

Run by .github/workflows/gauge-readings.yml, only for issues opened by the repository owner. The issue body is in
the BODY environment variable; each reading is a line "date,name,,,mm" (date = the morning of the 9am reading).
A reading for a gauge and day already in the file replaces the old one. Gauge positions are never taken from the
issue in plain form. A line "pos,name,<locked position>" carries a new gauge's position, locked (encrypted) on the
phone with the site's public key: it's checked here with the private key (the GAUGE_KEY secret) and saved, still
locked, in gauge_positions.json, which the radar job unlocks with the same key. Writes a short report to report.md
for the reply on the issue.
"""
import datetime as dt
import json
import os
import re

CSV = "my_gauges.csv"
POS = "gauge_positions.json"
POS_LINE = re.compile(r"^\s*pos\s*,\s*([^,#\n]{1,60}?)\s*,\s*([A-Za-z0-9+/=]{100,700})\s*$")


def unlock(b64):
    """[lat, lon] from a locked position, using the GAUGE_KEY secret (raises if it can't)."""
    import base64
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    key = serialization.load_pem_private_key(os.environ["GAUGE_KEY"].encode(), password=None)
    plain = key.decrypt(base64.b64decode(b64), padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
    lat, lon = json.loads(plain)
    return float(lat), float(lon)
LINE = re.compile(r"^\s*(\d{4}-\d{2}-\d{2})\s*,\s*([^,#\n]{1,60}?)\s*,\s*,\s*,\s*(\d{1,3}(?:\.\d{1,2})?)\s*$")


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
            try:
                lat, lon = unlock(pm.group(2))
                if not (-45 <= lat <= -9 and 110 <= lon <= 155):
                    raise ValueError("outside Australia")
                positions = {k: v for k, v in positions.items() if site_key(k) != site_key(name)}
                positions[name] = pm.group(2)
                saved.append(name)
            except Exception as e:
                failed.append(f"{name} ({'no GAUGE_KEY secret yet' if 'GAUGE_KEY' in str(e) else 'could not be read'})")
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
        out.append(f"Saved the position (locked, not readable here) for: {', '.join(saved)}.")
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
