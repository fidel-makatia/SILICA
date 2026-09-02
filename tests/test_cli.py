#!/usr/bin/env python3
"""The command line as an agent-facing surface.

A program error used to surface as a Python traceback. That is the failure an
author hits most while learning the language, so it is the one that most needs
to be readable and machine-parseable -- and it needs an exit code a Makefile
can branch on.
"""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)

fails = 0


def check(name, cond, detail=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name
          + ("" if cond else "  -- " + str(detail)))
    if not cond:
        fails += 1


tmp = tempfile.mkdtemp(prefix="silica_cli_")


def prog(text, name="p.sil"):
    path = os.path.join(tmp, name)
    with open(path, "w") as f:
        f.write(text)
    return path


def run(args):
    env = dict(os.environ, PYTHONPATH=ROOT)
    p = subprocess.run([sys.executable, "-m", "silica"] + args,
                       cwd=ROOT, env=env, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


HDR = ('design "chip.gds" top chip units nm grid 10\n'
       'stack { metal m1 = (1,0) }\ninvariants { connectivity }\n')

# ---- exit codes ----------------------------------------------------------
rc, out, _ = run([prog(HDR + "tx a { add m1 box(0,0,1000,100) on new_net }\n")])
check("a program that runs exits 0", rc == 0 and "COMMIT   a" in out, out)

# A rollback is normal feedback, not a process failure.
rc, out, _ = run([prog(HDR + """tx a { add m1 box(0,0,1000,100) on new_net }
tx b { add m1 box(900,0,1100,100) on new_net }
""")])
check("a rollback still exits 0 and is reported",
      rc == 0 and "ROLLBACK b" in out, out)

# ---- program errors are structured, not tracebacks -----------------------
rc, out, err = run([prog(HDR + "let x = 7 / 2\n")])
check("a program error exits 2", rc == 2, (rc, out, err))
check("a program error is not a traceback", "Traceback" not in err, err)
check("a program error is reported on stdout as ERROR + json",
      out.startswith("ERROR "), out)
try:
    payload = json.loads(out[len("ERROR "):])
except ValueError:
    payload = {}
check("the error payload carries line, message and file",
      payload.get("line") == 4 and "inexact division" in
      payload.get("message", "") and payload.get("file", "").endswith(".sil"),
      payload)

# ---- --json -------------------------------------------------------------
rc, out, _ = run(["--json", prog(HDR + """tx a { add m1 box(0,0,1000,100) on new_net }
tx b { add m1 box(900,0,1100,100) on new_net }
""")])
lines = [json.loads(ln) for ln in out.strip().splitlines()]
check("--json emits one object per tx",
      [(x["tx"], x["result"]) for x in lines]
      == [("a", "commit"), ("b", "rollback")], out)
check("--json carries the counterexample",
      lines[1]["counterexample"]["rule"] == "not-new", lines)

# ---- argument handling ---------------------------------------------------
# An ignored flag silently changes behaviour: --flwo would have run the
# program with the flow layer absent and then failed on an undefined name.
rc, out, err = run(["--flwo", prog(HDR)])
check("an unknown option is refused", rc == 2 and "unknown option" in err,
      (rc, err))

rc, out, err = run([])
check("no input file is a usage error", rc == 2 and "usage:" in err, err)

rc, out, err = run([os.path.join(tmp, "does_not_exist.sil")])
check("an unreadable input is a clean error, not a traceback",
      rc == 2 and "Traceback" not in err, err)

rc, out, _ = run(["--help"])
check("--help exits 0", rc == 0 and "usage:" in out, out)

# ---- flow halts ----------------------------------------------------------
rc, out, _ = run(["--flow", prog('step("x", "true", ["nope.txt"], [])\n')])
check("a flow halt exits 1 with a structured payload",
      rc == 1 and "HALT" in out and "missing-input" in out, out)

print("----")
print("ALL PASS" if fails == 0 else "%d FAILURES" % fails)
sys.exit(1 if fails else 0)
