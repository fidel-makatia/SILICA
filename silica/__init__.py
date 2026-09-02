"""SILICA -- Structured Invariant Language for Integrated Circuit Agents.

A tool-agnostic programming language that makes agentic physical chip design
deterministic: every edit is a typed, transactional transform carrying
invariant obligations, and failures return machine-readable counterexamples.
"""
__version__ = "0.4.0"

from silica.engine import Design, SimpleDesign
from silica.errors import Counterexample, ParseError, SilicaError
from silica.geometry import Box, UF, union_rect
from silica.interpreter import Env, Func, Interp, Parser, lex, truthy

__all__ = [
    "SilicaError", "ParseError", "Counterexample",
    "Box", "UF", "union_rect",
    "Design", "SimpleDesign",
    "lex", "Parser", "Env", "Func", "Interp", "truthy",
    "__version__",
]
