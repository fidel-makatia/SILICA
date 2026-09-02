#!/usr/bin/env python3
"""Bug-injection benchmark: what does the language buy over a careful wrapper?

Three arms attempt the same injected bugs:

  A  raw          edits applied with no checking at all
  B  guarded      a careful engineer's Python wrapper with asserts -- the
                  honest baseline, and the one that matters
  C  SILICA       the same edits expressed as transactions

Arm B is given SILICA's OWN connectivity engine. That is deliberate: it makes
the baseline as strong as possible and isolates the thing actually under test,
which is what a wrapper *remembers to check*, not whose geometry code is
better. Any bug B misses, it misses structurally.

Each scenario defines its bug by a GROUND-TRUTH predicate over the resulting
design, not by whether an arm complained. An arm "caught" a bug only if the
predicate is false afterwards -- i.e. the bad state never happened.

Run:  python3 eval/benchmark.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

from silica import Box, Design, Interp, Parser, lex  # noqa: E402
from silica.gds import read_gds  # noqa: E402

STACK = ('design "chip.gds" top chip units nm grid 10\n'
         'stack { metal m3 = (3,0)   metal m4 = (4,0)\n'
         '        via   v3 = (103,0) connects (m3,m4) }\n'
         'invariants { connectivity }\n'
         'rules { m3.width >= 100   m3.space >= 100 }\n')


# ---------------------------------------------------------------------------
# Arm B: the honest baseline


class GuardError(Exception):
    pass


class GuardedLayout:
    """What a careful engineer writes over a layout API.

    Deliberately generous: it checks bridging, floating, width AND spacing of
    everything it adds, and guards subtraction with a conductor count -- the
    discipline these campaigns actually used. Its blind spots are the realistic
    ones: guards get written per operation, and `sub` is the operation people
    under-guard, because subtraction feels like it only removes things.
    """

    MIN_W = 100
    MIN_S = 100

    def __init__(self):
        self.d = Design()
        self.d.declare_metal("m3", 3, 0)
        self.d.declare_metal("m4", 4, 0)
        self.d.declare_via("v3", 103, 0, "m3", "m4")

    def add(self, layer, box, net=None):
        touched = self.d.nets_touching(layer, box)
        if net is None:
            if touched:
                raise GuardError("new shape touches an existing net")
        else:
            if len(touched) > 1:
                raise GuardError("shape would bridge nets %s" % touched)
            if not touched:
                raise GuardError("shape floats")
        if box.width() < self.MIN_W:
            raise GuardError("shape narrower than minimum width")
        self.d.add(layer, box)
        halo = Box(box.x1 - 2 * self.MIN_S, box.y1 - 2 * self.MIN_S,
                   box.x2 + 2 * self.MIN_S, box.y2 + 2 * self.MIN_S)
        if self.d.spacing_violation(layer, halo, self.MIN_S) is not None:
            self.d.sub(layer, box)
            raise GuardError("shape violates minimum spacing")

    def sub(self, layer, box):
        before = self.d.net_count()
        self.d.sub(layer, box)
        if self.d.net_count() != before:
            # the conductor-count check: the standard field discipline
            raise GuardError("net count changed %d -> %d"
                             % (before, self.d.net_count()))

    def label(self, layer, text, x, y):
        if not self.d.on_metal(layer, x, y):
            raise GuardError("label %r attaches to no metal" % text)
        self.d.add_label(layer, text, x, y)

    def export(self, path, rules):
        from silica.gds import write_gds
        shapes = dict((k, v) for k, v in self.d.shapes.items()
                      if k in [r[0] for r in rules])
        write_gds(path, "chip", shapes, [], dict(
            ((r[0], None), (r[1], r[2])) for r in rules))


# ---------------------------------------------------------------------------
# ground truth


def nets_of(d):
    return {nid: frozenset(mem) for nid, mem in d.nets().items()}


def raw_apply(d, ops):
    for op in ops:
        k = op[0]
        if k == "add":
            d.add(op[1], op[2])
        elif k == "sub":
            d.sub(op[1], op[2])
        elif k == "label":
            d.add_label(op[1], op[2], op[3], op[4])


def guarded_apply(g, ops):
    for op in ops:
        k = op[0]
        if k == "add":
            g.add(op[1], op[2], op[5] if len(op) > 5 else None)
        elif k == "sub":
            g.sub(op[1], op[2])
        elif k == "label":
            g.label(op[1], op[2], op[3], op[4])


# ---------------------------------------------------------------------------
# scenarios


def bx(*a):
    return Box(*a)


SEED_TWO_WIRES = [("add", "m3", bx(0, 0, 1000, 100)),
                  ("add", "m3", bx(0, 200, 1000, 300))]

SCENARIOS = []


def scenario(sid, cls, name):
    def deco(fn):
        SCENARIOS.append((sid, cls, name, fn))
        return fn
    return deco


@scenario("B1", "B", "an added bar bridges two nets")
def _b1():
    ops = [("add", "m3", bx(400, 0, 500, 300), None, None, ("on", 500, 50))]
    sil = "tx t { add m3 box(400,0,500,300) on net_at(m3,500,50) }"

    def bad(d):
        return d.net_count() < 2        # the two wires became one
    return SEED_TWO_WIRES, ops, sil, bad


@scenario("B2", "B", "a via cut bridges two nets on different layers")
def _b2():
    seed = [("add", "m3", bx(0, 0, 1000, 100)),
            ("add", "m4", bx(0, 0, 1000, 100))]
    ops = [("add", "v3", bx(100, 10, 200, 90), None, None, ("on", 500, 50))]
    sil = "tx t { add v3 box(100,10,200,90) on net_at(m3,500,50) }"

    def bad(d):
        return d.net_count() < 2
    return seed, ops, sil, bad


@scenario("B3", "B", "a subtraction splits one net and deletes another")
def _b3():
    seed = [("add", "m3", bx(0, 0, 1000, 100)),
            ("add", "m3", bx(400, 200, 500, 300))]
    ops = [("sub", "m3", bx(400, 0, 500, 300))]
    sil = "tx t { sub m3 box(400,0,500,300) }"

    def bad(d):
        # the wire was cut in two and the pad vanished: the partition changed
        # completely while the COUNT stayed at 2
        return "m3@400,200" not in d.nets()
    return seed, ops, sil, bad


@scenario("L1", "L", "a label lands on no metal")
def _l1():
    ops = [("label", "m3", "clk", 500, 500)]
    sil = 'tx t { label m3 "clk" at (500,500) }'

    def bad(d):
        return any(not d.on_metal(la, x, y) for (la, _t, x, y) in d.labels)
    return SEED_TWO_WIRES, ops, sil, bad


@scenario("R1", "R", "an added shape is under minimum width")
def _r1():
    ops = [("add", "m3", bx(0, 500, 1000, 560))]
    sil = "tx t { add m3 box(0,500,1000,560) on new_net }"

    def bad(d):
        return d.width_violation("m3", bx(0, 400, 1000, 600), 100) is not None
    return SEED_TWO_WIRES, ops, sil, bad


@scenario("R2", "R", "a subtraction thins a wire below minimum width")
def _r2():
    seed = [("add", "m3", bx(0, 0, 1000, 200))]
    ops = [("sub", "m3", bx(0, 60, 1000, 200))]
    sil = "tx t { sub m3 box(0,60,1000,200) }"

    def bad(d):
        return d.width_violation("m3", bx(0, 0, 1000, 200), 100) is not None
    return seed, ops, sil, bad


@scenario("R3", "R", "an added shape violates minimum spacing")
def _r3():
    seed = [("add", "m3", bx(0, 0, 1000, 100))]
    ops = [("add", "m3", bx(0, 140, 1000, 240))]
    sil = "tx t { add m3 box(0,140,1000,240) on new_net }"

    def bad(d):
        return d.spacing_violation("m3", bx(0, 0, 1000, 300), 100) is not None
    return seed, ops, sil, bad


@scenario("A1", "A", "a composite edit half-applies before failing")
def _a1():
    seed = list(SEED_TWO_WIRES)
    ops = [("add", "m3", bx(1000, 0, 1200, 100), None, None, ("on", 500, 50)),
           ("add", "m3", bx(400, 0, 500, 300), None, None, ("on", 500, 50))]
    sil = ("tx t {\n"
           "  add m3 box(1000,0,1200,100) on net_at(m3,500,50)\n"
           "  add m3 box(400,0,500,300) on net_at(m3,500,50)\n}")

    def bad(d):
        # the extension must not survive the bridging edit's failure
        return any(b.x2 > 1000 for b in d.shapes.get("m3", []))
    return seed, ops, sil, bad


# ---------------------------------------------------------------------------
# runner


def run_scenario(sid, cls, name, fn, tmp):
    seed, ops, sil, bad = fn()

    # arm A: no checking
    a = Design()
    a.declare_metal("m3", 3, 0)
    a.declare_metal("m4", 4, 0)
    a.declare_via("v3", 103, 0, "m3", "m4")
    raw_apply(a, seed)
    try:
        raw_apply(a, [(o[0], o[1], o[2]) + tuple(o[3:5]) for o in ops])
    except Exception:
        pass
    arm_a = "caught" if not bad(a) else "ESCAPED"

    # arm B: the guarded wrapper
    g = GuardedLayout()
    raw_apply(g.d, seed)
    try:
        guarded_apply(g, ops)
    except GuardError:
        pass
    arm_b = "caught" if not bad(g.d) else "ESCAPED"

    # arm C: SILICA
    body = "\n".join("  add %s box(%d,%d,%d,%d) on new_net"
                     % ((o[1],) + tuple(o[2].as_list())) for o in seed)
    c = Design()
    Interp(Parser(lex(STACK + "tx seed {\n" + body + "\n}\n" + sil
                      )).parse(), c).run()
    arm_c = "caught" if not bad(c) else "ESCAPED"
    return arm_a, arm_b, arm_c


def schema_scenario(tmp):
    """S1 is about an artifact, so it is scored on the file, not the design."""
    d = Design()
    d.declare_metal("m3", 3, 0)
    d.declare_metal("m4", 4, 0)
    d.add("m3", Box(0, 0, 1000, 100))
    d.add("m4", Box(0, 300, 1000, 400))

    from silica.gds import write_gds
    # arm A: stream out with the map you have, skipping what you don't
    pa = os.path.join(tmp, "a.gds")
    write_gds(pa, "chip", {"m3": d.shapes["m3"]}, [], {("m3", None): (33, 0)})
    arm_a = "ESCAPED" if len(read_gds(pa)[1]) < 2 else "caught"

    # arm B: same, with an assert on the layers the engineer listed
    pb = os.path.join(tmp, "b.gds")
    listed = ["m3"]
    try:
        assert all(la in listed for la in ["m3"]), "unmapped"
        write_gds(pb, "chip", {"m3": d.shapes["m3"]}, [],
                  {("m3", None): (33, 0)})
    except AssertionError:
        pass
    arm_b = "ESCAPED" if (os.path.exists(pb)
                          and len(read_gds(pb)[1]) < 2) else "caught"

    # arm C: SILICA export refuses a map that does not cover the design
    pc = os.path.join(tmp, "c.gds")
    src = (STACK + "tx t {\n  add m3 box(0,0,1000,100) on new_net\n"
           "  add m4 box(0,300,1000,400) on new_net\n}\n"
           'export "%s" { m3 -> (33,0) }\n' % pc)
    try:
        Interp(Parser(lex(src)).parse(), Design()).run()
    except Exception:
        pass
    arm_c = "ESCAPED" if (os.path.exists(pc)
                          and len(read_gds(pc)[1]) < 2) else "caught"
    return arm_a, arm_b, arm_c


CLASSES = {"B": "connectivity", "L": "label/port", "R": "local rules",
           "A": "atomicity", "S": "artifact schema"}


def main():
    tmp = tempfile.mkdtemp(prefix="silica_eval_")
    rows = []
    for (sid, cls, name, fn) in SCENARIOS:
        rows.append((sid, cls, name) + run_scenario(sid, cls, name, fn, tmp))
    rows.append(("S1", "S", "stream-out drops an unmapped layer")
                + schema_scenario(tmp))

    w = max(len(r[2]) for r in rows)
    print("bug-injection benchmark -- an arm 'caught' a bug only if the bad "
          "state never happened\n")
    print("  id   class          %-*s   raw      guarded   SILICA" % (w, "bug"))
    print("  " + "-" * (w + 46))
    for (sid, cls, name, a, b, c) in rows:
        print("  %-4s %-14s %-*s   %-8s %-9s %s"
              % (sid, CLASSES[cls], w, name, a, b, c))
    tot = len(rows)
    for label, idx in (("raw", 3), ("guarded", 4), ("SILICA", 5)):
        n = sum(1 for r in rows if r[idx] == "caught")
        print("\n  %-8s caught %d/%d" % (label, n, tot), end="")
    print("\n")
    missed = [r for r in rows if r[4] != "caught" and r[5] == "caught"]
    print("  the language's marginal value over a careful wrapper is these "
          "%d:" % len(missed))
    for r in missed:
        print("    %s  %s" % (r[0], r[2]))
    both = [r for r in rows if r[4] == "caught" and r[5] == "caught"]
    print("\n  a careful wrapper already catches these %d, and the honest "
          "reading" % len(both))
    print("  is that most of the value here is discipline, not syntax:")
    for r in both:
        print("    %s  %s" % (r[0], r[2]))
    return rows


if __name__ == "__main__":
    main()
