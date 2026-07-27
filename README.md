# SILICA

**S**tructured **I**nvariant **L**anguage for **I**ntegrated **C**ircuit **A**gents —
a full, tool-agnostic programming language that makes agentic physical chip
design *deterministic*.

```
fn pad(i, pitch) { return box(i*pitch, 0, i*pitch + 56000, 68000) }

tx place_pads {
  for i in range(0, n) {
    add m6 pad(i, pitch) on new_net
    label m6 "PAD_" + str(i) at (i*pitch + 28000, 34000)
  }
  assert spacing(m6, window(0, 0, n*pitch, 68000)) >= 70
}
```

Full language: functions, control flow, lists, strings — a padframe is a loop,
not 400 hand-written polygons. Tool-agnostic: the interpreter drives an
abstract backend protocol; the pure-Python engine and a live KLayout adapter
both ship, and the test suite requires identical commit/rollback decisions on
both. An Innovus/OpenAccess backend is a protocol implementation, not a
language change.

## Thesis

LLM agents are already capable of driving full physical-design campaigns
(DRC closure, padframe assembly, ECO surgery). What makes those campaigns slow
and fragile is not the agent's reasoning — it is that the agent acts through
tools whose action spaces are **open** (raw polygons, raw Tcl, raw file edits)
and whose failure modes are **silent** (unit coercion, geometry normalization,
dropped map rows, dual sources of truth). SILICA closes the action space:

> The agent proposes; the runtime disposes.
> Every edit is a typed, transactional **transform** carrying invariant
> obligations. Illegal or ambiguous intent is *inexpressible or a hard error* —
> never coerced. Failures return machine-readable **counterexamples** that feed
> the next agent step.

Determinism here means two enforceable properties:
1. **Reproducibility** — hermetic execution: same design state + same program
   → same result, byte for byte. No ambient state (units, lib order, cwd).
2. **Predictable semantics** — no silent normalization, no implicit conversion,
   no "success" reports for off-by-415nm connections.

## Why we believe this (field data)

This language is distilled from real multi-day agentic tapeout campaigns
(commercial-40nm harvester SoC: 40+ Calibre signoff rounds to DRC-0/LVS-CORRECT;
ASAP7 chiplet GPU). Every design rule of SILICA answers a bug that actually
happened — see `spec/invariants.md` for the bug-to-principle map. Highlights:

| Real failure | SILICA answer |
|---|---|
| Genus took time units from the *first-read* liberty (1ns clock became 1µs) | units are typed & declared; loads that disagree are errors |
| KLayout silently normalized an inverted box → 4-net short | constructors reject degenerate geometry |
| streamOut map lacked `VIA` rows → every via cut silently dropped | artifact schemas are total: unmapped data is an error |
| LEF OBS covered the pin; router "connected" 415nm off-pin, reported success | one design DB; views are derived, never independently authored |
| hand-rolled edit guard had a cross-layer exemption bug → shorted 4 nets | invariant checks are runtime primitives, not user code |
| `wait($realtime)` never wakes; sed-mangled netlist fed to LVS | typed artifacts + effect system: a `netlist@lvs-source` cannot be produced by text substitution |

## The three layers

1. **Transform layer** — `tx { ... }` transactional edits on the design DB.
   Primitives (`add`, `sub`, `place`, `move`, `label`) execute on a shadow
   copy; declared invariants (`connectivity`, `ports`, plus local asserts) are
   checked at commit. Fail → rollback + counterexample (rule, coords, nets).
2. **Goal layer** — declarative objectives with *bounded tactic sets*
   (`close_timing`, `close_drc`) whose search traces are recorded and replayable.
3. **Flow layer** — hermetic, content-addressed steps over typed artifacts
   (`netlist@synth`, `spef@route`, `gds@signoff`) with resumable checkpoints.

## Repo layout

- `docs/ARCHITECTURE.md` — system design: the determinism boundary, where an
  LLM sits (and doesn't), layer diagrams, backend protocol
- `spec/SPEC.md` — language definition (types, semantics, error model)
- `spec/grammar.ebnf` — surface grammar
- `spec/invariants.md` — the standard invariant library + field bug map
- `prototype/silica.py` — reference interpreter (Python): full language
  (lexer/parser/evaluator) + transactional transform layer + pure-Python
  reference backend
- `prototype/backends/` — tool adapters implementing the backend protocol
  (KLayout shipping; Innovus/OpenAccess = future protocol implementations)
- `examples/` — real programs, incl. replays of actual harvester fixes
- `eval/` — evaluation plan: replay the harvester padframe campaign in SILICA
  vs. raw scripting; count rounds-to-clean and error classes made inexpressible
- `tests/` — interpreter self-tests

## Status

v0.2 — full general-purpose language (fn/if/while/for/lists/strings) with the
transactional transform layer; tool-agnostic backend protocol with pure-Python
and KLayout backends passing the same test suite. Goal/flow layers specified.

Run a program: `python3 prototype/silica.py examples/padframe_gen.sil`
Run the tests: `python3 tests/test_core.py && python3 tests/test_lang.py &&
python3 tests/test_backend_klayout.py`
