#!/usr/bin/env python3
"""Core semantics on the pure-Python reference backend.

Runs the shared backend conformance corpus, then the checks that are about the
reference engine itself rather than about backend agreement: hard errors, net
identity, and rollback leaving the live design untouched.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

from silica import Design, Interp, ParseError, Parser, lex  # noqa: E402
from conformance import STACK, SEED2, run_all  # noqa: E402

fails = 0


def check(name, cond, detail=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name
          + ("" if cond else "  -- " + str(detail)))
    if not cond:
        fails += 1


def run(src, design=None):
    d = Design() if design is None else design
    return Interp(Parser(lex(src)).parse(), d).run(), d


# ---- the shared corpus, on the reference backend -------------------------
fails += run_all(Design, "reference")

# ---- hard errors: the program is wrong, the design is never touched ------
# Inverted geometry is a constructor error, not a silent normalization.
try:
    run(STACK + "tx a { add m3 box(600,100,500,0) on new_net }")
    check("inverted box is a hard error", False)
except ParseError as e:
    check("inverted box is a hard error", "inverted" in str(e), e)

# Off-grid literals are rejected against the declared grid.
try:
    run(STACK + "tx a { add m3 box(0,0,1003,100) on new_net }")
    check("off-grid coordinate is a hard error", False)
except ParseError as e:
    check("off-grid coordinate is a hard error", "off-grid" in str(e), e)

# ...including when computed several calls deep, not just when written down.
try:
    run(STACK + """
fn edge(i) { return i * 3 }
fn wire(i) { return box(0, 0, edge(i), 100) }
tx a { add m3 wire(11) on new_net }
""")
    check("off-grid via a computed value is a hard error", False)
except ParseError as e:
    check("off-grid via a computed value is a hard error",
          "off-grid" in str(e), e)

# ---- rollback atomicity --------------------------------------------------
r, d = run(STACK + SEED2 + "tx cut { sub m3 box(400,0,600,100) }")
check("rollback leaves the live design intact",
      r[1][1] is False and d.net_count() == 2, d.net_count())

src = STACK + """
fn finger(i) { return box(i*200, 0, i*200 + 100, 500) }
tx comb { for i in range(0, 5) { add m3 finger(i) on new_net } }
"""
r, d = run(src)
check("parametric comb commits", r[0][1] and d.net_count() == 5,
      d.net_count())

src = STACK + """
fn finger(i) { return box(i*50, 0, i*50 + 100, 500) }
tx comb { for i in range(0, 5) { add m3 finger(i) on new_net } }
"""
r, d = run(src)
check("one bad finger rolls the whole tx back",
      (not r[0][1]) and r[0][2]["rule"] == "not-new" and d.net_count() == 0,
      r[0][2])

# ---- net identity --------------------------------------------------------
# Counterexample net ids must be stable strings, not shape indices: an agent
# reads them, and indices shift on every edit.
r, _ = run(STACK + SEED2 + """
tx bridge { add m3 box(500,0,600,300) on net_at(m3,500,50) }
""")
nets = r[1][2]["nets"]
check("counterexample net ids are stable and readable",
      nets == ["m3@0,0", "m3@0,200"], nets)

# The same net keeps its id after unrelated geometry is added before it.
r, _ = run(STACK + """
tx seed {
  add m3 box(0,200,1000,300) on new_net
  add m3 box(0,0,1000,100) on new_net
}
tx bridge { add m3 box(500,0,600,300) on net_at(m3,500,50) }
""")
check("net ids do not depend on insertion order",
      r[1][2]["nets"] == ["m3@0,0", "m3@0,200"], r[1][2]["nets"])

print("----")
print("ALL PASS" if fails == 0 else "%d FAILURES" % fails)
sys.exit(1 if fails else 0)
