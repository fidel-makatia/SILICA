"""SILICA flow layer -- hermetic-leaning tool steps as capability-gated builtins.

Installed into an `Interp` by the CLI's `--flow` flag:

  step(name, cmd, inputs, outputs) -> "ran" | "cached"
      Declared-input content hashing plus content-addressed caching. Refuses
      to run with a missing declared input; refuses to succeed with a missing
      declared output; a nonzero tool exit is a structured failure. Every run
      appends {step, cmd, input hashes, declared/produced outputs, seconds} to
      a JSONL trace -- the replayable record the flow layer is specified
      around.

  assert_lib_units(libfiles) -> unit string
      Every liberty file must declare the SAME time_unit. Encodes the
      first-read-liberty class of bug (a synthesis tool takes time units from
      whichever library it read first, so a 1 ns clock silently becomes 1 us
      and hours of synthesis produce garbage) as a hard, pre-tool error.

  assert_map_total(mapfile, layer_names) -> true
      Every named layer must have a stream-map row. Encodes the
      stream-out-silently-drops-unmapped-data class of bug (a missing via row
      drops every via cut from the GDS and LVS sees opens) as a hard, pre-tool
      error.

Honesty note: inputs are DECLARED, not sandbox-discovered. An undeclared input
read cannot yet be detected -- SPEC section 7 specifies the sandbox; this
subset hashes what the program declares, and the cache is only as sound as
that declaration. In particular, a script named inside `cmd` but absent from
`inputs` will not invalidate the cache when it changes: declare it. Failures
raise Counterexample -- the same machine-readable channel as tx rollbacks --
and halt the flow.
"""
import hashlib
import json
import os
import re
import subprocess
import time

from silica.interpreter import Counterexample, ParseError

# liberty files run to hundreds of MB; time_unit is declared in the header
_LIB_HEADER_BYTES = 200000


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _strlist(v, what):
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise ParseError("%s must be a list of path strings" % what)
    return v


class FlowRuntime:
    def __init__(self, trace_path="silica_flow_trace.jsonl"):
        self.trace_path = trace_path

    def _entries(self):
        out = []
        if os.path.exists(self.trace_path):
            with open(self.trace_path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        out.append(json.loads(line))
        return out

    def _cache_hit(self, name, cmd, ih, outputs):
        """A hit requires the same step, command and input hashes AND the same
        declared output set -- then every DECLARED output must exist with the
        recorded hash.

        Matching on the recorded outputs alone would report CACHED for a step
        whose declared outputs have since grown, and the new artifact would
        never be produced or checked: a silent success, which is the whole
        class of bug this layer exists to remove.
        """
        declared = sorted(outputs)
        for e in reversed(self._entries()):
            if (e["step"] != name or e["cmd"] != cmd or e["inputs"] != ih):
                continue
            if sorted(e.get("declared_outputs",
                            list(e["outputs"]))) != declared:
                continue
            produced = e["outputs"]
            return all(os.path.exists(p) and _sha(p) == produced.get(p)
                       for p in outputs)
        return False

    def step(self, name, cmd, inputs, outputs):
        if not isinstance(name, str) or not isinstance(cmd, str):
            raise ParseError(
                "step(name, cmd, inputs, outputs): name/cmd are strings")
        _strlist(inputs, "step inputs")
        _strlist(outputs, "step outputs")
        missing = [p for p in inputs if not os.path.exists(p)]
        if missing:
            raise Counterexample("flow.step", "missing-input", [], [],
                                 name + ": " + ", ".join(missing))
        ih = dict((p, _sha(p)) for p in inputs)
        if self._cache_hit(name, cmd, ih, outputs):
            print("[silica flow] %s: CACHED (inputs unchanged, "
                  "outputs verified)" % name)
            return "cached"
        print("[silica flow] %s: RUN  %s" % (name, cmd))
        t0 = time.time()
        rc = subprocess.call(cmd, shell=True)
        dt = int(time.time() - t0)
        if rc != 0:
            raise Counterexample("flow.step", "tool-exit", [], [],
                                 "%s: exit %d after %ds" % (name, rc, dt))
        miss_out = [p for p in outputs if not os.path.exists(p)]
        if miss_out:
            raise Counterexample("flow.step", "missing-output", [], [],
                                 name + ": tool exited 0 but did not produce: "
                                 + ", ".join(miss_out))
        oh = dict((p, _sha(p)) for p in outputs)
        with open(self.trace_path, "a") as f:
            f.write(json.dumps({"step": name, "cmd": cmd, "inputs": ih,
                                "declared_outputs": sorted(outputs),
                                "outputs": oh, "seconds": dt}) + "\n")
        print("[silica flow] %s: DONE in %ds" % (name, dt))
        return "ran"


def assert_lib_units(files):
    _strlist(files, "assert_lib_units files")
    seen = {}
    for p in files:
        if not os.path.exists(p):
            raise Counterexample("flow.lib-units", "missing-lib", [], [], p)
        with open(p, errors="ignore") as f:
            txt = f.read(_LIB_HEADER_BYTES)
        m = re.search(r'time_unit\s*:\s*"?([0-9]*\s*[munpf]?s)"?', txt)
        seen[p] = re.sub(r"\s+", "", m.group(1)) if m else "UNDECLARED"
    units = set(seen.values())
    if len(units) > 1 or "UNDECLARED" in units:
        raise Counterexample(
            "flow.lib-units", "unit-mismatch", [], [],
            "; ".join("%s=%s" % (os.path.basename(p), u)
                      for p, u in sorted(seen.items())))
    return units.pop()


def assert_map_total(mapfile, names):
    """Names may be plain layers ("M3": any row) or layer.kind pairs
    ("M3.NAME": a row of that object kind -- e.g. pin-text rows, whose absence
    silently drops every port label from the streamed GDS)."""
    _strlist(names, "assert_map_total names")
    if not os.path.exists(mapfile):
        raise Counterexample("flow.map-total", "missing-map", [], [], mapfile)
    rows, pairs = set(), set()
    with open(mapfile) as f:
        for ln in f:
            ln = ln.strip()
            if ln and not ln.startswith("#"):
                toks = ln.split()
                rows.add(toks[0])
                if len(toks) > 1:
                    pairs.add(toks[0] + "." + toks[1])
    missing = [n for n in names if n not in (pairs if "." in n else rows)]
    if missing:
        raise Counterexample("flow.map-total", "unmapped-layer", [], [],
                             "no stream-map rows for: " + ", ".join(missing))
    return True


def install(interp, trace_path="silica_flow_trace.jsonl"):
    rt = FlowRuntime(trace_path)
    interp.genv.define("step", rt.step)
    interp.genv.define("assert_lib_units", assert_lib_units)
    interp.genv.define("assert_map_total", assert_map_total)
    interp.genv.define("exists", os.path.exists)
    return rt
