"""Integer-DBU geometry primitives.

`Box` is treated as IMMUTABLE everywhere in SILICA: nothing mutates one in
place, edits replace it. That is what lets a shadow copy share Box objects with
the live design instead of deep-copying them, which is the difference between a
transaction costing O(n) pointer copies and O(n) object constructions.
"""
from silica.errors import ParseError


class Box:
    __slots__ = ("x1", "y1", "x2", "y2")

    def __init__(self, x1, y1, x2, y2):
        if not (x2 > x1 and y2 > y1):
            raise ParseError("degenerate/inverted box (%s,%s,%s,%s) -- "
                             "SILICA never normalizes geometry"
                             % (x1, y1, x2, y2))
        self.x1, self.y1, self.x2, self.y2 = x1, y1, x2, y2

    def touches(self, o):        # overlap or edge/corner abutment
        return not (o.x1 > self.x2 or o.x2 < self.x1 or
                    o.y1 > self.y2 or o.y2 < self.y1)

    def overlaps_open(self, o):  # strict interior overlap
        return (o.x1 < self.x2 and o.x2 > self.x1 and
                o.y1 < self.y2 and o.y2 > self.y1)

    def contains_pt(self, x, y):
        return self.x1 <= x <= self.x2 and self.y1 <= y <= self.y2

    def gap_to(self, o):
        dx = max(o.x1 - self.x2, self.x1 - o.x2, 0)
        dy = max(o.y1 - self.y2, self.y1 - o.y2, 0)
        return (dx * dx + dy * dy) ** 0.5 if (dx and dy) else max(dx, dy)

    def minus(self, o):
        if not self.overlaps_open(o):
            return [self]
        out = []
        if o.x1 > self.x1:
            out.append(Box(self.x1, self.y1, o.x1, self.y2))
        if o.x2 < self.x2:
            out.append(Box(o.x2, self.y1, self.x2, self.y2))
        lo, hi = max(self.x1, o.x1), min(self.x2, o.x2)
        if lo < hi:
            if o.y1 > self.y1:
                out.append(Box(lo, self.y1, hi, o.y1))
            if o.y2 < self.y2:
                out.append(Box(lo, o.y2, hi, self.y2))
        return out

    def width(self):
        return min(self.x2 - self.x1, self.y2 - self.y1)

    def as_list(self):
        return [self.x1, self.y1, self.x2, self.y2]

    def __repr__(self):
        return "(%d,%d,%d,%d)" % (self.x1, self.y1, self.x2, self.y2)


def union_rect(a, b):
    """The union of two boxes when that union is itself a rectangle, else None.

    Backends keep geometry in this canonical, maximally-coalesced form so that a
    measurement like `width` sees the same decomposition a merging backend
    (KLayout) sees. Without it, one wire stored as two abutting boxes would
    measure as two narrow shapes on one backend and one wide shape on the other
    -- the same program reaching different verdicts.
    """
    if a.x1 <= b.x1 and a.y1 <= b.y1 and a.x2 >= b.x2 and a.y2 >= b.y2:
        return a
    if b.x1 <= a.x1 and b.y1 <= a.y1 and b.x2 >= a.x2 and b.y2 >= a.y2:
        return b
    if a.x1 == b.x1 and a.x2 == b.x2 and b.y1 <= a.y2 and a.y1 <= b.y2:
        return Box(a.x1, min(a.y1, b.y1), a.x2, max(a.y2, b.y2))
    if a.y1 == b.y1 and a.y2 == b.y2 and b.x1 <= a.x2 and a.x1 <= b.x2:
        return Box(min(a.x1, b.x1), a.y1, max(a.x2, b.x2), a.y2)
    return None


class UF:
    def __init__(self):
        self.p = {}

    def find(self, a):
        self.p.setdefault(a, a)
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        self.p[ra] = rb
        return True
