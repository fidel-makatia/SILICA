#!/usr/bin/env python3
"""Flow layer: hermetic-leaning steps, content-addressed caching, and the two
pre-tool gates.

The caching tests matter more than they look: a cache that reports a hit it
cannot justify is a silent success, which is the one thing this project exists
to remove.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

from silica import Counterexample, Interp, Parser, lex  # noqa: E402
from silica import flow as silica_flow  # noqa: E402

fails = 0


def check(name, cond, detail=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name
          + ("" if cond else "  -- " + str(detail)))
    if not cond:
        fails += 1


def run(src):
    it = Interp(Parser(lex(src)).parse())
    silica_flow.install(it)
    it.run()
    return it


def halts(name, src, rule, needle=None):
    try:
        run(src)
        check(name, False, "did not halt")
    except Counterexample as ce:
        ok = ce.data["rule"] == rule and (needle is None
                                          or needle in ce.data["note"])
        check(name, ok, ce.data)


tmp = tempfile.mkdtemp(prefix="silica_flow_")
os.chdir(tmp)
with open("in.txt", "w") as f:
    f.write("hello\n")

COPY = 'let r = step("copy", "cp in.txt out.txt", ["in.txt"], ["out.txt"])\n'

# ---- caching -------------------------------------------------------------
it = run(COPY)
check("a step runs its tool and records the trace",
      it.genv.get("r") == "ran" and os.path.exists("out.txt"))

it = run(COPY)
check("unchanged inputs are a verified cache hit", it.genv.get("r") == "cached")

with open("in.txt", "w") as f:
    f.write("changed\n")
it = run(COPY)
check("a changed declared input re-runs the step", it.genv.get("r") == "ran")

with open("out.txt", "w") as f:
    f.write("tampered\n")
it = run(COPY)
check("a tampered output re-runs the step", it.genv.get("r") == "ran")

# Regression: the cache key must include the DECLARED output set. Matching on
# the recorded outputs alone reported CACHED for a step whose declared outputs
# had grown -- and the new artifact was never produced or checked.
run(COPY)   # prime the cache for this (step, cmd, inputs)
halts("growing the declared output set is a cache miss, not a hit",
      'step("copy", "cp in.txt out.txt", ["in.txt"],\n'
      '     ["out.txt", "second.txt"])\n',
      "missing-output", "second.txt")
check("...and the undeclared-until-now output was never silently accepted",
      not os.path.exists("second.txt"))

# ---- structured failures -------------------------------------------------
halts("a missing declared input halts before the tool runs",
      'step("x", "true", ["nope.txt"], [])\n', "missing-input")
halts("a nonzero tool exit halts",
      'step("x", "false", ["in.txt"], [])\n', "tool-exit")
halts("exit 0 without the declared outputs halts",
      'step("x", "true", ["in.txt"], ["ghost.txt"])\n', "missing-output")

# ---- gate 1: liberty time units -----------------------------------------
with open("a.lib", "w") as f:
    f.write('library(a){ time_unit : "1ns"; }\n')
with open("b.lib", "w") as f:
    f.write('library(b){ time_unit : "1ps"; }\n')
halts("mixed liberty time units halt before synthesis",
      'assert_lib_units(["a.lib", "b.lib"])\n', "unit-mismatch", "1ns")

with open("b.lib", "w") as f:
    f.write('library(b){ time_unit : 1ns ; }\n')   # unquoted is legal too
it = run('let u = assert_lib_units(["a.lib", "b.lib"])\n')
check("consistent liberty time units pass (quoted or not)",
      it.genv.get("u") == "1ns", it.genv.get("u"))

with open("c.lib", "w") as f:
    f.write('library(c){ /* no time_unit at all */ }\n')
halts("an undeclared time unit halts rather than being assumed",
      'assert_lib_units(["a.lib", "c.lib"])\n', "unit-mismatch", "UNDECLARED")

# ---- gate 2: stream-map totality ----------------------------------------
with open("t.map", "w") as f:
    f.write("M1 NET 31 0\nM2 NET 32 0\nVIA1 NET 51 0\n")
halts("an unmapped via layer halts before stream-out",
      'assert_map_total("t.map", ["M1", "M2", "VIA1", "VIA2"])\n',
      "unmapped-layer", "VIA2")
it = run('let ok = assert_map_total("t.map", ["M1", "M2", "VIA1"])\n')
check("a total map passes", it.genv.get("ok") is True)

halts("a map with no pin-text rows halts",
      'assert_map_total("t.map", ["M1", "M1.NAME"])\n',
      "unmapped-layer", "M1.NAME")
with open("t.map", "a") as f:
    f.write("M1 NAME 131 0\n")
it = run('let ok2 = assert_map_total("t.map", ["M1", "M1.NAME"])\n')
check("pin-text rows satisfy a layer.kind requirement",
      it.genv.get("ok2") is True)

# ---- capability gating ---------------------------------------------------
try:
    Interp(Parser(lex('step("x","true",[],[])\n')).parse()).run()
    check("the flow layer is capability-gated behind --flow", False)
except Exception as e:
    check("the flow layer is capability-gated behind --flow",
          "step" in str(e), e)

print("----")
print("ALL PASS" if fails == 0 else "%d FAILURES" % fails)
sys.exit(1 if fails else 0)
