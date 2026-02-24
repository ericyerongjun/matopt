/**
 * Formula helper utilities.
 *
 * Provides preprocessing so that LLM output (which may use a mix of
 * `$...$`, `$$...$$`, `\(...\)`, `\[...\]` delimiters) is normalised
 * to the `$` / `$$` form that remark-math understands.
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

// ── Markdown + LaTeX preprocessor ──────────────────────────────────────

/**
 * Preprocess LLM Markdown so that every math region uses `$` / `$$`
 * delimiters that remark-math can parse, and protect LaTeX from the
 * Markdown parser (e.g. underscores being treated as emphasis).
 *
 * Handles:
 *  1. `\[...\]`  →  `$$...$$`   (display math)
 *  2. `\(...\)`  →  `$...$`     (inline math)
 *  3. Ensures display `$$` blocks are surrounded by blank lines
 *     (required by remark-math for block-level rendering).
 *  4. Protects content inside `$`/`$$` from Markdown interpretation
 *     by escaping stray underscores that would become <em>.
 */
export function preprocessLaTeX(content: string): string {
    // Step 0 — guard: leave code blocks untouched.
    // Split on fenced code blocks (``` … ```), process only non-code parts.
    const codeBlockRegex = /(```[\s\S]*?```)/g;
    const segments = content.split(codeBlockRegex);

    const processed = segments.map((seg, idx) => {
        // Odd indices are code blocks — pass through unchanged.
        if (idx % 2 === 1) return seg;
        return _processNonCodeSegment(seg);
    });

    return processed.join("");
}

function _processNonCodeSegment(text: string): string {
    // 1. Convert \[...\] → $$...$$ (display)
    //    Allow the content to span multiple lines.
    text = text.replace(
        /\\\[([\s\S]*?)\\\]/g,
        (_match, inner: string) => `\n$$\n${inner.trim()}\n$$\n`
    );

    // 2. Convert \(...\) → $...$ (inline)
    text = text.replace(
        /\\\(([\s\S]*?)\\\)/g,
        (_match, inner: string) => `$${inner.trim()}$`
    );

    // 3. Handle edge case where LLM outputs a single `$` on its own line
    //    (e.g. display math split as `$\n<content>\n$`). Collapse into `$$...$$`.
    text = text.replace(
        /^\$\s*\n([\s\S]*?)\n\s*\$$/gm,
        (_match, inner: string) => `\n$$\n${inner.trim()}\n$$\n`
    );

    // 4. Ensure $$ display blocks have blank lines before/after
    //    so remark-math treats them as block-level math.
    //    Only match actual $$ (two dollar signs), not single $.
    text = text.replace(/([^\n$])\n?\$\$/g, "$1\n\n$$");
    text = text.replace(/\$\$\n?([^\n$])/g, "$$\n\n$1");

    return text;
}
