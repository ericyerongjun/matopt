/** Shared MatOpt KaTeX settings: preserve accessible MathML and common macros. */
export const KATEX_MACROS: Readonly<Record<string, string>> = Object.freeze({
    "\\R": "\\mathbb{R}",
    "\\N": "\\mathbb{N}",
    "\\Z": "\\mathbb{Z}",
    "\\Q": "\\mathbb{Q}",
    "\\C": "\\mathbb{C}",
    "\\E": "\\mathbb{E}",
});

/**
 * KaTeX writes into `options.macros` while rendering (`\tag` stores a
 * self-referencing `\df@tag`, `\gdef` stores definitions). A shared object
 * therefore leaks state between equations and becomes circular, which made
 * Streamdown's JSON comparison of plugin options throw mid-stream. Each read
 * of `macros` returns a fresh copy; renderers spread the options per equation.
 */
export function withKatexOptions<T extends object>(base: T): T & typeof KATEX_OPTIONS {
    return Object.defineProperty({ ...base, output: "htmlAndMathml" as const, throwOnError: false, strict: false as const }, "macros", {
        enumerable: true,
        get: () => ({ ...KATEX_MACROS }),
    }) as T & typeof KATEX_OPTIONS;
}

export const KATEX_OPTIONS: { output: "htmlAndMathml"; throwOnError: false; strict: false; macros: Record<string, string> } =
    withKatexOptions({}) as never;
