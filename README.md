# SILICA

**S**tructured **I**nvariant **L**anguage for **I**ntegrated **C**ircuit **A**gents —
a programming language that makes agentic physical chip design *deterministic*.

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

- `spec/SPEC.md` — language definition (types, semantics, error model)
- `spec/grammar.ebnf` — surface grammar
- `spec/invariants.md` — the standard invariant library + field bug map
- `prototype/` — reference interpreter (Python): core transform layer,
  pure-Python geometry engine + KLayout bridge backend
- `examples/` — real programs, incl. replays of actual harvester fixes
- `eval/` — evaluation plan: replay the harvester padframe campaign in SILICA
  vs. raw scripting; count rounds-to-clean and error classes made inexpressible
- `tests/` — interpreter self-tests

## Status

v0.1 — core transform layer implemented and self-testing; goal/flow layers specified.
