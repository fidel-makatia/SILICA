#!/usr/bin/env python3
"""SILICA v0.1 reference interpreter.

Implements the transform layer: design/stack/rules/invariants declarations and
transactional `tx` blocks with add/sub/label/assert over a pure-Python geometry
engine. Every commit checks the declared invariants on a shadow copy; failures
roll back and return a machine-readable Counterexample.

Design principles enforced here (each answers a field bug):
  * integer DBU only; off-grid literals are parse errors
  * Box(x1,y1,x2,y2) requires x2>x1 and y2>y1 -- no silent normalization
  * `add ... on <net>` must touch exactly that net (no bridges, no floats)
  * `sub` may not split its host net unless declared `splitting`
  * labels must attach to metal; floating labels fail the tx
  * there is no warning class
"""
import json, re, sys, copy

# ----------------------------------------------------------------------------
# errors
class SilicaError(Exception): pass
class ParseError(SilicaError): pass

class Counterexample(SilicaError):
    def __init__(self, check, rule, box, nets, note=""):
        self.data = {"check": check, "rule": rule, "box": box,
                     "nets": sorted(str(n) for n in nets), "note": note}
        super().__init__(json.dumps(self.data))

# ----------------------------------------------------------------------------
# geometry engine (pure python; O(n^2) merges -- reference semantics, not speed)
class Box:
    __slots__ = ("x1","y1","x2","y2")
    def __init__(self, x1, y1, x2, y2):
        if not (x2 > x1 and y2 > y1):
            raise ParseError(f"degenerate/inverted box ({x1},{y1},{x2},{y2}) "
                             "-- SILICA never normalizes geometry")
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
        return (dx*dx + dy*dy) ** 0.5 if (dx and dy) else max(dx, dy)
    def minus(self, o):
        if not self.overlaps_open(o): return [self]
        out = []
        if o.x1 > self.x1: out.append(Box(self.x1, self.y1, o.x1, self.y2))
        if o.x2 < self.x2: out.append(Box(o.x2, self.y1, self.x2, self.y2))
        lo, hi = max(self.x1, o.x1), min(self.x2, o.x2)
        if lo < hi:
            if o.y1 > self.y1: out.append(Box(lo, self.y1, hi, o.y1))
            if o.y2 < self.y2: out.append(Box(lo, o.y2, hi, self.y2))
        return out
    def as_list(self): return [self.x1, self.y1, self.x2, self.y2]
    def __repr__(self): return f"({self.x1},{self.y1},{self.x2},{self.y2})"

class UF:
    def __init__(self): self.p = {}
    def find(self, a):
        self.p.setdefault(a, a)
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]; a = self.p[a]
        return a
    def union(self, a, b): self.p[self.find(a)] = self.find(b)

class Design:
    def __init__(self):
        self.shapes = {}     # layer name -> [Box]
        self.labels = []     # (layer, text, x, y)
        self.metals = {}     # name -> (l,d)
        self.vias   = {}     # name -> ((l,d), (metal_a, metal_b))
    def clone(self): return copy.deepcopy(self)
    def add(self, layer, box): self.shapes.setdefault(layer, []).append(box)
    def sub(self, layer, box):
        out = []
        for s in self.shapes.get(layer, []):
            out.extend(s.minus(box))
        self.shapes[layer] = out

    # ---- connectivity: merged components per metal, linked by vias ----
    def nets(self):
        uf = UF()
        comp_of = {}                          # (layer, box-index) -> comp key
        for m in self.metals:
            bxs = self.shapes.get(m, [])
            for i in range(len(bxs)):
                uf.find((m, i))
            for i in range(len(bxs)):
                for j in range(i+1, len(bxs)):
                    if bxs[i].touches(bxs[j]):
                        uf.union((m, i), (m, j))
        for vname, (_, (ma, mb)) in self.vias.items():
            for v in self.shapes.get(vname, []):
                anchor = None
                for m in (ma, mb):
                    for i, b in enumerate(self.shapes.get(m, [])):
                        if v.overlaps_open(b) or v.touches(b):
                            if anchor is None: anchor = (m, i)
                            else: uf.union(anchor, (m, i))
        nets = {}
        for m in self.metals:
            for i in range(len(self.shapes.get(m, []))):
                nets.setdefault(uf.find((m, i)), set()).add((m, i))
        return nets

    def net_at(self, layer, x, y):
        for i, b in enumerate(self.shapes.get(layer, [])):
            if b.contains_pt(x, y):
                nets = self.nets()
                for root, members in nets.items():
                    if (layer, i) in members: return frozenset(members)
        return None

    def net_count(self): return len(self.nets())

    # ---- checks ----
    def min_spacing(self, layer, win):
        """min gap between DISTINCT nets' shapes on `layer` within window."""
        nets = self.nets()
        owner = {}
        for root, members in nets.items():
            for (m, i) in members:
                if m == layer: owner[i] = root
        bxs = self.shapes.get(layer, [])
        idx = [i for i, b in enumerate(bxs) if b.touches(win)]
        best = None
        for a in range(len(idx)):
            for b in range(a+1, len(idx)):
                i, j = idx[a], idx[b]
                if owner.get(i) == owner.get(j): continue
                g = bxs[i].gap_to(bxs[j])
                if best is None or g < best: best = g
        return best        # None = no facing pair in window

    def min_width(self, layer, win):
        best = None
        for b in self.shapes.get(layer, []):
            if b.touches(win):
                w = min(b.x2-b.x1, b.y2-b.y1)
                if best is None or w < best: best = w
        return best

# ----------------------------------------------------------------------------
# lexer / parser
TOKEN = re.compile(r'\s*(?:(//[^\n]*)|("([^"]*)")|([A-Za-z_][A-Za-z0-9_]*)'
                   r'|(-?\d+)|([{}(),.=]|>=))')

def lex(src):
    toks, i = [], 0
    while i < len(src):
        if src[i:].isspace(): break
        m = TOKEN.match(src, i)
        if not m:
            raise ParseError(f"lex error at: {src[i:i+30]!r}")
        i = m.end()
        if m.group(1): continue                       # comment
        if m.group(2): toks.append(("str", m.group(3)))
        elif m.group(4): toks.append(("id", m.group(4)))
        elif m.group(5): toks.append(("int", int(m.group(5))))
        elif m.group(6): toks.append(("sym", m.group(6)))
    return toks

class Parser:
    def __init__(self, toks):
        self.t, self.i = toks, 0
    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else ("eof", "")
    def next(self):
        tok = self.peek(); self.i += 1; return tok
    def expect(self, kind, val=None):
        k, v = self.next()
        if k != kind or (val is not None and v != val):
            raise ParseError(f"expected {val or kind}, got {v!r}")
        return v
    def kw(self, word): self.expect("id", word)

    def parse(self):
        prog = {"stack": [], "rules": [], "invariants": [], "txs": []}
        self.kw("design"); prog["gds"] = self.expect("str")
        self.kw("top");    prog["top"] = self.expect("id")
        self.kw("units");  prog["units"] = self.expect("id")
        self.kw("grid");   prog["grid"] = self.expect("int")
        while self.peek()[0] != "eof":
            k, v = self.peek()
            if v == "stack":      self.parse_stack(prog)
            elif v == "rules":    self.parse_rules(prog)
            elif v == "invariants": self.parse_inv(prog)
            elif v == "tx":       self.parse_tx(prog)
            else: raise ParseError(f"unexpected {v!r}")
        return prog

    def parse_stack(self, prog):
        self.kw("stack"); self.expect("sym","{")
        while self.peek()[1] != "}":
            kind = self.expect("id")
            name = self.expect("id"); self.expect("sym","=")
            self.expect("sym","("); l = self.expect("int")
            self.expect("sym",","); d = self.expect("int"); self.expect("sym",")")
            if kind == "metal":
                prog["stack"].append(("metal", name, (l,d)))
            elif kind == "via":
                self.kw("connects"); self.expect("sym","(")
                a = self.expect("id"); self.expect("sym",",")
                b = self.expect("id"); self.expect("sym",")")
                prog["stack"].append(("via", name, (l,d), (a,b)))
            else: raise ParseError(f"bad stack item {kind!r}")
        self.expect("sym","}")

    def parse_rules(self, prog):
        self.kw("rules"); self.expect("sym","{")
        while self.peek()[1] != "}":
            layer = self.expect("id"); self.expect("sym",".")
            kind = self.expect("id"); self.expect("sym",">=")
            val = self.expect("int")
            prog["rules"].append((layer, kind, val))
        self.expect("sym","}")

    def parse_inv(self, prog):
        self.kw("invariants"); self.expect("sym","{")
        while self.peek()[1] != "}":
            prog["invariants"].append(self.expect("id"))
            if self.peek()[1] == ",": self.next()
        self.expect("sym","}")

    def parse_tx(self, prog):
        self.kw("tx"); name = self.expect("id"); self.expect("sym","{")
        stmts = []
        while self.peek()[1] != "}":
            op = self.expect("id")
            if op == "add":
                layer = self.expect("id"); box = self.parse_box()
                self.kw("on"); net = self.parse_net()
                stmts.append(("add", layer, box, net))
            elif op == "sub":
                layer = self.expect("id"); box = self.parse_box()
                splitting = False
                if self.peek() == ("id","splitting"):
                    self.next(); splitting = True
                stmts.append(("sub", layer, box, splitting))
            elif op == "label":
                layer = self.expect("id"); text = self.expect("str")
                self.kw("at"); self.expect("sym","(")
                x = self.expect("int"); self.expect("sym",",")
                y = self.expect("int"); self.expect("sym",")")
                stmts.append(("label", layer, text, x, y))
            elif op == "assert":
                chk = self.expect("id"); self.expect("sym","(")
                layer = self.expect("id"); self.expect("sym",",")
                self.kw("window"); self.expect("sym","(")
                w = [self.expect("int")]
                for _ in range(3):
                    self.expect("sym",","); w.append(self.expect("int"))
                self.expect("sym",")"); self.expect("sym",")")
                self.expect("sym",">="); val = self.expect("int")
                stmts.append(("assert", chk, layer, w, val))
            else: raise ParseError(f"unknown statement {op!r}")
        self.expect("sym","}")
        prog["txs"].append((name, stmts))

    def parse_box(self):
        self.kw("box"); self.expect("sym","(")
        v = [self.expect("int")]
        for _ in range(3):
            self.expect("sym",","); v.append(self.expect("int"))
        self.expect("sym",")")
        return tuple(v)

    def parse_net(self):
        k, v = self.next()
        if v == "net_at":
            self.expect("sym","(")
            layer = self.expect("id"); self.expect("sym",",")
            x = self.expect("int");   self.expect("sym",",")
            y = self.expect("int");   self.expect("sym",")")
            return ("net_at", layer, x, y)
        if v == "new_net": return ("new_net",)
        raise ParseError(f"bad net expr {v!r}")

# ----------------------------------------------------------------------------
# interpreter
class Interp:
    def __init__(self, prog, design=None):
        self.prog = prog
        self.grid = prog["grid"]
        self.design = design or Design()
        for item in prog["stack"]:
            if item[0] == "metal": self.design.metals[item[1]] = item[2]
            else: self.design.vias[item[1]] = (item[2], item[3])
        self.log = []

    def check_grid(self, *vals):
        for v in vals:
            if v % self.grid:
                raise ParseError(f"off-grid coordinate {v} (grid {self.grid})")

    def run(self):
        results = []
        for name, stmts in self.prog["txs"]:
            results.append(self.run_tx(name, stmts))
        return results

    def run_tx(self, name, stmts):
        shadow = self.design.clone()
        pre_nets = self.design.net_count()
        declared_new = 0
        allowed_extra = 0
        try:
            for st in stmts:
                if st[0] == "add":
                    _, layer, bx, net = st
                    self.check_grid(*bx)
                    box = Box(*bx)
                    if net[0] == "net_at":
                        target = shadow.net_at(net[1], net[2], net[3])
                        if target is None:
                            raise Counterexample("net_at", "no-metal",
                                                 list(net[2:4]), [], "probe hit empty space")
                    else:
                        target, declared_new = None, declared_new + 1
                    # which nets does the new shape touch?
                    touched = self.nets_touching(shadow, layer, box)
                    if net[0] == "net_at":
                        if len(touched) == 0:
                            raise Counterexample("add.on", "floating", box.as_list(),
                                                 [], "shape touches no metal")
                        if len(touched) > 1:
                            raise Counterexample("add.on", "bridge", box.as_list(),
                                                 touched, "shape would merge distinct nets")
                        if touched[0] != self.net_key(shadow, net):
                            raise Counterexample("add.on", "wrong-net", box.as_list(),
                                                 touched, "shape touches a different net")
                    else:
                        if len(touched) > 0:
                            raise Counterexample("add.on", "not-new", box.as_list(),
                                                 touched, "new_net shape touches existing metal")
                    shadow.add(layer, box)
                elif st[0] == "sub":
                    _, layer, bx, splitting = st
                    self.check_grid(*bx)
                    before = shadow.net_count()
                    shadow.sub(layer, Box(*bx))
                    after = shadow.net_count()
                    if after > before and not splitting:
                        raise Counterexample("sub", "split", list(bx), [],
                                             f"nets {before}->{after}; declare `splitting` if intended")
                    if splitting:
                        allowed_extra += (after - before)
                elif st[0] == "label":
                    _, layer, text, x, y = st
                    self.check_grid(x, y)
                    if shadow.net_at(layer, x, y) is None:
                        raise Counterexample("label", "floating", [x, y, x, y], [],
                                             f'label "{text}" attaches to no metal')
                    shadow.labels.append((layer, text, x, y))
                elif st[0] == "assert":
                    _, chk, layer, w, val = st
                    win = Box(*w)
                    got = (shadow.min_spacing(layer, win) if chk == "spacing"
                           else shadow.min_width(layer, win))
                    if got is not None and got < val:
                        raise Counterexample("assert."+chk, f">={val}", w, [],
                                             f"measured {got}")
            # design-level invariants
            if "connectivity" in self.prog["invariants"]:
                post = shadow.net_count()
                if post != pre_nets + declared_new + allowed_extra:
                    raise Counterexample("connectivity", "net-count",
                                         [], [], f"{pre_nets}+{declared_new}+{allowed_extra} declared, got {post}")
            # local rules in the halos of touched geometry (v0.1: whole design)
            for layer, kind, val in self.prog["rules"]:
                win = Box(-10**9, -10**9, 10**9, 10**9)
                got = (shadow.min_spacing(layer, win) if kind == "space"
                       else shadow.min_width(layer, win))
                if got is not None and got < val:
                    raise Counterexample("rules."+kind, f"{layer}>={val}", [], [],
                                         f"measured {got}")
        except Counterexample as ce:
            self.log.append((name, "ROLLBACK", ce.data))
            return (name, False, ce.data)
        self.design = shadow
        self.log.append((name, "COMMIT", None))
        return (name, True, None)

    def nets_touching(self, design, layer, box):
        # via layers: the cut's touched nets are those of BOTH connected metals
        if layer in design.vias:
            _, (ma, mb) = design.vias[layer]
            pre = design.nets()
            touched = []
            for m in (ma, mb):
                for i, b in enumerate(design.shapes.get(m, [])):
                    if box.overlaps_open(b) or box.touches(b):
                        for root, members in pre.items():
                            if (m, i) in members:
                                fs = frozenset(members)
                                if fs not in touched: touched.append(fs)
        # metal layers: probe-union
        if layer in design.vias:
            return touched
        probe = design.clone()
        probe.add(layer, box)
        nets_after = probe.nets()
        # find the component containing the new shape (last index on layer)
        new_idx = len(probe.shapes[layer]) - 1
        merged = None
        for root, members in nets_after.items():
            if (layer, new_idx) in members:
                merged = members; break
        # map back: which pre-existing nets did it swallow?
        pre = design.nets()
        touched = []
        for root, members in pre.items():
            if members & (merged - {(layer, new_idx)}):
                touched.append(frozenset(members))
        return touched

    def net_key(self, design, net_expr):
        return design.net_at(net_expr[1], net_expr[2], net_expr[3])

# ----------------------------------------------------------------------------
def run_file(path, design=None):
    prog = Parser(lex(open(path).read())).parse()
    it = Interp(prog, design)
    results = it.run()
    for name, ok, ce in results:
        print(f"tx {name}: {'COMMIT' if ok else 'ROLLBACK ' + json.dumps(ce)}")
    return it, results

if __name__ == "__main__":
    run_file(sys.argv[1])
