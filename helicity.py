"""
Storm-relative helicity for the Storms tab: how much the low-level winds would spin a storm's updraft.

Used by storms.py (GFS) and storms_ecmwf.py (ECMWF). Give it one wind profile (heights above the
ground in metres, from the 10 m wind upwards, and the winds as u/v in m/s) and it returns the
0-3 km storm-relative helicity (m2/s2).

Storm motion is the Bunkers method: the 0-6 km mean wind, moved 7.5 m/s to one side of the
0-6 km shear. In the Southern Hemisphere the storm that rotates the same way as a low (clockwise)
is the LEFT-moving one, and its helicity comes out negative in the usual formula. So we use the
left mover and flip the sign: here a POSITIVE number always means "helps southern-hemisphere
supercells rotate". Rough guide: 100+ supports supercells, 150-250+ strong rotation, and high
helicity with a low cloud base is the tornado signal.
"""
import math

DEVIATION = 7.5          # m/s, Bunkers storm-motion offset
STEP = 250               # m, how finely the profile is sampled


def _at(z, u, v, h):
    """Wind at height h (m above ground), linear between the levels given."""
    if h <= z[0]:
        return u[0], v[0]
    for i in range(1, len(z)):
        if h <= z[i]:
            f = (h - z[i - 1]) / (z[i] - z[i - 1])
            return u[i - 1] + f * (u[i] - u[i - 1]), v[i - 1] + f * (v[i] - v[i - 1])
    return None


def _mean(z, u, v, lo, hi):
    pts = [_at(z, u, v, lo + k * (hi - lo) / 10) for k in range(11)]
    if any(p is None for p in pts):
        return None
    return sum(p[0] for p in pts) / 11, sum(p[1] for p in pts) / 11


def srh(z, u, v, depth=3000):
    """0-`depth` m storm-relative helicity (m2/s2), positive = southern-hemisphere supercell rotation.
    z: heights above ground (m), rising, starting with the 10 m wind. Returns None if the
    profile doesn't reach about 5.5 km."""
    prof = sorted((h, a, b) for h, a, b in zip(z, u, v) if None not in (h, a, b) and h >= 0)
    if len(prof) < 3:
        return None
    z, u, v = [p[0] for p in prof], [p[1] for p in prof], [p[2] for p in prof]
    top = min(6000, z[-1])
    if top < 5500:
        return None
    mean = _mean(z, u, v, 0, top)
    low, high = _mean(z, u, v, 0, 500), _mean(z, u, v, top - 500, top)
    su, sv = high[0] - low[0], high[1] - low[1]
    mag = math.hypot(su, sv)
    if mag < 0.1:
        cu, cv = mean
    else:   # left of the shear vector: the southern-hemisphere (cyclonic) supercell
        cu, cv = mean[0] - DEVIATION * sv / mag, mean[1] + DEVIATION * su / mag
    total, prev = 0.0, _at(z, u, v, 0)
    for k in range(1, depth // STEP + 1):
        cur = _at(z, u, v, k * STEP)
        total += (cur[0] - cu) * (prev[1] - cv) - (prev[0] - cu) * (cur[1] - cv)
        prev = cur
    return -total     # flip so positive = clockwise (cyclonic) rotation in the south


if __name__ == "__main__":
    # check: a hodograph that backs with height (the southern-hemisphere supercell pattern)
    # should give a clearly positive number; its mirror image a negative one
    zs = [10, 500, 1000, 1500, 2000, 3000, 4000, 5000, 6000]
    ang = [0, 20, 40, 60, 80, 100, 110, 115, 120]          # degrees, turning anticlockwise
    spd = [5, 10, 13, 15, 17, 20, 23, 26, 30]
    us = [s * math.cos(math.radians(a)) for s, a in zip(spd, ang)]
    vs = [s * math.sin(math.radians(a)) for s, a in zip(spd, ang)]
    print("backing (SH supercell) profile:", round(srh(zs, us, vs)))
    print("mirror (NH supercell) profile: ", round(srh(zs, us, [-x for x in vs])))
