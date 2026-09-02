# SILICA Architecture — and where the LLM sits (and doesn't)

*Companion to [`spec/SPEC.md`](../spec/SPEC.md) (the normative language
definition). This document explains the system design: what is deterministic,
what is agentic, how the pieces fit, and why each boundary is drawn where it
is.*

> **Note on process data.** Every layer number, grid, coordinate and rule value
> in this document is a placeholder. Nothing here is from, or corresponds to,
> any foundry's process design kit.

---

## 1. The one-sentence answer

**SILICA does not need an LLM to work.** It is an ordinary deterministic
programming language — `silica program.sil` runs with no model, no network, no
sampling anywhere in the loop. SILICA is designed to be *written by* agents,
not *powered by* them.

## 2. The division of labor

Agentic chip design has two halves that today's tooling smears together:

| Half | Nature | Who should own it |
|---|---|---|
| **Proposing** an edit ("extend this wire", "place 8 pads at this pitch") | Creative, contextual, fallible | The agent (LLM, human, or script) |
| **Judging** the edit (does it bridge nets? split a net? violate width?) | Mechanical, checkable, must never be wrong | A deterministic runtime |

When an LLM drives raw tools (layout-editor Python, P&R Tcl, `sed` on netlists)
it ends up owning *both* halves: it writes the edit **and** the guard that
checks the edit. Both are then fallible. That is precisely what failed in the
campaigns behind this project — a hand-rolled edit guard had a cross-layer
exemption bug that shorted several nets while reporting success, and only a
separately maintained conductor count caught it, rounds later.

SILICA's thesis:

> **The agent proposes; the runtime disposes.**
> Every edit is a typed, transactional transform carrying invariant
> obligations. Illegal or ambiguous intent is inexpressible or a hard error —
> never coerced. Failures return machine-readable counterexamples.

The judging half moves out of the agent into a runtime that cannot be
sweet-talked, hallucinated around, or subtly miswritten per campaign.

## 3. The agent loop (LLM outside, runtime inside)

```
        ┌─────────────────────────────────────────────────────────────┐
        │                     AGENT (LLM / human / script)            │
        │   "the m3 notch near (800, 200) needs a patch"               │
        └───────────────┬─────────────────────────────▲───────────────┘
                        │ writes a SILICA program     │ reads a result
                        ▼                             │
        ┌───────────────────────────────┐   ┌─────────┴────────────────┐
        │  tx fix_notch {               │   │ COMMIT                   │
        │    add m3 box(...) on         │   │   — or —                 │
        │        net_at(m3, x, y)       │   │ ROLLBACK + Counterexample│
        │    assert width(...) >= 100   │   │ {check: "add.on",        │
        │  }                            │   │  rule:  "bridge",        │
        └───────────────┬───────────────┘   │  box:   [x1,y1,x2,y2],   │
                        │                   │  nets:  ["m6@0,0", ...], │
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
   result. No ambient state (units, library order, cwd, model temperature) can
   change the outcome.
2. **The feedback channel is machine-readable.** A rollback returns
   `{check, rule, box, nets, note}` — the same shape as a DRC results-database
   marker. Net ids are stable strings (`m6@0,200`), not indices that shift on
   the next edit, so the counterexample is still meaningful when the agent acts
   on it. Program errors are structured too: `ERROR {error, line, message,
   file}` with an exit code, not a stack trace.
3. **The agent is swappable.** LLM, human, random search, or a Makefile — the
   runtime neither knows nor cares. That is what "does not need an LLM" means
   operationally.

## 4. The three layers

```
┌─────────────────────────────────────────────────────────────────────┐
│ LAYER 3 — FLOW (implemented subset)                                 │
│  Declared, content-addressed steps over artifacts:                  │
│    step(name, cmd, inputs, outputs) -> "ran" | "cached"             │
│  Plus two pre-tool gates: liberty-unit agreement, stream-map        │
│  totality. Sandboxed execution (undeclared-input detection) is      │
│  specified, not implemented — so the cache is exactly as sound as   │
│  the declaration.                                                   │
├─────────────────────────────────────────────────────────────────────┤
│ LAYER 2 — GOAL (specified, not implemented)                         │
│  goal close_timing(group alu_wb, target 1000ps) {                   │
│    tactics [ retime(u_alu), pipeline(wb,1), upsize(critical) ]      │
│    budget 3 runs                                                    │
│  }                                                                  │
│  A goal is satisfied by a TRACE: the ordered tactic applications    │
│  with result hashes. Traces are recorded artifacts; replay is       │
│  bit-identical.  ◀── the ONE place an LLM may sit inside the loop   │
├─────────────────────────────────────────────────────────────────────┤
│ LAYER 1 — TRANSFORM (implemented)                                   │
│  Full general-purpose language (fn/if/while/for/lists/strings)      │
│  wrapped around transactional `tx` edits with invariant checking.   │
│  Zero AI. This layer is the subject of the rest of this document.   │
└─────────────────────────────────────────────────────────────────────┘
```

### The goal-layer nuance

Layer 2 needs *something* to choose the next tactic. That chooser may be an LLM,
a heuristic, or exhaustive search — SILICA is agnostic. The determinism
guarantee is deliberately scoped as **determinism of record, not of search**:

- The tactic set is **bounded and declared** — the chooser picks from a menu, it
  cannot invent an unvetted transform.
- The **budget** is declared — no unbounded thrashing.
- Every run appends to a **trace**; replaying a trace on the same input state is
  bit-identical.

So even where a model may participate, its choices are fenced (menu), metered
(budget) and journaled (trace). The search may be stochastic; the record never
is.

## 5. Layer 1 in detail: the transform layer

### 5.1 Execution pipeline

```
  source (.sil)
      │
      ▼
  ┌────────┐   tokens   ┌────────┐    AST    ┌───────────────────────┐
  │ lexer  ├───────────▶│ parser ├──────────▶│ evaluator             │
  └────────┘  + lines   └────────┘  + lines  │  · env chain (let/fn) │
   hard errors:          hard errors:        │  · control flow       │
   · lex error           · syntax            │  · builtins           │
                         · unknown invariant │  · tx execution ──────┼──▶ backend
                         · unknown rule kind └───────────────────────┘   protocol
                         · unknown check
   evaluation hard errors (never rollbacks — they mean the PROGRAM is wrong):
   · undeclared layer             · inverted/degenerate box
   · off-grid coordinate          · inexact division
   · cross-type `+`               · `add` outside tx
   · label on a via layer         · undefined name
```

Every error carries the source line, and reaches the caller as a structured
payload rather than a traceback.

The error taxonomy is load-bearing (SPEC §8). Three classes, no fourth:

| Class | Meaning | Consumer action | Exit |
|---|---|---|---|
| **program error** | The program itself is malformed; the design was never touched | Fix the program | 2 |
| **commit failure** (Counterexample) | Well-formed, but the edit is illegal in this design state; rolled back atomically | Normal agent feedback — propose differently | 0 |
| **flow halt** | A declared gate or tool step failed | Fix the environment or the flow | 1 |
| **integrity panic** | The design DB failed a self-check | Should be unreachable; its existence is what makes the others trustworthy | — |

**There is no warning class.** Every failure that motivated SILICA was, in its
native tool, either silent or a warning that scrolled past.

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
      │                                each `sub` checked NOW, per net:
      │                                  a net fans out w/o `splitting` → split
      │                                  a net vanishes w/o `deleting`  → delete
      │                                each `label` checked NOW:
      │                                  attaches to no metal   → floating
      │
      ▼
  commit checks on the final shadow
      ├─ connectivity: net_count == pre + Σdeclared(new_net, merge, sub mods)
      └─ rules(local): width/space minima in the halo of every TOUCHED shape,
      │                adds and subs alike
      │
      ├── all pass ──▶ backend.absorb(shadow)       atomic commit
      └── any fail ──▶ shadow discarded            atomic rollback
                       + Counterexample returned
```

Two things are easy to get wrong here and are worth stating explicitly.

**The connectivity effect must be declared, not discovered — and it is checked
per net, not as a count.** `on new_net` declares a creation, `merge(a,b)` a
union, `sub ... splitting` a split, `sub ... deleting` a removal. At each `sub`
every surviving component is correlated back to the net it came from, so each
pre-net is classified individually.

Counting was the first implementation and it was unsound, in a way worth
stating plainly because it is the same shape as the field bug this project was
founded on: **scalars cancel.** One subtraction that split a net in two while
deleting another left the count unchanged, so nothing was declared and it
committed. The hand-maintained flat conductor count that caught the original
short is blind to exactly this, by construction. Counting is an approximation
of the invariant; the invariant is about the partition.

**Rules are checked against what the transaction left behind, not what it
added.** A subtraction can thin a wire below minimum width exactly as easily as
an undersized addition can place one, and the notch it leaves is invisible to
connectivity. So width and space are both evaluated on the final shadow, in the
halo of every shape the tx *touched*.

### 5.3 Names must resolve

A declared check that silently does nothing is indistinguishable from a check
that passed — the exact class of failure this project exists to remove. So the
runtime refuses any name it does not implement:

```
invariants { conectivity }             ERROR unknown invariant
invariants { ports }                   ERROR specified but not implemented
rules { m3.enclosure >= 100 }          ERROR specified but not implemented
rules { m3.space(wide>W) >= S }        ERROR conditional rules not checked yet
assert width(m9, window(...)) >= 100   ERROR m9 is not declared in `stack`
add m9 box(...) on new_net             ERROR m9 is not declared in `stack`
label v1 "clk" at (10,10)              ERROR v1 is a via layer
```

This includes features SILICA itself specifies but has not built. They become
legal when they are checked, and not before.

### 5.4 The general-purpose core

The language is general-purpose so that *programs can compute geometry* instead
of enumerating it, and the strictness follows the values rather than the syntax:

- `/` **divides exactly or errors.** `7 / 2` is a hard error, not `3` — SILICA
  never rounds a coordinate, including one computed three function calls away
  from any geometry.
- `+` **never coerces.** `"x" + 5` is an error; so is `int + list`.
- `box()` validates wherever it is called — off-grid or inverted geometry is
  rejected at construction, literal or computed.
- Declarations may not appear inside `tx`; everything else composes freely.

A whole-tx failure is **atomic**: if finger 4 of a 5-finger comb bridges,
fingers 0–3 roll back with it. The agent retries from the pre-tx state, never
from a half-applied one.

## 6. Tool-agnosticism: the backend protocol

The interpreter never manipulates geometry directly. It drives an abstract
protocol; which engine sits behind it is invisible to the language:

```
                       ┌──────────────────────────┐
                       │   SILICA interpreter     │
                       │   (silica/interpreter.py)│
                       └────────────┬─────────────┘
                                    │ backend protocol only:
                                    │  declare_metal/declare_via
                                    │  clone() / absorb(shadow)
                                    │  add / sub / add_label / on_metal
                                    │  nets() / net_count / net_at
                                    │  nets_touching(layer, box)
                                    │  width_violation / spacing_violation
                    ┌───────────────┼────────────────────┐
                    ▼               ▼                    ▼
          ┌──────────────┐  ┌───────────────┐   ┌──────────────────┐
          │ silica.Design│  │ KLayoutBackend│   │ (future)         │
          │ pure Python  │  │ live          │   │ OpenROAD /       │
          │ reference    │  │ pya.Layout,   │   │ Innovus /        │
          │ semantics    │  │ Region engine │   │ OpenAccess       │
          └──────────────┘  └───────────────┘   └──────────────────┘
```

Design decisions that make this real rather than aspirational:

- **Geometry crosses the interface as integer-DBU boxes.** No backend-native
  types leak upward; no floats exist to disagree between engines.
- **Net ids are opaque but stable.** The interpreter only compares them for
  equality, and never inspects them — but they appear in counterexamples, so a
  backend must derive them from geometry rather than from insertion order.
  Both shipped backends use the net's lowest shape corner (`m6@0,200`).
- **Measurements take their limit.** `width_violation(layer, win, limit)` lets a
  backend hand the query to a real DRC engine instead of computing a global
  minimum for the caller to compare. The KLayout adapter does exactly that.
- **`clone()`/`absorb()` are the transaction primitive.** Shadow-copy semantics
  are a backend obligation, so atomic rollback works identically on a dict of
  boxes and on a `pya.Layout`.
- **The corpus is the contract.** [`tests/conformance.py`](../tests/conformance.py)
  is one list of complete SILICA programs with expected per-tx verdicts, run
  against every backend. A new backend is done when it passes — no language
  change involved.

### Where the backends may legitimately differ

They must agree on the **verdict**. They may report a different
**measurement** on non-rectangular geometry: the reference engine reports the
narrowest box of its coalesced decomposition, KLayout the narrowest edge-pair
distance from its own width check. On an L-shaped shape those numbers differ
while both are below the limit — the transaction rolls back either way.

This is why the reference backend keeps geometry **maximally coalesced**: two
abutting boxes forming one wide wire would otherwise measure as two narrow
shapes on the reference engine and one wide shape on a merging backend, and the
same program would reach different verdicts. Coalescing removes that class of
disagreement rather than papering over it in the corpus.

## 6.1 Working on imported layout

`import` reads an existing layout — an OpenROAD result, say — onto declared
layers, and reports every layer in the file it did not take, so the subset
SILICA holds is never a surprise. `export` then refuses to stream an imported
design back out, because writing a subset as though it were a whole chip is the
same class of lie as dropping an unmapped layer.

Two things are worth knowing before trusting a check on imported geometry.

**Connectivity is exact.** Geometry arrives as rectangles — each layer merged,
then decomposed — and abutting rectangles are one component regardless of how
they were cut. Validated against real routed designs on sky130: OpenROAD's
`gcd` (13,291 shapes, both extractors say **712 nets**) and its
`aes_cipher_top` (37,788 cells, **540,071 shapes**, both extractors say
**18,396 nets**). At AES scale extraction takes 10.2 s in 0.8 GB, and a
transaction's shadow copy of the whole design takes 0.45 s.

**Width is not.** SILICA measures width per stored rectangle. When you author
geometry you choose that decomposition; when you import it, the source tool
chose it, and a band sliced across a wider shape reads as a narrow wire. On the
same routed design a declared `met1.width >= 140` rule fires on geometry
KLayout's own `width_check` says is clean — a false rollback, not a missed
violation, but wrong either way.

So a program working on imported layout should rely on `connectivity` and
declare no `rules` block, which is what `examples/sky130_eco.sil` does.
`tests/test_import.py` pins the failing case so it cannot quietly change, and
the fix is to give `width_violation` an exact rectilinear implementation rather
than a per-rectangle one.

## 7. Why every rule exists: the failure-class map

SILICA was distilled from real multi-day agentic tapeout campaigns — commercial
PDKs under NDA, plus an ASAP7 chiplet GPU — that each ran to dozens of signoff
iterations. Every strictness rule answers a failure that actually consumed a
round (full map: [`spec/invariants.md`](../spec/invariants.md)):

| Failure class | SILICA answer |
|---|---|
| Synthesis takes time units from the first-read library → a nanosecond clock becomes a microsecond one; hours of garbage | units are declared once; disagreeing libraries halt before any tool runs |
| The editor silently normalizes an inverted box → geometry lands outside the target and daisy-chains a column of nets | the `box` constructor rejects degenerate/inverted geometry, wherever computed |
| A hand-rolled edit guard has a cross-layer exemption bug → nets shorted, "success" reported | connectivity is a runtime primitive, not user code; deltas must be declared |
| The stream map lacks via rows → every via cut silently dropped; LVS sees opens | exporters are total: unmapped data aborts the export (today: a flow gate) |
| Labels placed on pad-body fragments bind to floating islands → a full LVS round lost | floating labels fail the tx at commit |
| Two ports aliased onto one net → LVS mismatch in a naive CDL | port aliases must be declared *(specified)* |
| A "connected" pin that ends short of its target, reported as routed success | `add ... on <net>` must touch exactly the named net; near misses are `floating`, not success |
| A repair that under-fills a notch and leaves the wire too narrow | width is re-measured after every edit, `sub` included |

## 8. Repo map and current state

```
SILICA/
├── README.md                    thesis + failure-class table
├── silica/
│   ├── interpreter.py           reference interpreter + pure-Python backend
│   ├── flow.py                  flow layer: step(), field-bug gates
│   ├── backends/klayout.py      live pya.Layout adapter (same protocol)
│   └── cli.py                   the `silica` command
├── spec/
│   ├── SPEC.md                  normative language definition
│   ├── grammar.ebnf             surface grammar
│   └── invariants.md            invariant library ↔ failure-class map
├── examples/
│   ├── fix_notch.sil            a notch repair done wrong, then right
│   ├── bridge_caught.sil        a bridging edit, caught at commit
│   ├── padframe_gen.sil         parametric pad row — a loop, not polygons
│   ├── asic_flow.sil            RTL→GDS→signoff as one program
│   └── add_pin_labels.py        deterministic GDS pin-label injector
├── tests/
│   ├── conformance.py           THE backend contract: shared program corpus
│   ├── test_core.py             corpus on the reference backend + engine checks
│   ├── test_backend_klayout.py  the same corpus on KLayout
│   ├── test_lang.py             language core + name strictness
│   ├── test_flow.py             flow layer and its gates
│   ├── test_cli.py              exit codes and structured errors
│   └── test_examples.py         every shipped example does what it says
├── eval/PLAN.md                 pre-registered replay of a logged campaign
└── docs/ARCHITECTURE.md         this document
```

| Layer | Status |
|---|---|
| Transform layer (language + tx + connectivity + width/space) | **implemented; 206 checks green on all three engines** |
| Backend protocol + pure-Python + KLayout backends | **implemented; shared conformance corpus** |
| Strict name resolution (no silent no-op checks) | **implemented** |
| Conditional rules (`m3.space(wide>W) >= S`) | in the grammar; **refused** until checked |
| Layout import (`import`) | **implemented**; connectivity exact, width not (§6.1) |
| Artifact totality (`export`) | **implemented**: refuses to write a design its map does not cover |
| `ports` / `density` invariants | specified; **refused** until checked |
| Goal layer (tactics, budgets, traces) | specified |
| Flow layer (declared steps, hashing, caching, gates) | **implemented subset** |
| Sandboxed flow steps (undeclared-input detection) | specified |

## 9. Summary

- SILICA runs with **zero AI in the loop**: deterministic parse → evaluate →
  shadow-tx → invariant check → atomic commit or counterexample.
- LLMs are the intended **authors** of SILICA programs; the runtime is the
  incorruptible **judge**. The counterexample channel is machine-readable so the
  judge's verdict feeds the agent's next step — but a human or a Makefile closes
  the loop just as well.
- The only place a model may sit *inside* the system is the goal layer's tactic
  chooser — and there it picks from a declared menu, under a declared budget,
  onto a replayable trace. Stochastic search, deterministic record.
