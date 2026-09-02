# SILICA language specification — v0.4

> **All layer numbers, grids, coordinates and rule values in this document are
> placeholders chosen for readability.** They are not from any process design
> kit and correspond to no foundry's rule deck.

## 0. What kind of language this is

SILICA is a **full, general-purpose programming language** — functions, control
flow, integer/string/list values — with a design database and transactional
geometry edits as built-in effects, and it is **tool-agnostic**: the interpreter
speaks only an abstract backend protocol (§9). A pad row is a loop that computes
geometry; a DRC fix is a function; the same program runs unchanged on the
pure-Python engine or on KLayout.

```
fn pad(i, pitch) { return box(i*pitch, 0, i*pitch + 50000, 60000) }

tx place_pads {
  for i in range(0, n) {
    add m6 pad(i, pitch) on new_net
    label m6 "PAD_" + str(i) at (i*pitch + 25000, 30000)
  }
  assert spacing(m6, window(0, 0, n*pitch, 60000)) >= 100
}
```

General-purpose core:
- `let` / assignment, `if`/`else`, `while`, `for x in list`, `fn`/`return`,
  lists (`[..]`, indexing, `append`), strings (`+` concat, `str()`), booleans,
  `and/or/not` (or `&&/||/!`)
- builtins: `print len str abs min max range append box`
- **`/` divides exactly or errors** — SILICA never rounds a coordinate. `+`
  never coerces across types.
- declarations (`design`/`stack`/`rules`/`invariants`/`fn`) may not appear
  inside a `tx`; everything else composes freely, including loops and function
  calls inside `tx` bodies.

## 1. Design model

A SILICA program operates on exactly one **design database**, opened by a
`design` declaration. All views (GDS, LEF abstract, netlist-for-LVS, SPEF) are
*derived* from the design by exporters; none may be authored independently. The
design carries:

- **geometry**: shapes on `(layer, datatype)` pairs, in integer database units
- **stack**: the declared conductor/via structure — which layers conduct, which
  connect which
- **nets**: the connectivity partition induced by geometry × stack
- **labels**: text bindings of names to nets
- **instances**: placed macros (opaque cells with pin geometry) *(specified)*

## 2. Lexical & types

- Integers are database units (declared once: `units nm`). **There are no floats
  in geometry.** Off-grid is an error against the declared `grid`, whether the
  coordinate is written literally or computed.
- `box(x1,y1,x2,y2)` **requires** `x2>x1 ∧ y2>y1`. A degenerate or inverted box
  is a *constructor error*, never normalized.
- Comments are `//` to end of line. Strings are double-quoted with no escapes.
- Every error carries the source line it occurred on.

Planned (specified, not implemented): unit-bearing types `Time(ps)`, `Freq`,
`Cap(fF)` that never implicitly convert.

## 3. Declarations

```
design "chip.gds" top chip units nm grid 10

stack {
  metal m1 = (1,0)   metal m2 = (2,0)   metal m3 = (3,0)   metal m4 = (4,0)
  via   v1 = (101,0) connects (m1,m2)
  via   v2 = (102,0) connects (m2,m3)
  via   v3 = (103,0) connects (m3,m4)
}

rules {
  m3.width >= 100
  m3.space >= 100
}

invariants { connectivity }     // checked at every tx commit
```

`stack` is the single source of connectivity truth: the `connectivity`
invariant, net probes and the LVS exporter all derive from it. There is no way
to give the extractor and the editor different models.

Declarations are executed in program order, so a program may load a pre-existing
state and only then declare the rules its own edits must satisfy. That is the
normal shape of an ECO: you inherit a design, you do not re-qualify it.

**Every name must resolve.** A layer named in `add`, `sub`, `label`, `net_at`,
`assert` or `rules` must be declared in `stack`; `label` requires a *metal*
layer. An invariant name, rule kind or check name that the runtime does not
implement is refused at parse time — including names that are specified but not
yet implemented (§5). A declared check that silently does nothing is
indistinguishable from a check that passed, which is the failure mode this
language exists to remove.

## 4. Transactions (the transform layer)

```
tx fix_notch {
  add m3 box(800, 200, 900, 240) on net_at(m3, 400, 250)
  assert width(m3, window(700, 100, 1000, 400)) >= 100
}
```

Semantics:
1. The `tx` body executes on a **shadow copy** of the design.
2. `add ... on <net>`: the added shape, unioned with existing geometry, must
   touch **exactly the named net and nothing else**. Bridging two nets, or
   floating (touching none), is a commit failure. `on new_net` and
   `on merge(a,b)` express the two intents explicitly when they *are* wanted.
3. `sub` must not change the net count unless declared: `splitting` permits an
   increase, `deleting` permits a decrease. Both may be given.
4. Declared design invariants and body `assert`s are evaluated on the shadow.
5. All pass → atomic commit. Any fail → rollback; the tx returns a
   **Counterexample** `{check, rule, box, nets, note}` — the same shape as a DRC
   results-database marker, so agent loops consume it directly.

Primitives: `add`, `sub`, `label <layer> "name" at (x,y)` (must attach to
metal), `assert spacing|width (...) >= expr`. Specified, not implemented:
`place <macro> at (x,y) [R0|R90|R180|R270|MX|MY]`, `move <inst> by (dx,dy)`,
`delete <inst>`.

## 5. Invariant library

Implemented as a runtime primitive:

- `connectivity` — every change to the net partition must be declared, and it
  is checked **structurally, per net**, not as a count.

  At each `sub`, every surviving component is correlated back to the net it came
  from (any point of retained metal lay inside the pre-state), and each pre-net
  is classified *survived*, *split* or *deleted*. A split requires `splitting`;
  a deletion requires `deleting`; the two are checked independently. At commit,
  the count is reconciled against the declared adds (`+1` per `on new_net`,
  `−1` per `merge(a,b)`) and the classified subtractions.

  A count alone is unsound, because scalars cancel: one subtraction that splits
  one net in two while deleting another leaves the count unchanged, and a
  count-based invariant would declare nothing and commit. See
  `tests/conformance.py`, "a split that cancels a delete in the count".

Also implemented, and always on when declared in `rules`:

- `width` / `space` minima, evaluated on the final shadow in the halo of every
  shape the transaction touched — **`add` and `sub` alike**, so a subtraction
  that thins a wire below the minimum fails exactly like an undersized add.

Specified, **not** implemented — declaring one of these is a hard error rather
than a no-op, and it will become legal only when it is checked:

- `ports` — every declared port name binds to exactly one net; no two port
  names bind the same net unless declared `alias(a,b)`; every port's label sits
  on metal of that net.
- `density(windowed)` — declared per-layer window/threshold density checks.
- `schema(artifact)` — exporters are total: any datum without a mapping rule
  aborts the export.
- conditional rules — `m3.space(wide>W, prl>P) >= S`, the wide-metal spacing
  tiers.

## 6. Goal layer (specified)

```
goal close_timing(group alu_wb, target 1000ps) {
  tactics [ retime(u_alu), pipeline(wb,1), upsize(critical, max=3) ]
  budget 3 runs
}
```
A goal is satisfied by a **trace** — the ordered list of tactic applications
with their result hashes. Traces are recorded artifacts: replaying a trace on
the same input state is bit-identical. Determinism of record, not of search.

## 7. Flow layer (implemented subset)

Steps are functions over declared artifacts with content-addressed caching:

```
step(name, cmd, inputs, outputs) -> "ran" | "cached"
```

A step refuses to run with a missing declared input, refuses to succeed with a
missing declared output, treats a nonzero exit as a structured failure, and
appends every run to a JSONL trace. A cache hit requires the same step name,
command, input hashes **and declared output set**, and then re-verifies every
declared output against its recorded hash.

Specified, not implemented: sandboxed execution, so that an *undeclared* input
read is an error. Until then the cache is exactly as sound as the declaration —
a file the step reads but does not declare will not invalidate it.

Two pre-tool gates ship with the layer: `assert_lib_units(libs)` and
`assert_map_total(mapfile, names)` (§ `spec/invariants.md`).

## 8. Error model

Three classes, no fourth:

- **program error** — syntax, type, or an unresolved name. The design is never
  touched. Carries a line number and a machine-readable payload; the CLI prints
  `ERROR {json}` and exits 2.
- **commit failure** — the program is well-formed but the edit is illegal in
  this design state. The tx rolls back atomically and returns a Counterexample.
  This is the *normal* agent feedback channel, not an exception; the CLI exits 0.
- **integrity panic** — the design DB failed a self-check; execution halts.
  Should be unreachable; the existence of this class is what makes the other two
  trustworthy.

A flow-layer gate or step failure raises a Counterexample outside any tx and
halts the program; the CLI exits 1.

**There is no warning class.** Anything worth saying is worth failing on or
staying silent about — warnings are where silent coercions hide.

## 9. Backend protocol (tool-agnosticism)

The interpreter never manipulates geometry directly. All geometry crosses the
interface as integer-DBU boxes:

```
declare_metal(name, num, dtype)     declare_via(name, num, dtype, a, b)
clone() -> backend                  # shadow copy for tx execution
absorb(shadow)                      # commit: adopt the shadow's state
add(layer, box)  sub(layer, box)  add_label(layer, text, x, y)
on_metal(layer, x, y) -> bool
nets() -> {net_id: members}         net_count() -> int
net_at(layer, x, y) -> net_id | None
nets_touching(layer, box) -> [net_id]
spacing_violation(layer, win, limit) -> measured | None
width_violation(layer, win, limit)   -> measured | None
```

Net ids are opaque — the interpreter only compares them for equality — but a
backend must make them **stable** (independent of insertion order and of shape
indices) and printable, because they appear in counterexamples. Both shipped
backends derive an id from the net's lowest shape corner, e.g. `m6@0,200`.

The two measurements take the limit they are judged against, so a backend built
on a real DRC engine can hand the query to it rather than computing a global
minimum for the caller to compare. They return the worst measurement strictly
below `limit`, or `None`.

Backends must agree on the **verdict**. The reported *measurement* may differ
on non-rectangular geometry — the reference engine reports the narrowest box of
its coalesced decomposition, KLayout the narrowest edge-pair distance from its
own width check. `tests/conformance.py` is the normative contract: one list of
complete SILICA programs, run against every backend, compared on verdict.

Two backends ship:
- `silica.Design` — pure-Python reference semantics; keeps geometry maximally
  coalesced so measurements see the same decomposition a merging backend sees
- `silica/backends/klayout.py` — the same protocol over a live `pya.Layout`

Adding an OpenROAD, Innovus or OpenAccess backend is implementing this protocol
and passing the corpus. It is not a language change.
