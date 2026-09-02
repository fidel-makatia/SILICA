"""The three error classes SILICA admits. There is no fourth, and no warning."""
import json


class SilicaError(Exception):
    """Base class for every error SILICA raises."""


class ParseError(SilicaError):
    """The program itself is wrong: syntax, types, or an undeclared name.

    The design is never touched. Carries `line` (1-based, 0 if unknown) and a
    machine-readable `data` dict, so an agent front-end gets the same
    structured channel it gets for commit failures.
    """

    def __init__(self, message, line=0):
        self.line = line
        self.message = message
        self.data = {"error": "program", "line": line, "message": message}
        super().__init__("line %d: %s" % (line, message) if line else message)


class Counterexample(SilicaError):
    """A commit failure: the program is well-formed, the edit is illegal here.

    This is the normal agent feedback channel, not an exception path.
    """

    def __init__(self, check, rule, box, nets, note=""):
        self.data = {"check": check, "rule": rule, "box": box,
                     "nets": sorted(str(n) for n in nets), "note": note}
        super().__init__(json.dumps(self.data))
