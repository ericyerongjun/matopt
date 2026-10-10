/**
 * Agent Markdown is messy: tables inside list items, adjacent figure
 * links, and bare T=500-style math. Normalise those so remark-gfm and
 * remark-math can actually parse them.
 */

const FENCE_START = /^ {0,3}(`{3,}|~{3,})([^\r\n]*)$/;
const FENCE_END = /^ {0,3}(`{3,}|~{3,})\s*$/;
const DISPLAY_ENV_START = /^\\begin\{(align\*?|equation\*?|gather\*?|multline\*?|eqnarray\*?|aligned|gathered)\}/;
const TABLE_ROW = /^\s*\|.+\|\s*$/;
const TABLE_SEP = /^\s*\|?(?:\s*:?-{2,}:?\s*\|)+\s*:?-{2,}:?\s*\|?\s*$/;
const IMAGE_LINE = /^\s*(?:!\[[^\]]*]\([^)]+\)\s*)+$/;
const BARE_ASSIGN = /(^|[^$A-Za-z0-9])([A-Za-z][A-Za-z0-9]*)\s*=\s*(-?\d+(?:\.\d+)?)(?![\d$])/g;
const BARE_FRACTION = /(^|[^$0-9])(\d{1,2})\s*\/\s*(\d{1,3})(?![\d$])/g;

export function normalizeAgentMarkdown(content: string): string {
    const lines = content.match(/[^\n]*\n|[^\n]+$/g) || [];
    let output = "", prose = "";
    for (let i = 0; i < lines.length; i++) {
        const start = FENCE_START.exec(lines[i].replace(/\r?\n$/, ""));
        if (!start) { prose += lines[i]; continue; }
        output += normalizeProse(prose);
        prose = "";
        const first = i;
        for (i++; i < lines.length; i++) {
            const end = FENCE_END.exec(lines[i].replace(/\r?\n$/, ""));
            if (end && end[1][0] === start[1][0] && end[1].length >= start[1].length) break;
        }
        // Keep the original code, including an unfinished streamed fence.
        output += lines.slice(first, Math.min(i + 1, lines.length)).join("");
    }
    return output + normalizeProse(prose);
}

function normalizeProse(text: string): string {
    text = liftTables(text);
    text = collapseFigureLines(text);
    text = wrapBareMath(text);
    return text;
}

function liftTables(text: string): string {
    const lines = text.split("\n");
    const out: string[] = [];
    let i = 0;
    while (i < lines.length) {
        const row = lines[i];
        const next = lines[i + 1];
        if (TABLE_ROW.test(row) && next !== undefined && TABLE_SEP.test(next)) {
            const table: string[] = [];
            while (i < lines.length && (TABLE_ROW.test(lines[i]) || TABLE_SEP.test(lines[i]))) {
                table.push(lines[i].trim());
                i += 1;
            }
            if (out.length && out[out.length - 1].trim() !== "") out.push("");
            out.push(...table);
            if (i < lines.length && lines[i].trim() !== "") out.push("");
            continue;
        }
        out.push(row);
        i += 1;
    }
    return out.join("\n");
}

function collapseFigureLines(text: string): string {
    const lines = text.split("\n");
    const out: string[] = [];
    let i = 0;
    while (i < lines.length) {
        if (!IMAGE_LINE.test(lines[i])) {
            out.push(lines[i]);
            i += 1;
            continue;
        }
        const imgs: string[] = [];
        while (i < lines.length && IMAGE_LINE.test(lines[i])) {
            imgs.push(lines[i].trim());
            i += 1;
            // Consume separators only when another image follows. The final
            // newline may be the boundary before a protected code fence; losing
            // it joins that fence to the image and turns its closer into a new
            // opener, swallowing the rest of the rendered answer as code.
            let nextImage = i;
            while (nextImage < lines.length && lines[nextImage].trim() === "") nextImage += 1;
            if (nextImage >= lines.length || !IMAGE_LINE.test(lines[nextImage])) break;
            i = nextImage;
        }
        if (out.length && out[out.length - 1].trim() !== "") out.push("");
        out.push(imgs.join(" "));
        if (i < lines.length && lines[i].trim() !== "") out.push("");
    }
    return out.join("\n");
}

function wrapBareMath(text: string): string {
    let output = "", proseStart = 0;
    for (let cursor = 0; cursor < text.length;) {
        const end = protectedSpanEnd(text, cursor);
        if (end !== null) {
            output += wrapBareProse(text.slice(proseStart, cursor)) + text.slice(cursor, end);
            cursor = proseStart = end;
        } else {
            // Escaped dollars/backticks do not open a protected span.
            cursor += text[cursor] === "\\" ? 2 : 1;
        }
    }
    return output + wrapBareProse(text.slice(proseStart));
}

function wrapBareProse(text: string): string {
    return text
        .replace(BARE_ASSIGN, (_m, pre: string, name: string, value: string) => `${pre}$${name}=${value}$`)
        .replace(BARE_FRACTION, (_m, pre: string, a: string, b: string) => `${pre}$${a}/${b}$`);
}

/** Existing math and inline code must reach their own parsers unchanged. */
function protectedSpanEnd(text: string, start: number): number | null {
    if (text[start] === "`") {
        let markerEnd = start + 1;
        while (text[markerEnd] === "`") markerEnd++;
        const marker = text.slice(start, markerEnd);
        let end = text.indexOf(marker, markerEnd);
        while (end >= 0 && (text[end - 1] === "`" || text[end + marker.length] === "`")) {
            end = text.indexOf(marker, end + marker.length);
        }
        return end < 0 ? text.length : end + marker.length;
    }
    if (text[start] === "$") {
        let markerEnd = start + 1;
        while (text[markerEnd] === "$") markerEnd++;
        const length = markerEnd - start;
        for (let cursor = markerEnd; cursor < text.length;) {
            if (text[cursor] === "\\") { cursor += 2; continue; }
            if (length === 1 && /[\r\n]/.test(text[cursor])) return null;
            if (text[cursor] !== "$") { cursor++; continue; }
            let end = cursor + 1;
            while (text[end] === "$") end++;
            if (end - cursor === length) return end;
            cursor = end;
        }
        return length >= 2 ? text.length : null;
    }
    if (text[start] === "\\") {
        for (const [open, close] of [["\\\\[", "\\\\]"], ["\\\\(", "\\\\)"], ["\\[", "\\]"], ["\\(", "\\)"]]) {
            if (!text.startsWith(open, start)) continue;
            const end = text.indexOf(close, start + open.length);
            return end < 0 ? text.length : end + close.length;
        }
        const environment = DISPLAY_ENV_START.exec(text.slice(start));
        if (environment) {
            const close = `\\end{${environment[1]}}`;
            const end = text.indexOf(close, start + environment[0].length);
            return end < 0 ? text.length : end + close.length;
        }
    }
    return null;
}
