# SILICA language specification — v0.2

## 0. What kind of language this is

SILICA is a **full, general-purpose programming language** — functions,
control flow, integer/string/list values — with the design database and
transactional geometry edits as built-in effects, and it is **tool-agnostic**:
the interpreter speaks only an abstract backend protocol (§9). A padframe is a
loop that computes geometry; a DRC fix is a function; the same program runs
unchanged on the pure-Python engine or on KLayout.

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

General-purpose core:
- `let` / assignment, `if`/`else`, `while`, `for x in list`, `fn`/`return`,
  lists (`[..]`, indexing, `append`), strings (`+` concat, `str()`),
  booleans, `and/or/not` (or `&&/||/!`)
- builtins: `print len str abs min max range append box`
- **`/` divides exactly or errors** — SILICA never rounds a coordinate.
  `+` never coerces across types.
- declarations (`design`/`stack`/`rules`/`invariants`/`fn`) may not appear
  inside a `tx`; everything else composes freely, including loops and
  function calls inside `tx` bodies.

## 1. Design model

A SILICA program operates on exactly one **design database** (the *design*),
opened by a `design` declaration. All views (GDS, LEF abstract, netlist-for-LVS,
SPEF) are *derived* from the design by exporters; none may be authored
independently. The design carries:

- **geometry**: shapes on `(layer, datatype)` pairs, in integer database units
- **stack**: the declared conductor/via structure (which layers conduct, which
  connect which)
- **nets**: the connectivity partition induced by geometry × stack
- **labels**: text bindings of names to nets
- **instances**: placed macros (opaque cells with pin geometry)

## 2. Lexical & types

- Integers are database units (declared once: `units nm`). **There are no
  floats in geometry.** Off-grid is a type error at parse time
  (`grid 5` declares the manufacturing grid; all literals must be multiples).
- Types: `Layer`, `Box`, `Point`, `Net`, `Inst`, `Width`, `Dist`, `Time(ps)`,
  `Freq`, `Cap(fF)`. Unit-bearing types never implicitly convert.
  `Time` literals require a unit suffix; mixing units across declarations
  without an explicit `convert` is an error (answering the Genus first-lib
  units bug).
- `Box(x1,y1,x2,y2)` **requires** `x2>x1 ∧ y2>y1`. A degenerate or inverted
  box is a *parse/constructor error*, never normalized (answering the
  KLayout inverted-box short).

## 3. Declarations

```
design "harvester_chip.gds" top harvester_chip units nm grid 5

stack {
  metal m1 = (31,0)   metal m2 = (32,0)   metal m3 = (33,0)  metal m4 = (34,0)
  via   v1 = (51,0) connects (m1,m2)
  via   v2 = (52,0) connects (m2,m3)
  via   v3 = (53,0) connects (m3,m4)
}

rules {
  m3.width  >= 70      m3.space >= 70
  m3.space(wide>1650, prl>1650) >= 500
}

invariants { connectivity, ports }     // checked at every tx commit
```

`stack` is the single source of connectivity truth: the `connectivity`
invariant, net probes, and the LVS exporter all derive from it. There is no
way to give the extractor and the editor different models (answering the
generous-vs-Calibre model divergence).

## 4. Transactions (the transform layer)

```
tx fix_notch {
  add m3 box(749980, 518345, 750065, 518355) on net_at(m3, 749990, 518350)
  assert spacing(m3, window(749480,517845,750565,518855)) >= 70
}
```

Semantics:
1. The `tx` body executes on a **shadow copy** of the design.
2. `add ... on <net>`: the added shape, unioned with existing geometry, must
   touch **exactly the named net and nothing else**. Bridging two nets, or
   floating (touching none), is a commit failure. `on new_net` and
   `on merge(a,b)` express the two intents explicitly when they *are* wanted.
3. `sub` must not split its host net unless declared `splitting`.
4. Declared design invariants + body `assert`s are evaluated on the shadow.
5. All pass → atomic commit. Any fail → rollback; the tx **returns a
   Counterexample**: `{check, rule, box, nets, suggestion?}` — the same shape
   as a DRC results-database marker, so agent loops consume it directly.

Primitives: `add`, `sub`, `place <macro> at (x,y) [R0|R90|R180|R270|MX|MY]`,
`move <inst> by (dx,dy)`, `label <layer> "name" at (x,y)` (must attach to
metal; floating labels are commit failures — answering the ports-on-fragments
LVS round), `delete <inst>`.

## 5. Invariant library (runtime primitives, not user code)

- `connectivity` — the multiset of net components over the declared stack is
  unchanged, except as explicitly declared by `on new_net / merge / splitting`.
  (Field-proven: the conductor-count check caught every bridge across 40+
  signoff rounds; SILICA makes it non-optional and precise per-net.)
- `ports` — every declared port name binds to exactly one net; no two ports
  bind the same net unless declared `alias(a,b)` (the pwm_out/adc_gate case).
- `rules(local)` — width/space/enclosure checks evaluated in the halo of every
  shape the tx touched. Local ⇒ cheap ⇒ run on every commit; full signoff
  remains an exporter-side gate.
- `density(windowed)` — declared window/threshold checks per layer.
- `schema(artifact)` — exporters are total: any datum without a mapping rule
  (e.g., a via def with no stream-map row) aborts the export.

## 6. Goal layer (v0.2, specified)

```
goal close_timing(group alu_wb, target 1000ps) {
  tactics [ retime(u_alu), pipeline(wb,1), upsize(critical, max=3) ]
  budget 3 runs
}
```
A goal is satisfied by a **trace** — the ordered list of tactic applications
with their result hashes. Traces are recorded artifacts: replaying a trace on
the same input state is bit-identical (determinism of record, not of search).

## 7. Flow layer (v0.2, specified)

Steps are hermetic functions over typed artifacts with content-addressed
caching: `synth: rtl@src × libs@ccs → netlist@synth`. Tool wrappers pin
versions and seeds and declare their full input closure; an undeclared input
read is a sandbox error. Checkpoints are first-class (`resume from step@hash`).

## 8. Error model

Three severities, no fourth:
- **parse/type error** — the program never touches the design
- **commit failure** — tx rolled back, Counterexample returned (this is the
  *normal* agent feedback channel, not an exception)
- **integrity panic** — the design DB failed a self-check; execution halts
  (should be unreachable; existence of this class is what makes the other two
  trustworthy)

There is no warning class. Anything worth saying is worth failing on or
staying silent about — warnings are where silent coercions hide.

## 9. Backend protocol (tool-agnosticism)

The interpreter never manipulates geometry directly; it drives a backend
through this protocol (all geometry crosses the interface as integer-DBU
boxes):

```
declare_metal(name, l, d)      declare_via(name, l, d, ma, mb)
clone() -> backend             # shadow copy for tx execution
absorb(shadow)                 # commit: adopt the shadow's state
add(layer, box)  sub(layer, box)  add_label(layer, text, x, y)
on_metal(layer, x, y) -> bool
nets() -> partition            net_count() -> int
net_at(layer, x, y) -> id|None
nets_touching(layer, box) -> [id]
min_spacing(layer, win) -> num|None    min_width(layer, win) -> num|None
```

Net ids are opaque; the interpreter only compares them. Two backends ship
with the reference implementation:
- `silica.Design` — pure-Python engine (reference semantics)
- `backends/klayout_backend.py` — the same protocol over a live
  `pya.Layout`, with KLayout's Region engine doing merge/subtract/interaction

The self-test suite runs the same SILICA programs on both and requires
identical commit/rollback decisions. Adding an Innovus/OpenAccess backend is
implementing this protocol, not changing the language.
