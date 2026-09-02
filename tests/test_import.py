#!/usr/bin/env python3
"""Reading a layout back in, and the one thing that does not survive it.

Connectivity is exact regardless of how a layout is decomposed into rectangles
-- abutting rectangles are one component either way. Width is NOT: SILICA
measures it per stored rectangle, so a decomposition that slices across a wide
shape reads as a narrow one. When you author geometry you choose the
decomposition; when you import it, some other tool chose it for you.

This was found by importing a real OpenROAD-routed sky130 design, where a
declared width rule fired on geometry that KLayout's own width check says is
clean. The last test here pins that behaviour so it cannot quietly change.
"""
import os
import subprocess
import sys
import tempfile

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)

from silica import Box, Design, Interp, Parser, lex  # noqa: E402
from silica import importer  # noqa: E402

fails = 0


def check(name, cond, detail=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name
          + ("" if cond else "  -- " + str(detail)))
    if not cond:
        fails += 1


if not importer.available():
    print("SKIP: klayout module not installed (pip install klayout)")
    sys.exit(0)

tmp = tempfile.mkdtemp(prefix="silica_import_")
GDS = os.path.join(tmp, "rt.gds")


def run(src, d=None):
    d = Design() if d is None else d
    return Interp(Parser(lex(src)).parse(), d).run(), d


HDR = '''design "rt.gds" top chip units nm grid 10
stack {
  metal m1 = (1,0)
  metal m2 = (2,0)
  via   v1 = (101,0) connects (m1,m2)
}
invariants { connectivity }
'''

# ---- round trip: export, then import, and compare ------------------------
BUILD = HDR + '''tx build {
  add m1 box(0,0,1000,100) on new_net
  add m2 box(900,0,1900,100) on new_net
  add v1 box(920,20,980,80) on merge(net_at(m1,500,50), net_at(m2,1500,50))
  add m1 box(0,300,1000,400) on new_net
}
export "%s" { m1 -> (11,0)  m2 -> (12,0)  v1 -> (111,0) }
''' % GDS
r, src_design = run(BUILD)
check("the source design builds", all(x[1] for x in r), r)
src_nets = src_design.net_count()
check("it has the nets we expect", src_nets == 2, src_nets)

BACK = HDR + '''import "%s" top chip {
  m1 -> (11,0)   m2 -> (12,0)   v1 -> (111,0)
}
''' % GDS
r2, back = run(BACK)
check("the exported layout imports back", back.net_count() == src_nets,
      (back.net_count(), src_nets))


def area(d, layer):
    return sum((b.x2 - b.x1) * (b.y2 - b.y1) for b in d.shapes.get(layer, []))


for la in ("m1", "m2", "v1"):
    check("layer %s survives the round trip with identical area" % la,
          area(back, la) == area(src_design, la),
          (area(back, la), area(src_design, la)))

# via-mediated connectivity has to survive too
check("the via still merges the two metals after the round trip",
      back.net_count() == 2, back.net_count())

# ---- import is explicit about what it did NOT take -----------------------
boxes, unmapped, dbu, cell = importer.read_layout(
    GDS, "chip", [("m1", 11, 0)])
check("import reports the layers it did not map",
      sorted(unmapped) == ["111/0", "12/0"], unmapped)
check("import reports the cell it read", cell == "chip", cell)

# ---- an imported design must not be streamed out as if it were whole ----
OUT = os.path.join(tmp, "again.gds")
env = dict(os.environ, PYTHONPATH=ROOT)
path = os.path.join(tmp, "p.sil")
with open(path, "w") as f:
    f.write(BACK + 'export "%s" { m1 -> (11,0) m2 -> (12,0) v1 -> (111,0) }\n'
            % OUT)
p = subprocess.run([sys.executable, "-m", "silica", path],
                   cwd=ROOT, env=env, capture_output=True, text=True)
check("exporting an imported design is refused",
      p.returncode == 1 and "partial-design" in p.stdout, p.stdout + p.stderr)
check("...and nothing is written", not os.path.exists(OUT))

# ---- width survives decomposition (this used to be a known limitation) ---
# A horizontal arm meeting a vertical arm that overhangs it. Every part of the
# shape is at least 200 wide and KLayout's own width check agrees: zero
# violations. Merged and decomposed into horizontal bands, the overhang below
# the arm becomes a 200 x 60 band -- and SILICA used to measure per stored
# rectangle and call that a 60-wide shape, which is a false violation on clean
# routed layout. Width is now measured over the union, so the decomposition no
# longer changes the answer.
L = os.path.join(tmp, "l.gds")
LSRC = HDR + '''tx t {
  add m1 box(0,60,1000,260) on new_net
  add m1 box(800,0,1000,900) on net_at(m1,900,100)
}
export "%s" { m1 -> (11,0) }
''' % L
run(LSRC)
_, lback = run(HDR + 'import "%s" top chip { m1 -> (11,0) }\n' % L)
authored = Design()
authored.declare_metal("m1", 1, 0)
authored.add("m1", Box(0, 60, 1000, 260))
authored.add("m1", Box(800, 0, 1000, 900))
win = Box(-100, -100, 1100, 1000)

import klayout.db as pya  # noqa: E402
reg = pya.Region()
reg.insert(pya.Box(0, 60, 1000, 260))
reg.insert(pya.Box(800, 0, 1000, 900))
real = reg.merged().width_check(200).count()
check("KLayout says this shape has no real width violation at 200",
      real == 0, real)
check("authored geometry agrees: no violation",
      authored.width_violation("m1", win, 200) is None,
      authored.width_violation("m1", win, 200))
check("the same shape imported reports no violation either",
      lback.width_violation("m1", win, 200) is None,
      lback.width_violation("m1", win, 200))
check("imported and authored geometry give the same width answer",
      lback.width_violation("m1", win, 300)
      == authored.width_violation("m1", win, 300),
      (lback.width_violation("m1", win, 300),
       authored.width_violation("m1", win, 300)))
check("connectivity is unaffected by the decomposition",
      lback.net_count() == authored.net_count() == 1,
      (lback.net_count(), authored.net_count()))

print("----")
print("ALL PASS" if fails == 0 else "%d FAILURES" % fails)
sys.exit(1 if fails else 0)
