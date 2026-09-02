#!/usr/bin/env python3
"""SILICA reference interpreter.

SILICA is a full general-purpose language (functions, control flow,
integer/string/list values) whose built-in effects are transactional edits to a
design database. The runtime is TOOL-AGNOSTIC: the interpreter speaks only the
backend protocol below. `silica.Design` is the pure-Python reference backend;
`silica/backends/klayout.py` adapts KLayout. Nothing in the interpreter knows
which backend it is driving.

Backend protocol (all geometry crosses the interface as integer-DBU Box):
    declare_metal(name, num, dtype) / declare_via(name, num, dtype, a, b)
    clone() -> backend                  shadow copy for tx execution
    absorb(shadow)                      commit: adopt the shadow's state
    add(layer, box) / sub(layer, box)   geometry writes
    add_label(layer, text, x, y)
    on_metal(layer, x, y) -> bool
    nets() -> {net_id: members}         connectivity partition
    net_count() -> int
    net_at(layer, x, y) -> net_id | None
    net_probe(net_id) -> (layer, x, y)  a point ON that net's geometry
    nets_touching(layer, box) -> [net_id]   nets the box would connect to
    spacing_violation(layer, win, limit) -> measured | None
    width_violation(layer, win, limit)   -> measured | None

Both measurements take the limit they are being judged against, so a backend
built on a real DRC engine can hand the query straight to it instead of
computing a global minimum and having the caller compare. They return the
worst measurement strictly below `limit`, or None when there is no violation.
Backends must agree on the VERDICT; the reported measurement may differ for
non-rectangular geometry (see the KLayout adapter's documented boundary).

Net ids are opaque to the interpreter, which only compares them for equality.
Backends must make them STABLE (independent of insertion order and of shape
indices) and printable, because they are reported in counterexamples.

Principles, each answering a class of field bug:
  * integer DBU only; off-grid box construction is a hard error
  * `/` divides exactly or errors -- SILICA never rounds a coordinate
  * Box(x1,y1,x2,y2) requires x2>x1 and y2>y1 -- no silent normalization
  * `add ... on <net>` must touch exactly that net (no bridges, no floats)
  * `sub` may not change the net count unless declared `splitting`/`deleting`
  * labels must attach to metal; floating labels fail the tx
  * every layer, invariant and rule kind named in a program must be declared
    and implemented -- an unrecognized name is an error, never a silent no-op
  * there is no warning class
"""
import re
import sys

from silica.engine import Design, SimpleDesign
from silica.errors import Counterexample, ParseError, SilicaError
from silica.gds import mapkey as _mapkey, write_gds
from silica.geometry import Box, UF, union_rect

__all_reexports__ = (Design, SimpleDesign, Counterexample, ParseError,
                     SilicaError, Box, UF, union_rect)


class _Return(Exception):
    def __init__(self, value):
        self.value = value


# ----------------------------------------------------------------------------
# lexer

TOKEN = re.compile(
    r'"([^"]*)"'
    r'|([A-Za-z_][A-Za-z0-9_]*)'
    r'|(\d+)'
    r'|(<=|>=|==|!=|&&|\|\||->)'
    r'|([{}()\[\],.=+\-*/%<>!])')


def lex(src):
    """Source -> [(kind, value, line)]. Comments are `//` to end of line."""
    toks, i, line, n = [], 0, 1, len(src)
    while i < n:
        c = src[i]
        if c == "\n":
            line += 1
            i += 1
            continue
        if c.isspace():
            i += 1
            continue
        if src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        m = TOKEN.match(src, i)
        if not m:
            raise ParseError("cannot tokenize %r" % src[i:i + 30], line)
        start_line = line
        line += src.count("\n", i, m.end())
        i = m.end()
        if m.group(1) is not None:
            toks.append(("str", m.group(1), start_line))
        elif m.group(2):
            toks.append(("id", m.group(2), start_line))
        elif m.group(3):
            toks.append(("int", int(m.group(3)), start_line))
        elif m.group(4):
            toks.append(("sym", m.group(4), start_line))
        else:
            toks.append(("sym", m.group(5), start_line))
    return toks


# ----------------------------------------------------------------------------
# parser -- full language, recursive descent with precedence climbing

RULE_KINDS_IMPLEMENTED = ("width", "space")
RULE_KINDS_SPECIFIED = ("enclosure", "area", "density")
INVARIANTS_IMPLEMENTED = ("connectivity",)
INVARIANTS_SPECIFIED = ("ports", "density", "schema")
CHECKS_IMPLEMENTED = ("spacing", "width")


class Parser:
    """Produces statements as (node, line) pairs; blocks are lists of pairs."""

    def __init__(self, toks):
        self.t, self.i = toks, 0

    def _tok(self):
        if self.i < len(self.t):
            return self.t[self.i]
        return ("eof", "", self.t[-1][2] if self.t else 1)

    def peek(self):
        k, v, _ = self._tok()
        return (k, v)

    def line(self):
        return self._tok()[2]

    def next(self):
        k, v, _ = self._tok()
        self.i += 1
        return (k, v)

    def err(self, msg):
        return ParseError(msg, self.line())

    def expect(self, kind, val=None):
        ln = self.line()
        k, v = self.next()
        if k != kind or (val is not None and v != val):
            raise ParseError("expected %s, got %r" % (val or kind, v), ln)
        return v

    def accept(self, kind, val):
        k, v = self.peek()
        if k == kind and v == val:
            self.i += 1
            return True
        return False

    def kw(self, word):
        self.expect("id", word)

    def parse(self):
        stmts = []
        while self.peek()[0] != "eof":
            stmts.append(self.stmt())
        return stmts

    def block(self):
        self.expect("sym", "{")
        stmts = []
        while not self.accept("sym", "}"):
            if self.peek()[0] == "eof":
                raise self.err("unterminated block")
            stmts.append(self.stmt())
        return stmts

    def stmt(self):
        """Returns (node, line)."""
        ln = self.line()
        return (self._stmt_node(), ln)

    def _stmt_node(self):
        k, v = self.peek()
        if k == "id":
            if v == "design":
                return self.design_decl()
            if v == "stack":
                return self.stack_decl()
            if v == "rules":
                return self.rules_decl()
            if v == "invariants":
                return self.inv_decl()
            if v == "fn":
                return self.fn_decl()
            if v == "export":
                return self.export_decl()
            if v == "let":
                self.next()
                name = self.expect("id")
                self.expect("sym", "=")
                return ("let", name, self.expr())
            if v == "if":
                return self.if_stmt()
            if v == "while":
                self.next()
                c = self.expr()
                return ("while", c, self.block())
            if v == "for":
                self.next()
                var = self.expect("id")
                self.kw("in")
                return ("for", var, self.expr(), self.block())
            if v == "return":
                self.next()
                if self.peek() == ("sym", "}"):
                    return ("return", None)
                return ("return", self.expr())
            if v == "tx":
                self.next()
                name = self.expect("id")
                return ("tx", name, self.block())
            if v == "add":
                self.next()
                layer = self.expect("id")
                bx = self.expr()
                self.kw("on")
                return ("add", layer, bx, self.net_expr())
            if v == "sub":
                self.next()
                layer = self.expect("id")
                bx = self.expr()
                mods = {}
                while True:
                    if self.accept("id", "splitting"):
                        key = "splitting"
                    elif self.accept("id", "deleting"):
                        key = "deleting"
                    else:
                        break
                    if key in mods:
                        raise self.err("`%s` declared twice" % key)
                    self.accept("id", "into")
                    n = None
                    if self.peek()[0] == "int":
                        ln2 = self.line()
                        n = self.expect("int")
                        if n < 1:
                            raise ParseError(
                                "`%s %d` declares nothing; omit the count to "
                                "declare the effect loosely" % (key, n), ln2)
                    mods[key] = n
                return ("sub", layer, bx, mods)
            if v == "label":
                self.next()
                layer = self.expect("id")
                text = self.expr()
                self.kw("at")
                self.expect("sym", "(")
                x = self.expr()
                self.expect("sym", ",")
                y = self.expr()
                self.expect("sym", ")")
                return ("label", layer, text, x, y)
            if v == "assert":
                self.next()
                chk = self.check_expr()
                self.expect("sym", ">=")
                return ("assert", chk, self.expr())
        # expression or assignment
        e = self.expr()
        if self.accept("sym", "="):
            rhs = self.expr()
            if e[0] == "var":
                return ("assign", e[1], rhs)
            if e[0] == "index":
                return ("assignidx", e[1], e[2], rhs)
            raise self.err("invalid assignment target")
        return ("expr", e)

    def design_decl(self):
        self.kw("design")
        f = self.expect("str")
        self.kw("top")
        top = self.expect("id")
        self.kw("units")
        ln = self.line()
        units = self.expect("id")
        if units not in ("nm", "um"):
            raise ParseError("units must be `nm` or `um`, got %r" % units, ln)
        self.kw("grid")
        ln = self.line()
        g = self.expect("int")
        if g != int(g) or g < 1:
            raise ParseError("grid must be a positive integer", ln)
        return ("design", f, top, units, g)

    def stack_decl(self):
        self.kw("stack")
        self.expect("sym", "{")
        items = []
        while not self.accept("sym", "}"):
            ln = self.line()
            kind = self.expect("id")
            name = self.expect("id")
            self.expect("sym", "=")
            self.expect("sym", "(")
            num = self.expect("int")
            self.expect("sym", ",")
            dtype = self.expect("int")
            self.expect("sym", ")")
            if kind == "metal":
                items.append(("metal", name, num, dtype))
            elif kind == "via":
                self.kw("connects")
                self.expect("sym", "(")
                a = self.expect("id")
                self.expect("sym", ",")
                b = self.expect("id")
                self.expect("sym", ")")
                items.append(("via", name, num, dtype, a, b))
            else:
                raise ParseError("unknown stack item %r -- expected `metal` "
                                 "or `via`" % kind, ln)
        return ("stack", items)

    def rules_decl(self):
        self.kw("rules")
        self.expect("sym", "{")
        rules = []
        while not self.accept("sym", "}"):
            ln = self.line()
            layer = self.expect("id")
            self.expect("sym", ".")
            kind = self.expect("id")
            if kind in RULE_KINDS_SPECIFIED:
                raise ParseError(
                    "rule kind `%s` is specified but not implemented -- "
                    "SILICA will not accept a rule it cannot check" % kind, ln)
            if kind not in RULE_KINDS_IMPLEMENTED:
                raise ParseError(
                    "unknown rule kind %r (implemented: %s)"
                    % (kind, ", ".join(RULE_KINDS_IMPLEMENTED)), ln)
            if self.peek() == ("sym", "("):
                raise ParseError(
                    "conditional rules (`%s.%s(...)`) are in the grammar but "
                    "not checked yet -- SILICA will not accept a rule it "
                    "cannot check" % (layer, kind), ln)
            self.expect("sym", ">=")
            rules.append((layer, kind, self.expr(), ln))
        return ("rules", rules)

    def export_decl(self):
        """export "file.gds" { m3 -> (33,0)   m3.NAME -> (233,0) }"""
        self.kw("export")
        path = self.expect("str")
        self.expect("sym", "{")
        rows = []
        while not self.accept("sym", "}"):
            ln = self.line()
            if self.peek()[0] == "eof":
                raise self.err("unterminated export block")
            layer = self.expect("id")
            kind = self.expect("id") if self.accept("sym", ".") else None
            self.expect("sym", "->")
            self.expect("sym", "(")
            num = self.expect("int")
            self.expect("sym", ",")
            dtype = self.expect("int")
            self.expect("sym", ")")
            rows.append((layer, kind, num, dtype, ln))
        return ("export", path, rows)

    def inv_decl(self):
        self.kw("invariants")
        self.expect("sym", "{")
        names = []
        while True:
            ln = self.line()
            name = self.expect("id")
            if name == "schema":
                raise ParseError(
                    "`schema` is not a per-tx invariant: artifact totality is "
                    "enforced by the `export` statement, which refuses to "
                    "write a design holding data its map does not cover", ln)
            if name in INVARIANTS_SPECIFIED:
                raise ParseError(
                    "invariant `%s` is specified but not implemented -- "
                    "declaring it would be a silent no-op" % name, ln)
            if name not in INVARIANTS_IMPLEMENTED:
                raise ParseError(
                    "unknown invariant %r (implemented: %s)"
                    % (name, ", ".join(INVARIANTS_IMPLEMENTED)), ln)
            names.append(name)
            if not self.accept("sym", ","):
                break
        self.expect("sym", "}")
        return ("inv", names)

    def fn_decl(self):
        self.kw("fn")
        name = self.expect("id")
        self.expect("sym", "(")
        params = []
        if not self.accept("sym", ")"):
            params.append(self.expect("id"))
            while self.accept("sym", ","):
                params.append(self.expect("id"))
            self.expect("sym", ")")
        return ("fn", name, params, self.block())

    def if_stmt(self):
        self.kw("if")
        c = self.expr()
        then = self.block()
        els = None
        if self.accept("id", "else"):
            if self.peek() == ("id", "if"):
                ln = self.line()
                els = [(self.if_stmt(), ln)]
            else:
                els = self.block()
        return ("if", c, then, els)

    def net_expr(self):
        ln = self.line()
        k, v = self.peek()
        if v == "new_net":
            self.next()
            return ("newnet",)
        if v == "net_at":
            self.next()
            self.expect("sym", "(")
            layer = self.expect("id")
            self.expect("sym", ",")
            x = self.expr()
            self.expect("sym", ",")
            y = self.expr()
            self.expect("sym", ")")
            return ("netat", layer, x, y)
        if v == "merge":
            self.next()
            self.expect("sym", "(")
            a = self.net_expr()
            self.expect("sym", ",")
            b = self.net_expr()
            self.expect("sym", ")")
            return ("merge", a, b)
        raise ParseError("expected a net expression (`new_net`, `net_at(..)` "
                         "or `merge(..)`), got %r" % v, ln)

    def check_expr(self):
        ln = self.line()
        name = self.expect("id")
        if name not in CHECKS_IMPLEMENTED:
            raise ParseError("unknown check %r (implemented: %s)"
                             % (name, ", ".join(CHECKS_IMPLEMENTED)), ln)
        self.expect("sym", "(")
        layer = self.expect("id")
        self.expect("sym", ",")
        self.kw("window")
        self.expect("sym", "(")
        es = [self.expr()]
        for _ in range(3):
            self.expect("sym", ",")
            es.append(self.expr())
        self.expect("sym", ")")
        self.expect("sym", ")")
        return ("check", name, layer, es, ln)

    # ---- expressions ----
    def _match_ops(self, *ops):
        k, v = self.peek()
        if (k == "sym" or k == "id") and v in ops:
            self.i += 1
            return v
        return None

    def expr(self):
        return self.p_or()

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
            if not op:
                return e
            e = ("bin", op, e, self.p_cmp())

    def p_cmp(self):
        e = self.p_add()
        while True:
            op = self._match_ops("<", ">", "<=", ">=")
            if not op:
                return e
            e = ("bin", op, e, self.p_add())

    def p_add(self):
        e = self.p_mul()
        while True:
            op = self._match_ops("+", "-")
            if not op:
                return e
            e = ("bin", op, e, self.p_mul())

    def p_mul(self):
        e = self.p_un()
        while True:
            op = self._match_ops("*", "/", "%")
            if not op:
                return e
            e = ("bin", op, e, self.p_un())

    def p_un(self):
        if self._match_ops("-"):
            return ("un", "-", self.p_un())
        if self._match_ops("!", "not"):
            return ("un", "not", self.p_un())
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
                idx = self.expr()
                self.expect("sym", "]")
                e = ("index", e, idx)
            else:
                return e

    def p_prim(self):
        ln = self.line()
        k, v = self.next()
        if k == "int":
            return ("int", v)
        if k == "str":
            return ("str", v)
        if k == "id":
            if v == "true":
                return ("bool", True)
            if v == "false":
                return ("bool", False)
            return ("var", v)
        if k == "sym" and v == "(":
            e = self.expr()
            self.expect("sym", ")")
            return e
        if k == "sym" and v == "[":
            items = []
            if self.peek() != ("sym", "]"):
                items.append(self.expr())
                while self.accept("sym", ","):
                    items.append(self.expr())
            self.expect("sym", "]")
            return ("list", items)
        raise ParseError("unexpected token %r" % v, ln)


# ----------------------------------------------------------------------------
# evaluator


def truthy(v):
    return bool(v)


class Env:
    def __init__(self, parent=None):
        self.v, self.parent = {}, parent

    def get(self, n):
        e = self
        while e:
            if n in e.v:
                return e.v[n]
            e = e.parent
        raise ParseError("undefined name %r" % n)

    def define(self, n, val):
        self.v[n] = val

    def assign(self, n, val):
        e = self
        while e:
            if n in e.v:
                e.v[n] = val
                return
            e = e.parent
        raise ParseError("assignment to undefined name %r" % n)


class Func:
    def __init__(self, name, params, body, env):
        self.name, self.params, self.body, self.env = name, params, body, env


_DECLS = {"design", "stack", "rules", "inv", "fn"}


class Interp:
    def __init__(self, prog, design=None):
        self.prog = prog
        self.backend = design if design is not None else Design()
        self.grid, self.units = 1, "nm"
        self.top_cell = "TOP"
        self.rules, self.invariants = [], set()
        self.metals, self.vias = set(), set()
        self.results = []
        self.txctx = None
        self.cur_line = 0
        self.genv = Env()
        builtins = {"print": print, "len": len, "str": str, "abs": abs,
                    "min": min, "max": max,
                    "range": lambda *a: list(range(*a)),
                    "append": lambda seq, x: seq.append(x)}
        for name, f in builtins.items():
            self.genv.define(name, f)
        # a backend handed in pre-populated (tests, embedding) still declares
        # its layers to the interpreter, so layer validation works
        self.metals |= set(getattr(self.backend, "metals", {}) or {})
        self.vias |= set(getattr(self.backend, "vias", {}) or {})

    def run(self):
        try:
            for node, line in self.prog:
                self.cur_line = line
                self.exec_stmt(node, self.genv)
        except _Return:
            raise ParseError("`return` outside a function", self.cur_line)
        except ParseError as e:
            if not e.line:
                raise ParseError(e.message, self.cur_line)
            raise
        return self.results

    # ---- name validation ----
    def _known_layer(self, name, where):
        if name not in self.metals and name not in self.vias:
            known = sorted(self.metals | self.vias)
            raise ParseError(
                "%s: layer %r is not declared in `stack` (declared: %s)"
                % (where, name, ", ".join(known) if known else "none"),
                self.cur_line)

    def _known_metal(self, name, where):
        self._known_layer(name, where)
        if name not in self.metals:
            raise ParseError("%s: %r is a via layer; a metal layer is required"
                             % (where, name), self.cur_line)

    # ---- statements ----
    def exec_block(self, stmts, env):
        for node, line in stmts:
            self.cur_line = line
            self.exec_stmt(node, env)

    def exec_stmt(self, s, env):
        t = s[0]
        if t in _DECLS and self.txctx is not None:
            raise ParseError("declaration `%s` is not allowed inside a tx" % t,
                             self.cur_line)
        if t == "design":
            self.units, self.grid = s[3], s[4]
            self.top_cell = s[2]
        elif t == "stack":
            for it in s[1]:
                if it[0] == "metal":
                    self.backend.declare_metal(it[1], it[2], it[3])
                    self.metals.add(it[1])
                else:
                    self._known_metal(it[4], "stack %s connects" % it[1])
                    self._known_metal(it[5], "stack %s connects" % it[1])
                    self.backend.declare_via(it[1], it[2], it[3], it[4], it[5])
                    self.vias.add(it[1])
        elif t == "rules":
            for (layer, kind, val, line) in s[1]:
                self.cur_line = line
                self._known_layer(layer, "rules")
                self.rules.append((layer, kind, self.eval(val, env)))
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
                raise ParseError("`for` iterates a list", self.cur_line)
            for v in seq:
                b = Env(env)
                b.define(s[1], v)
                self.exec_block(s[3], b)
        elif t == "return":
            raise _Return(self.eval(s[1], env) if s[1] is not None else None)
        elif t == "expr":
            self.eval(s[1], env)
        elif t == "export":
            self.exec_export(s[1], s[2])
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
            raise ParseError("unimplemented statement %r" % t, self.cur_line)

    # ---- export: artifact schemas are total ----
    def exec_export(self, path, rows):
        """Write the design out, refusing to drop anything unmapped.

        A stream-out that silently skips a layer with no map row is the bug
        that drops every via cut from a GDS and leaves LVS looking at opens.
        The rule here has no exception: if the design holds a datum the map
        does not cover, the export fails and writes nothing.
        """
        if self.txctx is not None:
            raise ParseError("`export` is not allowed inside a tx",
                             self.cur_line)
        rules = {}
        for (layer, kind, num, dtype, line) in rows:
            self.cur_line = line
            self._known_layer(layer, "export")
            key = (layer, kind)
            if key in rules:
                raise ParseError("duplicate export rule for %s" % _mapkey(key),
                                 line)
            rules[key] = (num, dtype)

        be = self.backend
        shapes = be.shapes if hasattr(be, "shapes") else {}
        missing = []
        for layer, boxes in sorted(shapes.items()):
            if boxes and (layer, None) not in rules:
                missing.append(_mapkey((layer, None)))
        for (layer, _t, _x, _y) in getattr(be, "labels", []):
            if (layer, "NAME") not in rules:
                k = _mapkey((layer, "NAME"))
                if k not in missing:
                    missing.append(k)
        if missing:
            raise Counterexample(
                "export", "unmapped-datum", [], [],
                "%s: the design holds data with no map rule: %s"
                % (path, ", ".join(sorted(missing))))

        unused = [_mapkey(k) for k in rules
                  if k[1] is None and not shapes.get(k[0])]
        n = write_gds(path, self.top_cell, shapes,
                      getattr(be, "labels", []), rules, self.grid, self.units)
        print("[silica export] %s: %d element(s), %d map rule(s)%s"
              % (path, n, len(rules),
                 "" if not unused else
                 " (%d rule(s) matched no geometry: %s)"
                 % (len(unused), ", ".join(sorted(unused)))))

    # ---- transactions ----
    def exec_tx(self, name, body, env):
        if self.txctx is not None:
            raise ParseError("nested tx is not allowed", self.cur_line)
        shadow = self.backend.clone()
        ctx = type("Ctx", (), {})()
        ctx.shadow = shadow
        ctx.pre = shadow.net_count()
        ctx.declared_new = 0
        ctx.declared_split = ctx.declared_delete = 0
        ctx.loose = False
        ctx.touched = []
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
            raise ParseError("`%s` is only allowed inside a tx" % what,
                             self.cur_line)
        return self.txctx

    def _grid(self, *vals):
        for v in vals:
            if not isinstance(v, int):
                raise ParseError("coordinates must be integers, got %r" % (v,),
                                 self.cur_line)
            if v % self.grid:
                raise ParseError("off-grid coordinate %d (grid %d)"
                                 % (v, self.grid), self.cur_line)

    def _eval_box(self, e, env):
        v = self.eval(e, env)
        if not isinstance(v, Box):
            raise ParseError("expected a box value, got %r" % (v,),
                             self.cur_line)
        return v

    def _resolve_net(self, ne, sh, env):
        if ne[0] != "netat":
            raise ParseError("merge() takes net_at() operands", self.cur_line)
        self._known_layer(ne[1], "net_at")
        x, y = self.eval(ne[2], env), self.eval(ne[3], env)
        n = sh.net_at(ne[1], x, y)
        if n is None:
            raise Counterexample("add.on", "no-net", [], [],
                                 "net_at(%s, %s, %s) found no shape"
                                 % (ne[1], x, y))
        return n

    def exec_add(self, s, env):
        ctx = self._tx("add")
        layer, ne = s[1], s[3]
        self._known_layer(layer, "add")
        box = self._eval_box(s[2], env)
        sh = ctx.shadow
        touched = sh.nets_touching(layer, box)
        if ne[0] == "newnet":
            if touched:
                raise Counterexample("add.on", "not-new", box.as_list(),
                                     touched,
                                     "`new_net` shape touches existing nets")
            ctx.declared_new += 1
        elif ne[0] == "netat":
            target = self._resolve_net(ne, sh, env)
            if not touched:
                raise Counterexample("add.on", "floating", box.as_list(), [],
                                     "shape touches no net")
            if len(touched) > 1:
                raise Counterexample("add.on", "bridge", box.as_list(),
                                     touched,
                                     "shape would merge distinct nets")
            if touched[0] != target:
                raise Counterexample("add.on", "wrong-net", box.as_list(),
                                     touched,
                                     "shape touches a different net")
        else:  # merge
            a = self._resolve_net(ne[1], sh, env)
            b = self._resolve_net(ne[2], sh, env)
            if a == b:
                raise Counterexample("add.on", "merge-same", box.as_list(),
                                     [a],
                                     "merge() operands are already one net")
            if set(touched) != {a, b}:
                raise Counterexample(
                    "add.on", "merge-mismatch", box.as_list(), touched,
                    "shape must touch exactly the two merged nets")
            ctx.declared_new -= 1
        sh.add(layer, box)
        ctx.touched.append((layer, box))

    def exec_sub(self, s, env):
        """Subtraction, checked as a TOPOLOGICAL EFFECT rather than a count.

        A net count is a scalar, and scalars cancel: one subtraction that
        splits one net in two while deleting another leaves the count
        unchanged, and a count-based check waves it through having declared
        nothing. So instead of comparing counts, correlate every surviving
        component back to the net it came from -- any point of retained metal
        lay inside the pre-state -- and classify each pre-net as survived,
        split or deleted. Each outcome must then be declared on its own.
        """
        ctx = self._tx("sub")
        layer, mods = s[1], s[3]
        self._known_layer(layer, "sub")
        box = self._eval_box(s[2], env)
        sh = ctx.shadow
        before = sh.clone()
        pre_nets = set(before.nets())
        sh.sub(layer, box)
        post_nets = list(sh.nets())

        fanout = {}
        for pn in post_nets:
            probe = sh.net_probe(pn)
            origin = before.net_at(*probe) if probe else None
            fanout[origin] = fanout.get(origin, 0) + 1
        split = sorted(n for n in pre_nets if fanout.get(n, 0) > 1)
        deleted = sorted(n for n in pre_nets if fanout.get(n, 0) == 0)
        # a net that fans out to k components adds k-1 nets
        gained = sum(fanout[n] - 1 for n in split)
        lost = len(deleted)

        if split and "splitting" not in mods:
            raise Counterexample(
                "sub", "split", box.as_list(), split,
                "%d net(s) split, adding %d net(s); declare `splitting` "
                "if intended" % (len(split), gained))
        if deleted and "deleting" not in mods:
            raise Counterexample(
                "sub", "delete", box.as_list(), deleted,
                "%d net(s) removed entirely; declare `deleting` if intended"
                % lost)
        want_split = mods.get("splitting")
        if want_split is not None and want_split != gained:
            raise Counterexample(
                "sub", "split-count", box.as_list(), split,
                "declared `splitting %d`, measured %d" % (want_split, gained))
        want_del = mods.get("deleting")
        if want_del is not None and want_del != lost:
            raise Counterexample(
                "sub", "delete-count", box.as_list(), deleted,
                "declared `deleting %d`, measured %d" % (want_del, lost))

        # An exact count is a declaration; a bare modifier is a deliberate
        # weakening, and the measured value stands in for it.
        ctx.declared_split += gained if want_split is None else want_split
        ctx.declared_delete += lost if want_del is None else want_del
        ctx.loose = ctx.loose or (("splitting" in mods and want_split is None)
                                  or ("deleting" in mods and want_del is None))
        ctx.touched.append((layer, box))

    def exec_label(self, s, env):
        ctx = self._tx("label")
        layer = s[1]
        self._known_metal(layer, "label")
        text = self.eval(s[2], env)
        if not isinstance(text, str):
            raise ParseError("label text must be a string, got %r" % (text,),
                             self.cur_line)
        x, y = self.eval(s[3], env), self.eval(s[4], env)
        self._grid(x, y)
        if not ctx.shadow.on_metal(layer, x, y):
            raise Counterexample("label", "floating", [x, y], [],
                                 'label "%s" attaches to no metal' % text)
        ctx.shadow.add_label(layer, text, x, y)

    def exec_assert(self, s, env):
        ctx = self._tx("assert")
        _, name, layer, es, line = s[1]
        self.cur_line = line
        self._known_layer(layer, "assert %s" % name)
        w = [self.eval(e, env) for e in es]
        self._grid(*w)
        win = Box(*w)
        m = self.eval(s[2], env)
        g = (ctx.shadow.spacing_violation(layer, win, m) if name == "spacing"
             else ctx.shadow.width_violation(layer, win, m))
        if g is not None:
            raise Counterexample("assert", name, win.as_list(), [],
                                 "measured %s < required %s" % (g, m))

    def check_commit(self, ctx):
        if "connectivity" in self.invariants:
            # Redundant with the per-edit checks by the soundness argument in
            # SPEC section 5 -- which is exactly why it is worth running: it is
            # the cross-check that catches a backend whose partition disagrees
            # with the effects the interpreter believes it applied.
            post = ctx.shadow.net_count()
            want = (ctx.pre + ctx.declared_new
                    + ctx.declared_split - ctx.declared_delete)
            if post != want:
                raise Counterexample(
                    "connectivity", "net-count", [], [],
                    "declared %d%+d%+d%+d = %d nets, measured %d"
                    % (ctx.pre, ctx.declared_new, ctx.declared_split,
                       -ctx.declared_delete, want, post))
        # local rules: evaluated on the final shadow, in the halo of every
        # shape the tx touched -- `add` AND `sub`, so a subtraction that thins
        # a wire below the minimum is caught exactly like an undersized add.
        for (layer, kind, m) in self.rules:
            for (lay, b) in ctx.touched:
                if lay != layer:
                    continue
                halo = 1 if kind == "width" else 2
                win = Box(b.x1 - halo * m, b.y1 - halo * m,
                          b.x2 + halo * m, b.y2 + halo * m)
                g = (ctx.shadow.width_violation(layer, win, m)
                     if kind == "width"
                     else ctx.shadow.spacing_violation(layer, win, m))
                if g is not None:
                    raise Counterexample("rules.%s" % kind,
                                         "%s>=%s" % (layer, m),
                                         b.as_list(), [], "measured %s" % g)

    # ---- expressions ----
    def eval(self, e, env):
        t = e[0]
        if t == "int" or t == "str" or t == "bool":
            return e[1]
        if t == "var":
            return env.get(e[1])
        if t == "list":
            return [self.eval(x, env) for x in e[1]]
        if t == "index":
            seq = self.eval(e[1], env)
            return seq[self.eval(e[2], env)]
        if t == "un":
            v = self.eval(e[2], env)
            if e[1] == "-":
                if not isinstance(v, int):
                    raise ParseError("unary `-` needs an int, got %r" % (v,),
                                     self.cur_line)
                return -v
            return not truthy(v)
        if t == "bin":
            op = e[1]
            if op == "and":
                return (truthy(self.eval(e[2], env)) and
                        truthy(self.eval(e[3], env)))
            if op == "or":
                return (truthy(self.eval(e[2], env)) or
                        truthy(self.eval(e[3], env)))
            a, b = self.eval(e[2], env), self.eval(e[3], env)
            if op == "==":
                return a == b
            if op == "!=":
                return a != b
            if op == "+":
                for ty in (int, str, list):
                    if isinstance(a, ty) and isinstance(b, ty):
                        return a + b
                raise ParseError("`+` on mismatched types (%s + %s) -- "
                                 "SILICA never coerces"
                                 % (type(a).__name__, type(b).__name__),
                                 self.cur_line)
            if op in ("<", ">", "<=", ">="):
                if not (type(a) is type(b) and isinstance(a, (int, str))):
                    raise ParseError("`%s` compares two ints or two strings"
                                     % op, self.cur_line)
                return {"<": a < b, ">": a > b,
                        "<=": a <= b, ">=": a >= b}[op]
            if not (isinstance(a, int) and isinstance(b, int)):
                raise ParseError("`%s` needs ints" % op, self.cur_line)
            if op == "-":
                return a - b
            if op == "*":
                return a * b
            if op == "/":
                if b == 0:
                    raise ParseError("division by zero", self.cur_line)
                if a % b:
                    raise ParseError("inexact division %d/%d -- SILICA never "
                                     "rounds a coordinate" % (a, b),
                                     self.cur_line)
                return a // b
            if op == "%":
                if b == 0:
                    raise ParseError("modulo by zero", self.cur_line)
                return a % b
        if t == "call":
            callee = e[1]
            args = [self.eval(a, env) for a in e[2]]
            if callee[0] == "var" and callee[1] == "box":
                if len(args) != 4 or not all(isinstance(a, int)
                                             for a in args):
                    raise ParseError("box(x1,y1,x2,y2) takes four ints",
                                     self.cur_line)
                self._grid(*args)
                return Box(*args)
            f = self.eval(callee, env)
            if isinstance(f, Func):
                if len(args) != len(f.params):
                    raise ParseError("%s() takes %d args, got %d"
                                     % (f.name, len(f.params), len(args)),
                                     self.cur_line)
                fenv = Env(f.env)
                for p, a in zip(f.params, args):
                    fenv.define(p, a)
                saved = self.cur_line
                try:
                    self.exec_block(f.body, fenv)
                    return None
                except _Return as r:
                    return r.value
                finally:
                    self.cur_line = saved
            if callable(f):
                return f(*args)
            raise ParseError("value is not callable", self.cur_line)
        raise ParseError("unimplemented expression %r" % t, self.cur_line)


# ----------------------------------------------------------------------------
if __name__ == "__main__":
    from silica.cli import main
    sys.exit(main())
