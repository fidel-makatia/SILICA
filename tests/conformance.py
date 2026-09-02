#!/usr/bin/env python3
"""The backend conformance corpus.

One list of complete SILICA programs with their expected per-tx outcomes. Both
shipped backends run the SAME source text and must reach the SAME decisions --
that is the contract a new backend has to satisfy, and it is checked rather
than asserted in prose.

Every case is pure SILICA: no Python seeds the geometry, so nothing about a
case can depend on backend internals.

All layer numbers, grids and rule values below are illustrative placeholders
(a toy 1/2/3 metal numbering, a 10 nm grid, 100 nm minima). They do not come
from, and do not correspond to, any foundry's process design kit.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

from silica import Interp, Parser, lex  # noqa: E402

STACK = """design "chip.gds" top chip units nm grid 10
stack {
  metal m2 = (2,0)
  metal m3 = (3,0)
  via   v2 = (102,0) connects (m2,m3)
}
invariants { connectivity }
"""

# two parallel m3 wires on distinct nets, 100 nm apart
SEED2 = """tx seed {
  add m3 box(0,0,1000,100) on new_net
  add m3 box(0,200,1000,300) on new_net
}
"""

OK = ("seed", True, None)


class Case:
    def __init__(self, name, src, expect):
        self.name, self.src, self.expect = name, src, expect


CASES = [
    Case("legal same-net extension commits",
         STACK + SEED2 + """
tx extend { add m3 box(1000,0,1200,100) on net_at(m3,500,50) }
""",
         [OK, ("extend", True, None)]),

    Case("bridging add rolls back",
         STACK + SEED2 + """
tx bridge { add m3 box(500,0,600,300) on net_at(m3,500,50) }
""",
         [OK, ("bridge", False, "bridge")]),

    Case("add on the wrong net rolls back",
         STACK + SEED2 + """
tx wrong { add m3 box(1000,200,1200,300) on net_at(m3,500,50) }
""",
         [OK, ("wrong", False, "wrong-net")]),

    Case("new_net that touches existing metal rolls back",
         STACK + SEED2 + """
tx overlap { add m3 box(900,0,1100,100) on new_net }
""",
         [OK, ("overlap", False, "not-new")]),

    Case("declared merge commits",
         STACK + SEED2 + """
tx join {
  add m3 box(400,0,600,300) on merge(net_at(m3,500,50), net_at(m3,500,250))
}
""",
         [OK, ("join", True, None)]),

    Case("undeclared split rolls back",
         STACK + SEED2 + """
tx cut { sub m3 box(400,0,600,100) }
""",
         [OK, ("cut", False, "split")]),

    Case("declared split commits",
         STACK + SEED2 + """
tx cut { sub m3 box(400,0,600,100) splitting }
""",
         [OK, ("cut", True, None)]),

    Case("undeclared net deletion rolls back",
         STACK + SEED2 + """
tx wipe { sub m3 box(0,180,1000,320) }
""",
         [OK, ("wipe", False, "delete")]),

    # A net count is a scalar and scalars cancel. This subtraction splits one
    # net in two AND deletes another: the count is unchanged, so a count-based
    # invariant declares nothing and commits. The effect must be checked
    # structurally -- per pre-net -- or the invariant is unsound.
    Case("a split that cancels a delete in the count is still caught",
         STACK + """tx seed {
  add m3 box(0,   0, 1000, 100) on new_net
  add m3 box(400, 200,  500, 300) on new_net
}
tx swap { sub m3 box(400, 0, 500, 300) }
""",
         [OK, ("swap", False, "split")]),

    Case("declaring only `splitting` still catches the deletion",
         STACK + """tx seed {
  add m3 box(0,   0, 1000, 100) on new_net
  add m3 box(400, 200,  500, 300) on new_net
}
tx swap { sub m3 box(400, 0, 500, 300) splitting }
""",
         [OK, ("swap", False, "delete")]),

    Case("declaring both effects commits",
         STACK + """tx seed {
  add m3 box(0,   0, 1000, 100) on new_net
  add m3 box(400, 200,  500, 300) on new_net
}
tx swap { sub m3 box(400, 0, 500, 300) splitting deleting }
""",
         [OK, ("swap", True, None)]),

    Case("an exact effect declaration that matches commits",
         STACK + """tx seed {
  add m3 box(0,   0, 1000, 100) on new_net
  add m3 box(400, 200,  500, 300) on new_net
}
tx swap { sub m3 box(400, 0, 500, 300) splitting into 1 deleting 1 }
""",
         [OK, ("swap", True, None)]),

    Case("an exact split count that does not match rolls back",
         STACK + """tx seed {
  add m3 box(0,   0, 1000, 100) on new_net
  add m3 box(400, 200,  500, 300) on new_net
}
tx swap { sub m3 box(400, 0, 500, 300) splitting into 2 deleting 1 }
""",
         [OK, ("swap", False, "split-count")]),

    Case("an exact delete count that does not match rolls back",
         STACK + """tx seed {
  add m3 box(0,   0, 1000, 100) on new_net
  add m3 box(400, 200,  500, 300) on new_net
}
tx swap { sub m3 box(400, 0, 500, 300) splitting into 1 deleting 2 }
""",
         [OK, ("swap", False, "delete-count")]),

    Case("declared net deletion commits",
         STACK + SEED2 + """
tx wipe { sub m3 box(0,180,1000,320) deleting }
""",
         [OK, ("wipe", True, None)]),

    Case("via bridging two nets rolls back",
         STACK + """tx seed {
  add m3 box(0,0,1000,100) on new_net
  add m3 box(0,200,1000,300) on new_net
  add m2 box(0,0,1000,100) on new_net
}
tx stitch { add v2 box(100,10,200,90) on net_at(m3,500,50) }
""",
         [OK, ("stitch", False, "bridge")]),

    Case("attached label commits",
         STACK + SEED2 + """
tx name { label m3 "clk" at (500,50) }
""",
         [OK, ("name", True, None)]),

    Case("floating label rolls back",
         STACK + SEED2 + """
tx name { label m3 "clk" at (500,500) }
""",
         [OK, ("name", False, "floating")]),

    Case("assert spacing measures the shadow",
         STACK + SEED2 + """
tx squeeze {
  add m3 box(0,140,1000,160) on new_net
  assert spacing(m3, window(0,0,1000,300)) >= 100
}
""",
         [OK, ("squeeze", False, "spacing")]),

    Case("rules.width catches an undersized add",
         STACK + """rules { m3.width >= 100 }
tx thin { add m3 box(0,0,1000,60) on new_net }
""",
         [("thin", False, "m3>=100")]),

    # the regression this corpus exists for: `sub` can thin a wire below the
    # declared minimum just as easily as `add` can place one
    Case("rules.width catches a sub that thins a wire",
         STACK + """rules { m3.width >= 100 }
tx seed { add m3 box(0,0,1000,200) on new_net }
tx thin { sub m3 box(0,60,1000,200) }
""",
         [OK, ("thin", False, "m3>=100")]),

    Case("rules.width accepts a sub that leaves legal width",
         STACK + """rules { m3.width >= 100 }
tx seed { add m3 box(0,0,1000,200) on new_net }
tx trim { sub m3 box(0,100,1000,200) }
""",
         [OK, ("trim", True, None)]),

    Case("rules.space catches a too-close add",
         STACK + """rules { m3.space >= 100 }
tx seed { add m3 box(0,0,1000,100) on new_net }
tx near { add m3 box(0,140,1000,240) on new_net }
""",
         [OK, ("near", False, "m3>=100")]),

    # a point one database unit outside a shape is OUTSIDE it. A backend whose
    # probe is fuzzy reports "connected" for a near miss -- the exact failure
    # `add ... on net_at(...)` exists to prevent.
    Case("net_at does not resolve one dbu outside a shape",
         """design "chip.gds" top chip units nm grid 1
stack { metal m3 = (3,0) }
invariants { connectivity }
tx seed { add m3 box(0,0,1000,100) on new_net }
tx reach { add m3 box(1000,0,1200,100) on net_at(m3,1001,50) }
""",
         [OK, ("reach", False, "no-net")]),

    Case("a label one dbu outside metal is floating",
         """design "chip.gds" top chip units nm grid 1
stack { metal m3 = (3,0) }
invariants { connectivity }
tx seed { add m3 box(0,0,1000,100) on new_net }
tx name { label m3 "clk" at (1001,50) }
""",
         [OK, ("name", False, "floating")]),

    # A notch -- a bite out of one edge of an otherwise legal wire. The
    # pre-state is loaded BEFORE `rules` is declared: you inherit a design,
    # then declare what the runtime will enforce on your own edits.
    Case("a notch fill that restores legal width commits",
         STACK + """tx load {
  add m3 box(0,   0, 2000, 100) on new_net
  add m3 box(0, 200, 2000, 300) on new_net
  sub m3 box(800, 200,  900, 240)
}
rules { m3.width >= 100 }

tx fix { add m3 box(800,200,900,240) on net_at(m3,400,250) }
""",
         [("load", True, None), ("fix", True, None)]),

    # Both backends must reach the same VERDICT here even though they measure
    # it differently: the reference engine sees the narrowest box of its
    # coalesced decomposition, KLayout runs its own width check.
    Case("a partial notch fill still violates width",
         STACK + """tx load {
  add m3 box(0,   0, 2000, 100) on new_net
  add m3 box(0, 200, 2000, 300) on new_net
  sub m3 box(800, 200,  900, 240)
}
rules { m3.width >= 100 }

tx fix { add m3 box(800,200,900,220) on net_at(m3,400,250) }
""",
         [("load", True, None), ("fix", False, "m3>=100")]),
]


def run_case(case, backend):
    """Returns (ok, detail) -- ok is True when the backend matched `expect`."""
    got = Interp(Parser(lex(case.src)).parse(), backend).run()
    if len(got) != len(case.expect):
        return False, "expected %d tx results, got %d" % (len(case.expect),
                                                          len(got))
    for (gname, gok, gce), (ename, eok, erule) in zip(got, case.expect):
        if gname != ename or gok != eok:
            return False, ("tx %s: expected %s, got %s"
                           % (ename, "COMMIT" if eok else "ROLLBACK",
                              "COMMIT" if gok else "ROLLBACK"
                              + " " + str(gce)))
        if erule is not None and gce.get("rule") != erule:
            return False, ("tx %s: expected rule %r, got %r"
                           % (ename, erule, gce.get("rule")))
    return True, ""


def run_all(backend_factory, label):
    """Runs the whole corpus against a fresh backend per case."""
    fails = 0
    for case in CASES:
        ok, detail = run_case(case, backend_factory())
        print(("PASS " if ok else "FAIL ") + label + ": " + case.name
              + ("" if ok else "  -- " + detail))
        if not ok:
            fails += 1
    return fails
