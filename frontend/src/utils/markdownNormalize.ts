/**
 * Agent Markdown is messy: tables inside list items, adjacent figure
 * links, and bare T=500-style math. Normalise those so remark-gfm and
 * remark-math can actually parse them.
 */

const FENCE = /(```[\s\S]*?```)/g;
const TABLE_ROW = /^\s*\|.+\|\s*$/;
const TABLE_SEP = /^\s*\|?(?:\s*:?-{2,}:?\s*\|)+\s*:?-{2,}:?\s*\|?\s*$/;
const IMAGE_LINE = /^\s*(?:!\[[^\]]*]\([^)]+\)\s*)+$/;
const BARE_ASSIGN = /(^|[^$A-Za-z0-9])([A-Za-z][A-Za-z0-9]*)\s*=\s*(-?\d+(?:\.\d+)?)(?![\d$])/g;
const BARE_FRACTION = /(^|[^$0-9])(\d{1,2})\s*\/\s*(\d{1,3})(?![\d$])/g;

export function normalizeAgentMarkdown(content: string): string {
    return content
        .split(FENCE)
        .map((seg, i) => (i % 2 === 1 ? seg : normalizeProse(seg)))
        .join("");
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
            while (i < lines.length && lines[i].trim() === "") i += 1;
        }
        if (out.length && out[out.length - 1].trim() !== "") out.push("");
        out.push(imgs.join(" "));
        if (i < lines.length && lines[i].trim() !== "") out.push("");
    }
    return out.join("\n");
}

function wrapBareMath(text: string): string {
    return text
        .replace(BARE_ASSIGN, (_m, pre: string, name: string, value: string) => `${pre}$${name}=${value}$`)
        .replace(BARE_FRACTION, (_m, pre: string, a: string, b: string) => `${pre}$${a}/${b}$`);
}
