#!/usr/bin/env python3
"""The general-purpose core, and the strictness rules that guard it.

Two halves:
  * the language itself -- functions, loops, control flow, lists, strings
  * name strictness -- every layer, invariant, rule kind and check named in a
    program must be declared AND implemented. A name the runtime does not
    recognize is an error, never a silent no-op, because a check that quietly
    does nothing is indistinguishable from a check that passed.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

from silica import Design, Interp, ParseError, Parser, lex  # noqa: E402

fails = 0


def check(name, cond, detail=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name
          + ("" if cond else "  -- " + str(detail)))
    if not cond:
        fails += 1


def run(src, d=None):
    it = Interp(Parser(lex(src)).parse(), d if d is not None else Design())
    return it, it.run()


def rejects(name, src, needle):
    """The program must be refused, with a message that says why."""
    try:
        run(src)
        check(name, False, "accepted")
    except ParseError as e:
        check(name, needle in str(e), e)


HDR = ('design "chip.gds" top chip units nm grid 10\n'
       'stack { metal m1 = (1,0)\n        via   v1 = (101,0) '
       'connects (m1,m1) }\n'
       'invariants { connectivity }\n')

# ---- the general-purpose core -------------------------------------------
it, _ = run('let total = 0\nlet i = 1\n'
            'while i <= 10 { if i % 2 == 0 { total = total + i } i = i + 1 }\n'
            'let msg = "sum=" + str(total)\n')
check("while / if / assignment / string concat",
      it.genv.get("total") == 30 and it.genv.get("msg") == "sum=30")

it, _ = run('let xs = [10, 20]\nappend(xs, 30)\nlet y = xs[2] * 2\n')
check("lists, append, indexing", it.genv.get("y") == 60)

it, _ = run('fn fib(n) { if n < 2 { return n } '
            'return fib(n-1) + fib(n-2) }\nlet f = fib(10)\n')
check("recursive functions", it.genv.get("f") == 55)

it, r = run(HDR + """
tx evens {
  for i in range(0, 6) {
    if i % 2 == 0 { add m1 box(i*200, 0, i*200 + 100, 500) on new_net }
  }
}
""")
check("conditional adds keep the declared-new count exact", r[0][1], r[0][2])

# `/` divides exactly or errors -- SILICA never rounds a coordinate.
rejects("inexact division is refused", "let x = 7 / 2\n", "inexact division")
rejects("`+` never coerces across types", 'let s = "x" + 5\n',
        "never coerces")
rejects("`return` outside a function is refused", "return 1\n",
        "outside a function")

# ---- errors are locatable and structured --------------------------------
try:
    run("let a = 1\nlet b = 2\nlet c = 7 / 2\n")
    check("errors carry a line number", False)
except ParseError as e:
    check("errors carry a line number", e.line == 3, "line %d" % e.line)
    check("errors carry a machine-readable payload",
          e.data["error"] == "program" and e.data["line"] == 3, e.data)

# ---- name strictness -----------------------------------------------------
# An assert on a layer that does not exist measured nothing and passed.
rejects("assert on an undeclared layer is refused",
        HDR + 'tx a { add m1 box(0,0,1000,100) on new_net\n'
              '  assert width(m9, window(0,0,1000,100)) >= 999999 }\n',
        "not declared in `stack`")
rejects("add on an undeclared layer is refused",
        HDR + "tx a { add m9 box(0,0,100,100) on new_net }\n",
        "not declared in `stack`")
rejects("sub on an undeclared layer is refused",
        HDR + "tx a { sub m9 box(0,0,100,100) }\n",
        "not declared in `stack`")
rejects("net_at on an undeclared layer is refused",
        HDR + "tx a { add m1 box(0,0,100,100) on net_at(m9,10,10) }\n",
        "not declared in `stack`")
rejects("rules on an undeclared layer is refused",
        HDR + "rules { m9.width >= 100 }\n", "not declared in `stack`")
rejects("a label on a via layer is refused",
        HDR + 'tx a { label v1 "clk" at (10,10) }\n',
        "a metal layer is required")
rejects("a via connecting an undeclared metal is refused",
        'design "chip.gds" top chip units nm grid 10\n'
        'stack { via v1 = (101,0) connects (m1,m2) }\n',
        "not declared in `stack`")

# A misspelled invariant silently switched the check off.
rejects("a misspelled invariant is refused",
        HDR.replace("connectivity", "conectivity"), "unknown invariant")
rejects("an unimplemented invariant is refused",
        HDR.replace("connectivity", "ports"),
        "specified but not implemented")

# A rule the runtime cannot check must not be accepted as though it could.
rejects("an unimplemented rule kind is refused",
        HDR + "rules { m1.enclosure >= 100 }\n",
        "specified but not implemented")
rejects("a misspelled rule kind is refused",
        HDR + "rules { m1.wdith >= 100 }\n", "unknown rule kind")
rejects("a conditional rule is refused while unchecked",
        HDR + "rules { m1.space(wide>1000, prl>1000) >= 300 }\n",
        "not checked yet")
rejects("an unknown check is refused",
        HDR + 'tx a { assert density(m1, window(0,0,10,10)) >= 1 }\n',
        "unknown check")

# ---- structural rules ----------------------------------------------------
rejects("declarations inside a tx are refused",
        HDR + "tx a { stack { metal m2 = (2,0) } }\n",
        "not allowed inside a tx")
rejects("nested tx is refused", HDR + "tx a { tx b { } }\n", "nested tx")
rejects("`add` outside a tx is refused",
        HDR + "add m1 box(0,0,100,100) on new_net\n", "only allowed inside")

print("----")
print("ALL PASS" if fails == 0 else "%d FAILURES" % fails)
sys.exit(1 if fails else 0)
