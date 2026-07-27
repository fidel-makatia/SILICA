#!/usr/bin/env python3
"""Deterministic GDS pin-label injector (stdlib only, py3.6+).

Usage: add_pin_labels.py TOPCELL in.gds pins.csv out.gds
pins.csv rows: name,Mn,x_um,y_um  ->  TEXT records on GDS layer 130+n, tt 0.

A pure function of its inputs -- fits a SILICA flow `step()` exactly. Written
because Innovus streamed zero pin text under a Virtuoso-style map (a WARNING,
naturally), which left 18 consecutive real LVS runs INCORRECT (Ports: 0 vs N).
"""
import struct, sys, csv

top, gin, pcsv, gout = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
data = open(gin, "rb").read()
TOP = top.encode()
i, name, ins_at = 0, None, None
while i < len(data):
    ln, rt, dt = struct.unpack(">HBB", data[i:i+4])
    if ln == 0:
        break
    if rt == 0x06 and dt == 0x06:                 # STRNAME
        name = data[i+4:i+ln].rstrip(b"\x00")
    elif rt == 0x07 and name == TOP:              # ENDSTR of the top cell
        ins_at = i
        break
    i += ln
assert ins_at is not None, "top cell %r not found" % top

def text_rec(layer, tt, x, y, s):
    b  = struct.pack(">HBB", 4, 0x0C, 0x00)                # TEXT
    b += struct.pack(">HBBh", 6, 0x0D, 0x02, layer)        # LAYER
    b += struct.pack(">HBBh", 6, 0x16, 0x02, tt)           # TEXTTYPE
    b += struct.pack(">HBBii", 12, 0x10, 0x03, x, y)       # XY
    sb = s.encode()
    if len(sb) % 2:
        sb += b"\x00"
    b += struct.pack(">HBB", 4 + len(sb), 0x19, 0x06) + sb # STRING
    b += struct.pack(">HBB", 4, 0x11, 0x00)                # ENDEL
    return b

out, n = [], 0
for row in csv.reader(open(pcsv)):
    nm, lay = row[0], row[1]
    if not lay.startswith("M"):
        continue
    layer = 130 + int(lay[1:])
    x = int(round(float(row[2]) * 1000))
    y = int(round(float(row[3]) * 1000))
    out.append(text_rec(layer, 0, x, y, nm)); n += 1
open(gout, "wb").write(data[:ins_at] + b"".join(out) + data[ins_at:])
print("LABELS_ADDED %d" % n)
