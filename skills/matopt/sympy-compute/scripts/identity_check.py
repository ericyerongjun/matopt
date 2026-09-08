#!/usr/bin/env python3
"""Check whether two SymPy-readable expressions are identical."""
from __future__ import annotations

import argparse
import sys

import sympy as sp
from sympy.parsing.sympy_parser import (
    implicit_multiplication_application,
    parse_expr,
    standard_transformations,
)


def parse(expr: str) -> sp.Expr:
    raw = expr.strip().strip("$")
    if "\\" in raw:
        from sympy.parsing.latex import parse_latex

        return parse_latex(raw)
    tf = standard_transformations + (implicit_multiplication_application,)
    return parse_expr(raw.replace("^", "**"), transformations=tf)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--a", required=True)
    p.add_argument("--b", required=True)
    args = p.parse_args()
    a, b = parse(args.a), parse(args.b)
    diff = sp.simplify(a - b)
    ok = diff == 0
    print("EQUIVALENT" if ok else "NOT equivalent")
    print(f"A = {a}")
    print(f"B = {b}")
    print(f"A - B = {diff}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
