# The invariant library — and the failure class each one answers

Every invariant in SILICA is a runtime primitive, not user code. This file maps
each one to the real failure that motivated it. The rule for adding one: **if a
bug class cost a signoff round, it becomes either inexpressible or a hard
error.**

> **Note on process data.** These entries describe *failure classes* observed
> while driving commercial and open PDK flows. No process data appears here:
> every layer name, number and value below is a placeholder, and nothing
> reproduces any foundry's rule deck.

---

## 1. `connectivity` — implemented

**Statement.** Every change to the net partition must be declared, and is
checked per net rather than as a total. `on new_net` declares a creation,
`merge(a,b)` a union, `sub ... splitting` a split, `sub ... deleting` a
removal. An undeclared change to the partition fails the transaction.

**Enforced by.** Union-find over shape adjacency per metal, linked through
declared vias. At every `add`, the shape's touched-net set is checked against
what was declared. At every `sub`, each surviving component is correlated back
to the net it came from — any point of retained metal lay inside the pre-state
— so each pre-net is classified survived, split or deleted, and each outcome is
required to have been declared on its own.

**Why not a count.** The first implementation compared net counts before and
after, which is unsound: scalars cancel. A single subtraction that split one net
in two while deleting another left the count unchanged, declared nothing, and
committed. Counting is an approximation of the invariant; the invariant is about
the *partition*.

**Failure classes answered.**

- *Edit guard with a cross-layer exemption bug.* A hand-rolled layout-editor
  guard exempted same-net checks across layers and silently bridged several
  nets while reporting success. It was caught only because a separate flat
  conductor count was being tracked by hand, several rounds later. SILICA makes
  that count non-optional and per-net precise, and names the bridged nets in
  the counterexample.
- *Daisy-chaining through a normalized box.* A slot pattern emitted an inverted
  interval; the editor normalized it into a box outside the intended shape,
  chaining a column of nets together. Two independent defences here: the `box`
  constructor rejects the inverted interval, and even if the geometry were
  legal the add would touch two nets and roll back.
- *The cancelling edit.* One subtraction that both splits a net and removes
  another leaves the conductor count untouched. Every count-based guard — the
  hand-maintained flat conductor count included — is blind to it by
  construction. This is the class that motivated moving from a scalar delta to
  a per-net effect.
- *The "connected" pin that wasn't.* A router reported a pin as routed while
  the wire ended a few hundred nanometres short. `add ... on net_at(...)`
  resolves a point by exact containment: a point one database unit outside a
  shape resolves to nothing, and the edit fails as `floating` or `no-net`
  rather than succeeding. Both shipped backends are tested at one-DBU
  precision for exactly this.

## 2. `rules(local)` — width/space implemented, tiers specified

**Statement.** Declared width/space minima are evaluated on the final shadow, in
the halo of every shape the transaction touched. Local ⇒ cheap ⇒ run on every
commit. Full-chip DRC remains an exporter-side gate; this invariant exists to
kill the *iteration loop*, not to replace signoff.

**Failure classes answered.**

- *A repair that under-fills.* A patch aimed at a notch filled only part of it
  and left the wire below minimum width. Connectivity was fine, the shape was
  visibly "there", and DRC found it a round later. Width is therefore
  re-measured after **every** edit — including `sub`, because a subtraction can
  thin a wire exactly as easily as an undersized `add` can place one.
  (`examples/fix_notch.sil` runs both the wrong repair and the right one.)
- *Wide-metal spacing tiers.* Bars added above a layer's maximum width violated
  the wide-metal spacing tiers, discovered two signoff rounds later. This needs
  conditional rules — `m3.space(wide>W, prl>P) >= S`. They are in the grammar
  and **not** checked, so the runtime refuses to accept one rather than
  pretending to enforce it.

## 3. `ports` — specified, not implemented

**Statement.** Every declared port name binds to exactly one net; no two port
names bind the same net unless declared `alias(a,b)`; every port's label sits on
metal of that net.

**Failure classes answered.**

- *Ports on fragments.* Pad bodies were electrical fragments under the signoff
  deck's via mapping, so labels placed at pad centres bound to floating islands
  and LVS failed. The fix took a full round. The floating-label half of this is
  already enforced today: `label` must attach to metal or the transaction fails.
- *Aliased ports.* Two port names on one net is legal in Verilog and an LVS
  mismatch in a naive CDL. SILICA will require the alias to be declared and the
  exporter to emit the connection from that declaration.

## 4. `density(windowed)` — specified, not implemented

**Statement.** Declared per-layer window/threshold density checks evaluated over
windows intersecting the transaction's touched region.

**Failure classes answered.**

- *The dummy-fill campaign.* Per-layer density windows were each discovered by a
  failing signoff round, as were the layers on which fill was forbidden.
  Declared once, they would be checked at edit time instead.

## 5. `schema(artifact)` — implemented, as the `export` obligation

**Statement.** An exporter (GDS stream-out, CDL, LEF) must have a mapping rule
for every datum it encounters. Unmapped data aborts the export; there is no
"skip silently".

**Failure classes answered.**

- *Missing via rows in the stream map.* Every via cut in routed nets was
  silently dropped from the streamed GDS and LVS saw opens; root-caused only by
  diffing conductor counts.
- *Missing text rows in the stream map.* The place-and-route tool streamed zero
  pin text, and a run of consecutive LVS results reported zero ports against a
  netlist with hundreds. It was a warning, in a log that scrolls.
- *Stream-out merge destroying same-named cells.* Two sources of truth for one
  cell name. SILICA has one design database; merged-in macros are opaque
  instances with collision-checked names.

The `export` statement enforces this for designs SILICA writes itself: it
proves its map covers every layer holding geometry and every layer carrying
labels *before* writing a byte, names what it would otherwise have dropped, and
leaves no file behind on failure (`tests/test_export.py`).

For artifacts written by somebody else's tool, the **flow layer** enforces the
same property from outside: `assert_map_total(mapfile, names)` refuses to
proceed unless every named layer — and every `layer.kind` pair, such as the
text rows above — has a stream-map row (`tests/test_flow.py`).

## 6. Constructor and type errors — implemented

These run before any invariant, and the design is never touched:

- **Inverted or degenerate `box`** → constructor error, never normalization.
- **Off-grid coordinate** → error against the declared `grid`, whether written
  literally or computed several function calls deep.
- **Inexact division** → error. `7 / 2` does not silently become `3`; SILICA
  never rounds a coordinate.
- **Cross-type `+`** → error. `+` never coerces.
- **Unresolved name** → error. A layer that is not in `stack`, an invariant or
  rule kind or check the runtime does not implement, or a `label` aimed at a via
  layer, all refuse the program. This is the guard against the quietest failure
  of all: a declared check that does nothing.
- **Mixed units** → the flow gate `assert_lib_units(libs)` refuses a library set
  whose `time_unit` declarations disagree, or in which any library fails to
  declare one. Synthesis tools have taken time units from whichever library
  they read first, turning a nanosecond clock constraint into a microsecond one
  and producing hours of plausible-looking garbage.

## The error-model consequence

There is no warning class (SPEC §8). Every failure above was, in its native
tool, either silent or a warning that scrolled past. Those campaigns reached
clean signoff by *manually* re-imposing hard-fail discipline — flat conductor
counts, hash gates, assert-before-emit in wrapper scripts. SILICA's thesis is
that the language should impose it for you.
