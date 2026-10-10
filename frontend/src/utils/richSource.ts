/** Preview eligibility is deliberately conservative. The isolated HTML runtime
 * and SVG image context, not this syntax check, are the security boundaries. */
export type RichSourceKind = "html" | "svg" | "markdown";
export const MAX_RICH_SOURCE_LENGTH = 2_000_000;

const htmlVoidTags = new Set(["area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"]);
const rawTextTags = new Set(["script", "style", "textarea", "title"]);

function hasCompleteMarkup(source: string, kind: "html" | "svg"): boolean {
    const stack: string[] = [];
    let cursor = 0;
    let elements = 0;
    let roots = 0;
    while (cursor < source.length) {
        const rawTag = stack.at(-1);
        if (rawTag && rawTextTags.has(rawTag.toLowerCase())) {
            const closing = new RegExp(`</${rawTag}\\s*>`, "i").exec(source.slice(cursor));
            if (!closing) return false;
            cursor += closing.index;
        } else {
            const next = source.indexOf("<", cursor);
            if (kind === "svg" && stack.length === 0 && source.slice(cursor, next < 0 ? undefined : next).trim()) return false;
            if (next < 0) break;
            cursor = next;
        }
        if (source.startsWith("<!--", cursor)) {
            const end = source.indexOf("-->", cursor + 4);
            if (end < 0) return false;
            cursor = end + 3;
            continue;
        }
        const remaining = source.slice(cursor);
        if (kind === "svg" && remaining.startsWith("<![CDATA[")) {
            const end = source.indexOf("]]>", cursor + 9);
            if (end < 0 || !stack.length) return false;
            cursor = end + 3; continue;
        }
        if (kind === "svg" && remaining.startsWith("<?xml ")) {
            const end = source.indexOf("?>", cursor + 6);
            if (end < 0 || elements > 0) return false;
            cursor = end + 2; continue;
        }
        if (kind === "svg" && /^<!DOCTYPE\s+svg\b/i.test(remaining)) {
            let end = cursor + 9;
            let quote = "";
            let subset = 0;
            for (; end < source.length; end++) {
                const character = source[end];
                if (quote) { if (character === quote) quote = ""; }
                else if (character === '"' || character === "'") quote = character;
                else if (character === "[") subset++;
                else if (character === "]") subset--;
                else if (character === ">" && subset === 0) break;
            }
            if (end >= source.length || elements > 0) return false;
            cursor = end + 1; continue;
        }
        const doctype = /^<!doctype\s+html\s*>/i.exec(remaining);
        if (kind === "html" && doctype) { cursor += doctype[0].length; continue; }
        const tag = /^<(\/)?([A-Za-z][\w:.-]*)(?=[\s/>])/.exec(remaining);
        if (!tag) return false;
        let end = cursor + tag[0].length;
        let quote = "";
        for (; end < source.length; end++) {
            const character = source[end];
            if (quote) { if (character === quote) quote = ""; }
            else if (character === '"' || character === "'") quote = character;
            else if (character === ">") break;
            else if (character === "<") return false;
        }
        if (end >= source.length || quote) return false;
        const name = kind === "html" ? tag[2].toLowerCase() : tag[2];
        if (tag[1]) {
            if (!/^\s*$/.test(source.slice(cursor + tag[0].length, end)) || stack.pop() !== name) return false;
        } else {
            elements++;
            if (kind === "svg" && stack.length === 0 && name !== "svg") return false;
            if (stack.length === 0) roots++;
            if (!/\/\s*$/.test(source.slice(cursor, end)) && !(kind === "html" && htmlVoidTags.has(name))) stack.push(name);
        }
        cursor = end + 1;
    }
    return elements > 0 && stack.length === 0 && (kind !== "svg" || roots === 1);
}

export function richSourcePreviewIssue(source: string, kind: RichSourceKind): string | null {
    if (!source.trim()) return "There is no content to preview.";
    if (source.length > MAX_RICH_SOURCE_LENGTH) return "This output is too large to preview. Its source is still available.";
    if (kind === "markdown") return null;
    const trimmed = source.trim();
    if (kind === "svg" && typeof DOMParser !== "undefined") {
        const document = new DOMParser().parseFromString(trimmed, "image/svg+xml");
        if (document.querySelector("parsererror") || document.documentElement.localName !== "svg"
            || (document.documentElement.namespaceURI && document.documentElement.namespaceURI !== "http://www.w3.org/2000/svg")) return "This SVG could not be previewed. Its source is still available.";
        return null;
    }
    if (!hasCompleteMarkup(trimmed, kind)) return "Finish the HTML or SVG tags to preview this source.";
    return null;
}

/** SVG loaded as an image is inert: scripts, event handlers, and external
 * resources cannot run in the host. Normalize only the preview copy. */
export function svgImageDataUrl(source: string): string {
    let imageSource = source;
    if (typeof DOMParser !== "undefined" && typeof XMLSerializer !== "undefined") {
        const document = new DOMParser().parseFromString(source, "image/svg+xml");
        if (!document.documentElement.namespaceURI) {
            document.documentElement.setAttribute("xmlns", "http://www.w3.org/2000/svg");
            imageSource = new XMLSerializer().serializeToString(document.documentElement);
        }
    }
    return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(imageSource)}`;
}

export function richFence(language: string): { kind: "html" | "svg"; defaultView: "code" | "preview" } | null {
    const normalized = language.toLowerCase();
    if (["html", "svg", "html-preview", "svg-preview"].includes(normalized)) {
        return { kind: normalized.startsWith("html") ? "html" : "svg", defaultView: normalized.endsWith("-preview") ? "preview" : "code" };
    }
    return null;
}

export function richFenceKey(language: string, code: string): string { return `${language.toLowerCase()}\0${code.replace(/\n+$/, "")}`; }

/** Streamdown can repair an unfinished fence. Only the original closing fence
 * makes executable output eligible for a preview. Outer source fences win. */
export function closedRichFences(markdown: string): Set<string> {
    const closed = new Set<string>();
    let active: { marker: string; length: number; language: string; lines: string[] } | null = null;
    for (const line of markdown.replace(/\r\n/g, "\n").split("\n")) {
        if (active) {
            const closing = /^ {0,3}(`{3,}|~{3,})\s*$/.exec(line);
            if (closing && closing[1][0] === active.marker && closing[1].length >= active.length) {
                if (richFence(active.language)) closed.add(richFenceKey(active.language, active.lines.join("\n")));
                active = null;
            } else active.lines.push(line);
        } else {
            const opening = /^ {0,3}(`{3,}|~{3,})[ \t]*([^\s`~]*)[^\n]*$/.exec(line);
            if (opening) active = { marker: opening[1][0], length: opening[1].length, language: opening[2], lines: [] };
        }
    }
    return closed;
}

/** Whole documents are output; mixed prose and explicitly fenced source stay
 * in the ordinary Markdown path. Fragments require a fence or typed artifact. */
export function standaloneRichSource(source: string): "html" | "svg" | null {
    const trimmed = source.trim();
    const kind = /^(?:<!doctype\s+html\s*>\s*)?<html(?:\s|>)/i.test(trimmed) && /<\/html\s*>$/i.test(trimmed) ? "html"
        : /^(?:<\?xml\b[\s\S]*?\?>\s*)?(?:<!DOCTYPE\s+svg\b[\s\S]*?>\s*)?(?:<!--[\s\S]*?-->\s*)*<svg(?:\s|>)/i.test(trimmed) ? "svg" : null;
    return kind && !richSourcePreviewIssue(trimmed, kind) ? kind : null;
}
