"""SILICA -- Structured Invariant Language for Integrated Circuit Agents.

A tool-agnostic programming language that makes agentic physical chip design
deterministic: every edit is a typed, transactional transform carrying
invariant obligations, and failures return machine-readable counterexamples.
"""
__version__ = "0.4.0"

from silica.interpreter import (
    Box,
    Counterexample,
    Design,
    Env,
    Func,
    Interp,
    ParseError,
    Parser,
    SilicaError,
    UF,
    lex,
    union_rect,
    truthy,
)

__all__ = [
    "SilicaError", "ParseError", "Counterexample",
    "Box", "UF", "Design", "union_rect",
    "lex", "Parser", "Env", "Func", "Interp", "truthy",
    "__version__",
]
