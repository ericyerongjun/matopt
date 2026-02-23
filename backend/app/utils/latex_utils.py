"""
LaTeX string validation, sanitisation, and extraction helpers.

Adapted in part from Qwen2.5-Math evaluation/parser.py strip_string() and
evaluation/math_utils.py clean_expr_str() utilities.
"""

import re
from typing import Optional


def validate_latex(latex: str) -> bool:
    """
    Quick syntactic check: balanced braces and no obiously broken commands.
    Returns True if the string looks like plausible LaTeX.
    """
    # Check balanced curly braces
    depth = 0
    for ch in latex:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        if depth < 0:
            return False
    return depth == 0


def escape_latex(text: str) -> str:
    """Escape characters that are special in LaTeX."""
    specials = {
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    for char, replacement in specials.items():
        text = text.replace(char, replacement)
    return text


def clean_latex(expr_str: str) -> str:
    """
    Normalise a LaTeX math string for comparison / parsing.
    Ported from Qwen2.5-Math evaluation/math_utils.py::clean_expr_str().
    """
    expr_str = (
        expr_str.replace(" . ", ".")
        .replace(". ", ".")
        .replace("**", "^")
        .replace("\\pm", "")
        .replace("*", "\\times ")
        .replace("\\\\", "\\")
        .replace("\\ne ", "\\neq ")
        .replace("!=", "\\neq")
        .replace(">=", "\\ge")
        .replace("<=", "\\le")
        .replace("≠", "\\neq")
        .replace("dfrac", "frac")
        .replace("tfrac", "frac")
        .replace("\\$", "")
        .replace("$", "")
        .replace("\\%", "")
        .replace("%", "")
        .replace("\\!", "")
        .replace("^\\circ", "\\times \\pi / 180")
        .replace("//", "/")
        .replace('"', "")
    )
    expr_str = re.sub(r"\\+", r"\\", expr_str)
    expr_str = re.sub(r"\^\s?\((.*?)\)", r"^{\1}", expr_str)
    expr_str = re.sub(r"\\frac\s?(\d)\s?(\d+)", r"\\frac{\1}{\2}", expr_str)
    expr_str = re.sub(r"\\log_\s?(\d)\s?(\d+)", r"\\log_{\1}{\2}", expr_str)
    expr_str = re.sub(r"\\frac\s?{(.*?)}\s?(\d)", r"\\frac{\1}{\2}", expr_str)
    expr_str = re.sub(r"\\frac\s?(\d)\s?{(.*?)}", r"\\frac{\1}{\2}", expr_str)
    expr_str = re.sub(r"\\sqrt\s?(\d)", r"\\sqrt{\1}", expr_str)
    expr_str = re.sub(r"sqrt\s?\((\d+)\)", r"\\sqrt{\1}", expr_str)
    expr_str = re.sub(r"sqrt\s?\((.*?)\)", r"\\sqrt{\1}", expr_str)
    expr_str = expr_str.replace(" sqrt", "\\sqrt")
    expr_str = (
        expr_str.replace("\\left", "").replace("\\right.", "").replace("\\right", "")
    )
    return expr_str


def strip_latex_string(string: str) -> str:
    """
    Comprehensive normalisation of a LaTeX answer string.
    Ported from Qwen2.5-Math evaluation/parser.py::strip_string().
    """
    string = str(string).strip().replace("\n", "").rstrip(".")
    string = string.replace("\\!", "")

    # matrix normalisation
    string = re.sub(r"\\begin\{array\}\{.*?\}", r"\\begin{pmatrix}", string)
    string = re.sub(r"\\end\{array\}", r"\\end{pmatrix}", string)
    string = string.replace("bmatrix", "pmatrix")

    # frac variants
    string = string.replace("tfrac", "frac").replace("dfrac", "frac")
    string = (
        string.replace("\\neq", "\\ne")
        .replace("\\leq", "\\le")
        .replace("\\geq", "\\ge")
    )

    # remove delimiters
    string = string.replace("\\left", "").replace("\\right", "")
    string = string.replace("\\{", "{").replace("\\}", "}")

    # remove trailing units (\\text{...})
    _string = re.sub(r"\\text{.*?}$", "", string).strip()
    if _string:
        string = _string

    string = string.replace("\\$", "").replace("$", "")
    string = string.replace("\\(", "").replace("\\)", "")
    string = re.sub(r"\\text\{(.*?)\}", r"\1", string)

    # remove percentage
    string = string.replace("\\%", "").replace("%", "")

    # leading-zero fix
    string = string.replace(" .", " 0.").replace("{.", "{0.")

    # inf
    string = string.replace("infinity", "\\infty")
    if "\\infty" not in string:
        string = string.replace("inf", "\\infty")

    string = string.replace("\\mathbf", "")
    string = re.sub(r"\\mbox{.*?}", "", string)

    # trailing .000
    string = re.sub(r"(\d+)\.0*([^\d])", r"\1\2", string)
    string = re.sub(r"(\d+)\.0*$", r"\1", string)

    if string and string[0] == ".":
        string = "0" + string

    # k = ... → just the value
    if len(string.split("=")) == 2 and len(string.split("=")[0]) <= 2:
        string = string.split("=")[1]

    string = _fix_sqrt(string)
    string = string.replace(" ", "")
    string = _fix_fracs(string)
    string = _fix_a_slash_b(string)
    return string


# ── internal helpers (from Qwen2.5-Math parser.py) ──────────────────────


def _fix_sqrt(string: str) -> str:
    return re.sub(r"\\sqrt(\w+)", r"\\sqrt{\1}", string)


def _fix_fracs(string: str) -> str:
    substrs = string.split("\\frac")
    new_str = substrs[0]
    if len(substrs) > 1:
        for substr in substrs[1:]:
            new_str += "\\frac"
            if not substr or substr[0] == "{":
                new_str += substr
            else:
                if len(substr) < 2:
                    return string
                a, b = substr[0], substr[1]
                rest = substr[2:] if len(substr) > 2 else ""
                if b != "{":
                    new_str += "{" + a + "}{" + b + "}" + rest
                else:
                    new_str += "{" + a + "}" + b + rest
    return new_str


def _fix_a_slash_b(string: str) -> str:
    parts = string.split("/")
    if len(parts) != 2:
        return string
    a, b = parts
    try:
        if "sqrt" not in a:
            a = int(a)
        if "sqrt" not in b:
            b = int(b)
        assert string == f"{a}/{b}"
        return f"\\frac{{{a}}}{{{b}}}"
    except (ValueError, AssertionError):
        return string


def extract_latex_blocks(text: str) -> list[str]:
    """
    Extract all LaTeX math blocks from a Markdown/LaTeX document.
    Matches $$..$$, \\[..\\], \\(..\\), and $..$ (inline).
    """
    patterns = [
        r"\$\$(.*?)\$\$",         # display math $$...$$
        r"\\\[(.*?)\\\]",         # display math \\[...\\]
        r"\\\((.*?)\\\)",         # inline math \\(...\\)
        r"(?<!\$)\$(?!\$)(.*?)(?<!\$)\$(?!\$)",  # inline $...$
    ]
    blocks: list[str] = []
    for pat in patterns:
        blocks.extend(re.findall(pat, text, re.DOTALL))
    return blocks


def find_boxed_answer(text: str) -> Optional[str]:
    """
    Extract the content inside \\boxed{...}, handling nested braces.
    Ported from Qwen2.5-Math parser.py::find_box().
    """
    if "boxed" not in text:
        return None
    ans = text.split("boxed")[-1]
    if not ans:
        return None
    if ans[0] == "{":
        stack = 1
        result = ""
        for ch in ans[1:]:
            if ch == "{":
                stack += 1
                result += ch
            elif ch == "}":
                stack -= 1
                if stack == 0:
                    break
                result += ch
            else:
                result += ch
        return result
    return ans.split("$")[0].strip()
