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


def derive_stack(lyp):
    """(metals, vias) as ordered [(name, layer, dtype)] / [(name, l, d, a, b)].

    Metals are `<word><n>.drawing`, ordered by n; a via with index k joins
    metal k and metal k+1. Purely nominal, but it is the same stack given to
    both engines, which is what the comparison tests.
    """
    metals, vias = {}, {}
    for full, (num, dt) in lyp.items():
        if "." not in full:
            continue
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
        m = re.fullmatch(r"(met|metal|m|tm)(\d+)", low)
        if m:
            metals[int(m.group(2))] = (name, num, dt)
            continue
        m = re.fullmatch(r"(via|v)(\d+)", low)
        if m:
            vias[int(m.group(2))] = (name, num, dt)
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


def main(flow):
    plats = os.path.join(flow, "platforms")
    results = os.path.join(flow, "results")
    rows = []
    for platform in sorted(os.listdir(results)):
        lyps = []
        for root, _d, files in os.walk(os.path.join(plats, platform)):
            lyps += [os.path.join(root, f) for f in files if f.endswith(".lyp")]
        if not lyps:
            continue
        metals, vias = derive_stack(parse_lyp(lyps[0]))
        if not metals:
            continue
        for design in sorted(os.listdir(os.path.join(results, platform))):
            base = os.path.join(results, platform, design, "base")
            gds = os.path.join(base, "6_final.gds")
            if not os.path.exists(gds):
                continue
            rows.append(one(platform, design, gds,
                            os.path.join(base, "6_final.def"), metals, vias))
            r = rows[-1]
            print("  %-11s %-16s %8s shapes  silica %7s  klayout %7s  %s"
                  % (r["platform"], r["design"], r["shapes"], r["silica_nets"],
                     r["klayout_nets"], r["verdict"]), flush=True)
    print()
    ok = sum(1 for r in rows if r["verdict"] == "AGREE")
    print("%d designs, %d agree on nets, %d agree on width"
          % (len(rows), ok, sum(1 for r in rows if r["width_agree"])))
    print("total shapes checked: %d" % sum(r["shapes"] or 0 for r in rows))
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
        rec["verdict"] = ("AGREE" if kn == rec["silica_nets"]
                          else "DISAGREE")
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
    flow = sys.argv[1] if len(sys.argv) > 1 else \
        os.path.expanduser("~/OpenROAD-flow-scripts/flow")
    main(flow)
