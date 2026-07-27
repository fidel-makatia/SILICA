<div align="center">

# ⛰️ SILICA

### The programming language that makes agentic chip design **deterministic**

**S**tructured **I**nvariant **L**anguage for **I**ntegrated **C**ircuit **A**gents

[![tests](https://github.com/fidel-makatia/SILICA/actions/workflows/ci.yml/badge.svg)](https://github.com/fidel-makatia/SILICA/actions/workflows/ci.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![python](https://img.shields.io/badge/python-3.6%2B-blue.svg)](pyproject.toml)
[![no deps](https://img.shields.io/badge/dependencies-zero-brightgreen.svg)](pyproject.toml)
[![PRs welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)

*The agent proposes; the runtime disposes.*

[Quick start](#-quick-start) •
[Why](#-why-silica-exists) •
[Tour](#-a-60-second-tour) •
[Architecture](#-architecture) •
[Real flows](#-it-runs-real-flows) •
[Docs](#-documentation)

</div>

---

LLM agents can already drive full physical-design campaigns — DRC closure,
padframe assembly, ECO surgery, RTL→GDS flows. What makes those campaigns
slow and fragile isn't the agent's reasoning. It's that the agent acts
through tools whose action spaces are **open** (raw polygons, raw Tcl, raw
file edits) and whose failure modes are **silent** (unit coercion, geometry
normalization, dropped stream-map rows).

SILICA closes the action space. Every edit is a typed, **transactional
transform** carrying invariant obligations. Illegal or ambiguous intent is
*inexpressible or a hard error* — never coerced. Failures return
machine-readable **counterexamples** that feed the agent's next step.

**No LLM required.** SILICA is a deterministic language designed to be
*written by* agents, not *powered by* them. Humans and Makefiles are equally
welcome authors.

## ⚡ Quick start

```bash
git clone https://github.com/fidel-makatia/SILICA.git
cd SILICA
pip install -e .            # zero dependencies; add ".[klayout]" for the KLayout backend

silica examples/padframe_gen.sil     # run a program
make test                            # 38 checks, ~5 seconds
```

## 🧨 Why SILICA exists

Every rule in SILICA answers a bug that burned a real signoff round in a real
multi-day agentic tapeout campaign (a commercial-40nm SoC: 40+ Calibre rounds to
DRC-0/LVS-CORRECT; an ASAP7 chiplet GPU):

| 💥 What actually happened | 🛡️ What SILICA makes of it |
|---|---|
| Synthesis took time units from the *first-read* liberty file → a 1 ns clock became 1 µs → 4 hours of garbage | units are declared once; disagreeing libs **halt before any tool runs** |
| The layout editor silently *normalized* an inverted box → the "fix" landed outside the pad and shorted a column of nets | degenerate geometry is a **constructor error**, wherever computed |
| A hand-rolled edit guard had a cross-layer exemption bug → 4 nets bridged, "success" reported | connectivity checking is a **runtime primitive**, not user code |
| The GDS stream map was missing `VIA` rows → every via cut silently dropped → LVS saw opens | artifact schemas are **total**: unmapped data refuses to export |
| A router "connected" a pin 415 nm off target and reported success | `add ... on <net>` must touch **exactly that net** — near misses fail |
| Labels landed on floating metal fragments → a full LVS round lost | floating labels **fail the transaction** |

## 🚀 A 60-second tour

**1 — Illegal intent doesn't commit.** Every `add` declares which net it
extends. Touch two nets? The whole transaction rolls back and you get the
evidence:

```
tx fix_notch {
  add m3 box(749980, 518285, 750065, 518355) on net_at(m3, 749990, 518350)
  assert spacing(m3, window(749480, 517845, 750565, 518855)) >= 70
}
```
```json
ROLLBACK fix_notch {"check": "add.on", "rule": "bridge",
                    "box": [747400, 518000, 747500, 518210],
                    "nets": ["net_a", "net_b"],
                    "note": "shape would merge distinct nets"}
```

**2 — It's a full language.** A padframe is a loop, not 400 hand-written
polygons — and the strictness follows computed values everywhere:

```
fn pad(i, pitch) { return box(i*pitch, 0, i*pitch + 56000, 68000) }

let n = 8
let pitch = 60000

tx place_pads {
  for i in range(0, n) {
    add m6 pad(i, pitch) on new_net
    label m6 "PAD_" + str(i) at (i*pitch + 28000, 34000)
  }
  assert spacing(m6, window(0, 0, n*pitch, 68000)) >= 70
}
```

- `7 / 2` → **hard error** (SILICA never rounds a coordinate)
- `"x" + 5` → **hard error** (`+` never coerces)
- an off-grid or inverted `box(...)` → **hard error**, even three function
  calls deep
- one bad finger in a 5-finger loop → the **whole tx** rolls back atomically

**3 — It orchestrates real EDA flows.** The flow layer wraps tools as
declared, hashed, cached steps — with pre-tool gates for the classic
flow-killers:

```
let unit = assert_lib_units(libs)             // mixed ns/ps libs? halt now,
                                              // not 4 hours into synthesis
assert_map_total(W + "/chip.map", stack)      // unmapped via layer? halt now,
                                              // not at LVS

step("synth",   "sbatch --wait synth.sbatch",  rtl + libs, [netlist, sdc])
step("pnr",     "sbatch --wait pnr.sbatch",    [netlist, lefs], [pnr_v, gds])
step("signoff", "sbatch --wait signoff.sbatch", [gds, pnr_v], [drc_sum, lvs_rep])
```

Inputs are content-hashed before the tool runs; outputs are verified after;
unchanged steps are **cache hits**. Change one RTL file → rebuild exactly
from synthesis onward.

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
(implemented subset — hermetic steps, content-addressed caching).

The interpreter speaks only an abstract backend protocol. The pure-Python
engine and a live **KLayout** adapter both ship, and CI requires the same
programs to produce **identical commit/rollback decisions on both**.

Deep dive: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)

## 🏭 It runs real flows

[`examples/senseedge_flow.sil`](examples/senseedge_flow.sil) rebuilds a real
35.7k-cell accelerator on a custom commercial-40nm PDK — Genus synthesis → Innovus
P&R/CTS/fill/streamOut → Calibre DRC+LVS — as one SILICA program with two
field-bug gates in front. The historical version of this flow took **21
numbered Innovus iterations and 33 command files** to converge; the SILICA
version is three declared steps behind two gates.

## 📦 Repository

```
silica/                 the language implementation
├── interpreter.py        lexer · parser · evaluator · tx engine · reference backend
├── flow.py               flow layer: step(), lib-units gate, map-totality gate
├── backends/klayout.py   KLayout adapter (same protocol, real pya.Layout)
└── cli.py                the `silica` command
spec/                   language definition · grammar · invariant/field-bug map
docs/ARCHITECTURE.md    system design & where the LLM sits (and doesn't)
examples/               runnable programs, incl. replays of real chip fixes
tests/                  4 suites / 38 checks — each encodes a real field bug
eval/                   pre-registered evaluation plan vs. a logged campaign
```

## 🗺 Roadmap

- [x] v0.1 — transactional transform layer + counterexamples
- [x] v0.2 — full general-purpose language; tool-agnostic backend protocol; KLayout backend
- [x] v0.3 — flow layer: hermetic steps, content-addressed caching, field-bug gates
- [ ] conditional DRC rules checked at commit (wide-metal spacing tiers)
- [ ] `ports` / `density` / `schema` invariants
- [ ] goal layer: bounded tactics, budgets, replayable traces
- [ ] sandboxed flow steps (undeclared-input detection)
- [ ] Innovus / OpenAccess backend
- [ ] the eval: replay a logged 11-round padframe campaign, publish rounds-to-clean

## 📚 Documentation

| Doc | What it answers |
|---|---|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | How it works; what's deterministic; where an LLM fits |
| [`spec/SPEC.md`](spec/SPEC.md) | The normative language definition |
| [`spec/grammar.ebnf`](spec/grammar.ebnf) | Surface grammar |
| [`spec/invariants.md`](spec/invariants.md) | Every invariant ↔ the field bug it answers |
| [`eval/PLAN.md`](eval/PLAN.md) | The pre-registered evaluation |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | Dev setup, design rules, adding a backend |

## 📄 License

[MIT](LICENSE) © 2026 Fidel Makatia
