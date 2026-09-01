"""KLayout backend for SILICA -- proof that the interpreter is tool-agnostic.

Implements the same backend protocol as `silica.Design`, but geometry lives in
a real `pya.Layout` and all merge/subtract/interaction ops run in KLayout's
Region engine. The interpreter never knows which backend it is driving.

Documented approximations (deterministic, and covered by the shared
conformance corpus in `tests/conformance.py`):
  * `width_violation` is exact: it is KLayout's own `width_check`. It can
    report a different MEASUREMENT from the reference engine on a
    non-rectangular shape (the reference reports the narrowest box of the
    coalesced decomposition, KLayout the narrowest edge-pair distance) while
    reaching the same VERDICT. The conformance corpus checks verdicts.
  * `spacing_violation` compares merged-polygon bounding boxes, per net pair.
    That is exact for rectangular nets; for an L-shaped net the bounding box
    is larger than the net, so the gap is under-reported and the check errs
    toward rejecting -- never toward a silent pass.
  * A metal add's touched-net probe checks the metal layer only. A shape that
    lands under an existing via cut is caught by the `connectivity` invariant
    at commit rather than at the `add`.
"""
try:
    import pya
except ImportError:
    try:
        import klayout.db as pya
    except ImportError:
        pya = None

from silica.interpreter import UF


def _bbox_gap(a, b):
    dx = max(b.left - a.right, a.left - b.right, 0)
    dy = max(b.bottom - a.top, a.bottom - b.top, 0)
    return (dx * dx + dy * dy) ** 0.5 if (dx and dy) else max(dx, dy)


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
    def declare_metal(self, name, num, dtype):
        self.metals[name] = (num, dtype)
        self.layer_idx[name] = self.ly.layer(num, dtype)

    def declare_via(self, name, num, dtype, ma, mb):
        self.vias[name] = ((num, dtype), (ma, mb))
        self.layer_idx[name] = self.ly.layer(num, dtype)

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
        r = self._region(layer) - pya.Region(
            pya.Box(box.x1, box.y1, box.x2, box.y2))
        self.top.shapes(li).clear()
        self.top.shapes(li).insert(r)

    def add_label(self, layer, text, x, y):
        self.top.shapes(self.layer_idx[layer]).insert(pya.Text(text, x, y))
        self.labels.append((layer, text, x, y))

    # -- protocol: connectivity --
    def _components(self):
        return dict((m, list(self._region(m).merged().each()))
                    for m in self.metals)

    @staticmethod
    def _net_id(comps, members):
        """Stable, printable net id: the net's lowest polygon corner.

        Must agree with `silica.Design._net_id` so both backends report the
        same ids in counterexamples.
        """
        best = None
        for (m, i) in members:
            bb = comps[m][i].bbox()
            key = (m, bb.left, bb.bottom)
            if best is None or key < best:
                best = key
        return "%s@%d,%d" % best

    def _partition(self, comps):
        uf = UF()
        for m, polys in comps.items():
            for i in range(len(polys)):
                uf.find((m, i))
        for vname, (_ld, (a, b)) in self.vias.items():
            for vp in self._region(vname).merged().each():
                vreg = pya.Region()
                vreg.insert(vp)
                anchor = None
                for m in (a, b):
                    for i, p in enumerate(comps.get(m, [])):
                        preg = pya.Region()
                        preg.insert(p)
                        if not preg.interacting(vreg).is_empty():
                            if anchor is None:
                                anchor = (m, i)
                            else:
                                uf.union(anchor, (m, i))
        parts = {}
        for m, polys in comps.items():
            for i in range(len(polys)):
                parts.setdefault(uf.find((m, i)), set()).add((m, i))
        return parts

    def nets(self):
        comps = self._components()
        return dict((self._net_id(comps, mem), frozenset(mem))
                    for mem in self._partition(comps).values())

    def net_count(self):
        return len(self.nets())

    def _poly_at(self, comps, layer, x, y):
        """Index of the polygon containing (x, y) -- exact containment.

        A point strictly outside a shape must NOT resolve to it: an "almost
        touching" probe reporting success is the exact failure mode `add ...
        on net_at(...)` exists to prevent.
        """
        pt = pya.Point(x, y)
        for i, p in enumerate(comps.get(layer, [])):
            if p.inside(pt):
                return i
        return None

    def net_at(self, layer, x, y):
        comps = self._components()
        i = self._poly_at(comps, layer, x, y)
        if i is None:
            return None
        for mem in self._partition(comps).values():
            if (layer, i) in mem:
                return self._net_id(comps, mem)
        return None

    def on_metal(self, layer, x, y):
        return self._poly_at(self._components(), layer, x, y) is not None

    def nets_touching(self, layer, box):
        comps = self._components()
        parts = self._partition(comps)
        br = pya.Region(pya.Box(box.x1, box.y1, box.x2, box.y2))
        metals = ([layer] if layer in self.metals
                  else list(self.vias[layer][1]))
        touched = []
        for m in metals:
            for i, p in enumerate(comps.get(m, [])):
                preg = pya.Region()
                preg.insert(p)
                if preg.interacting(br).is_empty():
                    continue
                for mem in parts.values():
                    if (m, i) in mem:
                        nid = self._net_id(comps, mem)
                        if nid not in touched:
                            touched.append(nid)
        return touched

    # -- protocol: measurements --
    def width_violation(self, layer, win, limit):
        """Exact: KLayout's own width check over the merged layer."""
        ep = self._region(layer).merged().width_check(limit)
        worst = None
        for pair in ep.each():
            if not _overlaps(pair.bbox(), win):
                continue
            d = pair.distance()
            if worst is None or d < worst:
                worst = d
        return worst

    def spacing_violation(self, layer, win, limit):
        comps = self._components()
        owner = {}
        for mem in self._partition(comps).values():
            nid = self._net_id(comps, mem)
            for (m, i) in mem:
                if m == layer:
                    owner[i] = nid
        polys = comps.get(layer, [])
        idx = [i for i, p in enumerate(polys) if _overlaps(p.bbox(), win)]
        worst = None
        for a in range(len(idx)):
            for b in range(a + 1, len(idx)):
                i, j = idx[a], idx[b]
                if owner.get(i) == owner.get(j):
                    continue
                g = _bbox_gap(polys[i].bbox(), polys[j].bbox())
                if g < limit and (worst is None or g < worst):
                    worst = g
        return worst
