"""SILICA -- Structured Invariant Language for Integrated Circuit Agents.

A full, tool-agnostic programming language that makes agentic physical chip
design deterministic: every edit is a typed, transactional transform carrying
invariant obligations; failures return machine-readable counterexamples.
"""
__version__ = "0.3.0"

from silica.interpreter import (
    SilicaError, ParseError, Counterexample,
    Box, UF, Design,
    lex, Parser, Env, Func, Interp, truthy,
)

__all__ = [
    "SilicaError", "ParseError", "Counterexample",
    "Box", "UF", "Design",
    "lex", "Parser", "Env", "Func", "Interp", "truthy",
    "__version__",
]
