"""Read an existing layout into a SILICA design.

The counterpart to `export`. Where export refuses to DROP anything, import is
explicit about what it TAKES: you declare which GDS layers become which SILICA
layers, and it reports every layer in the file that your map did not cover.

That reporting matters. The design SILICA then holds is the mapped subset, not
the chip -- so a design that was imported must not be streamed back out as
though it were complete, and `export` will say so if you try.

Geometry arrives as rectangles: each layer's shapes are merged, clipped to the
window if one was given, and decomposed into rectangles, which is exact for
Manhattan geometry. Connectivity is unaffected by the decomposition -- abutting
rectangles are one component either way -- but a width measurement sees the
decomposition it was given.
"""
from silica.errors import ParseError
from silica.geometry import Box

try:
    import pya as _pya
except ImportError:
    try:
        import klayout.db as _pya
    except ImportError:
        _pya = None


def available():
    return _pya is not None


def read_layout(path, top, rows, window=None, shallow=False):
    """Returns (per_layer_boxes, unmapped_gds_layers, dbu, cell_name).

    `rows` is [(silica_layer, gds_num, gds_dtype)].
    `window` is (x1, y1, x2, y2) in database units, or None for the whole cell.

    `shallow` takes only the geometry the named cell OWNS, without descending
    into placed instances. For a routed design that is the top-level routing --
    the wires the router made -- as opposed to the flattened view, which also
    contains every standard cell's internal geometry.
    """
    if _pya is None:
        raise ParseError("`import` needs the klayout module "
                         "(pip install klayout)")
    ly = _pya.Layout()
    ly.read(path)
    cell = ly.cell(top)
    if cell is None:
        names = [c.name for c in ly.each_cell()]
        raise ParseError("cell %r not found in %s (%d cells, top-level: %s)"
                         % (top, path, len(names),
                            ", ".join(c.name for c in ly.top_cells())))
    clip = None
    if window is not None:
        clip = _pya.Region(_pya.Box(*window))

    mapped = set()
    out = {}
    for (name, num, dtype) in rows:
        mapped.add((num, dtype))
        li = ly.find_layer(num, dtype)
        if li is None:
            out[name] = []
            continue
        reg = (_pya.Region(cell.shapes(li)) if shallow
               else _pya.Region(cell.begin_shapes_rec(li)))
        if clip is not None:
            reg = reg & clip
        reg.merge()
        boxes = []
        for poly in reg.each():
            if poly.is_box():
                bb = poly.bbox()
                boxes.append(Box(bb.left, bb.bottom, bb.right, bb.top))
                continue
            for tz in poly.decompose_trapezoids():
                bb = tz.bbox()
                if bb.width() > 0 and bb.height() > 0:
                    boxes.append(Box(bb.left, bb.bottom, bb.right, bb.top))
        out[name] = boxes

    unmapped = []
    for li in ly.layer_indexes():
        info = ly.get_info(li)
        key = (info.layer, info.datatype)
        if key in mapped:
            continue
        empty = (cell.shapes(li).is_empty() if shallow
                 else cell.begin_shapes_rec(li).at_end())
        if empty:
            continue                      # present in the file, empty here
        unmapped.append("%d/%d" % key)
    return out, sorted(unmapped), ly.dbu, cell.name
