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

The point is not expressiveness — it is **confinement**. A guarded library still
exposes its own internals: an agent that can call `guarded_add()` can also
append to the shape list directly, catch the exception, or write its own helper
that skips the guard. That is not hypothetical; it is the first row of the table
below, and it cost a signoff round. SILICA has no such escape: there is no
expressible program that mutates geometry without passing the check, for the
same reason you sandbox an agent's tool calls rather than trusting it to use the
safe API.

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
make test                            # 217 checks across 12 suites, ~10 seconds
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

## 🔍 Prior art, and what is actually new here

Most of the individual mechanisms in SILICA exist in shipping tools. Saying so
first is the point; a reader who has worked on any of these will place them
within a minute.

| Mechanism | Prior art |
|---|---|
| DRC feedback at edit time, on the real deck | Siemens **Calibre RealTime** (Custom and Digital) — a decade-plus old, and it runs foundry-qualified rules rather than reimplemented ones |
| Preventing an edit that would violate a rule | Cadence **design-rule-driven editing / LiveDRD**, plus a patent family on rule-driven editing to cut edit-verify iterations |
| Correct-by-construction layout from a constrained API | **BAG / Laygo** — templates and grids that make violations unrepresentable; real taped-out silicon |
| Content-hashed hermetic build steps | **Bazel**, **Nix**, and **SiliconCompiler** for EDA specifically |
| Agent proposes → deterministic checker → counterexample → retry | now the standard agentic framing; **ARGUS** does it for data-flow invariants with SMT and emits a concrete counterexample naming the offending thread and program point |

Against that, three things here are not standard practice:

**1. Connectivity change as a declared effect.** Every tool above checks
topology *after* an edit. SILICA makes the topological effect part of the edit —
`on new_net`, `merge(a,b)`, `sub ... splitting`, `sub ... deleting` — and an
undeclared change to the net partition is a rollback. This is a frame condition
in the sense of an effect system: a signature for what an operation is permitted
to disturb, applied to layout topology. It is the part worth formalizing, and
the part most likely to be genuinely new.

**2. Atomic rollback across a composite edit.** Interactive DRD is per-edit and
GUI-coupled. A SILICA transaction can be a loop that places sixty shapes, and if
the fifty-ninth bridges, all sixty are rolled back. A machine author never
inspects a half-applied change.

**3. Refusing to accept a check it cannot perform.** A conditional spacing rule
is in the grammar and is *rejected*, not parsed-and-ignored. Most tools would
accept it and warn. There is no warning class here, and that applies reflexively
to SILICA's own unfinished features.

What is **not** claimed: that local width/space checking is new (it is not, and
the checks here are weaker than a real deck); that this replaces signoff; or
that any of it is yet validated against a controlled baseline. The evaluation in
[`eval/PLAN.md`](eval/PLAN.md) is pre-registered and **not yet run** — and its
honest third arm is a plain Python-plus-asserts wrapper, which may well capture
most of the benefit. If it does, that is the finding.

## 🔬 On a real routed chip

`examples/sky130_eco.sil` runs against real designs synthesized, placed and
routed by OpenROAD on the open SkyWater **sky130** PDK. (Those layer numbers
are real — sky130 is an open PDK.)

**Connectivity agrees with a mature extractor, exactly**, on both:

| design | cells | shapes in | SILICA | KLayout `LayoutToNetlist` | |
|---|---|---|---|---|---|
| `gcd` | 710 | 13,291 | **712 nets** | **712 nets** | agree |
| `aes_cipher_top` | 37,788 | **540,071** | **18,396 nets** | **18,396 nets** | agree |

On the AES core (475 µm², 58% utilization): import 6.2 s, extraction 10.2 s,
peak RSS 0.8 GB. A transaction's shadow copy of all 540k shapes takes **0.45 s**
— that is the rollback primitive at real scale, not a toy.

**An ECO that would short two real nets is refused**, naming both. In the AES
database two met1 wires pass within 140 nm of each other; a patch across the
gap gets:

```json
ROLLBACK shorting_patch {"rule": "bridge",
  "nets": ["met1@140830,1740", "met1@161530,1400"]}
```

**A bug this found, since fixed:** a declared `width` rule fired on imported
geometry that KLayout calls clean, because width was measured per stored
rectangle and import chooses the decomposition. Width is now measured over the
union of the geometry, so the decomposition cannot change the answer — verified
against KLayout on 3,000 randomized layouts and on AES's 246,049 met1
rectangles. See `docs/ARCHITECTURE.md` §6.1.

## 📊 Does it actually help? (`eval/benchmark.py`)

Nine bugs from the failure taxonomy, attempted three ways. An arm "caught" a
bug only if the **bad state never happened**, scored by a ground-truth
predicate — not by whether it complained.

```
raw edits, no checking ......... 0/9
a careful asserts wrapper ...... 5/9
SILICA ......................... 9/9
```

**Quote the middle number.** A competent Python wrapper — given SILICA's own
connectivity engine, and checking bridging, floating, width *and* spacing on
every add, plus a conductor count across subtraction — already catches five of
nine. Most of the value in this project is discipline, not syntax.

The four it misses are the ones that need machinery a per-operation guard
cannot have:

| Bug | Why a wrapper misses it |
|---|---|
| a `sub` splits one net **and** deletes another | the guard counts conductors; `+1` and `−1` cancel. Needs the partition, per net |
| a `sub` thins a wire below minimum width | guards are per-operation, and subtraction is the one people under-guard |
| a composite edit half-applies | nothing to roll back to; the design is left in a state nobody authored |
| stream-out drops an unmapped layer | the engineer asserts over their own list; only the design knows what it holds |

Honest limit: these scenarios were written by the same person who wrote the
runtime, so this shows **coverage against a stated taxonomy**, not unbiased
field value. The blinded campaign replay in [`eval/PLAN.md`](eval/PLAN.md) is
still unrun, and it is the one that would settle it. `tests/test_eval.py` pins
these numbers so this section cannot drift from the code.

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
tests/                  12 suites / 217 checks; conformance.py is the backend contract
eval/                   pre-registered evaluation plan vs. a logged campaign
```

## 🗺 Roadmap

- [x] transactional transform layer + counterexamples
- [x] full general-purpose language; tool-agnostic backend protocol; KLayout backend
- [x] flow layer: hermetic-leaning steps, content-addressed caching, field-bug gates
- [x] shared conformance corpus both backends must pass
- [x] strict name resolution: no declared check can silently do nothing
- [ ] conditional DRC rules checked at commit (wide-metal spacing tiers)
- [x] artifact totality: `export` refuses to drop unmapped data
- [ ] `ports` / `density` invariants
- [ ] goal layer: bounded tactics, budgets, replayable traces
- [ ] evaluation budget (step limit) so an agent's runaway loop is a rollback
- [ ] sandboxed flow steps (undeclared-input detection)
- [x] `import`: read an OpenROAD/KLayout layout in and edit it
- [x] exact rectilinear width, so rules hold on imported geometry
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
