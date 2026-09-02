"""Geometry engines implementing the SILICA backend protocol.

Two pure-Python engines live here:

  SimpleDesign  the obviously-correct one. Recomputes connectivity from
                scratch by pairwise adjacency, O(n^2), no indexing, no
                incremental state. It exists to be read and to be the
                differential-test oracle -- not to be fast.

  Design        the one you get by default. Same semantics, with a spatial
                hash index and an incrementally maintained partition, so an
                `add` costs O(neighbours) instead of O(shapes) and a run of n
                adds is linear rather than cubic.

`tests/test_engine.py` fuzzes the two against each other. If they ever
disagree, the simple one is right.
"""
from silica.geometry import Box, UF, union_rect

_CELL = 1 << 12      # spatial-hash cell, in DBU
_MAX_CELLS = 256     # a shape spanning more cells than this goes in `big`


# ---------------------------------------------------------------------------
# the readable, obviously-correct engine


class SimpleDesign:
    """Reference semantics: no index, no incremental state, O(n^2)."""

    def __init__(self):
        self.shapes = {}     # layer -> [Box]
        self.labels = []
        self.metals = {}
        self.vias = {}

    # -- declarations / lifecycle --
    def declare_metal(self, name, num, dtype):
        self.metals[name] = (num, dtype)

    def declare_via(self, name, num, dtype, ma, mb):
        self.vias[name] = ((num, dtype), (ma, mb))

    def clone(self):
        n = SimpleDesign()
        n.shapes = dict((k, list(v)) for k, v in self.shapes.items())
        n.labels = list(self.labels)
        n.metals, n.vias = dict(self.metals), dict(self.vias)
        return n

    def absorb(self, shadow):
        self.shapes = dict((k, list(v)) for k, v in shadow.shapes.items())
        self.labels = list(shadow.labels)
        self.metals, self.vias = dict(shadow.metals), dict(shadow.vias)

    # -- writes --
    def _insert(self, layer, box):
        self.shapes.setdefault(layer, []).append(box)

    def _coalesce_from(self, layer, idx):
        bxs = self.shapes[layer]
        merged = True
        while merged:
            merged = False
            for j in range(len(bxs)):
                if j == idx:
                    continue
                u = union_rect(bxs[idx], bxs[j])
                if u is not None:
                    bxs[idx] = u
                    bxs.pop(j)
                    if j < idx:
                        idx -= 1
                    merged = True
                    break
        return idx

    def add(self, layer, box):
        self._insert(layer, box)
        self._coalesce_from(layer, len(self.shapes[layer]) - 1)

    def bulk_add(self, layer, boxes):
        for b in boxes:
            self.add(layer, b)

    def sub(self, layer, box):
        out = []
        for s in self.shapes.get(layer, []):
            out.extend(s.minus(box))
        self.shapes[layer] = out
        i = 0
        while i < len(self.shapes[layer]):
            i = self._coalesce_from(layer, i) + 1

    def add_label(self, layer, text, x, y):
        self.labels.append((layer, text, x, y))

    # -- connectivity --
    def _net_key(self, members):
        best = None
        for (m, i) in members:
            b = self.shapes[m][i]
            key = (m, b.x1, b.y1)
            if best is None or key < best:
                best = key
        return best

    def _net_id(self, members):
        return "%s@%d,%d" % self._net_key(members)

    def _partition(self):
        uf = UF()
        for m in self.metals:
            bxs = self.shapes.get(m, [])
            for i in range(len(bxs)):
                uf.find((m, i))
            for i in range(len(bxs)):
                for j in range(i + 1, len(bxs)):
                    if bxs[i].touches(bxs[j]):
                        uf.union((m, i), (m, j))
        for vname, (_, (ma, mb)) in self.vias.items():
            for v in self.shapes.get(vname, []):
                anchor = None
                for m in (ma, mb):
                    for i, b in enumerate(self.shapes.get(m, [])):
                        if v.touches(b):
                            if anchor is None:
                                anchor = (m, i)
                            else:
                                uf.union(anchor, (m, i))
        parts = {}
        for m in self.metals:
            for i in range(len(self.shapes.get(m, []))):
                parts.setdefault(uf.find((m, i)), set()).add((m, i))
        return parts

    def nets(self):
        return dict((self._net_id(mem), frozenset(mem))
                    for mem in self._partition().values())

    def net_count(self):
        return len(self._partition())

    def net_at(self, layer, x, y):
        for i, b in enumerate(self.shapes.get(layer, [])):
            if b.contains_pt(x, y):
                for mem in self._partition().values():
                    if (layer, i) in mem:
                        return self._net_id(mem)
        return None

    def net_probe(self, nid):
        for n, mem in self.nets().items():
            if n == nid:
                return self._net_key(mem)
        return None

    def on_metal(self, layer, x, y):
        return any(b.contains_pt(x, y) for b in self.shapes.get(layer, []))

    def nets_touching(self, layer, box):
        if layer in self.vias:
            # a via cut is never a net MEMBER, only a mediator, so the
            # probe-and-repartition trick finds nothing for it: ask directly
            _, (ma, mb) = self.vias[layer]
            touched = []
            for nid, mem in self.nets().items():
                hit = any(mm in (ma, mb) and self.shapes[mm][i].touches(box)
                          for (mm, i) in mem)
                if hit and nid not in touched:
                    touched.append(nid)
            return touched
        pre = self.nets()
        probe = self.clone()
        probe._insert(layer, box)
        idx = len(probe.shapes[layer]) - 1
        members = set()
        for mem in probe.nets().values():
            if (layer, idx) in mem:
                members = set(mem) - {(layer, idx)}
                break
        touched = []
        for nid, mem in pre.items():
            if set(mem) & members and nid not in touched:
                touched.append(nid)
        return touched

    # -- measurements --
    def spacing_violation(self, layer, win, limit):
        owner = {}
        for nid, members in self.nets().items():
            for (m, i) in members:
                if m == layer:
                    owner[i] = nid
        bxs = self.shapes.get(layer, [])
        idx = [i for i, b in enumerate(bxs) if b.touches(win)]
        worst = None
        for a in range(len(idx)):
            for b in range(a + 1, len(idx)):
                i, j = idx[a], idx[b]
                if owner.get(i) == owner.get(j):
                    continue
                g = bxs[i].gap_to(bxs[j])
                if g < limit and (worst is None or g < worst):
                    worst = g
        return worst

    def width_violation(self, layer, win, limit):
        worst = None
        for b in self.shapes.get(layer, []):
            if b.touches(win):
                w = b.width()
                if w < limit and (worst is None or w < worst):
                    worst = w
        return worst


# ---------------------------------------------------------------------------
# the indexed, incremental engine


class Design:
    """Spatially indexed, incrementally connected. Same semantics as
    SimpleDesign; the partition is maintained across `add` rather than
    recomputed, and rebuilt lazily after `sub` (which can only break
    connections, never create them)."""

    def __init__(self):
        self.metals = {}
        self.vias = {}
        self.labels = []
        self._shapes = {}      # layer -> {sid: Box}
        self._grid = {}        # layer -> {(cx, cy): set(sid)}
        self._big = {}         # layer -> set(sid)
        self._next_sid = 0
        self._uf = None        # None => partition needs a rebuild
        self._ncomp = 0
        self._key = {}         # uf root -> (layer, x1, y1), the net's id key

    # -- declarations / lifecycle --
    def declare_metal(self, name, num, dtype):
        self.metals[name] = (num, dtype)
        self._shapes.setdefault(name, {})
        self._grid.setdefault(name, {})
        self._big.setdefault(name, set())

    def declare_via(self, name, num, dtype, ma, mb):
        self.vias[name] = ((num, dtype), (ma, mb))
        self._shapes.setdefault(name, {})
        self._grid.setdefault(name, {})
        self._big.setdefault(name, set())

    def clone(self):
        n = Design.__new__(Design)
        n.metals, n.vias = dict(self.metals), dict(self.vias)
        n.labels = list(self.labels)
        # Box is immutable, so shapes are shared rather than copied
        n._shapes = dict((k, dict(v)) for k, v in self._shapes.items())
        n._grid = dict((k, dict((c, set(s)) for c, s in g.items()))
                       for k, g in self._grid.items())
        n._big = dict((k, set(v)) for k, v in self._big.items())
        n._next_sid = self._next_sid
        n._uf = None if self._uf is None else _copy_uf(self._uf)
        n._ncomp = self._ncomp
        n._key = dict(self._key)
        return n

    def absorb(self, shadow):
        self.metals, self.vias = shadow.metals, shadow.vias
        self.labels = shadow.labels
        self._shapes, self._grid, self._big = (shadow._shapes, shadow._grid,
                                               shadow._big)
        self._next_sid = shadow._next_sid
        self._uf, self._ncomp = shadow._uf, shadow._ncomp
        self._key = shadow._key

    # -- compatibility view --
    @property
    def shapes(self):
        """Layer -> list of boxes. A materialized view; the engine works on
        {sid: Box} dicts so that removal does not shift identifiers."""
        return dict((k, list(v.values())) for k, v in self._shapes.items())

    def boxes(self, layer):
        return self._shapes.get(layer, {})

    # -- spatial index --
    @staticmethod
    def _cells(box):
        x0, y0 = box.x1 // _CELL, box.y1 // _CELL
        x1, y1 = box.x2 // _CELL, box.y2 // _CELL
        if (x1 - x0 + 1) * (y1 - y0 + 1) > _MAX_CELLS:
            return None
        return [(cx, cy)
                for cx in range(x0, x1 + 1)
                for cy in range(y0, y1 + 1)]

    def _index_add(self, layer, sid, box):
        cells = self._cells(box)
        if cells is None:
            self._big.setdefault(layer, set()).add(sid)
            return
        g = self._grid.setdefault(layer, {})
        for c in cells:
            g.setdefault(c, set()).add(sid)

    def _index_del(self, layer, sid, box):
        cells = self._cells(box)
        if cells is None:
            self._big.get(layer, set()).discard(sid)
            return
        g = self._grid.get(layer, {})
        for c in cells:
            bucket = g.get(c)
            if bucket:
                bucket.discard(sid)
                if not bucket:
                    del g[c]

    def _near(self, layer, box):
        """Candidate sids on `layer` whose box might touch `box`."""
        out = set(self._big.get(layer, ()))
        g = self._grid.get(layer)
        if g:
            cells = self._cells(box)
            if cells is None:
                for bucket in g.values():
                    out |= bucket
            else:
                for c in cells:
                    b = g.get(c)
                    if b:
                        out |= b
        return out

    def _touching(self, layer, box, exclude=()):
        shp = self._shapes.get(layer, {})
        return [s for s in self._near(layer, box)
                if s not in exclude and s in shp and shp[s].touches(box)]

    # -- writes --
    def _put(self, layer, box):
        sid = self._next_sid
        self._next_sid += 1
        self._shapes.setdefault(layer, {})[sid] = box
        self._index_add(layer, sid, box)
        return sid

    def _drop(self, layer, sid):
        box = self._shapes[layer].pop(sid)
        self._index_del(layer, sid, box)
        return box

    def add(self, layer, box):
        # coalesce into the largest rectangle this addition can form
        absorbed = []
        merged = True
        while merged:
            merged = False
            shp = self._shapes.get(layer, {})
            for s in self._touching(layer, box):
                u = union_rect(box, shp[s])
                if u is not None:
                    box = u
                    self._drop(layer, s)
                    absorbed.append(s)
                    merged = True
                    break
        sid = self._put(layer, box)
        if self._uf is None:
            return
        # incremental partition maintenance
        uf = self._uf
        if layer in self.metals:
            self._ncomp += 1
            root = uf.find((layer, sid))
            self._key[root] = (layer, box.x1, box.y1)
            roots = set()
            for s in absorbed:
                roots.add(uf.find((layer, s)))
            for s in self._touching(layer, box, exclude=(sid,)):
                roots.add(uf.find((layer, s)))
            for other, _vbox in self._via_links(layer, box):
                roots.add(uf.find(other))
            for r in roots:
                self._join(uf, (layer, sid), r)
        else:
            _, (ma, mb) = self.vias[layer]
            members = ([(ma, s) for s in self._touching(ma, box)]
                       + [(mb, s) for s in self._touching(mb, box)])
            for k in members[1:]:
                self._join(uf, members[0], k)

    def _join(self, uf, a, b):
        """Union two components, carrying the smaller id key to the survivor."""
        ra, rb = uf.find(a), uf.find(b)
        if ra == rb:
            return
        ka, kb = self._key.get(ra), self._key.get(rb)
        uf.union(a, b)
        surv = uf.find(a)
        best = min([k for k in (ka, kb) if k is not None], default=None)
        if best is not None:
            self._key[surv] = best
        self._ncomp -= 1

    def _via_links(self, layer, box):
        """Metal shapes reachable from `box` on `layer` through a via cut."""
        out = []
        for vname, (_, (ma, mb)) in self.vias.items():
            if layer not in (ma, mb):
                continue
            vshp = self._shapes.get(vname, {})
            for vs in self._touching(vname, box):
                vbox = vshp[vs]
                for m in (ma, mb):
                    for s in self._touching(m, vbox):
                        out.append(((m, s), vbox))
        return out

    def bulk_add(self, layer, boxes):
        """Insert many shapes without per-shape partition maintenance.

        Import brings in geometry that some other tool already merged, so the
        incremental bookkeeping has nothing to do but cost time; the partition
        is rebuilt once, lazily, on the first query.
        """
        for b in boxes:
            self._put(layer, b)
        self._uf = None
        self._key = {}

    def sub(self, layer, box):
        shp = self._shapes.get(layer, {})
        hit = [s for s in self._near(layer, box)
               if s in shp and shp[s].overlaps_open(box)]
        pieces = []
        for s in hit:
            pieces.extend(self._drop(layer, s).minus(box))
        for p in pieces:
            self._put(layer, p)
        # re-coalesce the fragments, which may recombine into rectangles
        again = True
        while again:
            again = False
            shp = self._shapes.get(layer, {})
            for s in list(shp):
                if s not in shp:
                    continue
                for t in self._touching(layer, shp[s], exclude=(s,)):
                    u = union_rect(shp[s], shp[t])
                    if u is not None:
                        self._drop(layer, s)
                        self._drop(layer, t)
                        self._put(layer, u)
                        again = True
                        break
                if again:
                    break
        # subtraction can only break connections; rebuild lazily
        self._uf = None
        self._key = {}

    def add_label(self, layer, text, x, y):
        self.labels.append((layer, text, x, y))

    # -- connectivity --
    def _ensure(self):
        if self._uf is not None:
            return self._uf
        uf = UF()
        self._uf, self._ncomp, self._key = uf, 0, {}
        for m in self.metals:
            for s, b in self._shapes.get(m, {}).items():
                uf.find((m, s))
                self._key[(m, s)] = (m, b.x1, b.y1)
                self._ncomp += 1
        for m in self.metals:
            shp = self._shapes.get(m, {})
            for s, b in shp.items():
                for t in self._touching(m, b, exclude=(s,)):
                    self._join(uf, (m, s), (m, t))
        for vname, (_, (ma, mb)) in self.vias.items():
            for vbox in self._shapes.get(vname, {}).values():
                members = ([(ma, s) for s in self._touching(ma, vbox)]
                           + [(mb, s) for s in self._touching(mb, vbox)])
                for k in members[1:]:
                    self._join(uf, members[0], k)
        return uf

    def _net_key(self, members):
        best = None
        for (m, s) in members:
            b = self._shapes[m][s]
            key = (m, b.x1, b.y1)
            if best is None or key < best:
                best = key
        return best

    def _net_id(self, members):
        return "%s@%d,%d" % self._net_key(members)

    def _partition(self):
        uf = self._ensure()
        parts = {}
        for m in self.metals:
            for s in self._shapes.get(m, {}):
                parts.setdefault(uf.find((m, s)), set()).add((m, s))
        return parts

    def nets(self):
        return dict((self._net_id(mem), frozenset(mem))
                    for mem in self._partition().values())

    def net_count(self):
        self._ensure()
        return self._ncomp

    def _sid_net(self, layer, sid):
        """The net id of one shape, in near-constant time.

        Enumerating the component to find its lowest corner would make every
        touched-net probe O(shapes); the key is carried on the union-find root
        instead and updated as components merge.
        """
        uf = self._ensure()
        key = self._key.get(uf.find((layer, sid)))
        return None if key is None else "%s@%d,%d" % key

    def net_at(self, layer, x, y):
        shp = self._shapes.get(layer, {})
        probe = _point_box(x, y)
        for s in self._near(layer, probe):
            if s in shp and shp[s].contains_pt(x, y):
                return self._sid_net(layer, s)
        return None

    def net_probe(self, nid):
        for n, mem in self.nets().items():
            if n == nid:
                return self._net_key(mem)
        return None

    def on_metal(self, layer, x, y):
        shp = self._shapes.get(layer, {})
        probe = _point_box(x, y)
        return any(s in shp and shp[s].contains_pt(x, y)
                   for s in self._near(layer, probe))

    def nets_touching(self, layer, box):
        """Nets the box would join, computed from the index directly.

        The obvious implementation clones the design, inserts the shape and
        re-partitions. That is one full copy and one full connectivity pass per
        `add`, which is what made a run of adds cubic.
        """
        self._ensure()
        touched = []

        def note(m, s):
            nid = self._sid_net(m, s)
            if nid is not None and nid not in touched:
                touched.append(nid)

        if layer in self.vias:
            _, (ma, mb) = self.vias[layer]
            for m in (ma, mb):
                for s in self._touching(m, box):
                    note(m, s)
            return touched
        for s in self._touching(layer, box):
            note(layer, s)
        for (m, s), _vbox in self._via_links(layer, box):
            note(m, s)
        return touched

    # -- measurements --
    def width_violation(self, layer, win, limit):
        shp = self._shapes.get(layer, {})
        worst = None
        for s in self._near(layer, win):
            b = shp.get(s)
            if b is not None and b.touches(win):
                w = b.width()
                if w < limit and (worst is None or w < worst):
                    worst = w
        return worst

    def spacing_violation(self, layer, win, limit):
        uf = self._ensure()
        shp = self._shapes.get(layer, {})
        worst = None
        seen = set()
        for s in self._near(layer, win):
            b = shp.get(s)
            if b is None or not b.touches(win):
                continue
            halo = Box(b.x1 - limit, b.y1 - limit, b.x2 + limit, b.y2 + limit)
            for t in self._near(layer, halo):
                if t == s or (t, s) in seen:
                    continue
                seen.add((s, t))
                ob = shp.get(t)
                if ob is None or not ob.touches(win):
                    # `spacing(layer, window)` measures pairs INSIDE the
                    # window; the halo is only how candidates are found
                    continue
                if uf.find((layer, s)) == uf.find((layer, t)):
                    continue
                g = b.gap_to(ob)
                if g < limit and (worst is None or g < worst):
                    worst = g
        return worst


def _point_box(x, y):
    """A degenerate query box for point lookups (never stored)."""
    b = Box.__new__(Box)
    b.x1 = b.x2 = x
    b.y1 = b.y2 = y
    return b


def _copy_uf(uf):
    n = UF()
    n.p = dict(uf.p)
    return n
