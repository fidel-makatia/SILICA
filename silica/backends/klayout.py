"""KLayout backend for SILICA -- proof that the interpreter is tool-agnostic.

Implements the same backend protocol as silica.Design, but geometry lives in a
real pya.Layout and all merge/subtract/interaction ops run in KLayout's engine.
The interpreter never knows which backend it is driving.

Beta-level approximations (documented, deterministic):
  * min_spacing / min_width use merged-polygon bounding boxes -- exact for
    rectilinear box geometry (all v0.2 programs), approximate for L-shapes
  * a metal add's touched-net probe checks the metal layer only (a shape that
    lands under an existing via cut is caught by the connectivity invariant
    at commit, not at the add)
"""
try:
    import pya
except ImportError:
    try:
        import klayout.db as pya
    except ImportError:
        pya = None

from silica.interpreter import Box, UF


def _bbox_gap(a, b):
    dx = max(b.left - a.right, a.left - b.right, 0)
    dy = max(b.bottom - a.top, a.bottom - b.top, 0)
    return (dx*dx + dy*dy) ** 0.5 if (dx and dy) else max(dx, dy)


def _overlaps(bb, win):
    return not (win.x1 > bb.right or win.x2 < bb.left or
                win.y1 > bb.top or win.y2 < bb.bottom)


class KLayoutBackend:
    def __init__(self):
        if pya is None:
            raise RuntimeError("klayout python module not available "
                               "(pip install klayout)")
        self.ly = pya.Layout()
        self.ly.dbu = 0.001
        self.top = self.ly.create_cell("TOP")
        self.metals, self.vias = {}, {}
        self.layer_idx = {}
        self.labels = []

    # -- protocol: declarations / lifecycle --
    def declare_metal(self, name, l, d):
        self.metals[name] = (l, d)
        self.layer_idx[name] = self.ly.layer(l, d)

    def declare_via(self, name, l, d, ma, mb):
        self.vias[name] = ((l, d), (ma, mb))
        self.layer_idx[name] = self.ly.layer(l, d)

    def clone(self):
        nb = object.__new__(KLayoutBackend)
        nb.ly = self.ly.dup()
        nb.top = nb.ly.cell(self.top.name)
        nb.metals, nb.vias = dict(self.metals), dict(self.vias)
        nb.layer_idx = dict(self.layer_idx)
        nb.labels = list(self.labels)
        return nb

    def absorb(self, shadow):
        self.ly, self.top = shadow.ly, shadow.top
        self.metals, self.vias = shadow.metals, shadow.vias
        self.layer_idx, self.labels = shadow.layer_idx, shadow.labels

    # -- protocol: writes --
    def _region(self, name):
        return pya.Region(self.top.begin_shapes_rec(self.layer_idx[name]))

    def add(self, layer, box):
        self.top.shapes(self.layer_idx[layer]).insert(
            pya.Box(box.x1, box.y1, box.x2, box.y2))

    def sub(self, layer, box):
        li = self.layer_idx[layer]
        r = self._region(layer) - pya.Region(pya.Box(box.x1, box.y1, box.x2, box.y2))
        self.top.shapes(li).clear()
        self.top.shapes(li).insert(r)

    def add_label(self, layer, text, x, y):
        self.top.shapes(self.layer_idx[layer]).insert(pya.Text(text, x, y))
        self.labels.append((layer, text, x, y))

    # -- protocol: connectivity --
    def _components(self):
        return {m: [p for p in self._region(m).merged().each()]
                for m in self.metals}

    def nets(self):
        comps = self._components()
        uf = UF()
        for m, polys in comps.items():
            for i in range(len(polys)):
                uf.find((m, i))
        for vname, (_ld, (a, b)) in self.vias.items():
            for vp in self._region(vname).merged().each():
                vreg = pya.Region(); vreg.insert(vp)
                anchor = None
                for m in (a, b):
                    for i, p in enumerate(comps.get(m, [])):
                        preg = pya.Region(); preg.insert(p)
                        if not preg.interacting(vreg).is_empty():
                            if anchor is None: anchor = (m, i)
                            else: uf.union(anchor, (m, i))
        nets = {}
        for m, polys in comps.items():
            for i in range(len(polys)):
                nets.setdefault(uf.find((m, i)), set()).add((m, i))
        return nets

    def net_count(self):
        return len(self.nets())

    def _poly_at(self, comps, layer, x, y):
        pr = pya.Region(pya.Box(x - 1, y - 1, x + 1, y + 1))
        for i, p in enumerate(comps.get(layer, [])):
            preg = pya.Region(); preg.insert(p)
            if not preg.interacting(pr).is_empty():
                return i
        return None

    def net_at(self, layer, x, y):
        comps = self._components()
        i = self._poly_at(comps, layer, x, y)
        if i is None: return None
        for root, mem in self.nets().items():
            if (layer, i) in mem:
                return frozenset(mem)
        return None

    def on_metal(self, layer, x, y):
        return self._poly_at(self._components(), layer, x, y) is not None

    def nets_touching(self, layer, box):
        comps = self._components()
        pre = self.nets()
        br = pya.Region(pya.Box(box.x1, box.y1, box.x2, box.y2))
        metals = [layer] if layer in self.metals else list(self.vias[layer][1])
        touched = []
        for m in metals:
            for i, p in enumerate(comps.get(m, [])):
                preg = pya.Region(); preg.insert(p)
                if preg.interacting(br).is_empty(): continue
                for root, mem in pre.items():
                    if (m, i) in mem:
                        fs = frozenset(mem)
                        if fs not in touched: touched.append(fs)
        return touched

    # -- protocol: measurements (bbox-based; exact for rectilinear boxes) --
    def min_spacing(self, layer, win):
        comps = self._components()
        owner = {}
        for root, mem in self.nets().items():
            for (m, i) in mem:
                if m == layer: owner[i] = root
        polys = comps.get(layer, [])
        idx = [i for i, p in enumerate(polys) if _overlaps(p.bbox(), win)]
        best = None
        for a in range(len(idx)):
            for b in range(a + 1, len(idx)):
                i, j = idx[a], idx[b]
                if owner.get(i) == owner.get(j): continue
                g = _bbox_gap(polys[i].bbox(), polys[j].bbox())
                if best is None or g < best: best = g
        return best

    def min_width(self, layer, win):
        best = None
        for p in self._components().get(layer, []):
            bb = p.bbox()
            if _overlaps(bb, win):
                w = min(bb.right - bb.left, bb.top - bb.bottom)
                if best is None or w < best: best = w
        return best
