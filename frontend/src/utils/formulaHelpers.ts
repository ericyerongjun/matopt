/**
 * Formula helper utilities.
 *
 * LLM Markdown uses a mix of `$...$`, `$$...$$`, `\(...\)`, `\[...\]`,
 * AMS environments, and the occasional ```latex fence. remark-math only
 * understands `$` / `$$`, so we normalise everything else to that form
 * without rewriting delimiters that are already valid.
 */
import { normalizeMessageSingleDollarMath } from "../zcode/lib/markdownMath.ts";

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

const FENCE_START = /^ {0,3}(`{3,}|~{3,})([^\r\n]*)$/;
const FENCE_END = /^ {0,3}(`{3,}|~{3,})\s*$/;

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
 * A complete `$$...$$` occupying its own line becomes a display fence.
 * Double-dollar expressions within a prose line keep their existing form.
 */
export function preprocessLaTeX(content: string): string {
    const lines = content.match(/[^\n]*\n|[^\n]+$/g) || [];
    let output = "";
    let prose = "";
    for (let i = 0; i < lines.length; i++) {
        const start = FENCE_START.exec(lines[i].replace(/\r?\n$/, ""));
        if (!start) { prose += lines[i]; continue; }
        output += processProseOutsideInlineCode(prose);
        prose = "";
        const first = i;
        let closed = false;
        for (i++; i < lines.length; i++) {
            const end = FENCE_END.exec(lines[i].replace(/\r?\n$/, ""));
            if (end && end[1][0] === start[1][0] && end[1].length >= start[1].length) {
                closed = true;
                break;
            }
        }
        if (closed && /^(latex|tex|math|katex)$/i.test(start[2].trim())) {
            output += asBlockMath(lines.slice(first + 1, i).join(""));
        } else {
            // Also protect an unfinished fence while the assistant streams it.
            output += lines.slice(first, Math.min(i + 1, lines.length)).join("");
        }
    }
    output += processProseOutsideInlineCode(prose);
    return normalizeMessageSingleDollarMath(output);
}

function processProseOutsideInlineCode(text: string): string {
    let output = "";
    let cursor = 0;
    while (cursor < text.length) {
        const start = text.indexOf("`", cursor);
        if (start < 0) return output + normalizeProseMath(text.slice(cursor));
        let markerEnd = start + 1;
        while (text[markerEnd] === "`") markerEnd++;
        const marker = text.slice(start, markerEnd);
        let end = text.indexOf(marker, markerEnd);
        while (end >= 0 && (text[end - 1] === "`" || text[end + marker.length] === "`")) {
            end = text.indexOf(marker, end + marker.length);
        }
        if (end < 0) return output + normalizeProseMath(text.slice(cursor));
        output += normalizeProseMath(text.slice(cursor, start));
        output += text.slice(start, end + marker.length);
        cursor = end + marker.length;
    }
    return output;
}

function normalizeProseMath(text: string): string {
    const normalized = normalizeMessageSingleDollarMath(processMarkdownSegment(text));
    let output = "";
    for (let cursor = 0; cursor < normalized.length;) {
        if (normalized[cursor] === "\\") {
            output += normalized.slice(cursor, cursor + 2);
            cursor += 2;
            continue;
        }
        if (normalized[cursor] !== "$") {
            output += normalized[cursor++];
            continue;
        }
        let markerEnd = cursor + 1;
        while (normalized[markerEnd] === "$") markerEnd++;
        const length = markerEnd - cursor;
        const closing = findClosingDollarRun(normalized, markerEnd, length);
        if (closing >= 0) {
            output += normalized.slice(cursor, closing + length);
            cursor = closing + length;
        } else if (length === 1) {
            // The upstream guard leaves an unmatched `$50` intact. Micromark
            // can then pair it with a dollar *inside* later inline code before
            // the code tokenizer gets there. Escape unpaired prose dollars;
            // a later streaming chunk is normalized afresh when math closes.
            output += "\\$";
            cursor++;
        } else {
            // Keep an unfinished display-math fence intact while it streams.
            output += normalized.slice(cursor);
            break;
        }
    }
    return output;
}

function findClosingDollarRun(text: string, start: number, length: number): number {
    for (let cursor = start; cursor < text.length;) {
        if (text[cursor] === "\\") { cursor += 2; continue; }
        if (length === 1 && (text[cursor] === "\n" || text[cursor] === "\r")) return -1;
        if (text[cursor] !== "$") { cursor++; continue; }
        let end = cursor + 1;
        while (text[end] === "$") end++;
        if (end - cursor === length) return cursor;
        cursor = end;
    }
    return -1;
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
    // A standalone one-line display is valid LaTeX but remark-math treats it
    // as inline math unless the body is on a separate line. Keep the full pair.
    text = text.replace(/^([ \t]{0,3})\$\$([^\n]+?)\$\$[ \t]*$/gm,
        (_match, indent: string, body: string) => `${indent}$$\n${body.trim()}\n${indent}$$`);
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
    return text.replace(/(?<![\\$])\$(?!\$)([^\n$]+?)(?<!\\)\$(?!\$)/g, (_m, inner: string) => {
        const trimmed = inner.trim();
        const candidate = `$${trimmed}$`;
        // Trimming an apparent `$5 to $10` pair would eat a prose space.
        // Let the upstream guard distinguish real math before normalizing it.
        return trimmed && normalizeMessageSingleDollarMath(candidate) === candidate ? candidate : _m;
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
