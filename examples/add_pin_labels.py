#!/usr/bin/env python3
"""Deterministic GDS pin-label injector (standard library only).

Usage:
    add_pin_labels.py TOPCELL in.gds pins.csv out.gds [TEXT_LAYER_BASE]

pins.csv rows:  name,Mn,x_um,y_um
Each row becomes a TEXT record on GDS layer TEXT_LAYER_BASE + n, texttype 0.
TEXT_LAYER_BASE defaults to 200; set it to whatever layer range the signoff
deck you use reads port text from -- there is no universal value, which is
exactly why it is an argument and not a constant.

Why this exists: place-and-route tools have streamed a GDS with no pin text at
all when the stream map declared no text rows. That is a warning at most, and
LVS then reports zero ports against a netlist that has hundreds. This script is
a pure function of its inputs, so it slots into a SILICA flow `step()` and
caches like any other.
"""
import csv
import struct
import sys

if len(sys.argv) not in (5, 6):
    sys.exit(__doc__)

top, gin, pcsv, gout = sys.argv[1:5]
layer_base = int(sys.argv[5]) if len(sys.argv) == 6 else 200

data = open(gin, "rb").read()
TOP = top.encode()
i, name, ins_at = 0, None, None
while i < len(data):
    ln, rt, dt = struct.unpack(">HBB", data[i:i + 4])
    if ln == 0:
        break
    if rt == 0x06 and dt == 0x06:                 # STRNAME
        name = data[i + 4:i + ln].rstrip(b"\x00")
    elif rt == 0x07 and name == TOP:              # ENDSTR of the top cell
        ins_at = i
        break
    i += ln
if ins_at is None:
    sys.exit("top cell %r not found in %s" % (top, gin))


def text_rec(layer, tt, x, y, s):
    b = struct.pack(">HBB", 4, 0x0C, 0x00)                 # TEXT
    b += struct.pack(">HBBh", 6, 0x0D, 0x02, layer)        # LAYER
    b += struct.pack(">HBBh", 6, 0x16, 0x02, tt)           # TEXTTYPE
    b += struct.pack(">HBBii", 12, 0x10, 0x03, x, y)       # XY
    sb = s.encode()
    if len(sb) % 2:
        sb += b"\x00"
    b += struct.pack(">HBB", 4 + len(sb), 0x19, 0x06) + sb  # STRING
    b += struct.pack(">HBB", 4, 0x11, 0x00)                # ENDEL
    return b


out, n = [], 0
with open(pcsv) as f:
    for row in csv.reader(f):
        if len(row) < 4 or not row[1].startswith("M"):
            continue
        layer = layer_base + int(row[1][1:])
        x = int(round(float(row[2]) * 1000))
        y = int(round(float(row[3]) * 1000))
        out.append(text_rec(layer, 0, x, y, row[0]))
        n += 1

open(gout, "wb").write(data[:ins_at] + b"".join(out) + data[ins_at:])
print("LABELS_ADDED %d" % n)
