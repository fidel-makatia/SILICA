# Contributing to SILICA

Thanks for your interest. SILICA is young and contributions land fast.

## Dev setup

```bash
git clone https://github.com/fidel-makatia/SILICA.git
cd SILICA
pip install -e ".[klayout]"   # the klayout extra enables the second backend
make test                     # 121 checks across 6 suites, ~10 seconds
make lint                     # flake8, if you have it
make demo                     # run examples/padframe_gen.sil
```

No dependencies beyond the standard library. KLayout is optional and only
needed for the second backend; its suite skips cleanly without it. Python ≥ 3.8.

## No process data, ever

SILICA is developed alongside work under foundry NDAs, so this repository
carries a hard rule: **no process data, in any file, including tests.**

Every layer number, grid, spacing value, coordinate, cell name and design name
here is a placeholder chosen to be readable. When you add an example or a test,
invent values in the same spirit — small round numbers, a toy `m1/m2/m3` stack,
generic names like `chip` or `top`. Do not paste real coordinates, cell names,
deck names, site paths, machine names, job ids, or numbers taken from a rule
deck, and do not describe a failure in a way that identifies the process, the
design or the customer.

Failure *classes* are welcome and are the whole point of `spec/invariants.md` —
"a synthesis tool took time units from the first-read library" is a story about
a tool, not about a PDK. Write that story; leave out which chip it happened on.

## Where things live

| Path | What |
|---|---|
| `silica/interpreter.py` | lexer, parser, evaluator, tx engine, pure-Python backend |
| `silica/flow.py` | flow layer: `step()`, the pre-tool gates |
| `silica/backends/` | tool adapters implementing the backend protocol |
| `silica/cli.py` | the `silica` command |
| `spec/` | normative language definition + grammar |
| `tests/conformance.py` | the backend contract: shared SILICA programs + verdicts |
| `tests/test_*.py` | self-test suites (plain scripts, exit 1 on failure) |

## Design rules for contributions

These are the language's identity. PRs that violate them won't merge:

1. **No warning class.** A check either fails the commit/flow or stays silent.
   Warnings are where silent coercions hide.
2. **No silent normalization or coercion.** Degenerate geometry, off-grid
   coordinates, inexact division, cross-type `+` — hard errors everywhere,
   including deep inside computed values.
3. **No silent no-ops.** If the runtime cannot check something, it must refuse
   to accept the declaration. Adding a rule kind or invariant to the grammar
   without implementing the check means making it a hard error, not letting it
   through.
4. **Connectivity deltas are declared, not discovered.** Any new way to change
   the net count needs its own declaration syntax — `new_net`, `merge`,
   `splitting`, `deleting` are the pattern.
5. **The interpreter speaks only the backend protocol.** No backend-specific
   types or calls above `silica/backends/`. New tool support is a new protocol
   implementation, never a language change.
6. **Every new invariant cites its failure class.** Add the failure it answers
   to `spec/invariants.md`. If it never cost a signoff round, it's a feature
   request, not an invariant.

## Adding a backend

Implement the protocol documented in the `silica/interpreter.py` module
docstring — about a dozen methods — then run the shared corpus against it:

```python
from conformance import run_all
from my_backend import MyBackend
fails = run_all(MyBackend, "mybackend")
```

The corpus is complete SILICA programs with expected per-tx verdicts, so
passing it means your backend reaches the same decisions as the reference on
the same source text. Backends must agree on the **verdict**; the reported
measurement may differ on non-rectangular geometry (see `docs/ARCHITECTURE.md`
§6). If you find a program where two backends disagree on a verdict, that is a
bug in one of them — add it to the corpus with the PR that fixes it.

## Tests

Each test encodes one behaviour, usually one real failure class. Keep the style:
plain scripts, `PASS`/`FAIL` lines, exit code 1 on any failure, no test
framework. If you fix an interpreter bug, add the check that would have caught
it — and if the bug was a divergence between backends, add it to
`tests/conformance.py` so every future backend inherits the check.
