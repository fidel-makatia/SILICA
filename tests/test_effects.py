#!/usr/bin/env python3
"""Property test for the connectivity-effect soundness claim (SPEC 5.1).

The interpreter classifies a subtraction's effect by correlating surviving
components back through the pre-state. Testing that against itself would prove
nothing, so this recomputes the fanout of every net from raw box arithmetic --
`Box.minus` plus a union-find over the resulting pieces, touching no part of
the engine's partition machinery -- and asserts that the runtime's verdict
agrees on randomized cases.

Clause (2) of the theorem: a transaction commits with no modifier only if
every net survives; `splitting` is required exactly when some net fans out to
two or more components, `deleting` exactly when one disappears.
"""
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

from silica import Box, Design, Interp, Parser, UF, lex  # noqa: E402

fails = 0


def check(name, cond, detail=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name
          + ("" if cond else "  -- " + str(detail)))
    if not cond:
        fails += 1


def independent_fanout(boxes, subbox):
    """Fanout of one net under a subtraction, from box arithmetic alone.

    Returns how many connected components its geometry leaves behind: 0 means
    the net is gone, 1 that it survived, k>=2 that it split k ways.
    """
    pieces = []
    for b in boxes:
        pieces.extend(b.minus(subbox))
    if not pieces:
        return 0
    uf = UF()
    for i in range(len(pieces)):
        uf.find(i)
    for i in range(len(pieces)):
        for j in range(i + 1, len(pieces)):
            if pieces[i].touches(pieces[j]):
                uf.union(i, j)
    return len({uf.find(i) for i in range(len(pieces))})


HDR = ('design "chip.gds" top chip units nm grid 10\n'
       'stack { metal m1 = (1,0) }\ninvariants { connectivity }\n')


def rbox(rng, lo=0, hi=1600, span=500):
    x, y = rng.randrange(lo, hi, 20), rng.randrange(lo, hi, 20)
    return Box(x, y, x + rng.randrange(40, span, 20),
               y + rng.randrange(40, span, 20))


def rwire(rng):
    """A long thin shape -- the thing a subtraction can actually bisect."""
    x, y = rng.randrange(0, 1200, 20), rng.randrange(0, 1200, 20)
    long_, thin = rng.randrange(400, 1000, 20), rng.randrange(40, 120, 20)
    return (Box(x, y, x + long_, y + thin) if rng.random() < 0.5
            else Box(x, y, x + thin, y + long_))


def rcut(rng, seeds):
    """Aim the cut at the geometry: bisect a wire, erase a shape, or miss."""
    b = rng.choice(seeds)
    roll = rng.random()
    if roll < 0.45:                       # slice across the middle
        if b.x2 - b.x1 >= b.y2 - b.y1:
            mid = ((b.x1 + b.x2) // 2 // 20) * 20
            return Box(mid, b.y1 - 40, mid + rng.randrange(20, 100, 20),
                       b.y2 + 40)
        mid = ((b.y1 + b.y2) // 2 // 20) * 20
        return Box(b.x1 - 40, mid, b.x2 + 40,
                   mid + rng.randrange(20, 100, 20))
    if roll < 0.75:                       # swallow it whole
        return Box(b.x1 - 20, b.y1 - 20, b.x2 + 20, b.y2 + 20)
    return rbox(rng, span=700)


bad = None
cases = commits = splits = deletes = 0
for seed in range(400):
    rng = random.Random(seed)
    seeds = [rwire(rng) for _ in range(rng.randint(2, 5))]
    cut = rcut(rng, seeds)

    # build the pre-state through the language, so the pre-partition is the
    # runtime's own; then classify its nets independently
    body = "\n".join("  add m1 box(%d,%d,%d,%d) on new_net"
                     % tuple(b.as_list()) for b in seeds)
    pre_design = Design()
    r = Interp(Parser(lex(HDR + "tx seed {\n" + body + "\n}\n")).parse(),
               pre_design).run()
    if not r[0][1]:
        continue                      # seeds overlapped; not a case
    pre_nets = pre_design.nets()

    fan = {}
    for nid, members in pre_nets.items():
        boxes = [pre_design._shapes[m][s] for (m, s) in members]
        fan[nid] = independent_fanout(boxes, cut)
    want_split = sum(1 for v in fan.values() if v >= 2)
    want_delete = sum(1 for v in fan.values() if v == 0)
    want_gain = sum(v - 1 for v in fan.values() if v >= 2)

    for mods, should_commit in (
            ("", want_split == 0 and want_delete == 0),
            ("splitting", want_delete == 0),
            ("deleting", want_split == 0),
            ("splitting deleting", True)):
        d2 = Design()
        src = (HDR + "tx seed {\n" + body + "\n}\n"
               + "tx cut { sub m1 box(%d,%d,%d,%d) %s }\n"
               % tuple(cut.as_list() + [mods]))
        got = Interp(Parser(lex(src)).parse(), d2).run()[1]
        cases += 1
        if got[1] != should_commit:
            bad = ("seed %d mods=%r: runtime %s, independent fanout %r"
                   % (seed, mods, "COMMIT" if got[1] else "ROLLBACK", fan))
            break
        if got[1]:
            commits += 1
            # clause (1): the count identity, checked against raw geometry
            if d2.net_count() != len(pre_nets) + want_gain - want_delete:
                bad = ("seed %d mods=%r: |P'|=%d, expected %d"
                       % (seed, mods, d2.net_count(),
                          len(pre_nets) + want_gain - want_delete))
                break
    splits += want_split
    deletes += want_delete
    if bad:
        break

check("runtime effect classification matches raw box arithmetic "
      "(%d cases, %d commits, %d splits, %d deletions seen)"
      % (cases, commits, splits, deletes), bad is None, bad)
check("the randomized corpus actually exercised splits", splits > 20, splits)
check("the randomized corpus actually exercised deletions", deletes > 20,
      deletes)

# exact counts must match the independently measured effect
d = Design()
src = (HDR + """tx seed {
  add m1 box(0,   0, 1000, 100) on new_net
  add m1 box(400, 200,  500, 300) on new_net
}
tx cut { sub m1 box(400, 0, 500, 300) splitting into 1 deleting 1 }
""")
r = Interp(Parser(lex(src)).parse(), d).run()
check("an exact effect declaration commits when it is right", r[1][1], r[1][2])
check("and the resulting partition matches the declaration",
      d.net_count() == 2, d.net_count())

print("----")
print("ALL PASS" if fails == 0 else "%d FAILURES" % fails)
sys.exit(1 if fails else 0)
