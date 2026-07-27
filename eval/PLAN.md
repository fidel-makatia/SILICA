# SILICA evaluation plan

## Claim to test

An agent driving physical-design edits through SILICA reaches signoff-clean in
fewer tool rounds than the same agent driving raw scripts (KLayout pya + Tcl),
because the error classes that consumed rounds are caught at commit time with
machine-readable counterexamples.

## Benchmark: the harvester padframe campaign (ground truth exists)

The 2026-06/07 commercial-40nm harvester campaign is fully logged: 40+ Calibre rounds
core + 11 rounds padframe, every script and every DRC results-DB retained
(`~/HARVESTER_SOC/flow/`, olympus `harvester/signoff_chip/`). That gives an
exact per-round record of (a) the edit attempted, (b) the failure Calibre
found, (c) rounds until clean.

### Protocol

1. **Corpus extraction.** For each historical round, classify the failure:
   - `B` bridge / connectivity (guard bug, daisy-chain, inverted-interval)
   - `L` label/port binding (floating labels, alias)
   - `R` local rules (wide-metal tiers, max width, slot spacing)
   - `S` schema/export (map rows, merge collisions)
   - `D` density / fill legality
   - `X` genuinely global (antenna, full-chip density interactions) — SILICA
     does NOT claim these; they stay signoff-side.
2. **Replay arm A (baseline).** Historical record as-is: rounds-to-clean = 11
   (padframe), error-class sequence as logged.
3. **Replay arm B (SILICA).** Re-express each historical edit as a `tx`.
   Score: for each historical failure, does the v0.1 interpreter reject the
   faulty tx at commit (round saved) or would it have passed to Calibre
   (round spent)?
4. **Metrics.**
   - rounds-to-clean (primary)
   - % of historical failures caught at commit, by class
   - counterexample actionability: does `{check, rule, box, nets}` contain
     the coordinates the eventual human/agent fix actually used?

### Expected result (pre-registered)

Classes B, L, S are 100% catchable by v0.1 (connectivity/ports/schema
invariants). Class R needs the conditional-rule syntax (declared in grammar,
partially implemented). Class D needs windowed density (specified, not
implemented). Class X is out of scope by design. From the historical logs,
B+L+S ≈ 7 of the 11 padframe rounds → predicted rounds-to-clean ≈ 4.

## Secondary study: agent-in-the-loop

Give the same LLM agent the same task (add RF padframe to the harvester core)
twice: once with pya/Tcl tools, once with only the SILICA interpreter as its
edit tool. Count tool calls, wall-clock, and silent-failure incidents (edits
that "succeeded" but were later found wrong). This is the demo that matters
for the agentic-chip-design pitch.

## Non-goals

- Speed of the geometry engine (reference interpreter is O(n²); a KLayout
  Region backend is the production path).
- Routing/placement quality — SILICA constrains *edits*, it is not a router.
