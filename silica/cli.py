"""SILICA command-line interface.

    silica [--flow] [--json] [--trace PATH] program.sil

Exit codes:
    0  the program ran to completion (individual tx rollbacks are normal
       feedback, not failures -- they are reported, not fatal)
    1  a flow-layer halt: a declared gate or tool step failed
    2  the program itself is wrong (syntax, types, undeclared name) or the
       command line is wrong
"""
import json
import sys

from silica.interpreter import Counterexample, Interp, Parser, ParseError, lex

USAGE = "usage: silica [--flow] [--json] [--trace PATH] program.sil"


def _emit(obj, as_json, human):
    print(json.dumps(obj) if as_json else human)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    use_flow = as_json = False
    trace = "silica_flow_trace.jsonl"
    files = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--flow":
            use_flow = True
        elif a == "--json":
            as_json = True
        elif a == "--trace":
            i += 1
            if i >= len(argv):
                print("--trace needs a path\n" + USAGE, file=sys.stderr)
                return 2
            trace = argv[i]
        elif a in ("-h", "--help"):
            print(USAGE)
            return 0
        elif a.startswith("-"):
            # an ignored flag is a silent behaviour change; refuse it
            print("unknown option %r\n%s" % (a, USAGE), file=sys.stderr)
            return 2
        else:
            files.append(a)
        i += 1
    if len(files) != 1:
        print(USAGE, file=sys.stderr)
        return 2

    try:
        with open(files[0]) as f:
            src = f.read()
    except OSError as e:
        print("cannot read %s: %s" % (files[0], e.strerror), file=sys.stderr)
        return 2

    try:
        program = Parser(lex(src)).parse()
        it = Interp(program)
        if use_flow:
            from silica.flow import install
            install(it, trace)
        results = it.run()
    except ParseError as e:
        # SPEC section 8 class 1: the design was never touched. Structured,
        # because the intended author of a SILICA program is an agent.
        _emit(dict(e.data, file=files[0]), as_json,
              "ERROR %s" % json.dumps(dict(e.data, file=files[0])))
        return 2
    except Counterexample as ce:
        # a gate, step or export failure outside any tx: structured halt
        _emit(dict(ce.data, halt="flow"), as_json,
              "HALT " + json.dumps(ce.data))
        return 1

    for name, ok, ce in results:
        if as_json:
            print(json.dumps({"tx": name,
                              "result": "commit" if ok else "rollback",
                              "counterexample": ce}))
        else:
            print(("COMMIT   " if ok else "ROLLBACK ") + name,
                  json.dumps(ce) if ce else "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
