/** Shared MatOpt KaTeX settings: preserve accessible MathML and common macros. */
export const KATEX_OPTIONS = {
    output: "htmlAndMathml" as const,
    throwOnError: false,
    strict: false as const,
    macros: {
        "\\R": "\\mathbb{R}",
        "\\N": "\\mathbb{N}",
        "\\Z": "\\mathbb{Z}",
        "\\Q": "\\mathbb{Q}",
        "\\C": "\\mathbb{C}",
        "\\E": "\\mathbb{E}",
    },
};
