#!/usr/bin/env python3
"""Tool-agnosticism proof: the SAME SILICA programs run unchanged on the
KLayout backend. Skips cleanly if the klayout python module is absent."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "prototype"))
try:
    import klayout.db  # noqa: F401
except ImportError:
    try:
        import pya  # noqa: F401
    except ImportError:
        print("SKIP: klayout python module not installed (pip install klayout)")
        sys.exit(0)
from silica import Interp, Parser, lex, Box
from backends.klayout_backend import KLayoutBackend

HDR = ('design "t.gds" top t units nm grid 5\n'
       'stack {\n metal m2 = (32,0)\n metal m3 = (33,0)\n'
       ' via v2 = (52,0) connects (m2,m3)\n}\n'
       'invariants { connectivity }\n')

def run(src, be):
    return Interp(Parser(lex(src)).parse(), be).run()

def build():
    be = KLayoutBackend()
    be.declare_metal("m2", 32, 0)
    be.declare_metal("m3", 33, 0)
    be.declare_via("v2", 52, 0, "m2", "m3")
    be.add("m3", Box(0, 0, 1000, 70))
    be.add("m3", Box(0, 140, 1000, 210))
    return be

fails = 0
def check(name, cond):
    global fails
    print(("PASS " if cond else "FAIL ") + name)
    if not cond: fails += 1

r = run(HDR + 'tx a { add m3 box(1000,0,1200,70) on net_at(m3,500,35) }', build())
check("klayout: legal extension commits", r[0][1])

r = run(HDR + 'tx a { add m3 box(500,0,600,210) on net_at(m3,500,35) }', build())
check("klayout: bridge rejected", not r[0][1] and r[0][2]["rule"] == "bridge")

be = build()
r = run(HDR + 'tx a { sub m3 box(400,0,600,70) }', be)
check("klayout: silent split rejected",
      not r[0][1] and r[0][2]["rule"] == "split")
check("klayout: rollback left design intact", be.net_count() == 2)

r = run(HDR + 'tx a { sub m3 box(400,0,600,70) splitting }', build())
check("klayout: declared split commits", r[0][1])

be = build()
be.add("m2", Box(0, 0, 1000, 70))
r = run(HDR + 'tx a { add v2 box(100,10,170,60) on net_at(m3,500,35) }', be)
check("klayout: via bridging two nets rejected",
      not r[0][1] and r[0][2]["rule"] == "bridge")

print("----")
print("ALL PASS" if fails == 0 else f"{fails} FAILURES")
sys.exit(1 if fails else 0)
