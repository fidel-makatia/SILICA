#!/usr/bin/env python3
"""The benchmark's published numbers are checked, not transcribed.

`eval/benchmark.py` is quoted in the README and in eval/PLAN.md. If its result
ever changes, this fails, so the claim in the docs cannot silently drift away
from the code.
"""
import io
import os
import sys
from contextlib import redirect_stdout

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "eval"))

import benchmark  # noqa: E402

fails = 0


def check(name, cond, detail=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name
          + ("" if cond else "  -- " + str(detail)))
    if not cond:
        fails += 1


buf = io.StringIO()
with redirect_stdout(buf):
    rows = benchmark.main()

got = {"raw": sum(1 for r in rows if r[3] == "caught"),
       "guarded": sum(1 for r in rows if r[4] == "caught"),
       "silica": sum(1 for r in rows if r[5] == "caught")}
check("the benchmark runs every scenario", len(rows) == 9, len(rows))
check("unguarded edits catch nothing (0/9)", got["raw"] == 0, got)
check("the guarded baseline catches 5/9", got["guarded"] == 5, got)
check("SILICA catches 9/9", got["silica"] == 9, got)

missed = sorted(r[0] for r in rows if r[4] != "caught" and r[5] == "caught")
check("the language's marginal value is exactly B3, R2, A1, S1",
      missed == ["A1", "B3", "R2", "S1"], missed)

# every bug must actually be a bug: the raw arm has to reach the bad state,
# or the scenario is not testing anything
check("every scenario's bug is reachable without checking",
      all(r[3] == "ESCAPED" for r in rows),
      [r[0] for r in rows if r[3] != "ESCAPED"])

# and the baseline must not be a strawman: it has to beat raw substantially
check("the baseline is a real baseline, not a strawman",
      got["guarded"] >= 5, got)

print("----")
print("ALL PASS" if fails == 0 else "%d FAILURES" % fails)
sys.exit(1 if fails else 0)
