"""SILICA command-line interface: `silica [--flow] program.sil`."""
import json
import sys

from silica.interpreter import Counterexample, Interp, Parser, lex


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    use_flow = "--flow" in argv
    files = [a for a in argv if not a.startswith("-")]
    if len(files) != 1:
        print("usage: silica [--flow] program.sil")
        return 2
    with open(files[0]) as f:
        program = Parser(lex(f.read())).parse()
    it = Interp(program)
    if use_flow:
        from silica.flow import install
        install(it)
    try:
        results = it.run()
    except Counterexample as ce:
        # a flow-layer failure outside any tx: structured halt
        print("FLOW-HALT " + json.dumps(ce.data))
        return 1
    for name, ok, ce in results:
        print(("COMMIT   " if ok else "ROLLBACK ") + name,
              json.dumps(ce) if ce else "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
