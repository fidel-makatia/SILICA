#!/usr/bin/env python3
"""Validate SILICA against KLayout on every routed design it can find.

For each finished OpenROAD result this derives the routing stack from the
platform's own KLayout layer-properties file, imports the layout into SILICA,
and compares two things against KLayout on identical geometry:

  nets   SILICA's union-find over the declared stack vs KLayout's
         LayoutToNetlist extractor, flattened
  width  SILICA's projection-metric width check vs Region.width_check under
         the same metric

Agreement on the VERDICT is the contract. Run:

    python3 eval/validate_designs.py [ORFS_FLOW_DIR]
"""
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

from silica import Box, Design                       # noqa: E402
from silica.importer import read_layout              # noqa: E402

try:
    import klayout.db as pya
except ImportError:
    print("this harness needs the klayout module (pip install klayout)")
    sys.exit(2)

# metals whose names carry no index, and the order they sit in
SPECIAL_METALS = {"li1": 0, "li": 0}
# vias whose names carry no index: name -> the metal index below them
SPECIAL_VIAS = {"mcon": 0, "via": 1, "licon1": -1, "cont": -1}


def parse_lyp(path):
    """name.purpose -> (layer, datatype), from a KLayout layer-properties file."""
    txt = open(path, errors="ignore").read()
    out = {}
    for m in re.finditer(r"<name>([^<]*)</name>\s*<source>(\d+)/(\d+)", txt):
        out[m.group(1).split(" - ")[0]] = (int(m.group(2)), int(m.group(3)))
    return out


def parse_lyt(path):
    """KLayout tech file: <layer-map>layer_map(\'M1 : 19/0\';...)</layer-map>."""
    txt = open(path, errors="ignore").read()
    out = {}
    for blk in re.findall(r"<layer-map>(.*?)</layer-map>", txt, re.S):
        for m in re.finditer(r"'([^':]+?)\s*:\s*(\d+)/(\d+)'", blk):
            out.setdefault(m.group(1).strip() + ".drawing",
                           (int(m.group(2)), int(m.group(3))))
    return out


def parse_layermap(path):
    """Stream map: `Name Kind layer datatype` per row, as fed to stream-out."""
    out = {}
    with open(path, errors="ignore") as f:
        for line in f:
            t = line.split()
            if len(t) >= 4 and t[2].isdigit() and t[3].isdigit():
                out.setdefault(t[0] + ".drawing", (int(t[2]), int(t[3])))
    return out


def layer_sources(platform_dir):
    """Every file that could name this platform's GDS layers, best first.

    Platforms disagree about where this lives: a KLayout tech file with an
    inline layer_map, a stream map fed to stream-out, or layer-properties.
    Try all of them rather than assume one.
    """
    found = []
    for root, _d, files in os.walk(platform_dir):
        for f in files:
            full = os.path.join(root, f)
            if f.endswith(".lyt"):
                found.append((0, parse_lyt, full))
            elif f.endswith(".layermap") or f.endswith(".map"):
                found.append((1, parse_layermap, full))
            elif f.endswith(".lyp"):
                found.append((2, parse_lyp, full))
    return [(fn, p) for _r, fn, p in sorted(found, key=lambda x: x[0])]


def derive_stack(lyp):
    """(metals, vias) as ordered [(name, layer, dtype)] / [(name, l, d, a, b)].

    Metals are `<word><n>.drawing`, ordered by n; a via with index k joins
    metal k and metal k+1. Purely nominal, but it is the same stack given to
    both engines, which is what the comparison tests.
    """
    metals, vias = {}, {}
    for full, (num, dt) in lyp.items():
        if "." not in full:
            # some platforms ship a .lyp with bare layer names and no purpose
            name, purpose = full, "drawing"
        else:
            name, purpose = full.rsplit(".", 1)
        if purpose != "drawing":
            continue
        low = name.lower()
        if low in SPECIAL_METALS:
            metals[SPECIAL_METALS[low]] = (name, num, dt)
            continue
        if low in SPECIAL_VIAS:
            vias[SPECIAL_VIAS[low]] = (name, num, dt)
            continue
        m = re.fullmatch(r"(met|metal|m|tm)(\d+)[a-z]*", low)
        if m:
            metals.setdefault(int(m.group(2)), (name, num, dt))
            continue
        m = re.fullmatch(r"(via|v)(\d+)[a-z]*", low)
        if m:
            vias.setdefault(int(m.group(2)), (name, num, dt))
    if len(metals) < 2:
        return None, None
    order = sorted(metals)
    pos = {k: i for i, k in enumerate(order)}
    ms = [metals[k] for k in order]
    vs = []
    for k in sorted(vias):
        if k in pos and (k + 1) in pos:
            a, b = metals[k][0], metals[k + 1][0]
        elif k == 1 and order[0] == 0 and 1 in pos:
            a, b = metals[order[0]][0], metals[1][0]
        else:
            continue
        vs.append(vias[k] + (a, b))
    return ms, vs


def _unstacked(rows, platform, designs, why):
    """Record, loudly, that a platform could not be checked.

    Dropping it from the table would be the exact failure this project exists
    to remove: a check that quietly did nothing, indistinguishable from one
    that passed.
    """
    print("  %-11s %-16s  NO STACK: %s" % (platform, "(%d designs)"
                                           % len(designs), why), flush=True)
    for d in designs:
        rows.append({"platform": platform, "design": d, "shapes": None,
                     "silica_nets": None, "klayout_nets": None,
                     "verdict": "NO-STACK", "width_agree": False,
                     "def_nets": None, "note": why})


def klayout_nets(gds, top, metals, vias):
    ly = pya.Layout()
    ly.read(gds)
    cell = ly.cell(top) or ly.top_cell()
    l2n = pya.LayoutToNetlist(pya.RecursiveShapeIterator(ly, cell, []))
    lay = {}
    for (n, num, dt) in metals + [(v[0], v[1], v[2]) for v in vias]:
        li = ly.find_layer(num, dt)
        if li is None:
            li = ly.layer(num, dt)
        lay[n] = l2n.make_polygon_layer(li, n)
    for (n, _, _) in metals:
        l2n.connect(lay[n])
    for (n, _, _, a, b) in vias:
        l2n.connect(lay[n])
        l2n.connect(lay[a], lay[n])
        l2n.connect(lay[n], lay[b])
    l2n.extract_netlist()
    nl = l2n.netlist()
    nl.flatten()
    return sum(1 for _ in nl.circuit_by_name(cell.name).each_net()), ly, cell


def klayout_width(ly, cell, num, dt, limit):
    li = ly.find_layer(num, dt)
    if li is None:
        return False
    reg = pya.Region(cell.begin_shapes_rec(li)).merged()
    return reg.width_check(limit, False, pya.Metrics.Projection).count() > 0


def def_nets(defpath):
    if not os.path.exists(defpath):
        return None
    with open(defpath, errors="ignore") as f:
        for line in f:
            if line.startswith("NETS "):
                return int(line.split()[1])
    return None


# A GDS far above this is skipped by default: reading one costs tens of GB and
# tens of minutes, which is a different experiment from "do the two engines
# agree". Raise it with --max-gb if that is the experiment you want.
DEFAULT_MAX_GB = 0.5


def run_isolated(flow, platform, design, timeout):
    """Check one design in a child process.

    A design large enough to be OOM-killed takes the whole run with it, and a
    SIGKILL leaves no traceback -- the harness just stops, with a partial table
    that looks complete. One process per design turns that into a recorded
    CRASHED row instead of silence.
    """
    key = "%s/%s" % (platform, design)
    try:
        p = subprocess.run(
            [sys.executable, os.path.abspath(__file__), flow, "--only=" + key],
            capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"platform": platform, "design": design, "verdict": "TIMEOUT",
                "shapes": None, "silica_nets": None, "klayout_nets": None,
                "width_agree": False, "def_nets": None,
                "note": "exceeded %ds" % timeout}
    for line in p.stdout.splitlines():
        if line.startswith("{"):
            return json.loads(line)
    return {"platform": platform, "design": design, "verdict": "CRASHED",
            "shapes": None, "silica_nets": None, "klayout_nets": None,
            "width_agree": False, "def_nets": None,
            "note": "exit %d: %s" % (p.returncode,
                                     (p.stderr or "no output").strip()
                                     .splitlines()[-1][:120]
                                     if p.stderr else "killed, no output")}


def main(flow, max_gb=DEFAULT_MAX_GB, only=None, timeout=1800):
    plats = os.path.join(flow, "platforms")
    results = os.path.join(flow, "results")
    rows = []
    for platform in sorted(os.listdir(results)):
        pdir = os.path.join(results, platform)
        designs = [d for d in sorted(os.listdir(pdir))
                   if os.path.exists(os.path.join(pdir, d, "base",
                                                  "6_final.gds"))]
        metals = vias = None
        tried = []
        for fn, src in layer_sources(os.path.join(plats, platform)):
            tried.append(os.path.basename(src))
            try:
                m, v = derive_stack(fn(src))
            except Exception:                          # noqa: BLE001
                continue
            if m and len(m) >= 2 and v:
                metals, vias, used = m, v, os.path.basename(src)
                break
        if not metals:
            _unstacked(rows, platform, designs,
                       "no routing stack derivable from %s"
                       % (", ".join(tried[:4]) or "any file"))
            continue
        print("  %-11s stack from %s: %d metals, %d vias"
              % (platform, used, len(metals), len(vias)), flush=True)
        for design in sorted(os.listdir(os.path.join(results, platform))):
            base = os.path.join(results, platform, design, "base")
            gds = os.path.join(base, "6_final.gds")
            if not os.path.exists(gds):
                continue
            gb = os.path.getsize(gds) / 1e9
            if gb > max_gb:
                print("  %-11s %-16s  SKIPPED (%.1f GB > --max-gb %.1f)"
                      % (platform, design, gb, max_gb), flush=True)
                rows.append({"platform": platform, "design": design,
                             "shapes": None, "silica_nets": None,
                             "klayout_nets": None, "verdict": "SKIPPED",
                             "width_agree": False, "def_nets": None,
                             "note": "%.1f GB" % gb})
                continue
            if only and only != "%s/%s" % (platform, design):
                continue
            if only:
                print(json.dumps(one(platform, design, gds,
                                     os.path.join(base, "6_final.def"),
                                     metals, vias)))
                return []
            rows.append(run_isolated(flow, platform, design, timeout))
            r = rows[-1]
            print("  %-11s %-18s %9s shapes  silica %7s  klayout %7s  %s%s"
                  % (r["platform"], r["design"][:18], r["shapes"],
                     r["silica_nets"], r["klayout_nets"], r["verdict"],
                     ("  " + r["note"]) if r.get("note") else ""), flush=True)
    print()
    done = [r for r in rows if r["verdict"] in ("AGREE", "DISAGREE")]
    ok = sum(1 for r in done if r["verdict"] == "AGREE")
    print("%d designs checked, %d agree on nets, %d agree on width"
          % (len(done), ok, sum(1 for r in done if r["width_agree"])))
    for v in ("SKIPPED", "NO-STACK", "CRASHED", "TIMEOUT", "ERROR"):
        n = sum(1 for r in rows if r["verdict"] == v)
        if n:
            print("%d design(s) %s -- not checked, not counted as passing"
                  % (n, v.lower()))
    skip = ("AGREE", "SKIPPED", "NO-STACK", "CRASHED", "TIMEOUT", "ERROR")
    bad = [r for r in rows if r["verdict"] not in skip]
    for r in bad:
        print("  NOT AGREED: %s/%s  %s" % (r["platform"], r["design"],
                                           r["verdict"] or r["note"]))
    print("total shapes checked: %d"
          % sum(r["shapes"] or 0 for r in done))
    if not only:
        with open("silica_validation.json", "w") as f:
            json.dump(rows, f, indent=1)
    return rows


def one(platform, design, gds, defp, metals, vias):
    rec = {"platform": platform, "design": design, "shapes": None,
           "silica_nets": None, "klayout_nets": None, "verdict": "ERROR",
           "width_agree": False, "def_nets": def_nets(defp), "note": ""}
    try:
        ly0 = pya.Layout()
        ly0.read(gds)
        top = ly0.top_cell().name
        rows = [(n, l, d) for (n, l, d) in metals] + \
               [(v[0], v[1], v[2]) for v in vias]
        t0 = time.time()
        boxes, _un, _dbu, _c = read_layout(gds, top, rows)
        d = Design()
        for (n, l, dt) in metals:
            d.declare_metal(n, l, dt)
        for (n, l, dt, a, b) in vias:
            d.declare_via(n, l, dt, a, b)
        for n, _l, _d in rows:
            if boxes[n]:
                d.bulk_add(n, boxes[n])
        rec["shapes"] = sum(len(v) for v in boxes.values())
        rec["silica_nets"] = d.net_count()
        rec["silica_seconds"] = round(time.time() - t0, 1)
        kn, ly, cell = klayout_nets(gds, top, metals, vias)
        rec["klayout_nets"] = kn
        # The two models differ on one point, by design: KLayout treats every
        # declared conductive layer as net-forming, so a via cut touching no
        # metal is a net of its own. SILICA treats cuts as mediators only --
        # they join metals and are never net members -- so an isolated cut
        # belongs to no net. Reconcile explicitly rather than call it a
        # disagreement, and say how many were involved.
        orphans = 0
        d._ensure()
        for (vn, _l, _dt, a, b) in vias:
            for _s, vb in d._shapes.get(vn, {}).items():
                if not d._touching(a, vb) and not d._touching(b, vb):
                    orphans += 1
        rec["isolated_via_cuts"] = orphans
        if kn == rec["silica_nets"]:
            rec["verdict"] = "AGREE"
        elif kn == rec["silica_nets"] + orphans:
            rec["verdict"] = "AGREE"
            rec["note"] = "+%d isolated via cut(s)" % orphans
        else:
            rec["verdict"] = "DISAGREE"
        # width, on the busiest metal layer, at a limit that finds both answers
        busiest = max(metals, key=lambda m: len(boxes[m[0]]))
        widths = sorted({b.width() for b in boxes[busiest[0]]})
        limit = widths[len(widths) // 2] if widths else 0
        if limit:
            xs = [b.x1 for b in boxes[busiest[0]]]
            win = Box(min(xs) - 1000, min(b.y1 for b in boxes[busiest[0]]) - 1000,
                      max(b.x2 for b in boxes[busiest[0]]) + 1000,
                      max(b.y2 for b in boxes[busiest[0]]) + 1000)
            sw = d.width_violation(busiest[0], win, limit) is not None
            kw = klayout_width(ly, cell, busiest[1], busiest[2], limit)
            rec["width_agree"] = (sw == kw)
            rec["width_limit"] = limit
    except Exception as e:                            # noqa: BLE001
        rec["note"] = "%s: %s" % (type(e).__name__, e)
    return rec


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    mg, only, tmo = DEFAULT_MAX_GB, None, 1800
    for a in sys.argv[1:]:
        if a.startswith("--max-gb="):
            mg = float(a.split("=", 1)[1])
        elif a.startswith("--only="):
            only = a.split("=", 1)[1]
        elif a.startswith("--timeout="):
            tmo = int(a.split("=", 1)[1])
    flow = args[0] if args else \
        os.path.expanduser("~/OpenROAD-flow-scripts/flow")
    main(flow, mg, only, tmo)
