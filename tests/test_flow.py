#!/usr/bin/env python3
"""SILICA v0.3 flow-layer tests: hermetic steps, caching, and the two
field-bug gates (liberty units, stream-map totality)."""
import sys, os, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from silica import Interp, Parser, lex, Counterexample
from silica import flow as silica_flow

fails = 0
def check(name, cond):
    global fails
    print(("PASS " if cond else "FAIL ") + name)
    if not cond: fails += 1

def run(src):
    it = Interp(Parser(lex(src)).parse())
    silica_flow.install(it)
    it.run()
    return it

tmp = tempfile.mkdtemp(prefix="silica_flow_")
os.chdir(tmp)
with open("in.txt", "w") as f: f.write("hello\n")

# 1. a step runs its tool and records the trace
it = run('let r = step("copy", "cp in.txt out.txt", ["in.txt"], ["out.txt"])\n')
check("step runs", it.genv.get("r") == "ran" and os.path.exists("out.txt"))

# 2. identical step with unchanged inputs is a verified cache hit
it = run('let r = step("copy", "cp in.txt out.txt", ["in.txt"], ["out.txt"])\n')
check("step caches", it.genv.get("r") == "cached")

# 3. changing a declared input invalidates the cache
with open("in.txt", "w") as f: f.write("changed\n")
it = run('let r = step("copy", "cp in.txt out.txt", ["in.txt"], ["out.txt"])\n')
check("input change reruns", it.genv.get("r") == "ran")

# 4. tampering with an output invalidates the cache (outputs are verified)
with open("out.txt", "w") as f: f.write("tampered\n")
it = run('let r = step("copy", "cp in.txt out.txt", ["in.txt"], ["out.txt"])\n')
check("output tamper reruns", it.genv.get("r") == "ran")

# 5. missing declared input is a structured failure before the tool runs
try:
    run('step("x", "true", ["nope.txt"], [])\n')
    check("missing input halts", False)
except Counterexample as ce:
    check("missing input halts", ce.data["rule"] == "missing-input")

# 6. nonzero tool exit is a structured failure
try:
    run('step("x", "false", ["in.txt"], [])\n')
    check("tool exit halts", False)
except Counterexample as ce:
    check("tool exit halts", ce.data["rule"] == "tool-exit")

# 7. tool exiting 0 without producing declared outputs is a failure
try:
    run('step("x", "true", ["in.txt"], ["ghost.txt"])\n')
    check("missing output halts", False)
except Counterexample as ce:
    check("missing output halts", ce.data["rule"] == "missing-output")

# 8. liberty units gate: mixed time units are a hard error (Genus first-lib bug)
with open("a.lib", "w") as f: f.write('library(a){ time_unit : "1ns"; }\n')
with open("b.lib", "w") as f: f.write('library(b){ time_unit : "1ps"; }\n')
try:
    run('assert_lib_units(["a.lib", "b.lib"])\n')
    check("mixed lib units halt", False)
except Counterexample as ce:
    check("mixed lib units halt", ce.data["rule"] == "unit-mismatch"
          and "1ns" in ce.data["note"] and "1ps" in ce.data["note"])

with open("b.lib", "w") as f: f.write('library(b){ time_unit : "1ns"; }\n')
it = run('let u = assert_lib_units(["a.lib", "b.lib"])\n')
check("consistent lib units pass", it.genv.get("u") == "1ns")

# 9. stream-map totality gate (the silent via-drop bug)
with open("t.map", "w") as f:
    f.write("M1 NET 31 0\nM2 NET 32 0\nVIA1 NET 51 0\n")
try:
    run('assert_map_total("t.map", ["M1", "M2", "VIA1", "VIA2"])\n')
    check("unmapped via halts", False)
except Counterexample as ce:
    check("unmapped via halts", ce.data["rule"] == "unmapped-layer"
          and "VIA2" in ce.data["note"])
it = run('let ok = assert_map_total("t.map", ["M1", "M2", "VIA1"])\n')
check("total map passes", it.genv.get("ok") is True)

# 9b. layer.kind rows: a map with no NAME (pin-text) rows is caught
# (the bug that made 18 consecutive real LVS runs INCORRECT: Ports 0 vs 395)
try:
    run('assert_map_total("t.map", ["M1", "M1.NAME"])\n')
    check("missing NAME rows halt", False)
except Counterexample as ce:
    check("missing NAME rows halt", ce.data["rule"] == "unmapped-layer"
          and "M1.NAME" in ce.data["note"])
with open("t.map", "a") as f:
    f.write("M1 NAME 131 0\n")
it = run('let ok2 = assert_map_total("t.map", ["M1", "M1.NAME"])\n')
check("NAME rows pass", it.genv.get("ok2") is True)

# 10. without --flow capability, step() is undefined
it2 = Interp(Parser(lex('let x = 1\n')).parse())
try:
    Interp(Parser(lex('step("x","true",[],[])\n')).parse()).run()
    check("flow capability gated", False)
except Exception:
    check("flow capability gated", True)

print("----")
print("ALL PASS" if fails == 0 else "%d FAILURES" % fails)
sys.exit(1 if fails else 0)
