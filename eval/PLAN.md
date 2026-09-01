# SILICA evaluation plan

*Pre-registered: written before the experiment, so the prediction can be wrong
in public.*

> No process data appears in this plan or will appear in its results. Published
> outputs are round counts and error-class histograms — never geometry, layer
> numbers, rule values or deck contents.

## Claim to test

An agent driving physical-design edits through SILICA reaches signoff-clean in
fewer tool rounds than the same agent driving raw scripts (layout-editor Python
plus Tcl), because the error classes that consumed rounds are caught at commit
time with machine-readable counterexamples.

## Benchmark: a logged padframe campaign (ground truth exists)

The campaign is fully logged: every script and every DRC results database was
retained, giving an exact per-round record of (a) the edit attempted, (b) the
failure the signoff tool found, (c) rounds until clean. That log is the
baseline; it is not redistributable, so the published artifact is the
classification and the counts, not the source material.

### Protocol

1. **Corpus extraction.** Classify each historical round's failure:
   - `B` bridge / connectivity (guard bug, daisy-chain, inverted interval)
   - `L` label / port binding (floating labels, alias)
   - `R` local rules (wide-metal tiers, max width, notch/width after edit)
   - `S` schema / export (map rows, merge collisions)
   - `D` density / fill legality
   - `X` genuinely global (antenna, full-chip density interactions) — SILICA
     does **not** claim these; they stay signoff-side.
2. **Arm A (baseline).** The historical record as-is: rounds-to-clean and the
   error-class sequence, as logged.
3. **Arm B (SILICA).** Re-express each historical edit as a `tx`. Score: for
   each historical failure, does the interpreter reject the faulty transaction
   at commit (round saved) or would it have passed to signoff (round spent)?
4. **Metrics.**
   - rounds-to-clean (primary)
   - share of historical failures caught at commit, by class
   - counterexample actionability: does `{check, rule, box, nets}` contain the
     coordinates the eventual fix actually used?

### Expected result (pre-registered)

Classes `B`, `L` and `R` are catchable today: connectivity is a runtime
primitive, floating labels fail the transaction, and width/space are re-checked
after every edit including subtraction. Class `S` is caught from outside the
tool by the flow layer's map-totality gate rather than by an exporter. Class `D`
needs windowed density, which is specified and not implemented. Class `X` is out
of scope by design.

From the historical logs, `B + L + R + S` accounts for roughly two thirds of the
padframe rounds, so the prediction is **rounds-to-clean falls by about half**.

Recording the prediction matters more than its accuracy: if the caught-at-commit
share is high and rounds-to-clean barely moves, the interesting result is that
the bottleneck was never the error classes SILICA targets.

## Secondary study: agent-in-the-loop

Give the same LLM agent the same task twice — once with layout-editor Python and
Tcl, once with only the SILICA interpreter as its edit tool. Count tool calls,
wall-clock time, and *silent-failure incidents*: edits that reported success and
were later found wrong. The last metric is the one that matters for the agentic
pitch, and it is the one SILICA is designed to drive to zero.

## Non-goals

- Speed of the geometry engine. The reference interpreter is deliberately
  simple; a production backend is the performance path.
- Routing or placement quality. SILICA constrains *edits*; it is not a router.
