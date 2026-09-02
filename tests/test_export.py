#!/usr/bin/env python3
"""Artifact schemas are total: an export drops nothing, or it does not happen.

A stream-out that silently omits a layer it has no map row for is how every via
cut vanishes from a GDS and LVS ends up looking at opens. `export` proves its
map covers the design before writing a byte, and names what it would have had
to drop.
"""
import os
import subprocess
import sys
import tempfile

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)

from silica.gds import read_gds  # noqa: E402

fails = 0


def check(name, cond, detail=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name
          + ("" if cond else "  -- " + str(detail)))
    if not cond:
        fails += 1


tmp = tempfile.mkdtemp(prefix="silica_export_")


def run(body, name="p.sil"):
    path = os.path.join(tmp, name)
    with open(path, "w") as f:
        f.write(body)
    env = dict(os.environ, PYTHONPATH=ROOT)
    p = subprocess.run([sys.executable, "-m", "silica", path],
                       cwd=ROOT, env=env, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


HDR = '''design "out.gds" top chip units nm grid 10
stack { metal m3 = (3,0)   metal m4 = (4,0) }
invariants { connectivity }
tx build {
  add m3 box(0,0,1000,100) on new_net
  add m4 box(0,300,1000,400) on new_net
  label m3 "clk" at (500,50)
}
'''
OUT = os.path.join(tmp, "out.gds")

# ---- totality ------------------------------------------------------------
rc, out, _ = run(HDR + 'export "%s" { m3 -> (33,0) }\n' % OUT)
check("an export missing a geometry rule halts", rc == 1 and "HALT" in out, out)
check("...and names the unmapped layer", '"m4"' in out or "m4" in out, out)
check("...and names the unmapped text", "m3.NAME" in out, out)
check("...and writes nothing at all", not os.path.exists(OUT), "file exists")

rc, out, _ = run(HDR + 'export "%s" { m3 -> (33,0)  m4 -> (34,0) }\n' % OUT)
check("text with no rule is still a missing datum",
      rc == 1 and "m3.NAME" in out, out)
check("...and still writes nothing", not os.path.exists(OUT), "file exists")

# ---- the success path ----------------------------------------------------
FULL = (HDR + 'export "%s" {\n  m3 -> (33,0)\n  m4 -> (34,0)\n'
        '  m3.NAME -> (233,0)\n}\n' % OUT)
rc, out, err = run(FULL)
check("a total map exports", rc == 0 and "3 element(s)" in out,
      out + err)
check("the file exists", os.path.exists(OUT))

top, els = read_gds(OUT)
check("the top cell name comes from the design declaration", top == "chip", top)
bnds = [e for e in els if e[0] == "boundary"]
txts = [e for e in els if e[0] == "text"]
check("every shape was written", len(bnds) == 2, els)
check("shapes carry the mapped layer numbers",
      sorted(b[1] for b in bnds) == [33, 34], bnds)
check("geometry survives the round trip",
      bnds[0][3][:2] == [(0, 0), (1000, 0)], bnds[0])
check("the label was written on its mapped text layer",
      len(txts) == 1 and txts[0][1] == 233 and txts[0][4] == "clk", txts)

# ---- a real tool must be able to read it --------------------------------
try:
    import klayout.db as pya
    ly = pya.Layout()
    ly.read(OUT)
    c = ly.top_cell()
    got = sorted(str(ly.get_info(i)) for i in ly.layer_indexes())
    check("KLayout reads the stream back",
          c.name == "chip" and got == ["233/0", "33/0", "34/0"], got)
    check("KLayout sees the right area on the mapped layer",
          pya.Region(c.begin_shapes_rec(ly.layer(33, 0))).area() == 100000)
    check("KLayout resolves the database unit as declared",
          abs(ly.dbu - 0.001) < 1e-12, ly.dbu)
except ImportError:
    print("SKIP klayout round trip (module not installed)")

# ---- program errors ------------------------------------------------------
rc, out, err = run(HDR + 'export "%s" { m9 -> (9,0) }\n' % OUT)
check("an export rule for an undeclared layer is refused",
      rc == 2 and "not declared in `stack`" in out, out + err)

rc, out, err = run(HDR + 'export "%s" { m3 -> (33,0)  m3 -> (35,0) }\n' % OUT)
check("a duplicate export rule is refused",
      rc == 2 and "duplicate export rule" in out, out + err)

rc, out, err = run('''design "o.gds" top chip units nm grid 10
stack { metal m3 = (3,0) }
tx t { export "%s" { m3 -> (33,0) } }
''' % OUT)
check("export inside a tx is refused",
      rc == 2 and "not allowed inside a tx" in out, out + err)

# ---- an unused rule is reported, not fatal ------------------------------
rc, out, _ = run('''design "o.gds" top chip units nm grid 10
stack { metal m3 = (3,0)   metal m4 = (4,0) }
invariants { connectivity }
tx build { add m3 box(0,0,1000,100) on new_net }
export "%s" { m3 -> (33,0)  m4 -> (34,0) }
''' % os.path.join(tmp, "sparse.gds"))
check("a map rule matching no geometry is reported but not fatal",
      rc == 0 and "matched no geometry" in out and "m4" in out, out)

print("----")
print("ALL PASS" if fails == 0 else "%d FAILURES" % fails)
sys.exit(1 if fails else 0)
