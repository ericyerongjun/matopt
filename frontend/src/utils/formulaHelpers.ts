/**
 * Formula helper utilities.
 *
 * LLM Markdown uses a mix of `$...$`, `$$...$$`, `\(...\)`, `\[...\]`,
 * AMS environments, and the occasional ```latex fence. remark-math only
 * understands `$` / `$$`, so we normalise everything else to that form
 * without rewriting delimiters that are already valid.
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
    return /\$\$[\s\S]+?\$\$|\$[^$]+?\$|\\\[[\s\S]+?\\\]|\\\([\s\S]+?\\\)|\\begin\{(?:align|equation|gather)/.test(
        text
    );
}

const FENCED_BLOCK = /(```[\s\S]*?```)/g;
const MATH_FENCE = /^```(?:latex|tex|math|katex)\s*\r?\n([\s\S]*?)```$/i;

/** Display environments KaTeX cannot use as-is (or only as a sibling of `aligned`). */
const DISPLAY_ENV =
    /\\begin\{(align\*?|equation\*?|gather\*?|multline\*?|eqnarray\*?|aligned|gathered)\}([\s\S]*?)\\end\{\1\}/g;

/**
 * Preprocess LLM Markdown so remark-math sees `$` / `$$` delimiters.
 *
 * Handles:
 *  1. ```latex / ```tex / ```math fences → display math
 *  2. `\[...\]`  →  `$$...$$`
 *  3. `\(...\)`  →  `$...$`
 *  4. `$` on its own lines around a block → `$$...$$`
 *  5. AMS display environments (`align` → `aligned`, wrap in `$$`)
 *  6. Bare `\boxed{...}` → display math
 *  7. `$ x $`  →  `$x$`  (remark-math rejects spaces next to `$`)
 *
 * Existing `$$...$$` is left intact — do not inject blank lines around
 * `$$`, which splits `$$E = mc^2$$` into invalid fragments.
 */
export function preprocessLaTeX(content: string): string {
    const segments = content.split(FENCED_BLOCK);

    return segments
        .map((seg, idx) => {
            if (idx % 2 === 1) {
                const fence = seg.match(MATH_FENCE);
                if (fence) return `\n$$\n${fence[1].trim()}\n$$\n`;
                return seg;
            }
            return processMarkdownSegment(seg);
        })
        .join("");
}

function processMarkdownSegment(text: string): string {
    text = text.replace(/\\\\\[([\s\S]*?)\\\\\]/g, (_m, inner: string) => asBlockMath(inner));
    text = text.replace(/\\\\\(([\s\S]*?)\\\\\)/g, (_m, inner: string) => `$${String(inner).trim()}$`);

    text = text.replace(/\\\[([\s\S]*?)\\\]/g, (_m, inner: string) => asBlockMath(inner));
    text = text.replace(/\\\(([\s\S]*?)\\\)/g, (_m, inner: string) => `$${String(inner).trim()}$`);

    text = text.replace(
        /^\$\s*\n([\s\S]*?)\n\s*\$$/gm,
        (_m, inner: string) => asBlockMath(inner)
    );

    text = rewriteDisplayEnvironments(text);
    text = wrapBareBoxed(text);
    text = trimInlineDollarMath(text);
    text = fenceifyMultilineDollars(text);
    return text;
}

function rewriteDisplayEnvironments(text: string): string {
    return text.replace(DISPLAY_ENV, (match, env: string, inner: string, offset: number) => {
        const rewritten = toKatexDisplay(env, inner);
        if (isInsideMath(text, offset)) return rewritten;
        return asBlockMath(rewritten);
    });
}

/** remark-math only treats `$$` as a display *fence* when it sits on its own line. */
function asBlockMath(body: string): string {
    return `\n$$\n${body.trim()}\n$$\n`;
}

/** Same-line `$$...$$` that contains newlines is not a valid remark-math fence. */
function fenceifyMultilineDollars(text: string): string {
    return text.replace(/\$\$([\s\S]*?)\$\$/g, (match, inner: string, offset: number) => {
        if (!inner.includes("\n")) return match;
        const prev = offset > 0 ? text[offset - 1] : "\n";
        const nextIdx = offset + match.length;
        const next = nextIdx < text.length ? text[nextIdx] : "\n";
        if (inner.startsWith("\n") && inner.endsWith("\n") && prev === "\n") {
            if (next === "\n" || nextIdx >= text.length) return match;
        }
        return asBlockMath(inner);
    });
}

function toKatexDisplay(env: string, inner: string): string {
    const body = inner.trim();
    const name = env.replace(/\*$/, "");
    if (name === "align" || name === "eqnarray") {
        return `\\begin{aligned}\n${body}\n\\end{aligned}`;
    }
    if (name === "gather") {
        return `\\begin{gathered}\n${body}\n\\end{gathered}`;
    }
    if (name === "aligned" || name === "gathered") {
        return `\\begin{${env}}\n${body}\n\\end{${env}}`;
    }
    return body;
}

function wrapBareBoxed(text: string): string {
    const start = "\\boxed{";
    let out = "";
    let i = 0;
    while (i < text.length) {
        const idx = text.indexOf(start, i);
        if (idx === -1) {
            out += text.slice(i);
            break;
        }
        out += text.slice(i, idx);
        const end = findMatchingBrace(text, idx + start.length - 1);
        if (end < 0) {
            out += text.slice(idx);
            break;
        }
        const cmd = text.slice(idx, end + 1);
        out += isInsideMath(text, idx) ? cmd : `$$${cmd}$$`;
        i = end + 1;
    }
    return out;
}

function findMatchingBrace(text: string, openIdx: number): number {
    let depth = 0;
    for (let j = openIdx; j < text.length; j++) {
        if (text[j] === "{") depth++;
        else if (text[j] === "}") {
            depth--;
            if (depth === 0) return j;
        }
    }
    return -1;
}

/**
 * remark-math (micromark-extension-math) requires the opening `$` not to be
 * followed by whitespace and the closing `$` not to be preceded by it.
 */
function trimInlineDollarMath(text: string): string {
    return text.replace(/\$(?!\$)([^\n$]+?)\$(?!\$)/g, (_m, inner: string) => {
        const trimmed = inner.trim();
        return trimmed ? `$${trimmed}$` : _m;
    });
}

function isInsideMath(text: string, offset: number): boolean {
    let i = 0;
    let inInline = false;
    let inDisplay = false;
    while (i < offset) {
        if (text[i] === "\\" && i + 1 < text.length) {
            i += 2;
            continue;
        }
        if (text.startsWith("$$", i)) {
            inDisplay = !inDisplay;
            i += 2;
            continue;
        }
        if (text[i] === "$") {
            inInline = !inInline;
            i += 1;
            continue;
        }
        i += 1;
    }
    return inInline || inDisplay;
}
