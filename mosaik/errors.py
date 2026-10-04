"""Compiler diagnostics that carry a place (review L-9).

`compile_program` RETURNS a string beginning with "Compilation error:" rather
than raising, and it keeps doing so: 59 test files and the build tool read that
contract, several of them asserting that a bad program produces it. What was
worth changing is what the string SAYS - it used to be the exception text plus
a full Python traceback, which names `gen_expr.py` and not the line of mosaik
that cannot be compiled.

So the location travels on the exception, `compile_program` formats it, and the
traceback is available but opt-in (`MOSAIK_TRACEBACK=1`, and the caller can
read `compiler.last_error` for the structured form).
"""


class CompileError(Exception):
    """A failure with a source location, when one is known."""

    def __init__(self, message, file=None, line=None):
        super().__init__(message)
        self.message = str(message)
        self.file = file
        self.line = line

    def located(self) -> str:
        """`file:line: message`, dropping whichever half is unknown."""
        where = ""
        if self.file and self.line:
            where = "%s:%d: " % (self.file, self.line)
        elif self.line:
            where = "line %d: " % self.line
        elif self.file:
            where = "%s: " % self.file
        return where + self.message

    def __str__(self):
        return self.located()
