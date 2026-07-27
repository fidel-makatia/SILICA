#!/usr/bin/env python3
"""SILICA v0.2 reference interpreter.

v0.2 makes SILICA a full general-purpose language (functions, control flow,
integer/string/list values) and makes the runtime TOOL-AGNOSTIC: the
interpreter speaks only the backend protocol below. `silica.Design` is the
pure-Python reference backend; `backends/klayout_backend.py` adapts KLayout.
Nothing in the interpreter knows which backend it is driving.

Backend protocol (all geometry crosses the interface as integer-DBU Box):
    declare_metal(name, l, d) / declare_via(name, l, d, ma, mb)
    clone() -> backend                  shadow copy for tx execution
    absorb(shadow)                      commit: adopt the shadow's state
    add(layer, box) / sub(layer, box)   geometry writes
    add_label(layer, text, x, y)
    on_metal(layer, x, y) -> bool
    nets() -> {root: members}           connectivity partition (opaque ids)
    net_count() -> int
    net_at(layer, x, y) -> id | None
    nets_touching(layer, box) -> [id]   nets the box would connect to
    min_spacing(layer, win) -> num|None between DISTINCT nets in window
    min_width(layer, win) -> num|None

Unchanged v0.1 principles, each answering a field bug:
  * integer DBU only; off-grid box construction is a hard error
  * `/` divides exactly or errors -- SILICA never rounds a coordinate
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

class _Return(Exception):
    def __init__(self, value): self.value = value

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
    """Pure-Python reference backend. Implements the full backend protocol."""
    def __init__(self):
        self.shapes = {}     # layer name -> [Box]
        self.labels = []     # (layer, text, x, y)
        self.metals = {}     # name -> (l,d)
        self.vias   = {}     # name -> ((l,d), (metal_a, metal_b))
    # -- protocol: declarations / lifecycle --
    def declare_metal(self, name, l, d): self.metals[name] = (l, d)
    def declare_via(self, name, l, d, ma, mb):
        self.vias[name] = ((l, d), (ma, mb))
    def clone(self): return copy.deepcopy(self)
    def absorb(self, shadow):
        self.shapes, self.labels = shadow.shapes, shadow.labels
        self.metals, self.vias = shadow.metals, shadow.vias
    # -- protocol: writes --
    def add(self, layer, box): self.shapes.setdefault(layer, []).append(box)
    def sub(self, layer, box):
        out = []
        for s in self.shapes.get(layer, []):
            out.extend(s.minus(box))
        self.shapes[layer] = out
    def add_label(self, layer, text, x, y):
        self.labels.append((layer, text, x, y))
    # -- protocol: connectivity --
    def nets(self):
        uf = UF()
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
    def net_count(self): return len(self.nets())
    def net_at(self, layer, x, y):
        for i, b in enumerate(self.shapes.get(layer, [])):
            if b.contains_pt(x, y):
                for root, members in self.nets().items():
                    if (layer, i) in members: return frozenset(members)
        return None
    def on_metal(self, layer, x, y):
        return any(b.contains_pt(x, y) for b in self.shapes.get(layer, []))
    def nets_touching(self, layer, box):
        # via layers: the cut's touched nets are those of BOTH connected metals
        if layer in self.vias:
            _, (ma, mb) = self.vias[layer]
            pre = self.nets()
            touched = []
            for m in (ma, mb):
                for i, b in enumerate(self.shapes.get(m, [])):
                    if box.touches(b):
                        for root, mem in pre.items():
                            if (m, i) in mem:
                                fs = frozenset(mem)
                                if fs not in touched: touched.append(fs)
            return touched
        # metal layers: probe-union (also captures via-mediated connection)
        pre = self.nets()
        probe = self.clone(); probe.add(layer, box)
        idx = len(probe.shapes[layer]) - 1
        members = set()
        for root, mem in probe.nets().items():
            if (layer, idx) in mem:
                members = set(mem) - {(layer, idx)}; break
        touched = []
        for root, mem in pre.items():
            if set(mem) & members:
                fs = frozenset(mem)
                if fs not in touched: touched.append(fs)
        return touched
    # -- protocol: measurements --
    def min_spacing(self, layer, win):
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
# lexer
TOKEN = re.compile(
    r'\s*(?:(//[^\n]*)'
    r'|"([^"]*)"'
    r'|([A-Za-z_][A-Za-z0-9_]*)'
    r'|(\d+)'
    r'|(<=|>=|==|!=|&&|\|\|)'
    r'|([{}()\[\],.=+\-*/%<>!]))')

def lex(src):
    toks, i = [], 0
    while i < len(src):
        if src[i:].isspace(): break
        m = TOKEN.match(src, i)
        if not m:
            raise ParseError(f"lex error at: {src[i:i+30]!r}")
        i = m.end()
        if m.group(1): continue                       # comment
        if m.group(2) is not None: toks.append(("str", m.group(2)))
        elif m.group(3): toks.append(("id", m.group(3)))
        elif m.group(4): toks.append(("int", int(m.group(4))))
        elif m.group(5): toks.append(("sym", m.group(5)))
        elif m.group(6): toks.append(("sym", m.group(6)))
    return toks

# ----------------------------------------------------------------------------
# parser -- full language, recursive descent with precedence climbing
class Parser:
    def __init__(self, toks): self.t, self.i = toks, 0
    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else ("eof", "")
    def next(self):
        tok = self.peek(); self.i += 1; return tok
    def expect(self, kind, val=None):
        k, v = self.next()
        if k != kind or (val is not None and v != val):
            raise ParseError(f"expected {val or kind}, got {v!r}")
        return v
    def accept(self, kind, val):
        k, v = self.peek()
        if k == kind and v == val:
            self.i += 1; return True
        return False
    def kw(self, word): self.expect("id", word)

    def parse(self):
        stmts = []
        while self.peek()[0] != "eof":
            stmts.append(self.stmt())
        return stmts

    def block(self):
        self.expect("sym", "{")
        stmts = []
        while not self.accept("sym", "}"):
            if self.peek()[0] == "eof": raise ParseError("unterminated block")
            stmts.append(self.stmt())
        return stmts

    def stmt(self):
        k, v = self.peek()
        if k == "id":
            if v == "design": return self.design_decl()
            if v == "stack": return self.stack_decl()
            if v == "rules": return self.rules_decl()
            if v == "invariants": return self.inv_decl()
            if v == "fn": return self.fn_decl()
            if v == "let":
                self.next(); name = self.expect("id")
                self.expect("sym", "=")
                return ("let", name, self.expr())
            if v == "if": return self.if_stmt()
            if v == "while":
                self.next(); c = self.expr(); return ("while", c, self.block())
            if v == "for":
                self.next(); var = self.expect("id"); self.kw("in")
                return ("for", var, self.expr(), self.block())
            if v == "return":
                self.next()
                if self.peek() == ("sym", "}"): return ("return", None)
                return ("return", self.expr())
            if v == "tx":
                self.next(); name = self.expect("id")
                return ("tx", name, self.block())
            if v == "add":
                self.next(); layer = self.expect("id")
                bx = self.expr(); self.kw("on")
                return ("add", layer, bx, self.net_expr())
            if v == "sub":
                self.next(); layer = self.expect("id"); bx = self.expr()
                return ("sub", layer, bx, self.accept("id", "splitting"))
            if v == "label":
                self.next(); layer = self.expect("id")
                text = self.expr(); self.kw("at")
                self.expect("sym", "(")
                x = self.expr(); self.expect("sym", ","); y = self.expr()
                self.expect("sym", ")")
                return ("label", layer, text, x, y)
            if v == "assert":
                self.next(); chk = self.check_expr()
                self.expect("sym", ">=")
                return ("assert", chk, self.expr())
        # expression or assignment
        e = self.expr()
        if self.accept("sym", "="):
            rhs = self.expr()
            if e[0] == "var": return ("assign", e[1], rhs)
            if e[0] == "index": return ("assignidx", e[1], e[2], rhs)
            raise ParseError("invalid assignment target")
        return ("expr", e)

    def design_decl(self):
        self.kw("design"); f = self.expect("str")
        self.kw("top"); top = self.expect("id")
        self.kw("units"); units = self.expect("id")
        self.kw("grid"); g = self.expect("int")
        return ("design", f, top, units, g)

    def stack_decl(self):
        self.kw("stack"); self.expect("sym", "{")
        items = []
        while not self.accept("sym", "}"):
            kind = self.expect("id")
            name = self.expect("id"); self.expect("sym", "=")
            self.expect("sym", "(")
            l = self.expect("int"); self.expect("sym", ",")
            d = self.expect("int"); self.expect("sym", ")")
            if kind == "metal":
                items.append(("metal", name, l, d))
            elif kind == "via":
                self.kw("connects"); self.expect("sym", "(")
                a = self.expect("id"); self.expect("sym", ",")
                b = self.expect("id"); self.expect("sym", ")")
                items.append(("via", name, l, d, a, b))
            else:
                raise ParseError(f"unknown stack item {kind!r}")
        return ("stack", items)

    def rules_decl(self):
        self.kw("rules"); self.expect("sym", "{")
        rules = []
        while not self.accept("sym", "}"):
            layer = self.expect("id"); self.expect("sym", ".")
            kind = self.expect("id")
            conds = []
            if self.accept("sym", "("):        # conditional rule: stored;
                depth = 1                      # v0.2 checks unconditional only
                while depth:
                    k, v = self.next()
                    if k == "eof": raise ParseError("unterminated rule condition")
                    if k == "sym" and v == "(": depth += 1
                    elif k == "sym" and v == ")": depth -= 1
                    else: conds.append(v)
            self.expect("sym", ">=")
            rules.append((layer, kind, self.expr(), conds))
        return ("rules", rules)

    def inv_decl(self):
        self.kw("invariants"); self.expect("sym", "{")
        names = [self.expect("id")]
        while self.accept("sym", ","):
            names.append(self.expect("id"))
        self.expect("sym", "}")
        return ("inv", names)

    def fn_decl(self):
        self.kw("fn"); name = self.expect("id")
        self.expect("sym", "(")
        params = []
        if not self.accept("sym", ")"):
            params.append(self.expect("id"))
            while self.accept("sym", ","):
                params.append(self.expect("id"))
            self.expect("sym", ")")
        return ("fn", name, params, self.block())

    def if_stmt(self):
        self.kw("if"); c = self.expr(); then = self.block()
        els = None
        if self.accept("id", "else"):
            els = [self.if_stmt()] if self.peek() == ("id", "if") else self.block()
        return ("if", c, then, els)

    def net_expr(self):
        k, v = self.peek()
        if v == "new_net": self.next(); return ("newnet",)
        if v == "net_at":
            self.next(); self.expect("sym", "(")
            layer = self.expect("id"); self.expect("sym", ",")
            x = self.expr(); self.expect("sym", ",")
            y = self.expr(); self.expect("sym", ")")
            return ("netat", layer, x, y)
        if v == "merge":
            self.next(); self.expect("sym", "(")
            a = self.net_expr(); self.expect("sym", ",")
            b = self.net_expr(); self.expect("sym", ")")
            return ("merge", a, b)
        raise ParseError(f"expected net expression, got {v!r}")

    def check_expr(self):
        name = self.expect("id")
        if name not in ("spacing", "width"):
            raise ParseError(f"unknown check {name!r}")
        self.expect("sym", "(")
        layer = self.expect("id"); self.expect("sym", ",")
        self.kw("window"); self.expect("sym", "(")
        es = [self.expr()]
        for _ in range(3):
            self.expect("sym", ","); es.append(self.expr())
        self.expect("sym", ")"); self.expect("sym", ")")
        return ("check", name, layer, es)

    # ---- expressions ----
    def _match_ops(self, *ops):
        k, v = self.peek()
        if (k == "sym" or k == "id") and v in ops:
            self.i += 1; return v
        return None

    def expr(self): return self.p_or()
    def p_or(self):
        e = self.p_and()
        while self._match_ops("||", "or"):
            e = ("bin", "or", e, self.p_and())
        return e
    def p_and(self):
        e = self.p_eq()
        while self._match_ops("&&", "and"):
            e = ("bin", "and", e, self.p_eq())
        return e
    def p_eq(self):
        e = self.p_cmp()
        while True:
            op = self._match_ops("==", "!=")
            if not op: return e
            e = ("bin", op, e, self.p_cmp())
    def p_cmp(self):
        e = self.p_add()
        while True:
            op = self._match_ops("<", ">", "<=", ">=")
            if not op: return e
            e = ("bin", op, e, self.p_add())
    def p_add(self):
        e = self.p_mul()
        while True:
            op = self._match_ops("+", "-")
            if not op: return e
            e = ("bin", op, e, self.p_mul())
    def p_mul(self):
        e = self.p_un()
        while True:
            op = self._match_ops("*", "/", "%")
            if not op: return e
            e = ("bin", op, e, self.p_un())
    def p_un(self):
        if self._match_ops("-"): return ("un", "-", self.p_un())
        if self._match_ops("!", "not"): return ("un", "not", self.p_un())
        return self.p_post()
    def p_post(self):
        e = self.p_prim()
        while True:
            if self.accept("sym", "("):
                args = []
                if not self.accept("sym", ")"):
                    args.append(self.expr())
                    while self.accept("sym", ","):
                        args.append(self.expr())
                    self.expect("sym", ")")
                e = ("call", e, args)
            elif self.accept("sym", "["):
                idx = self.expr(); self.expect("sym", "]")
                e = ("index", e, idx)
            else:
                return e
    def p_prim(self):
        k, v = self.next()
        if k == "int": return ("int", v)
        if k == "str": return ("str", v)
        if k == "id":
            if v == "true": return ("bool", True)
            if v == "false": return ("bool", False)
            return ("var", v)
        if k == "sym" and v == "(":
            e = self.expr(); self.expect("sym", ")"); return e
        if k == "sym" and v == "[":
            items = []
            k2, v2 = self.peek()
            if not (k2 == "sym" and v2 == "]"):
                items.append(self.expr())
                while self.accept("sym", ","):
                    items.append(self.expr())
            self.expect("sym", "]")
            return ("list", items)
        raise ParseError(f"unexpected token {v!r}")

# ----------------------------------------------------------------------------
# evaluator
def truthy(v): return bool(v)

class Env:
    def __init__(self, parent=None): self.v, self.parent = {}, parent
    def get(self, n):
        e = self
        while e:
            if n in e.v: return e.v[n]
            e = e.parent
        raise ParseError(f"undefined name {n!r}")
    def define(self, n, val): self.v[n] = val
    def assign(self, n, val):
        e = self
        while e:
            if n in e.v: e.v[n] = val; return
            e = e.parent
        raise ParseError(f"assignment to undefined name {n!r}")

class Func:
    def __init__(self, name, params, body, env):
        self.name, self.params, self.body, self.env = name, params, body, env

_DECLS = {"design", "stack", "rules", "inv", "fn"}

class Interp:
    def __init__(self, prog, design=None):
        self.prog = prog
        self.backend = design if design is not None else Design()
        self.grid, self.units = 1, "nm"
        self.rules, self.invariants = [], set()
        self.results = []
        self.txctx = None
        self.genv = Env()
        for name, f in {"print": print, "len": len, "str": str, "abs": abs,
                        "min": min, "max": max,
                        "range": lambda *a: list(range(*a)),
                        "append": lambda l, x: l.append(x)}.items():
            self.genv.define(name, f)

    def run(self):
        try:
            for s in self.prog:
                self.exec_stmt(s, self.genv)
        except _Return:
            raise ParseError("return outside function")
        return self.results

    # ---- statements ----
    def exec_block(self, stmts, env):
        for s in stmts: self.exec_stmt(s, env)

    def exec_stmt(self, s, env):
        t = s[0]
        if t in _DECLS and self.txctx is not None:
            raise ParseError(f"declaration `{t}` not allowed inside tx")
        if t == "design":
            self.units, self.grid = s[3], s[4]
        elif t == "stack":
            for it in s[1]:
                if it[0] == "metal":
                    self.backend.declare_metal(it[1], it[2], it[3])
                else:
                    self.backend.declare_via(it[1], it[2], it[3], it[4], it[5])
        elif t == "rules":
            for (layer, kind, val, conds) in s[1]:
                self.rules.append((layer, kind, self.eval(val, env), conds))
        elif t == "inv":
            self.invariants.update(s[1])
        elif t == "fn":
            env.define(s[1], Func(s[1], s[2], s[3], env))
        elif t == "let":
            env.define(s[1], self.eval(s[2], env))
        elif t == "assign":
            env.assign(s[1], self.eval(s[2], env))
        elif t == "assignidx":
            seq = self.eval(s[1], env)
            seq[self.eval(s[2], env)] = self.eval(s[3], env)
        elif t == "if":
            if truthy(self.eval(s[1], env)):
                self.exec_block(s[2], Env(env))
            elif s[3] is not None:
                self.exec_block(s[3], Env(env))
        elif t == "while":
            while truthy(self.eval(s[1], env)):
                self.exec_block(s[2], Env(env))
        elif t == "for":
            seq = self.eval(s[2], env)
            if not isinstance(seq, list):
                raise ParseError("`for` iterates a list")
            for v in seq:
                b = Env(env); b.define(s[1], v)
                self.exec_block(s[3], b)
        elif t == "return":
            raise _Return(self.eval(s[1], env) if s[1] is not None else None)
        elif t == "expr":
            self.eval(s[1], env)
        elif t == "tx":
            self.exec_tx(s[1], s[2], env)
        elif t == "add":
            self.exec_add(s, env)
        elif t == "sub":
            self.exec_sub(s, env)
        elif t == "label":
            self.exec_label(s, env)
        elif t == "assert":
            self.exec_assert(s, env)
        else:
            raise ParseError(f"unimplemented statement {t!r}")

    # ---- transactions ----
    def exec_tx(self, name, body, env):
        if self.txctx is not None:
            raise ParseError("nested tx is not allowed")
        shadow = self.backend.clone()
        ctx = type("Ctx", (), {})()
        ctx.shadow = shadow
        ctx.pre = shadow.net_count()
        ctx.declared_new = ctx.allowed_extra = 0
        ctx.added, ctx.touched = [], []
        self.txctx = ctx
        try:
            self.exec_block(body, Env(env))
            self.check_commit(ctx)
        except Counterexample as ce:
            self.results.append((name, False, ce.data))
            return
        finally:
            self.txctx = None
        self.backend.absorb(shadow)
        self.results.append((name, True, None))

    def _tx(self, what):
        if self.txctx is None:
            raise ParseError(f"`{what}` outside tx")
        return self.txctx

    def _grid(self, *vals):
        for v in vals:
            if v % self.grid:
                raise ParseError(f"off-grid coordinate {v} (grid {self.grid})")

    def _eval_box(self, e, env):
        v = self.eval(e, env)
        if not isinstance(v, Box):
            raise ParseError("expected a box value")
        return v

    def _resolve_net(self, ne, sh, env):
        if ne[0] != "netat":
            raise ParseError("merge() takes net_at() operands")
        n = sh.net_at(ne[1], self.eval(ne[2], env), self.eval(ne[3], env))
        if n is None:
            raise Counterexample("add.on", "no-net", [], [],
                                 f"net_at({ne[1]},..) found no shape")
        return n

    def exec_add(self, s, env):
        ctx = self._tx("add")
        layer, box, ne = s[1], self._eval_box(s[2], env), s[3]
        sh = ctx.shadow
        touched = sh.nets_touching(layer, box)
        if ne[0] == "newnet":
            if touched:
                raise Counterexample("add.on", "not-new", box.as_list(), touched,
                                     "`new_net` shape touches existing nets")
            ctx.declared_new += 1
        elif ne[0] == "netat":
            target = self._resolve_net(ne, sh, env)
            if not touched:
                raise Counterexample("add.on", "floating", box.as_list(), [],
                                     "shape touches no net")
            if len(touched) > 1:
                raise Counterexample("add.on", "bridge", box.as_list(), touched,
                                     "shape would merge distinct nets")
            if touched[0] != target:
                raise Counterexample("add.on", "wrong-net", box.as_list(), touched,
                                     "shape touches a different net")
        else:  # merge
            a = self._resolve_net(ne[1], sh, env)
            b = self._resolve_net(ne[2], sh, env)
            if a == b:
                raise Counterexample("add.on", "merge-same", box.as_list(), [a],
                                     "merge() operands are already one net")
            if set(touched) != {a, b}:
                raise Counterexample("add.on", "merge-mismatch", box.as_list(),
                                     touched,
                                     "shape must touch exactly the two merged nets")
            ctx.declared_new -= 1
        sh.add(layer, box)
        ctx.added.append((layer, box))
        ctx.touched.append((layer, box))

    def exec_sub(self, s, env):
        ctx = self._tx("sub")
        layer, box, splitting = s[1], self._eval_box(s[2], env), s[3]
        sh = ctx.shadow
        before = sh.net_count()
        sh.sub(layer, box)
        after = sh.net_count()
        if after > before and not splitting:
            raise Counterexample("sub", "split", box.as_list(), [],
                                 f"nets {before}->{after}; declare `splitting` if intended")
        if splitting:
            ctx.allowed_extra += (after - before)
        ctx.touched.append((layer, box))

    def exec_label(self, s, env):
        ctx = self._tx("label")
        layer, text = s[1], self.eval(s[2], env)
        if not isinstance(text, str):
            raise ParseError("label text must be a string")
        x, y = self.eval(s[3], env), self.eval(s[4], env)
        self._grid(x, y)
        if not ctx.shadow.on_metal(layer, x, y):
            raise Counterexample("label", "floating", [x, y], [],
                                 f'label "{text}" attaches to no metal')
        ctx.shadow.add_label(layer, text, x, y)

    def exec_assert(self, s, env):
        ctx = self._tx("assert")
        _, name, layer, es = s[1]
        w = [self.eval(e, env) for e in es]
        self._grid(*w)
        win = Box(*w)
        m = self.eval(s[2], env)
        g = (ctx.shadow.min_spacing(layer, win) if name == "spacing"
             else ctx.shadow.min_width(layer, win))
        if g is not None and g < m:
            raise Counterexample("assert", name, win.as_list(), [],
                                 f"measured {g} < required {m}")

    def check_commit(self, ctx):
        if "connectivity" in self.invariants:
            post = ctx.shadow.net_count()
            want = ctx.pre + ctx.declared_new + ctx.allowed_extra
            if post != want:
                raise Counterexample(
                    "connectivity", "net-count", [], [],
                    f"{ctx.pre}+{ctx.declared_new}+{ctx.allowed_extra} declared, got {post}")
        for (layer, kind, m, conds) in self.rules:
            if conds: continue   # conditional rules unchecked in v0.2
            if kind == "width":
                for (lay, b) in ctx.added:
                    if lay != layer: continue
                    w = min(b.x2 - b.x1, b.y2 - b.y1)
                    if w < m:
                        raise Counterexample("rules.width", f"{layer}>={m}",
                                             b.as_list(), [], f"measured {w}")
            elif kind == "space":
                for (lay, b) in ctx.touched:
                    if lay != layer: continue
                    win = Box(b.x1 - 2*m, b.y1 - 2*m, b.x2 + 2*m, b.y2 + 2*m)
                    g = ctx.shadow.min_spacing(layer, win)
                    if g is not None and g < m:
                        raise Counterexample("rules.space", f"{layer}>={m}",
                                             b.as_list(), [], f"measured {g}")

    # ---- expressions ----
    def eval(self, e, env):
        t = e[0]
        if t == "int" or t == "str" or t == "bool": return e[1]
        if t == "var": return env.get(e[1])
        if t == "list": return [self.eval(x, env) for x in e[1]]
        if t == "index":
            seq = self.eval(e[1], env)
            return seq[self.eval(e[2], env)]
        if t == "un":
            v = self.eval(e[2], env)
            if e[1] == "-":
                if not isinstance(v, int): raise ParseError("unary - on non-int")
                return -v
            return not truthy(v)
        if t == "bin":
            op = e[1]
            if op == "and":
                return truthy(self.eval(e[2], env)) and truthy(self.eval(e[3], env))
            if op == "or":
                return truthy(self.eval(e[2], env)) or truthy(self.eval(e[3], env))
            a, b = self.eval(e[2], env), self.eval(e[3], env)
            if op == "==": return a == b
            if op == "!=": return a != b
            if op == "+":
                for ty in (int, str, list):
                    if isinstance(a, ty) and isinstance(b, ty): return a + b
                raise ParseError("`+` on mismatched types -- SILICA never coerces")
            if op in ("<", ">", "<=", ">="):
                if not (type(a) is type(b) and isinstance(a, (int, str))):
                    raise ParseError(f"`{op}` compares two ints or two strings")
                return {"<": a < b, ">": a > b,
                        "<=": a <= b, ">=": a >= b}[op]
            if not (isinstance(a, int) and isinstance(b, int)):
                raise ParseError(f"`{op}` needs ints")
            if op == "-": return a - b
            if op == "*": return a * b
            if op == "/":
                if b == 0: raise ParseError("division by zero")
                if a % b:
                    raise ParseError(f"inexact division {a}/{b} -- "
                                     "SILICA never rounds a coordinate")
                return a // b
            if op == "%":
                if b == 0: raise ParseError("modulo by zero")
                return a % b
        if t == "call":
            callee = e[1]
            args = [self.eval(a, env) for a in e[2]]
            if callee[0] == "var" and callee[1] == "box":
                if len(args) != 4 or not all(isinstance(a, int) for a in args):
                    raise ParseError("box(x1,y1,x2,y2) takes four ints")
                self._grid(*args)
                return Box(*args)
            f = self.eval(callee, env)
            if isinstance(f, Func):
                if len(args) != len(f.params):
                    raise ParseError(f"{f.name}() takes {len(f.params)} args, "
                                     f"got {len(args)}")
                fenv = Env(f.env)
                for p, a in zip(f.params, args):
                    fenv.define(p, a)
                try:
                    self.exec_block(f.body, fenv)
                except _Return as r:
                    return r.value
                return None
            if callable(f): return f(*args)
            raise ParseError("value is not callable")
        raise ParseError(f"unimplemented expression {t!r}")

# ----------------------------------------------------------------------------
if __name__ == "__main__":
    from silica.cli import main
    sys.exit(main())
