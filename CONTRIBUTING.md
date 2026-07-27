# Contributing to SILICA

Thanks for your interest! SILICA is young and contributions land fast.

## Dev setup

```bash
git clone https://github.com/fidel-makatia/SILICA.git
cd SILICA
pip install -e ".[klayout]"   # klayout extra enables the second backend
make test                     # 38 checks across 4 suites, ~5 seconds
make demo                     # run examples/padframe_gen.sil
```

No dependencies beyond the standard library (KLayout is optional, for the
second backend). Python ≥ 3.6.

## Where things live

| Path | What |
|---|---|
| `silica/interpreter.py` | lexer, parser, evaluator, tx engine, pure-Python backend |
| `silica/flow.py` | flow layer: hermetic `step()`, field-bug gates |
| `silica/backends/` | tool adapters implementing the backend protocol |
| `silica/cli.py` | the `silica` command |
| `spec/` | normative language definition + grammar |
| `tests/` | self-test suites (plain scripts, exit 1 on failure) |

## Design rules for contributions

These are the language's identity — PRs that violate them won't merge:

1. **No warning class.** A check either fails the commit/flow or stays
   silent. Warnings are where silent coercions hide.
2. **No silent normalization or coercion.** Degenerate geometry, off-grid
   coordinates, inexact division, cross-type `+` — hard errors, everywhere,
   including deep inside computed values.
3. **Connectivity deltas are declared, not discovered.** Any new way to
   change net count needs its own declaration syntax (`new_net`, `merge`,
   `splitting` are the pattern).
4. **The interpreter speaks only the backend protocol.** No backend-specific
   types or calls above `silica/backends/`. New tool support = new protocol
   implementation, never a language change.
5. **Every new invariant cites its field bug.** Add the failure it answers to
   `spec/invariants.md`. If it never burned a real signoff round, it's a
   feature request, not an invariant.

## Adding a backend

Implement the protocol documented in `silica/interpreter.py` (module
docstring) — about a dozen methods. Then make the same-decisions suite pass
against it (see `tests/test_backend_klayout.py` for the pattern): the same
SILICA programs must produce identical commit/rollback decisions as the
pure-Python reference.

## Tests

Each test encodes one behavior, usually one real field bug. Keep the style:
plain scripts, `PASS`/`FAIL` lines, exit code 1 on any failure. If you fix an
interpreter bug, add the test that would have caught it.
