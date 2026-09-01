<div align="center">

# ⛰️ SILICA

### The programming language that makes agentic chip design **deterministic**

**S**tructured **I**nvariant **L**anguage for **I**ntegrated **C**ircuit **A**gents

[![tests](https://github.com/fidel-makatia/SILICA/actions/workflows/ci.yml/badge.svg)](https://github.com/fidel-makatia/SILICA/actions/workflows/ci.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![python](https://img.shields.io/badge/python-3.8%2B-blue.svg)](pyproject.toml)
[![no deps](https://img.shields.io/badge/dependencies-zero-brightgreen.svg)](pyproject.toml)
[![PRs welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)

*The agent proposes; the runtime disposes.*

[Quick start](#-quick-start) •
[Why](#-why-silica-exists) •
[Tour](#-a-60-second-tour) •
[Architecture](#-architecture) •
[Flows](#-it-runs-real-flows) •
[Docs](#-documentation)

</div>

---

LLM agents can already drive full physical-design campaigns — DRC closure,
padframe assembly, ECO surgery, RTL→GDS flows. What makes those campaigns slow
and fragile isn't the agent's reasoning. It's that the agent acts through tools
whose action spaces are **open** (raw polygons, raw Tcl, raw file edits) and
whose failure modes are **silent** (unit coercion, geometry normalization,
dropped stream-map rows).

SILICA closes the action space. Every edit is a typed, **transactional
transform** carrying invariant obligations. Illegal or ambiguous intent is
*inexpressible or a hard error* — never coerced. Failures return
machine-readable **counterexamples** that feed the agent's next step.

**No LLM required.** SILICA is a deterministic language designed to be *written
by* agents, not *powered by* them. Humans and Makefiles are equally welcome
authors.

> **Note on process data.** SILICA is developed alongside work under foundry
> NDAs. **This repository contains no process data.** Every layer number,
> grid, spacing value, coordinate, cell name and design name in it — in the
> docs, the examples and the tests alike — is a placeholder chosen to be
> readable, and corresponds to no foundry's rule deck. The examples will not
> run against a real PDK until you substitute your own values.

## ⚡ Quick start

```bash
git clone https://github.com/fidel-makatia/SILICA.git
cd SILICA
pip install -e .            # zero dependencies; add ".[klayout]" for the KLayout backend

silica examples/fix_notch.sil        # run a program
make test                            # 115 checks across 6 suites, ~10 seconds
```

## 🧨 Why SILICA exists

Every rule in SILICA answers a bug class that burned a real signoff round in a
real multi-day agentic tapeout campaign (commercial PDKs under NDA, plus an
ASAP7 chiplet GPU):

| 💥 The failure class | 🛡️ What SILICA makes of it |
|---|---|
| Synthesis takes time units from the *first-read* liberty file → a 1 ns clock becomes 1 µs → hours of garbage | units are declared once; disagreeing libraries **halt before any tool runs** |
| The layout editor silently *normalizes* an inverted box → the "fix" lands outside the target and shorts a column of nets | degenerate geometry is a **constructor error**, wherever computed |
| A hand-rolled edit guard has a cross-layer exemption bug → nets bridged, "success" reported | connectivity checking is a **runtime primitive**, not user code |
| The GDS stream map is missing via rows → every via cut silently dropped → LVS sees opens | artifact schemas are **total**: unmapped data refuses to export |
| A router "connects" a pin a few hundred nm off target and reports success | `add ... on <net>` must touch **exactly that net** — near misses fail |
| Labels land on floating metal fragments → a full LVS round lost | floating labels **fail the transaction** |
| A repair fills only part of a notch and leaves the wire too narrow | width is re-measured after **every** edit, subtraction included |

## 🚀 A 60-second tour

**1 — Illegal intent doesn't commit.** Every `add` declares which net it
extends. Touch two nets and the whole transaction rolls back with the evidence:

```
tx bad_bridge {
  add m6 box(400, 0, 500, 300) on net_at(m6, 100, 50)
}
```
```json
ROLLBACK bad_bridge {"check": "add.on", "rule": "bridge",
                     "box": [400, 0, 500, 300],
                     "nets": ["m6@0,0", "m6@0,200"],
                     "note": "shape would merge distinct nets"}
```

Net ids are stable and printable, so the counterexample survives the next edit
and an agent can act on it directly. That is `examples/bridge_caught.sil`;
run it.

**2 — It's a full language.** A pad row is a loop, not four hundred hand-written
polygons — and the strictness follows computed values everywhere:

```
fn pad(i, pitch) { return box(i*pitch, 0, i*pitch + 50000, 60000) }

let n = 8
let pitch = 55000

tx place_pads {
  for i in range(0, n) {
    add m6 pad(i, pitch) on new_net
    label m6 "PAD_" + str(i) at (i*pitch + 25000, 30000)
  }
  assert spacing(m6, window(0, 0, n*pitch, 60000)) >= 100
}
```

- `7 / 2` → **hard error** (SILICA never rounds a coordinate)
- `"x" + 5` → **hard error** (`+` never coerces)
- an off-grid or inverted `box(...)` → **hard error**, even three function
  calls deep
- one bad finger in a five-finger loop → the **whole tx** rolls back atomically

**3 — A name the runtime doesn't recognize is an error.** A check that quietly
does nothing is indistinguishable from a check that passed, so SILICA refuses
the program instead:

```
invariants { conectivity }         // ERROR: unknown invariant 'conectivity'
invariants { ports }               // ERROR: specified but not implemented
rules { m3.enclosure >= 100 }      // ERROR: specified but not implemented
assert width(m9, window(...)) >= 100   // ERROR: m9 is not declared in `stack`
```

**4 — It orchestrates real EDA flows.** The flow layer wraps tools as declared,
hashed, cached steps — with pre-tool gates for the classic flow-killers:

```
let unit = assert_lib_units(libs)             // mixed ns/ps libs? halt now,
                                              // not hours into synthesis
assert_map_total(R + "/stream.map", layers)   // unmapped via layer? halt now,
                                              // not at LVS

step("synth", "sbatch --wait " + R + "/synth.sbatch",
     rtl + libs + [ R + "/synth.tcl", R + "/synth.sbatch" ],
     [ R + "/syn/top.v", R + "/syn/top.sdc" ])
```

Inputs are content-hashed before the tool runs; outputs are verified after;
unchanged steps are **cache hits**. Change one RTL file and everything from
synthesis onward rebuilds — and nothing else does.

## 🏗 Architecture

```
        ┌────────────────────────────────────────────────────────┐
        │            AGENT  (LLM / human / Makefile)             │
        └──────────────┬──────────────────────────▲──────────────┘
                       │ writes SILICA            │ reads COMMIT or
                       ▼                          │ Counterexample
   ═════════════════════════════════════════════════════════════════
   ║        SILICA RUNTIME  (deterministic — no AI below this line) ║
   ║                                                               ║
   ║   parse ─▶ evaluate ─▶ tx on shadow copy ─▶ check invariants  ║
   ║                          all pass → atomic commit             ║
   ║                          any fail → rollback + counterexample ║
   ═════════════════════════════════════════════════════════════════
                       │ backend protocol (integer-DBU boxes,
                       │ opaque net ids, clone/absorb)
          ┌────────────┼──────────────┐
          ▼            ▼              ▼
    pure-Python     KLayout       your tool here
    (reference)   (pya.Layout)   (protocol impl, not a language change)
```

Three layers: **transform** (implemented — transactional edits + invariants),
**goal** (specified — bounded tactics, replayable traces), **flow**
(implemented subset — hermetic-leaning steps, content-addressed caching).

The interpreter speaks only an abstract backend protocol. The pure-Python
engine and a live **KLayout** adapter both ship, and both run the *same*
[conformance corpus](tests/conformance.py) — one list of complete SILICA
programs that must reach identical commit/rollback verdicts on every backend.

Deep dive: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)

## 🏭 It runs real flows

[`examples/asic_flow.sil`](examples/asic_flow.sil) is a full RTL → synthesis →
P&R → deterministic pin labelling → DRC/LVS signoff flow expressed as one
SILICA program: four declared, hashed, cached steps behind two pre-tool gates.
Point the paths at your own site and it runs; leave them and it halts, in
structured form, on the first missing declared input.

The flow it is modelled on historically took twenty-one numbered P&R iterations
and thirty-three command files to converge. The gates in front of it exist
because two of those rounds were spent on a units mismatch and a stream map
with no text rows.

## 📦 Repository

```
silica/                 the language implementation
├── interpreter.py        lexer · parser · evaluator · tx engine · reference backend
├── flow.py               flow layer: step(), lib-units gate, map-totality gate
├── backends/klayout.py   KLayout adapter (same protocol, real pya.Layout)
└── cli.py                the `silica` command
spec/                   language definition · grammar · invariant/field-bug map
docs/ARCHITECTURE.md    system design & where the LLM sits (and doesn't)
examples/               runnable programs, incl. replays of real fix classes
tests/                  6 suites / 115 checks; conformance.py is the backend contract
eval/                   pre-registered evaluation plan vs. a logged campaign
```

## 🗺 Roadmap

- [x] transactional transform layer + counterexamples
- [x] full general-purpose language; tool-agnostic backend protocol; KLayout backend
- [x] flow layer: hermetic-leaning steps, content-addressed caching, field-bug gates
- [x] shared conformance corpus both backends must pass
- [x] strict name resolution: no declared check can silently do nothing
- [ ] conditional DRC rules checked at commit (wide-metal spacing tiers)
- [ ] `ports` / `density` / `schema` invariants
- [ ] goal layer: bounded tactics, budgets, replayable traces
- [ ] evaluation budget (step limit) so an agent's runaway loop is a rollback
- [ ] sandboxed flow steps (undeclared-input detection)
- [ ] OpenROAD / Innovus / OpenAccess backends
- [ ] the eval: replay a logged padframe campaign, publish rounds-to-clean

## 📚 Documentation

| Doc | What it answers |
|---|---|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | How it works; what's deterministic; where an LLM fits |
| [`spec/SPEC.md`](spec/SPEC.md) | The normative language definition |
| [`spec/grammar.ebnf`](spec/grammar.ebnf) | Surface grammar |
| [`spec/invariants.md`](spec/invariants.md) | Every invariant ↔ the failure class it answers |
| [`eval/PLAN.md`](eval/PLAN.md) | The pre-registered evaluation |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | Dev setup, design rules, adding a backend |

## 📄 License

[MIT](LICENSE) © 2026 Fidel Makatia
