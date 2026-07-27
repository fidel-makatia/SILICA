# SILICA Architecture — and where the LLM sits (and doesn't)

*Companion to `spec/SPEC.md` (normative language definition). This document
explains the system design: what is deterministic, what is agentic, how the
pieces fit, and why each boundary is drawn where it is.*

---

## 1. The one-sentence answer

**SILICA does not need an LLM to work.** It is an ordinary deterministic
programming language — `silica program.sil` runs with no
model, no network, no sampling anywhere in the loop. SILICA is designed to be
*written by* agents, not *powered by* them.

## 2. The division of labor

Agentic chip design has two halves that today's tooling smears together:

| Half | Nature | Who should own it |
|---|---|---|
| **Proposing** an edit ("extend this wire", "place 8 pads at this pitch") | Creative, contextual, fallible | The agent (LLM, human, or script) |
| **Judging** the edit (does it bridge nets? split a net? violate spacing?) | Mechanical, checkable, must never be wrong | A deterministic runtime |

When an LLM drives raw tools (KLayout pya, Innovus Tcl, sed on netlists), it
ends up owning *both* halves: it writes the edit **and** writes the guard that
checks the edit. Both are then fallible. In the commercial-40nm harvester campaign this
is precisely what failed — a hand-rolled edit guard had a cross-layer
exemption bug that shorted 4 nets while reporting success, and only a
separately hand-maintained flat conductor count caught it, rounds later.

SILICA's thesis:

> **The agent proposes; the runtime disposes.**
> Every edit is a typed, transactional transform carrying invariant
> obligations. Illegal or ambiguous intent is inexpressible or a hard error —
> never coerced. Failures return machine-readable counterexamples.

The judging half is moved out of the agent into a runtime that cannot be
sweet-talked, hallucinated around, or subtly miswritten per-campaign.

## 3. The agent loop (LLM outside, runtime inside)

```
        ┌─────────────────────────────────────────────────────────────┐
        │                     AGENT (LLM / human / script)            │
        │   "the M3 notch at (749980, 518345) needs a patch"          │
        └───────────────┬─────────────────────────────▲───────────────┘
                        │ writes a SILICA program     │ reads a result
                        ▼                             │
        ┌───────────────────────────────┐   ┌─────────┴────────────────┐
        │  tx fix_notch {               │   │ COMMIT                   │
        │    add m3 box(...) on         │   │   — or —                 │
        │        net_at(m3, x, y)       │   │ ROLLBACK + Counterexample│
        │    assert spacing(...) >= 70  │   │ {check: "add.on",        │
        │  }                            │   │  rule:  "bridge",        │
        └───────────────┬───────────────┘   │  box:   [x1,y1,x2,y2],   │
                        │                   │  nets:  [netA, netB],    │
   ═════════════════════▼═══════════════════│  note:  "..."}           │
   ║          SILICA RUNTIME (deterministic, no AI below this line)    ║
   ║                                                                   ║
   ║   parse ─▶ evaluate ─▶ tx on shadow copy ─▶ check invariants      ║
   ║                                                │                  ║
   ║                             all pass ──────────┤─────── any fail  ║
   ║                                ▼               ▼                  ║
   ║                         atomic commit      rollback +             ║
   ║                         (absorb shadow)    counterexample         ║
   ═════════════════════════════════════════════════════════════════════
```

Three properties of this boundary:

1. **The runtime is closed-world.** Same design state + same program → same
   result, byte for byte. No ambient state (units, lib order, cwd, model
   temperature) can change the outcome.
2. **The feedback channel is machine-readable.** A rollback returns
   `{check, rule, box, nets, note}` — the same shape as a DRC results-database
   marker. An agent consumes it directly as its next observation; a human
   reads it just as well. Nothing in the loop requires the consumer to be a
   model.
3. **The agent is swappable.** LLM, human, random search, or a Makefile —
   the runtime neither knows nor cares. That is what "does not need an LLM"
   means operationally.

## 4. The three layers

```
┌─────────────────────────────────────────────────────────────────────┐
│ LAYER 3 — FLOW (specified, v0.2 not implemented)                    │
│  Hermetic, content-addressed steps over typed artifacts:            │
│    synth: rtl@src × libs@ccs → netlist@synth                        │
│  Tool wrappers pin versions/seeds, declare full input closure;      │
│  undeclared input read = sandbox error. Resumable checkpoints.      │
├─────────────────────────────────────────────────────────────────────┤
│ LAYER 2 — GOAL (specified, v0.2 not implemented)                    │
│  goal close_timing(group alu_wb, target 1000ps) {                   │
│    tactics [ retime(u_alu), pipeline(wb,1), upsize(critical) ]      │
│    budget 3 runs                                                    │
│  }                                                                  │
│  A goal is satisfied by a TRACE: the ordered tactic applications    │
│  with result hashes. Traces are recorded artifacts; replay is       │
│  bit-identical.  ◀── the ONE place an LLM may sit inside the loop   │
├─────────────────────────────────────────────────────────────────────┤
│ LAYER 1 — TRANSFORM (implemented, v0.2)                             │
│  Full general-purpose language (fn/if/while/for/lists/strings)      │
│  wrapped around transactional `tx` edits with invariant checking.   │
│  Zero AI. This layer is the subject of the rest of this document.   │
└─────────────────────────────────────────────────────────────────────┘
```

### The goal-layer nuance

Layer 2 needs *something* to choose the next tactic. That chooser may be an
LLM, a heuristic, or exhaustive search — SILICA is agnostic. The determinism
guarantee is deliberately scoped as **determinism of record, not of search**:

- The tactic set is **bounded and declared** — the chooser picks from a menu,
  it cannot invent an unvetted transform.
- The **budget** is declared — no unbounded thrashing.
- Every run appends to a **trace**; replaying a trace on the same input state
  is bit-identical.

So even in the one place a model may participate, its choices are fenced
(menu), metered (budget), and journaled (trace). The search may be
stochastic; the record never is.

## 5. Layer 1 in detail: the transform layer

### 5.1 Execution pipeline

```
  source (.sil)
      │
      ▼
  ┌────────┐   tokens   ┌────────┐    AST    ┌───────────────────────┐
  │ lexer  ├───────────▶│ parser ├──────────▶│ evaluator             │
  └────────┘            └────────┘           │  · env chain (let/fn) │
   hard errors:          hard errors:        │  · control flow       │
   · lex error           · syntax            │  · builtins           │
                         · unknown check     │  · tx execution ──────┼──▶ backend
                                             └───────────────────────┘   protocol
   evaluation hard errors (never rollbacks — they mean the PROGRAM is wrong):
   · off-grid coordinate          · inverted/degenerate box
   · inexact division             · cross-type `+`
   · undefined name               · `add` outside tx
```

The error taxonomy is load-bearing (SPEC §8). Three severities, no fourth:

| Class | Meaning | Consumer action |
|---|---|---|
| **parse/type error** | The program itself is malformed; the design was never touched | Fix the program |
| **commit failure** (Counterexample) | The program is well-formed but the edit is illegal in this design state; rolled back atomically | Normal agent feedback — propose differently |
| **integrity panic** | The design DB failed a self-check | Should be unreachable; its existence is what makes the other two trustworthy |

**There is no warning class.** Every field bug that motivated SILICA was, in
its native tool, either silent or a warning that scrolled past. Anything
worth saying is worth failing on.

### 5.2 Transaction lifecycle

```
  tx name { body }
      │
      ▼
  shadow = backend.clone()          ── the live design is never touched
      │
      ▼
  execute body on shadow            ── loops, fn calls, add/sub/label/assert
      │                                each `add ... on <net>` checked NOW:
      │                                  touches nothing        → floating
      │                                  touches 2+ nets        → bridge
      │                                  touches the wrong net  → wrong-net
      │                                  `new_net` touches any  → not-new
      │                                each `sub` checked NOW:
      │                                  splits host net w/o `splitting` → split
      │                                each `label` checked NOW:
      │                                  attaches to no metal   → floating
      │
      ▼
  commit checks on the final shadow
      ├─ connectivity: net_count == pre + Σdeclared(new_net, merge, splitting)
      └─ rules(local): width/space minima in the halo of touched shapes
      │
      ├── all pass ──▶ backend.absorb(shadow)       atomic commit
      └── any fail ──▶ shadow discarded            atomic rollback
                       + Counterexample returned
```

Key invariant: **the connectivity delta must be declared, not discovered.**
`on new_net` declares +1, `merge(a,b)` declares −1, `sub ... splitting`
declares +k as measured. Anything else that changes the net count fails the
commit. This generalizes the flat conductor-count check (14718) that was the
only thing standing between the harvester padframe campaign and four silently
shorted nets — except here it is per-net precise and non-optional.

### 5.3 The general-purpose core

v0.2 is a full language so that *programs can compute geometry* instead of
enumerating it:

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

The strictness follows the values, not just the syntax:

- `/` **divides exactly or errors.** `7 / 2` is a hard error, not `3` —
  SILICA never rounds a coordinate, including ones computed three function
  calls away from any geometry.
- `+` **never coerces.** `"x" + 5` is an error; so is `int + list`.
- `box()` validates wherever it is called — off-grid or inverted geometry is
  rejected at construction, whether written literally or computed in a loop.
- Declarations (`design/stack/rules/invariants/fn`) may not appear inside
  `tx`; everything else composes freely — loops and function calls inside tx
  bodies are the intended style.

A whole-tx failure is **atomic**: if finger 4 of a 5-finger comb bridges,
fingers 0–3 are rolled back with it. The agent retries from the pre-tx state,
never from a half-applied one.

## 6. Tool-agnosticism: the backend protocol

The interpreter never manipulates geometry directly. It drives an abstract
protocol; which engine sits behind it is invisible to the language:

```
                       ┌──────────────────────────┐
                       │   SILICA interpreter     │
                       │   (silica/interpreter.py)  │
                       └────────────┬─────────────┘
                                    │ backend protocol only:
                                    │  declare_metal/declare_via
                                    │  clone() / absorb(shadow)
                                    │  add / sub / add_label / on_metal
                                    │  nets() / net_count / net_at
                                    │  nets_touching(layer, box)
                                    │  min_spacing / min_width
                    ┌───────────────┼────────────────────┐
                    ▼               ▼                    ▼
          ┌──────────────┐  ┌───────────────┐   ┌──────────────────┐
          │ silica.Design│  │ KLayoutBackend│   │ (future)         │
          │ pure Python  │  │ live          │   │ Innovus /        │
          │ reference    │  │ pya.Layout,   │   │ OpenAccess /     │
          │ semantics    │  │ Region engine │   │ OpenROAD backend │
          └──────────────┘  └───────────────┘   └──────────────────┘
```

Design decisions that make this real rather than aspirational:

- **Geometry crosses the interface as integer-DBU boxes.** No backend-native
  types leak upward; no floats exist to disagree between engines.
- **Net ids are opaque.** The interpreter only ever compares them for
  equality. The pure-Python backend uses frozensets of shape indices; KLayout
  uses merged-polygon components; neither representation is visible to
  programs.
- **`clone()/absorb()` are the transaction primitive.** Shadow-copy
  semantics are a backend obligation, so atomic rollback works identically on
  a dict of boxes and on a `pya.Layout`.
- **The test suite is the contract.** The same SILICA programs run on both
  shipped backends and must produce identical commit/rollback decisions
  (`tests/test_core.py` + `tests/test_lang.py` on pure Python,
  `tests/test_backend_klayout.py` on KLayout). A new backend is done when it
  passes the suite — no language change involved.

The KLayout adapter's documented beta approximations (bbox-based
spacing/width — exact for rectilinear boxes; via-mediated touch resolved at
commit rather than at the add) live in the adapter, not the language.

## 7. Why every rule exists: the field-bug map

SILICA was distilled from real multi-day agentic tapeout campaigns (commercial-40nm
harvester SoC: 40+ Calibre signoff rounds to DRC-0/LVS-CORRECT; ASAP7 chiplet
GPU). Every strictness rule answers a failure that actually consumed a
signoff round (full map: `spec/invariants.md`):

| Real failure | SILICA answer |
|---|---|
| Genus took time units from the *first-read* liberty → 1 ns clock became 1 µs; 4-hour synthesis of garbage | units are typed and declared once; disagreeing loads are errors |
| KLayout silently normalized an inverted box → geometry landed OUTSIDE the pad and daisy-chained the W-column nets | `Box` constructor rejects degenerate/inverted geometry, wherever computed |
| Hand-rolled edit guard had a cross-layer exemption bug → 4 nets shorted, "success" reported | connectivity checking is a runtime primitive, not user code; deltas must be declared |
| Innovus stream map lacked `VIA` rows → every via cut silently dropped; LVS saw opens | exporters are total: unmapped data aborts the export |
| Labels placed on pad-body fragments bound to floating islands → full LVS round lost | floating labels fail the tx at commit |
| `assign pwm_out = adc_gate` alias → LVS mismatch in naive CDL | port aliases must be declared; exporter emits `.CONNECT` from the declaration |
| Off-by-415 nm "connected" pin reported as routed success | `add ... on <net>` must touch exactly the named net — near misses are `floating`, not success |

## 8. Repo map and current state

```
SILICA/
├── README.md                    thesis + field-data table
├── silica/
│   ├── interpreter.py           reference interpreter + pure-Python backend
│   ├── flow.py                  flow layer: step(), field-bug gates
│   ├── backends/klayout.py      live pya.Layout adapter (same protocol)
│   └── cli.py                   the `silica` command
├── spec/
│   ├── SPEC.md                  normative language definition
│   ├── grammar.ebnf             surface grammar
│   └── invariants.md            invariant library ↔ field-bug map
├── examples/
│   ├── fix_notch.sil            replay of a real harvester DRC fix (commits)
│   ├── pad_bridge_caught.sil    replay of the round-7 guard bug (rolls back)
│   ├── padframe_gen.sil         parametric padframe — loop, not 400 polygons
│   └── senseedge_flow.sil       real RTL→GDS→signoff flow on commercial-40nm
├── tests/                       4 suites / 38 checks
├── eval/PLAN.md                 pre-registered replay of the 11-round
│                                padframe campaign; prediction ≈4 rounds
└── docs/ARCHITECTURE.md         this document
```

| Layer | Status |
|---|---|
| Transform layer (language + tx + invariants) | **implemented, 38 checks green on both backends** |
| Backend protocol + pure-Python + KLayout backends | **implemented** |
| Conditional rules (`m3.space(wide>1650) >= 500`) | parsed, unchecked |
| `ports` / `density` / `schema` invariants | specified |
| Goal layer (tactics, budgets, traces) | specified |
| Flow layer (hermetic content-addressed steps) | **implemented subset (v0.3): step(), caching, gates** |

## 9. Summary

- SILICA runs with **zero AI in the loop**: deterministic parse → evaluate →
  shadow-tx → invariant check → atomic commit or counterexample.
- LLMs are the intended **authors** of SILICA programs; the runtime is the
  incorruptible **judge**. The counterexample channel is machine-readable so
  the judge's verdict feeds the agent's next step — but a human or a Makefile
  closes the loop just as well.
- The only place a model may ever sit *inside* the system is the goal layer's
  tactic chooser — and there it picks from a declared menu, under a declared
  budget, onto a replayable trace. Stochastic search, deterministic record.
