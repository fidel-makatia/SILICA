"""GDSII stream-out, and the totality obligation that goes with it.

The failure this exists to prevent: a stream map that does not cover every
layer in the design, so the writer silently omits the data it has no rule for.
A missing via row drops every via cut and LVS reports opens; a missing text row
drops every port label and LVS reports zero ports. Both are, in the tools where
this has happened, a warning at worst.

`write_gds` has no skip path. The caller (`Interp.exec_export`) proves the map
is total before anything is written, and this writer raises rather than omit an
element it was not given a rule for.
"""
import struct
import time

# record types
_HEADER, _BGNLIB, _LIBNAME, _UNITS = 0x00, 0x01, 0x02, 0x03
_ENDLIB, _BGNSTR, _STRNAME, _ENDSTR = 0x04, 0x05, 0x06, 0x07
_BOUNDARY, _TEXT, _LAYER, _DATATYPE = 0x08, 0x0C, 0x0D, 0x0E
_XY, _ENDEL, _TEXTTYPE, _STRING = 0x10, 0x11, 0x16, 0x19

# database unit in (user units, metres), by declared unit
_UNIT_SCALE = {"nm": (1e-3, 1e-9), "um": (1.0, 1e-6)}


def mapkey(key):
    layer, kind = key
    return layer if kind is None else "%s.%s" % (layer, kind)


def _rec(rtype, dtype, payload=b""):
    if len(payload) + 4 > 0xFFFF:
        raise ValueError("GDS record too large")
    return struct.pack(">HBB", 4 + len(payload), rtype, dtype) + payload


def _real8(v):
    """IBM excess-64 base-16 float, the only real format GDSII admits."""
    if v == 0:
        return b"\x00" * 8
    sign = 0
    if v < 0:
        sign, v = 0x80, -v
    exp = 64
    while v >= 1.0:
        v /= 16.0
        exp += 1
    while v < 1.0 / 16.0:
        v *= 16.0
        exp -= 1
    mant = int(round(v * (1 << 56)))
    if mant >= (1 << 56):
        mant >>= 4
        exp += 1
    return struct.pack(">B", sign | exp) + mant.to_bytes(7, "big")


def _cstr(s):
    b = s.encode("ascii", "replace")
    return b + b"\x00" if len(b) % 2 else b


def _stamp():
    t = time.localtime()
    f = [t.tm_year, t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec]
    return struct.pack(">12h", *(f + f))


def write_gds(path, top, shapes, labels, rules, grid=1, units="nm"):
    """Write every shape and label, or raise. Returns the element count.

    `rules` maps (layer, None) -> (num, datatype) for geometry and
    (layer, "NAME") -> (num, datatype) for text. There is no default and no
    skip: a datum with no rule is a bug in the caller's totality check.
    """
    if units not in _UNIT_SCALE:
        raise ValueError("unknown unit %r" % units)
    uu, meters = _UNIT_SCALE[units]
    out = [_rec(_HEADER, 0x02, struct.pack(">h", 600)),
           _rec(_BGNLIB, 0x02, _stamp()),
           _rec(_LIBNAME, 0x06, _cstr("LIB")),
           _rec(_UNITS, 0x05, _real8(uu) + _real8(meters)),
           _rec(_BGNSTR, 0x02, _stamp()),
           _rec(_STRNAME, 0x06, _cstr(top))]
    n = 0
    for layer in sorted(shapes):
        key = (layer, None)
        if key not in rules:
            if shapes[layer]:
                raise ValueError("no export rule for layer %r" % layer)
            continue
        num, dtype = rules[key]
        for b in shapes[layer]:
            pts = [(b.x1, b.y1), (b.x2, b.y1), (b.x2, b.y2),
                   (b.x1, b.y2), (b.x1, b.y1)]
            xy = b"".join(struct.pack(">ii", x, y) for x, y in pts)
            out += [_rec(_BOUNDARY, 0x00),
                    _rec(_LAYER, 0x02, struct.pack(">h", num)),
                    _rec(_DATATYPE, 0x02, struct.pack(">h", dtype)),
                    _rec(_XY, 0x03, xy),
                    _rec(_ENDEL, 0x00)]
            n += 1
    for (layer, text, x, y) in labels:
        key = (layer, "NAME")
        if key not in rules:
            raise ValueError("no export rule for text on layer %r" % layer)
        num, dtype = rules[key]
        out += [_rec(_TEXT, 0x00),
                _rec(_LAYER, 0x02, struct.pack(">h", num)),
                _rec(_TEXTTYPE, 0x02, struct.pack(">h", dtype)),
                _rec(_XY, 0x03, struct.pack(">ii", x, y)),
                _rec(_STRING, 0x06, _cstr(text)),
                _rec(_ENDEL, 0x00)]
        n += 1
    out += [_rec(_ENDSTR, 0x00), _rec(_ENDLIB, 0x00)]
    with open(path, "wb") as f:
        f.write(b"".join(out))
    return n


def read_gds(path):
    """Minimal reader for round-trip checks.

    Returns (top_cell, [("boundary", layer, dtype, [(x, y), ...]),
                        ("text", layer, texttype, (x, y), string)]).
    """
    data = open(path, "rb").read()
    i, top, out, cur, kind = 0, None, [], None, None
    while i + 4 <= len(data):
        ln, rt, dt = struct.unpack(">HBB", data[i:i + 4])
        if ln < 4:
            break
        payload = data[i + 4:i + ln]
        if rt == _STRNAME:
            top = payload.rstrip(b"\x00").decode()
        elif rt == _BOUNDARY:
            cur, kind = {}, "boundary"
        elif rt == _TEXT:
            cur, kind = {}, "text"
        elif cur is not None:
            if rt == _LAYER:
                cur["layer"] = struct.unpack(">h", payload)[0]
            elif rt in (_DATATYPE, _TEXTTYPE):
                cur["dtype"] = struct.unpack(">h", payload)[0]
            elif rt == _XY:
                pts = [struct.unpack(">ii", payload[k:k + 8])
                       for k in range(0, len(payload), 8)]
                cur["xy"] = pts
            elif rt == _STRING:
                cur["text"] = payload.rstrip(b"\x00").decode()
            elif rt == _ENDEL:
                if kind == "boundary":
                    out.append(("boundary", cur["layer"], cur["dtype"],
                                cur["xy"]))
                else:
                    out.append(("text", cur["layer"], cur["dtype"],
                                cur["xy"][0], cur["text"]))
                cur = None
        i += ln
    return top, out
