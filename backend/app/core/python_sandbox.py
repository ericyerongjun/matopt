"""
Safe Python code executor for LLM-generated math code.

Adapted from Qwen2.5-Math evaluation/python_executor.py but hardened:
- no file / network access
- execution timeout
- output truncation
- restricted builtins

"""

from __future__ import annotations

import io
import copy
import signal
import traceback
from contextlib import redirect_stdout
from typing import Any, Optional

from app.config import settings

# ── Restricted builtins ─────────────────────────────────────────────────
_SAFE_BUILTINS = {
    "abs", "all", "any", "bin", "bool", "chr", "complex", "dict",
    "divmod", "enumerate", "filter", "float", "format", "frozenset",
    "getattr", "hasattr", "hash", "hex", "int", "isinstance", "issubclass",
    "iter", "len", "list", "map", "max", "min", "next", "oct", "ord",
    "pow", "print", "range", "repr", "reversed", "round", "set",
    "slice", "sorted", "str", "sum", "tuple", "type", "zip",
    "True", "False", "None",
}

import builtins as _builtins
_RESTRICTED_BUILTINS = {
    k: getattr(_builtins, k) for k in _SAFE_BUILTINS if hasattr(_builtins, k)
}
# Block dangerous ones explicitly
for _blocked in ("__import__", "exec", "eval", "compile", "open", "input", "breakpoint"):
    _RESTRICTED_BUILTINS.pop(_blocked, None)


class SandboxRuntime:
    """
    Minimal runtime that pre-injects common math libraries.
    """

    HEADERS = [
        "import math",
        "import cmath",
        "import sympy",
        "from sympy import *",
        "import numpy as np",
        "import numpy.linalg as la",
        "import scipy",
        "import scipy.optimize",
        "import scipy.integrate as sp_integrate",
        "import scipy.interpolate",
        "import scipy.linalg",
        "import scipy.signal",
        "import scipy.stats",
        "import scipy.special",
        "import scipy.sparse",
        "import scipy.fft",
        "import mpmath",
        "import statistics",
        "import pandas as pd",
        "import networkx as nx",
        "from fractions import Fraction",
        "from collections import Counter, defaultdict",
        "from itertools import combinations, permutations, product",
        "from functools import reduce",
        # matplotlib – Agg backend so it renders to bytes, not a GUI window
        "import matplotlib; matplotlib.use('Agg')",
        "import matplotlib.pyplot as plt",
    ]

    def __init__(self, restricted: bool = True):
        self._global_vars: dict[str, Any] = {}
        if restricted:
            self._global_vars["__builtins__"] = _RESTRICTED_BUILTINS
        for header in self.HEADERS:
            try:
                exec(header, self._global_vars)  # noqa: S102
            except Exception:
                pass  # some imports may fail; that's fine

    def exec_code(self, code: str) -> None:
        import re
        if re.search(r"(\s|^)?input\(", code):
            raise RuntimeError("input() is not allowed in the sandbox")
        if re.search(r"(\s|^)?open\(", code):
            raise RuntimeError("open() is not allowed in the sandbox")
        exec(code, self._global_vars)  # noqa: S102

    def eval_code(self, expr: str) -> Any:
        return eval(expr, self._global_vars)  # noqa: S307


class PythonSandbox:
    """
    Execute a code snippet safely (timeout + restricted builtins).

    Usage::

        sandbox = PythonSandbox()
        result, report = sandbox.run("x = 2 + 3\\nprint(x)")
        # result = "5", report = "Done"
    """

    def __init__(
        self,
        timeout: int | None = None,
        max_output_chars: int = 2000,
        restricted: bool = True,
    ):
        self.timeout = timeout or settings.sandbox_timeout
        self.max_output = max_output_chars
        self.restricted = restricted

    def run(self, code: str) -> tuple[str, str]:
        """
        Execute *code* and return ``(stdout_output, report)``.

        ``report`` is ``"Done"`` on success or a short traceback on failure.

        """
        runtime = SandboxRuntime(restricted=self.restricted)
        lines = code.strip().splitlines()

        def _execute() -> tuple[str, str]:
            try:
                buf = io.StringIO()
                with redirect_stdout(buf):
                    # If last line looks like an expression, eval it
                    if lines and not lines[-1].strip().startswith(("print", "import", "from", "def", "class", "if", "for", "while", "with", "try", "#", "assert", "raise", "return", "del")):
                        if len(lines) > 1:
                            runtime.exec_code("\n".join(lines[:-1]))
                        try:
                            result = runtime.eval_code(lines[-1])
                            if result is not None:
                                print(result)
                        except SyntaxError:
                            runtime.exec_code("\n".join(lines))
                    else:
                        runtime.exec_code("\n".join(lines))

                output = buf.getvalue()
                return self._truncate(output), "Done"
            except Exception:
                tb = traceback.format_exc().strip().splitlines()
                short = tb[-1] if tb else "Unknown error"
                return "", self._truncate(short)

        # Apply timeout on POSIX
        if hasattr(signal, "SIGALRM"):
            def _handler(signum: int, frame: Any):
                raise TimeoutError("Code execution timed out")

            old = signal.signal(signal.SIGALRM, _handler)
            signal.alarm(self.timeout)
            try:
                return _execute()
            finally:
                signal.alarm(0)
                signal.signal(signal.SIGALRM, old)
        else:
            return _execute()

    def _truncate(self, s: str) -> str:
        if len(s) > self.max_output:
            half = self.max_output // 2
            return s[:half] + "\n...[truncated]...\n" + s[-half:]
        return s


# Module-level singleton
python_sandbox = PythonSandbox()
