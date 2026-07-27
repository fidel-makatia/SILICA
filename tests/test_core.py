#!/usr/bin/env python3
"""SILICA v0.1 self-tests: each test encodes one field bug made inexpressible."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "prototype"))
from silica import Interp, Parser, lex, Design, Box, ParseError

HDR = '''design "t.gds" top t units nm grid 5
stack {
  metal m2 = (32,0)
  metal m3 = (33,0)
  via   v2 = (52,0) connects (m2,m3)
}
invariants { connectivity }
'''

def build():
    d = Design()
    d.metals = {"m2": (32,0), "m3": (33,0)}
    d.vias = {"v2": ((52,0), ("m2","m3"))}
    # two parallel m3 wires (distinct nets), 70 apart
    d.add("m3", Box(0, 0, 1000, 70))
    d.add("m3", Box(0, 140, 1000, 210))
    return d

def run(src, design):
    prog = Parser(lex(HDR + src)).parse()
    it = Interp(prog, design)
    return it.run()

fails = 0
def check(name, cond):
    global fails
    print(("PASS " if cond else "FAIL ") + name)
    if not cond: fails += 1

# 1. legal same-net extension commits
r = run('tx a { add m3 box(1000,0,1200,70) on net_at(m3,500,35) }', build())
check("legal extension commits", r[0][1])

# 2. bridging add is a rollback with a counterexample (the guard-bug class)
r = run('tx a { add m3 box(500,0,600,210) on net_at(m3,500,35) }', build())
check("bridge rejected", not r[0][1] and r[0][2]["rule"] == "bridge")

# 3. inverted box is a hard error (the KLayout normalization short)
try:
    run('tx a { add m3 box(600,70,500,0) on net_at(m3,500,35) }', build())
    check("inverted box rejected", False)
except ParseError:
    check("inverted box rejected", True)

# 4. off-grid coordinate is a hard error (type-error class, not a rollback)
try:
    run('tx a { add m3 box(1000,0,1203,70) on net_at(m3,500,35) }', build())
    check("off-grid rejected", False)
except ParseError:
    check("off-grid rejected", True)

# 5. sub that splits a net without `splitting` rolls back
d = build()
r = run('tx a { sub m3 box(400,0,600,70) }', d)
check("silent split rejected", not r[0][1] and r[0][2]["rule"] == "split")
check("rollback left design intact", d.net_count() == 2)

# 6. declared split commits
r = run('tx a { sub m3 box(400,0,600,70) splitting }', build())
check("declared split commits", r[0][1])

# 7. floating label rolls back (the ports-on-fragments LVS round)
r = run('tx a { label m3 "clk" at (500,500) }', build())
check("floating label rejected", not r[0][1] and r[0][2]["rule"] == "floating")

# 8. attached label commits
r = run('tx a { label m3 "clk" at (500,35) }', build())
check("attached label commits", r[0][1])

# 9. spacing assert returns measured counterexample
r = run('tx a { add m3 box(0,100,1000,130) on new_net }', build())
check("spacing violation via new_net rejected by assert-free rules? none declared -> commits",
      r[0][1])
src = ('tx a { add m3 box(0,100,1000,130) on new_net\n'
       '  assert spacing(m3, window(0,0,1000,210)) >= 70 }')
r = run(src, build())
check("assert spacing catches 30nm gap", not r[0][1] and "measured" in r[0][2]["note"])

# 10. new_net that touches existing metal is rejected
r = run('tx a { add m3 box(900,0,1100,70) on new_net }', build())
check("new_net overlap rejected", not r[0][1] and r[0][2]["rule"] == "not-new")

# 11. via-linked connectivity: add via merging m2/m3 must be declared
d = build()
d.add("m2", Box(0, 0, 1000, 70))          # m2 wire under m3 wire (3 nets total)
r = run('tx a { add v2 box(100,10,170,60) on net_at(m3,500,35) }', d)
check("via bridging two nets rejected", not r[0][1] and r[0][2]["rule"] == "bridge")

print("----")
print("ALL PASS" if fails == 0 else f"{fails} FAILURES")
sys.exit(1 if fails else 0)
