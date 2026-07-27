"""SILICA v0.3 flow layer -- hermetic-leaning tool steps as builtins.

Installs three capability-gated builtins into an Interp (CLI flag `--flow`):

  step(name, cmd, inputs, outputs) -> "ran" | "cached"
      Declared-input content hashing + content-addressed caching. Refuses to
      run with a missing declared input; refuses to succeed with a missing
      declared output; a nonzero tool exit is a structured failure. Every run
      appends {step, cmd, input hashes, output hashes, seconds} to a JSONL
      trace -- the replayable record the flow layer is specified around.

  assert_lib_units(libfiles) -> unit string
      All liberty files must declare the SAME time_unit. Encodes the Genus
      first-read-liberty bug (1 ns clock silently became 1 us; a 4-hour
      synthesis of garbage) as a hard, pre-tool error.

  assert_map_total(mapfile, layer_names) -> true
      Every named layer must have a stream-map row. Encodes the
      streamOut-silently-drops-unmapped-vias bug (every via cut vanished;
      LVS saw opens) as a hard, pre-tool error.

v0.3 honesty note: inputs are DECLARED, not sandbox-discovered. An undeclared
input read cannot yet be detected (SPEC §7 specifies the sandbox; this subset
hashes what is declared). Failures raise Counterexample -- same machine-
readable channel as tx rollbacks -- and halt the flow.
"""
import hashlib, json, os, re, subprocess, time

from silica.interpreter import Counterexample, ParseError


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

    def step(self, name, cmd, inputs, outputs):
        if not isinstance(name, str) or not isinstance(cmd, str):
            raise ParseError("step(name, cmd, inputs, outputs): name/cmd are strings")
        _strlist(inputs, "step inputs"); _strlist(outputs, "step outputs")
        missing = [p for p in inputs if not os.path.exists(p)]
        if missing:
            raise Counterexample("flow.step", "missing-input", [], [],
                                 name + ": " + ", ".join(missing))
        ih = dict((p, _sha(p)) for p in inputs)
        for e in reversed(self._entries()):
            if e["step"] == name and e["cmd"] == cmd and e["inputs"] == ih:
                if all(os.path.exists(p) and _sha(p) == h
                       for p, h in e["outputs"].items()):
                    print("[silica flow] %s: CACHED (inputs unchanged, "
                          "outputs verified)" % name)
                    return "cached"
                break
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
            txt = f.read(200000)
        m = re.search(r'time_unit\s*:\s*"([^"]+)"', txt)
        seen[p] = m.group(1) if m else "UNDECLARED"
    units = set(seen.values())
    if len(units) > 1 or "UNDECLARED" in units:
        raise Counterexample(
            "flow.lib-units", "unit-mismatch", [], [],
            "; ".join("%s=%s" % (os.path.basename(p), u)
                      for p, u in sorted(seen.items())))
    return units.pop()


def assert_map_total(mapfile, names):
    _strlist(names, "assert_map_total names")
    if not os.path.exists(mapfile):
        raise Counterexample("flow.map-total", "missing-map", [], [], mapfile)
    rows = set()
    with open(mapfile) as f:
        for ln in f:
            ln = ln.strip()
            if ln and not ln.startswith("#"):
                rows.add(ln.split()[0])
    missing = [n for n in names if n not in rows]
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
