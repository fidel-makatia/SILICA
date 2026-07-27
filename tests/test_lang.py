#!/usr/bin/env python3
"""SILICA v0.2 language-feature tests: functions, loops, control flow,
parametric geometry -- the general-purpose core wrapped around the tx layer."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "prototype"))
from silica import Interp, Parser, lex, Design, ParseError

fails = 0
def check(name, cond):
    global fails
    print(("PASS " if cond else "FAIL ") + name)
    if not cond: fails += 1

def run(src, d=None):
    it = Interp(Parser(lex(src)).parse(), d if d is not None else Design())
    return it, it.run()

HDR = ('design "t.gds" top t units nm grid 5\n'
       'stack { metal m1 = (31,0) }\n'
       'invariants { connectivity }\n')

# 1. functions + for-loop generate a parametric comb inside ONE tx
d = Design()
src = HDR + '''
fn finger(i) { return box(i*200, 0, i*200 + 70, 500) }
let n = 5
tx combs {
  for i in range(0, n) { add m1 finger(i) on new_net }
}
'''
it, r = run(src, d)
check("parametric comb commits", r[0][1] and d.net_count() == 5)

# 2. same generator, bad pitch: fingers touch -> whole tx rolls back atomically
d = Design()
src = HDR + '''
fn finger(i) { return box(i*50, 0, i*50 + 70, 500) }
tx combs {
  for i in range(0, 5) { add m1 finger(i) on new_net }
}
'''
it, r = run(src, d)
check("overlapping comb rolls back atomically",
      (not r[0][1]) and r[0][2]["rule"] == "not-new" and d.net_count() == 0)

# 3. general-purpose core: while / if / assignment / string concat
src = ('let total = 0\nlet i = 1\n'
       'while i <= 10 { if i % 2 == 0 { total = total + i } i = i + 1 }\n'
       'let msg = "sum=" + str(total)\n')
it, _ = run(src)
check("while/if arithmetic", it.genv.get("total") == 30
      and it.genv.get("msg") == "sum=30")

# 4. inexact division is a hard error (no silent rounding of coordinates)
try:
    run('let x = 7 / 2\n')
    check("inexact division rejected", False)
except ParseError:
    check("inexact division rejected", True)

# 5. lists, append, indexing
src = 'let xs = [10, 20]\nappend(xs, 30)\nlet y = xs[2] * 2\n'
it, _ = run(src)
check("lists/append/index", it.genv.get("y") == 60)

# 6. conditional adds inside tx: declared-new count still exact
d = Design()
src = HDR + '''
tx evens {
  for i in range(0, 6) {
    if i % 2 == 0 { add m1 box(i*200, 0, i*200 + 70, 500) on new_net }
  }
}
'''
it, r = run(src, d)
check("conditional adds", r[0][1] and d.net_count() == 3)

# 7. `+` never coerces across types
try:
    run('let s = "x" + 5\n')
    check("cross-type + rejected", False)
except ParseError:
    check("cross-type + rejected", True)

print("----")
print("ALL PASS" if fails == 0 else f"{fails} FAILURES")
sys.exit(1 if fails else 0)
