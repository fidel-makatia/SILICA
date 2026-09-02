#!/usr/bin/env python3
"""Width is measured over the union, and must agree with a real DRC engine.

SILICA used to measure width per stored rectangle. That is wrong whenever a
rectangle is a slice across a wider shape, which is exactly what an imported
layout looks like -- the decomposition was chosen by whatever tool wrote it.
The symptom was a width rule firing on real routed geometry that KLayout's own
width check calls clean.

`width_violation_rects` now takes cross-sections of the union instead. This
fuzzes it against KLayout's `Region.width_check` on randomized rectilinear
geometry: the two must agree on the VERDICT for every case.

SILICA defines width by the PROJECTION metric -- opposing edges that face each
other. KLayout defaults to Euclidian, which additionally measures diagonally
across a re-entrant corner, so a staircase of 200-wide arms reports a 141
violation against a 200 limit. That is a different rule, not a disagreement,
and both backends are pinned to the projection metric so they cannot drift
apart on it.
"""
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

from silica.geometry import Box, width_violation_rects  # noqa: E402

fails = 0


def check(name, cond, detail=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name
          + ("" if cond else "  -- " + str(detail)))
    if not cond:
        fails += 1


try:
    import klayout.db as pya
except ImportError:
    print("SKIP: klayout module not installed (pip install klayout)")
    sys.exit(0)

WIN = Box(-100000, -100000, 100000, 100000)


def verdicts(boxes, limit):
    s = width_violation_rects([Box(*b) for b in boxes], WIN, limit)
    r = pya.Region()
    for b in boxes:
        r.insert(pya.Box(*b))
    # projection metric: opposing edges that actually face each other. The
    # default Euclidian metric also measures corner-to-corner across a
    # re-entrant corner, which is a different rule (see the staircase case).
    k = r.merged().width_check(limit, False, pya.Metrics.Projection).count()
    return (s is not None), (k > 0), s


# ---- the cases that motivated the change ---------------------------------
NAMED = [
    ("a plain wire narrower than the limit", [(0, 0, 1000, 60)], 200, True),
    ("a plain wire at exactly the limit", [(0, 0, 1000, 200)], 200, False),
    ("an arm with an overhang (the import bug)",
     [(0, 60, 1000, 260), (800, 0, 1000, 900)], 200, False),
    ("a T junction", [(0, 0, 1000, 200), (400, 200, 600, 900)], 200, False),
    ("a thin tab hanging off a block",
     [(0, 0, 1000, 1000), (1000, 400, 1050, 460)], 200, True),
    ("one wire stored as two abutting halves",
     [(0, 0, 500, 200), (500, 0, 1000, 200)], 200, False),
    ("a narrow neck between two pads",
     [(0, 0, 400, 400), (400, 180, 600, 220), (600, 0, 1000, 400)], 200, True),
    # every arm here is 300 wide and no two opposing edges face each other
    # closer than that; the Euclidian metric would still flag the diagonal
    # across each re-entrant corner at 141
    ("a staircase of legal-width arms", [(0, 0, 300, 300),
                                         (200, 200, 500, 500),
                                         (400, 400, 700, 700)], 200, False),
]
for name, boxes, limit, want in NAMED:
    sv, kv, meas = verdicts(boxes, limit)
    check("%s: %s" % (name, "violation" if want else "clean"),
          sv == kv == want, "silica=%s klayout=%s measured=%s"
          % (sv, kv, meas))


# ---- randomized agreement ------------------------------------------------
def rboxes(rng):
    n = rng.randint(1, 5)
    out = []
    for _ in range(n):
        x = rng.randrange(0, 800, 20)
        y = rng.randrange(0, 800, 20)
        w = rng.randrange(20, 500, 20)
        h = rng.randrange(20, 500, 20)
        out.append((x, y, x + w, y + h))
    return out


bad, n = None, 0
for seed in range(3000):
    rng = random.Random(seed)
    boxes = rboxes(rng)
    limit = rng.choice([60, 100, 140, 200, 300])
    sv, kv, meas = verdicts(boxes, limit)
    n += 1
    if sv != kv:
        bad = "seed %d limit %d boxes %s: silica=%s klayout=%s" % (
            seed, limit, boxes, sv, kv)
        break
check("verdict matches KLayout's width check on %d random layouts" % n,
      bad is None, bad)

# the fuzz has to actually produce both answers or it proves nothing
viol = sum(1 for s in range(600)
           if verdicts(rboxes(random.Random(s)),
                       random.Random(s).choice([60, 100, 140, 200, 300]))[0])
check("the random corpus exercises both verdicts", 50 < viol < 550, viol)

print("----")
print("ALL PASS" if fails == 0 else "%d FAILURES" % fails)
sys.exit(1 if fails else 0)
