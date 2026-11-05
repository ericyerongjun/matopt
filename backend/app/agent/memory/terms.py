"""Text normalisation and term extraction for the recall index.

Two jobs, both deterministic and free of model calls:

``index_text``  a lexical form of math-heavy Markdown for SQLite FTS5. LaTeX
                commands become words (``\\nabla f(x)`` → ``nabla f x``,
                ``\\mathbb{E}`` → ``mathbb E``) so a BM25 query for "nabla" or
                "expectation" can hit a formula written in TeX.
``terms``       the entity nodes of the recall graph. LinearRAG (Zhuang et al.,
                ICLR 2026, arXiv 2510.10114) builds its relation-free graph from
                spaCy named entities; generic NER finds almost nothing in
                mathematics, so this extractor looks for what names things in
                MatOpt conversations instead: named results ("Hoeffding's
                inequality", "Adam optimizer"), acronyms and CamelCase names
                (SGD, ADMM, LoRA, AdamW), defined terms in bold, code
                identifiers and file names in backticks, Theorem/Lemma labels,
                and the distinctive LaTeX operators.

Written from the paper's description; no LinearRAG code is used (its
reference implementation is GPL-3.0).
"""

from __future__ import annotations

import re

# Formatting commands carry no meaning of their own.
_TEX_NOISE = frozenset("""
begin end left right frac dfrac tfrac mathrm mathbf mathit mathsf mathtt text textbf textit emph operatorname
quad qquad displaystyle limits big Big bigg Bigg cdot ldots cdots dots vdots ddots hline label ref eqref cite
tag nonumber hspace vspace mbox boxed overline underline hat tilde bar vec dot ddot widehat widetilde sqrt
over le ge leq geq neq approx sim to in times mid colon mathbb mathcal mathfrak mathscr mathbb
""".split())

_STOP = frozenset("""
a an and are as at be been but by can could did do does for from had has have how i if in into is it its
just me my no not of on or our so than that the their them then there these they this to up us was we were
what when where which who why will with would you your about after again all also any because before being
between both each few more most other over same should some such through under until very while yes ok
please thanks thank let lets want need make use using used get got show tell give find explain help
""".split())

_SUFFIXES = (
    "theorem|lemma|inequality|inequalities|bound|bounds|method|algorithm|distribution|test|estimator|transform|"
    "process|model|decomposition|conjecture|series|equation|equations|optimizer|optimiser|regression|loss|norm|"
    "criterion|condition|conditions|principle|identity|formula|kernel|matrix|network|sampler|sampling|filter|"
    "integral|space|spaces|operator|function|problem|dataset|law|rule|trick|descent|iteration|estimate|divergence"
)
_NAMED = re.compile(
    r"\b((?:[A-Z][\w\-]*(?:['’]s)?\s+){1,3}(?i:" + _SUFFIXES + r"))\b")
_CAPS_PHRASE = re.compile(r"\b([A-Z][a-z]{2,}(?:[\s\-]+[A-Z][a-z]{2,}){1,3})\b")
_ACRONYM = re.compile(r"\b([A-Z][A-Z0-9]{1,7}s?|[A-Z][a-z]+[A-Z][A-Za-z0-9]*|[a-z]+[A-Z][A-Za-z0-9]+)\b")
_BOLD = re.compile(r"\*\*([^*\n]{2,60})\*\*|__([^_\n]{2,60})__")
_CODE = re.compile(r"`([^`\n]{2,60})`")
_LABEL = re.compile(r"\b((?:theorem|lemma|definition|proposition|corollary|equation|eq\.|figure|table|section)"
                    r"\s*\(?\d+(?:\.\d+)*\)?)", re.IGNORECASE)
_TEX_CMD = re.compile(r"\\([A-Za-z]+)")
_WORD = re.compile(r"[\w]+", re.UNICODE)

_SENTENCE_START = frozenset({"The", "This", "That", "These", "Those", "We", "You", "It", "In", "On", "For", "If",
                             "When", "Then", "So", "But", "And", "Here", "There", "Let", "Now", "Use", "Note"})


def index_text(text: str) -> str:
    """The lexical form of ``text`` for FTS5: LaTeX commands as words, symbols as spaces."""
    text = _TEX_CMD.sub(lambda m: f" {m.group(1)} ", text or "")
    return re.sub(r"[{}$^_\\]+", " ", text)


def query_terms(text: str, *, limit: int = 32) -> list[str]:
    """Distinct content words of a query, lowercased, stopwords and one-letter noise removed."""
    out: list[str] = []
    for word in _WORD.findall(index_text(text).lower()):
        if word in _STOP or word in _TEX_NOISE or (len(word) < 2 and not word.isdigit()):
            continue
        if word not in out:
            out.append(word)
        if len(out) >= limit:
            break
    return out


def fts_query(text: str) -> str:
    """An FTS5 ``MATCH`` expression: any content word, each quoted so no syntax leaks through."""
    return " OR ".join(f'"{t}"' for t in query_terms(text))


def normalize(term: str) -> str:
    term = re.sub(r"['’]s\b", "", term.strip().lower())
    term = re.sub(r"[\s\-]+", " ", term)
    return term.strip(" .,:;()")


def terms(text: str, *, limit: int = 40) -> list[str]:
    """Entity-like terms of ``text``, normalised, most specific patterns first."""
    text = text or ""
    found: list[str] = []

    def add(raw: str) -> None:
        norm = normalize(raw)
        if 2 <= len(norm) <= 60 and norm not in _STOP and norm not in found:
            found.append(norm)

    for m in _NAMED.finditer(text):
        phrase = m.group(1)
        words = phrase.split()
        while words and words[0] in _SENTENCE_START:
            words = words[1:]
        if len(words) >= 2:
            add(" ".join(words))
    for m in _LABEL.finditer(text):
        add(m.group(1).replace("eq.", "equation"))
    for m in _BOLD.finditer(text):
        add(m.group(1) or m.group(2))
    for m in _CODE.finditer(text):
        token = m.group(1).strip()
        if len(token.split()) <= 3:
            add(token)
    for m in _CAPS_PHRASE.finditer(text):
        words = m.group(1).split()
        if words[0] in _SENTENCE_START:
            words = words[1:]
        if len(words) >= 2:
            add(" ".join(words))
    for m in _ACRONYM.finditer(text):
        token = m.group(1)
        if token not in _SENTENCE_START and token.upper() not in ("I", "OK", "AM", "PM"):
            add(token)
    for m in _TEX_CMD.finditer(text):
        name = m.group(1)
        if name not in _TEX_NOISE and len(name) > 2:
            add("\\" + name)
    return found[:limit]
