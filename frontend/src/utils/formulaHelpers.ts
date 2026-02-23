/**
 * Formula helper utilities.
 */

/** Wrap a raw LaTeX string in display-math delimiters if not already wrapped. */
export function ensureDisplayMath(latex: string): string {
    const trimmed = latex.trim();
    if (trimmed.startsWith("$$") && trimmed.endsWith("$$")) return trimmed;
    if (trimmed.startsWith("\\[") && trimmed.endsWith("\\]")) return trimmed;
    return `$$\n${trimmed}\n$$`;
}

/** Wrap a string in inline-math delimiters if not already wrapped. */
export function ensureInlineMath(latex: string): string {
    const trimmed = latex.trim();
    if (trimmed.startsWith("$") && trimmed.endsWith("$")) return trimmed;
    if (trimmed.startsWith("\\(") && trimmed.endsWith("\\)")) return trimmed;
    return `$${trimmed}$`;
}

/** Quick check whether a string contains any LaTeX math delimiters. */
export function containsMath(text: string): boolean {
    return /\$\$[\s\S]+?\$\$|\$[^$]+?\$|\\\[[\s\S]+?\\\]|\\\([\s\S]+?\\\)/.test(
        text
    );
}
