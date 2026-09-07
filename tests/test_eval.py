#!/usr/bin/env python3
"""The benchmark's published numbers are checked, not transcribed.

`eval/benchmark.py` is quoted in the README and in eval/PLAN.md. If its result
ever changes, this fails, so the claim in the docs cannot silently drift away
from the code.
"""
import io
import os
import sys
from contextlib import redirect_stdout

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "eval"))

import benchmark  # noqa: E402

fails = 0


def check(name, cond, detail=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name
          + ("" if cond else "  -- " + str(detail)))
    if not cond:
        fails += 1


buf = io.StringIO()
with redirect_stdout(buf):
    rows = benchmark.main()

got = {"raw": sum(1 for r in rows if r[3] == "caught"),
       "guarded": sum(1 for r in rows if r[4] == "caught"),
       "silica": sum(1 for r in rows if r[5] == "caught")}
check("the benchmark runs every scenario", len(rows) == 9, len(rows))
check("unguarded edits catch nothing (0/9)", got["raw"] == 0, got)
check("the guarded baseline catches 5/9", got["guarded"] == 5, got)
check("SILICA catches 9/9", got["silica"] == 9, got)

missed = sorted(r[0] for r in rows if r[4] != "caught" and r[5] == "caught")
check("the language's marginal value is exactly B3, R2, A1, S1",
      missed == ["A1", "B3", "R2", "S1"], missed)

# every bug must actually be a bug: the raw arm has to reach the bad state,
# or the scenario is not testing anything
check("every scenario's bug is reachable without checking",
      all(r[3] == "ESCAPED" for r in rows),
      [r[0] for r in rows if r[3] != "ESCAPED"])

# and the baseline must not be a strawman: it has to beat raw substantially
check("the baseline is a real baseline, not a strawman",
      got["guarded"] >= 5, got)

# ---- the harness must model vias the way both engines do ----------------
# `make validate` compares SILICA against an extractor, and the comparison is
# only as good as how it configures that extractor. It used to self-connect
# the via layer, which models cut geometry as a conducting SHEET: two cuts
# abutting at a corner then carry current between them and bridge the metals
# above and below. A via cut does not do that. Cuts are plugs.
#
# The difference is invisible on routed designs -- routers do not abut bare
# cuts -- so 36 real layouts never caught it. Randomized geometry does: as a
# sheet, the two disagree on 53 of 400 layouts; as plugs, on none.
try:
    import klayout.db as pya
    from validate_designs import isolated_cuts
    from silica import Box, Design

    def kl(sh):
        ly = pya.Layout()
        tc = ly.create_cell("T")
        idx = {n: ly.layer(l, d)
               for n, l, d in [("m1", 1, 0), ("m2", 2, 0), ("v1", 101, 0)]}
        for n, bs in sh.items():
            for b in bs:
                tc.shapes(idx[n]).insert(pya.Box(*b))
        l2n = pya.LayoutToNetlist(pya.RecursiveShapeIterator(ly, tc, []))
        L = {n: l2n.make_polygon_layer(idx[n], n) for n in idx}
        l2n.connect(L["m1"])
        l2n.connect(L["m2"])
        l2n.connect(L["m1"], L["v1"])
        l2n.connect(L["v1"], L["m2"])
        l2n.extract_netlist()
        nl = l2n.netlist()
        nl.flatten()
        return sum(1 for _ in nl.circuit_by_name("T").each_net())

    VIAS = [("v1", 101, 0, "m1", "m2")]
    import random
    bad, trials, clustered = None, 0, 0
    for seed in range(300):
        rng = random.Random(seed)
        sh = {"m1": [], "m2": [], "v1": []}
        for _ in range(rng.randint(1, 4)):
            x, y = rng.randrange(0, 600, 50), rng.randrange(0, 600, 50)
            sh["m1"].append((x, y, x + rng.randrange(50, 300, 50),
                             y + rng.randrange(50, 300, 50)))
        for _ in range(rng.randint(1, 4)):
            x, y = rng.randrange(0, 600, 50), rng.randrange(0, 600, 50)
            sh["m2"].append((x, y, x + rng.randrange(50, 300, 50),
                             y + rng.randrange(50, 300, 50)))
        # cuts on the same pitch as each other, so some of them abut
        for _ in range(rng.randint(1, 6)):
            x, y = rng.randrange(0, 700, 50), rng.randrange(0, 700, 50)
            sh["v1"].append((x, y, x + 50, y + 50))
        d = Design()
        d.declare_metal("m1", 1, 0)
        d.declare_metal("m2", 2, 0)
        d.declare_via("v1", 101, 0, "m1", "m2")
        for n, bs in sh.items():
            if bs:
                d.bulk_add(n, [Box(*b) for b in bs])
        d._ensure()
        cuts = isolated_cuts(d, VIAS)
        if any(d._touching("v1", vb, exclude=(sid,))
               for sid, vb in d._shapes.get("v1", {}).items()):
            clustered += 1
        trials += 1
        if d.net_count() + cuts != kl(sh):
            bad = ("seed %d: silica %d + %d clusters != klayout %d  %s"
                   % (seed, d.net_count(), cuts, kl(sh), sh))
            break
    check("reconciliation matches the extractor on %d randomized layouts"
          % trials, bad is None, bad)
    check("the corpus exercises abutting cuts, which a sheet model would "
          "chain (%d of %d layouts)" % (clustered, trials), clustered > 10,
          clustered)
except ImportError:
    print("SKIP reconciliation fuzz (klayout module not installed)")

print("----")
print("ALL PASS" if fails == 0 else "%d FAILURES" % fails)
sys.exit(1 if fails else 0)
