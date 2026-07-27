# The standard invariant library — and the field bugs each one answers

Every invariant in SILICA is a runtime primitive, not user code. This file maps
each one to the real failure (from a commercial-40nm harvester SoC and an ASAP7 GPU
campaigns, 2026-06/07) that motivated it. The rule: **if a bug class cost a
signoff round, it becomes either inexpressible or a hard error.**

## 1. `connectivity`

**Statement.** At tx commit, the number of nets (connected components of
geometry × declared stack) equals the pre-tx count plus exactly the declared
delta: +1 per `on new_net`, −1 per `merge(a,b)`, +k as measured for each
`sub ... splitting`.

**Enforced by.** Union-find over shape adjacency per metal, linked through
declared vias; recomputed on the shadow copy at commit.

**Field bugs answered.**
- *Guard cross-layer exemption bug* (harvester padframe, round 7): a hand-rolled
  KLayout edit guard exempted same-net checks across layers and silently
  bridged 4 nets. Caught only because a separate flat conductor-count
  invariant (14718) was being tracked by hand. SILICA makes that count
  non-optional and per-net precise.
- *Pad daisy-chaining* (round 5): a slot pattern emitted an inverted interval;
  KLayout normalized it into a box OUTSIDE the pad that chained the W-column
  nets together. Two independent defenses here: Box constructor rejects the
  inverted interval (§2 of SPEC), and even if geometry were legal, the add
  would touch two nets → bridge rollback.

## 2. `ports`

**Statement.** Every declared port name binds to exactly one net; no two port
names bind the same net unless declared `alias(a,b)`; every port's label sits
on metal of that net.

**Field bugs answered.**
- *Ports on fragments* (harvester LVS): pad bodies were electrical fragments
  under Calibre's via-57 mapping, so labels placed on pad centers bound to
  floating islands. Fix took a full round (labels moved to router wires at DEF
  first-ROUTED points). In SILICA a floating label is a commit failure with
  the offending net in the counterexample.
- *`assign pwm_out = adc_gate`* alias: two ports on one net is legal in
  Verilog, an LVS mismatch in a naive CDL. SILICA requires the alias to be
  declared, and the exporter emits the `.CONNECT` from the declaration.

## 3. `rules(local)`

**Statement.** Declared width/space/enclosure minima are evaluated in the halo
of every shape the tx touched. Local ⇒ cheap ⇒ every commit. Full-chip DRC
remains an exporter-side gate; this invariant exists to kill the *iteration
loop*, not replace signoff.

**Field bugs answered.**
- *Wide-metal spacing tiers* (M3/M4.S.3, S.2.x tiers): bars added at 8.4 µm
  width violated the 4.95 µm max and the wide-spacing tiers — discovered two
  Calibre rounds later. With conditional rules declared
  (`m3.space(wide>1650, prl>300) >= 500`), the tx that adds the bar fails
  immediately with the measured pair in the counterexample.

## 4. `density(windowed)`

**Statement.** Declared per-layer window/threshold density checks
(e.g. `m1.density(window 125um) >= 10%`) evaluated over windows intersecting
the tx's touched region.

**Field bugs answered.**
- *Dummy-fill campaign* (harvester core): OD/PO/M1–M4 density windows each
  discovered by a failing Calibre round; forbidden fill layers (37;1/38;1,
  NOUSEM7/8) discovered the same way. Declared once, checked at edit time.

## 5. `schema(artifact)` — exporters are total

**Statement.** An exporter (GDS stream-out, CDL, LEF) must have a mapping rule
for every datum it encounters. Unmapped data aborts the export; there is no
"skip silently."

**Field bugs answered.**
- *Missing `VIA` rows in the Innovus stream map* (SenseEdge → harvester): every
  via cut in routed nets was silently dropped from the GDS; LVS saw opens.
  Root-caused only by diffing conductor counts. A total-schema exporter
  refuses to stream a net whose via has no map row.
- *`streamOut -merge` destroying same-named VIA cells*: two sources of truth
  for one cell name. SILICA has one design DB; merged-in macros are opaque
  instances with collision-checked names.

## 6. Constructor/type errors (parse layer, before any invariant runs)

- **Inverted/degenerate `Box`** → constructor error. (KLayout normalization
  short, above.)
- **Off-grid literal** → parse error against declared `grid`. (Multiple pad
  lattice iterations off the 5 nm grid.)
- **Unit mixing without `convert`** → type error. (Genus took time units from
  the *first-read* liberty: an ns-unit SRAM lib listed first turned
  `create_clock -period 1000` into 1 µs — a full 4-hour synthesis job of
  garbage, Aurora job 512790.)

## The error-model consequence

There is no warning class (SPEC §8). Every bug above was, in its native tool,
either silent or a warning that scrolled past. The campaigns got to DRC-0 /
LVS-CORRECT by *manually* re-imposing hard-fail discipline (flat conductor
counts, md5 gates, assert-before-emit in scripts). SILICA's thesis is that the
language should impose it for you.
