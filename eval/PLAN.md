# SILICA evaluation

Three studies. The first has been run and its numbers are below and pinned by
`tests/test_eval.py`. The other two are pre-registered and **not yet run**.

> No process data appears here or will appear in any result. Published outputs
> are round counts and error-class histograms — never geometry, layer numbers,
> rule values or deck contents.

---

## Study 1 — bug-injection benchmark (run)

`python3 eval/benchmark.py`

Nine bugs drawn from the failure taxonomy in `spec/invariants.md`, attempted
through three arms:

- **raw** — edits applied with no checking
- **guarded** — a careful engineer's Python wrapper with asserts
- **SILICA** — the same edits as transactions

An arm "caught" a bug only if the **bad state never happened** — scored by a
ground-truth predicate over the resulting design or artifact, not by whether
the arm complained.

The guarded arm is deliberately strong. It is given SILICA's own connectivity
engine, so the comparison isolates *what a wrapper remembers to check* rather
than whose geometry code is better, and it checks bridging, floating, width
**and** spacing on everything it adds, plus a conductor count across
subtraction — the discipline these campaigns actually used.

### Result

```
  id   class            bug                                        raw      guarded  SILICA
  B1   connectivity     an added bar bridges two nets              ESCAPED  caught   caught
  B2   connectivity     a via cut bridges two nets across layers   ESCAPED  caught   caught
  B3   connectivity     a sub splits one net and deletes another   ESCAPED  ESCAPED  caught
  L1   label/port       a label lands on no metal                  ESCAPED  caught   caught
  R1   local rules      an added shape is under minimum width      ESCAPED  caught   caught
  R2   local rules      a sub thins a wire below minimum width     ESCAPED  ESCAPED  caught
  R3   local rules      an added shape violates minimum spacing    ESCAPED  caught   caught
  A1   atomicity        a composite edit half-applies              ESCAPED  ESCAPED  caught
  S1   artifact schema  stream-out drops an unmapped layer         ESCAPED  ESCAPED  caught

  raw 0/9      guarded 5/9      SILICA 9/9
```

### Reading it honestly

**A careful wrapper already catches five of the nine.** That is the headline,
and it is the number to quote. Most of the value in this project is discipline,
not syntax, and a library could deliver that much without a parser.

The four it misses are the interesting ones, because each needs machinery a
per-operation guard structurally cannot have:

| | Why a wrapper misses it |
|---|---|
| **B3** sub splits one net and deletes another | The guard uses a conductor **count**, the discipline these campaigns really used. Counts cancel: `+1` and `−1` net out to zero and the guard sees nothing. Catching it needs the partition, per net. |
| **R2** sub thins a wire | Guards are written per operation, and subtraction is the operation people under-guard — it feels like it only removes things. Catching it needs rules re-evaluated over whatever the transaction *left behind*. |
| **A1** composite edit half-applies | The first edit succeeded and the second failed. Without a transaction there is nothing to roll back to, and the design is left in a state no one authored. |
| **S1** stream-out drops a layer | The engineer asserts over the layer list they wrote down. The design is the thing that knows what it contains, so the totality obligation has to run from the design, not from the list. |

### What this study does not show

The scenarios were written by the same person who wrote the runtime. This is a
**demonstration of coverage against a stated taxonomy**, not an unbiased
estimate of field value, and it cannot be — the author knew every bug in
advance. Studies 2 and 3 exist because this one cannot settle the question.

---

## Study 2 — campaign replay (pre-registered, not run)

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

---

## Study 3 — agent-in-the-loop (pre-registered, not run)

Give the same LLM agent the same task twice — once with layout-editor Python and
Tcl, once with only the SILICA interpreter as its edit tool. Count tool calls,
wall-clock time, and *silent-failure incidents*: edits that reported success and
were later found wrong. The last metric is the one that matters for the agentic
pitch, and it is the one SILICA is designed to drive to zero.

## Non-goals

- Speed of the geometry engine. The reference interpreter is deliberately
  simple; a production backend is the performance path.
- Routing or placement quality. SILICA constrains *edits*; it is not a router.
