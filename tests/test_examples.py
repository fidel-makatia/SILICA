#!/usr/bin/env python3
"""Every shipped example is executed and its documented outcome checked.

An example that no longer does what its comments say is a broken promise to
whoever reads the repository first, so this runs in CI alongside the unit
suites rather than being left to manual inspection.
"""
import os
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
EX = os.path.join(ROOT, "examples")
sys.path.insert(0, ROOT)

fails = 0


def check(name, cond, detail=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name
          + ("" if cond else "  -- " + str(detail)))
    if not cond:
        fails += 1


def silica(args):
    env = dict(os.environ, PYTHONPATH=ROOT)
    p = subprocess.run([sys.executable, "-m", "silica"] + args,
                       cwd=ROOT, env=env, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


# ---- the .sil examples ---------------------------------------------------
EXPECT = {
    "fix_notch.sil": (0, ["COMMIT   load",
                          "ROLLBACK underfill",
                          "COMMIT   fix_notch"]),
    "bridge_caught.sil": (0, ["COMMIT   seed",
                              "ROLLBACK bad_bridge"]),
    "padframe_gen.sil": (0, ["placed 8 pads", "COMMIT   place_pads"]),
}

for name, (want_rc, want_lines) in EXPECT.items():
    rc, out, err = silica(["examples/" + name])
    ok = rc == want_rc and all(w in out for w in want_lines)
    check("example %s behaves as documented" % name, ok,
          "rc=%s out=%r err=%r" % (rc, out, err))

# the bridge counterexample must name both nets it would have shorted
rc, out, _ = silica(["examples/bridge_caught.sil"])
check("bridge_caught reports both net ids",
      '"m6@0,0"' in out and '"m6@0,200"' in out, out)

# the flow example halts structurally on its first missing input rather than
# launching anything
rc, out, _ = silica(["--flow", "examples/asic_flow.sil"])
check("asic_flow halts on a missing declared input",
      rc == 1 and "FLOW-HALT" in out and "missing-lib" in out, out)

# ---- the pin-label injector ----------------------------------------------


def minimal_gds(path, cell="top"):
    """The smallest GDS that contains one named, empty top cell."""
    def rec(rtype, dtype, payload=b""):
        return struct.pack(">HBB", 4 + len(payload), rtype, dtype) + payload

    name = cell.encode()
    if len(name) % 2:
        name += b"\x00"
    stamp = struct.pack(">12h", *([2026, 1, 1, 0, 0, 0] * 2))
    body = (rec(0x00, 0x02, struct.pack(">h", 600))            # HEADER
            + rec(0x01, 0x02, stamp)                           # BGNLIB
            + rec(0x02, 0x06, b"LIB\x00")                      # LIBNAME
            + rec(0x03, 0x05, struct.pack(">dd", 1e-3, 1e-9))  # UNITS
            + rec(0x05, 0x02, stamp)                           # BGNSTR
            + rec(0x06, 0x06, name)                            # STRNAME
            + rec(0x07, 0x00)                                  # ENDSTR
            + rec(0x04, 0x00))                                 # ENDLIB
    open(path, "wb").write(body)


def text_records(path):
    """Scan a GDS for TEXT elements: [(layer, x, y, string)]."""
    data = open(path, "rb").read()
    i, out, cur = 0, [], None
    while i < len(data) - 3:
        ln, rt, dt = struct.unpack(">HBB", data[i:i + 4])
        if ln < 4:
            break
        payload = data[i + 4:i + ln]
        if rt == 0x0C:
            cur = {}
        elif cur is not None:
            if rt == 0x0D:
                cur["layer"] = struct.unpack(">h", payload)[0]
            elif rt == 0x10:
                cur["xy"] = struct.unpack(">ii", payload)
            elif rt == 0x19:
                cur["text"] = payload.rstrip(b"\x00").decode()
            elif rt == 0x11:
                out.append((cur.get("layer"), cur.get("xy"),
                            cur.get("text")))
                cur = None
        i += ln
    return out


tmp = tempfile.mkdtemp(prefix="silica_ex_")
gin = os.path.join(tmp, "in.gds")
gout = os.path.join(tmp, "out.gds")
pins = os.path.join(tmp, "pins.csv")
minimal_gds(gin)
with open(pins, "w") as f:
    f.write("clk,M3,1.5,2.25\nrst,M4,10.0,0.5\nvdd,PG,0,0\n")

p = subprocess.run([sys.executable, os.path.join(EX, "add_pin_labels.py"),
                    "top", gin, pins, gout, "200"],
                   capture_output=True, text=True)
check("add_pin_labels runs and reports its count",
      p.returncode == 0 and "LABELS_ADDED 2" in p.stdout,
      p.stdout + p.stderr)

recs = text_records(gout)
check("add_pin_labels writes one TEXT per routable pin", len(recs) == 2, recs)
check("add_pin_labels honours the layer base argument",
      sorted(r[0] for r in recs) == [203, 204], recs)
check("add_pin_labels converts um to dbu exactly",
      dict((r[2], r[1]) for r in recs)
      == {"clk": (1500, 2250), "rst": (10000, 500)}, recs)

p = subprocess.run([sys.executable, os.path.join(EX, "add_pin_labels.py"),
                    "nosuchcell", gin, pins, gout],
                   capture_output=True, text=True)
check("add_pin_labels refuses an unknown top cell", p.returncode != 0,
      p.stdout + p.stderr)

print("----")
print("ALL PASS" if fails == 0 else "%d FAILURES" % fails)
sys.exit(1 if fails else 0)
