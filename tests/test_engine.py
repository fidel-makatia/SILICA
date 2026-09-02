#!/usr/bin/env python3
"""The indexed engine must be indistinguishable from the simple one.

`silica.engine.Design` maintains a spatial index and an incremental partition
so that an `add` costs O(neighbours). `SimpleDesign` recomputes everything
pairwise and is obviously correct. This suite runs the shared conformance
corpus against the simple engine, then fuzzes the two against each other on
randomized edit sequences and compares every protocol observation.

If they disagree, the simple one is right.
"""
import os
import random
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

from conformance import run_all                      # noqa: E402
from silica import Box, Design, SimpleDesign         # noqa: E402

fails = 0


def check(name, cond, detail=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name
          + ("" if cond else "  -- " + str(detail)))
    if not cond:
        fails += 1


# ---- the corpus, on the simple engine too --------------------------------
fails += run_all(SimpleDesign, "simple")


# ---- differential fuzz ---------------------------------------------------
def build(cls):
    d = cls()
    d.declare_metal("m1", 1, 0)
    d.declare_metal("m2", 2, 0)
    d.declare_via("v1", 101, 0, "m1", "m2")
    return d


def observations(d, points, windows, probes):
    """Everything the interpreter can learn through the backend protocol."""
    obs = [sorted(d.nets()), d.net_count()]
    for (layer, x, y) in points:
        obs.append(d.net_at(layer, x, y))
        obs.append(d.on_metal(layer, x, y))
    for (layer, box) in probes:
        obs.append(frozenset(d.nets_touching(layer, box)))
        obs.append(len(d.nets_touching(layer, box)))
    for (layer, win, limit) in windows:
        obs.append(d.width_violation(layer, win, limit))
        obs.append(d.spacing_violation(layer, win, limit))
    obs.append(sorted(d.net_probe(n) for n in d.nets()))
    return obs


def rbox(rng, span=600, lo=0, hi=2400):
    x = rng.randrange(lo, hi, 20)
    y = rng.randrange(lo, hi, 20)
    w = rng.randrange(40, span, 20)
    h = rng.randrange(40, span, 20)
    return Box(x, y, x + w, y + h)


LAYERS = ["m1", "m2", "v1"]
mismatch = None
ops_run = 0
for seed in range(60):
    rng = random.Random(seed)
    a, b = build(SimpleDesign), build(Design)
    for step in range(24):
        layer = rng.choice(LAYERS)
        box = rbox(rng)
        if rng.random() < 0.25:
            a.sub(layer, box)
            b.sub(layer, box)
            op = "sub %s %s" % (layer, box)
        else:
            a.add(layer, box)
            b.add(layer, box)
            op = "add %s %s" % (layer, box)
        ops_run += 1
        pts = [(rng.choice(["m1", "m2"]), rng.randrange(0, 2400, 20),
                rng.randrange(0, 2400, 20)) for _ in range(4)]
        wins = [(rng.choice(["m1", "m2"]), rbox(rng), rng.choice([40, 100]))
                for _ in range(3)]
        probes = [(rng.choice(LAYERS), rbox(rng, span=300))
                  for _ in range(3)]
        oa, ob = (observations(a, pts, wins, probes),
                  observations(b, pts, wins, probes))
        if oa != ob:
            first = next(i for i in range(len(oa)) if oa[i] != ob[i])
            mismatch = ("seed %d step %d after %s: observation %d "
                        "simple=%r indexed=%r"
                        % (seed, step, op, first, oa[first], ob[first]))
            break
    if mismatch:
        break

check("indexed engine matches the simple engine on %d randomized edits"
      % ops_run, mismatch is None, mismatch)


# ---- the property the index is allowed to change: cost, not answers ------
def bench(cls, n):
    d = cls()
    d.declare_metal("m6", 6, 0)
    t0 = time.time()
    for i in range(n):
        d.add("m6", Box(i * 55000, 0, i * 55000 + 50000, 60000))
        d.nets_touching("m6", Box(i * 55000, 0, i * 55000 + 50000, 60000))
    d.net_count()
    return time.time() - t0


print("----")
print("cost of n adds with a touched-net probe each (seconds):")
print("      n     simple    indexed   speedup")
for n in (100, 200, 400, 800):
    ts = bench(SimpleDesign, n)
    ti = bench(Design, n)
    print("   %4d   %8.3f   %8.3f   %6.1fx" % (n, ts, ti, ts / max(ti, 1e-9)))

t_big = bench(Design, 20000)
print("   indexed, 20000 shapes: %.2fs" % t_big)
check("the indexed engine stays usable at 20k shapes", t_big < 20.0,
      "%.2fs" % t_big)

print("----")
print("ALL PASS" if fails == 0 else "%d FAILURES" % fails)
sys.exit(1 if fails else 0)
