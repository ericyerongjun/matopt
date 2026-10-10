/** File preview adapter for MatOpt HTTP workspaces.
 * The 256 KiB limit and extension map are migrated from ZCode packages/ui/src/lib/codeViewer.ts,
 * 29628c9acdb81b703bbd4080c207a0e7ce5e276e (Apache-2.0). Fetch/CSV/math adapters are MatOpt code. */
import type { WorkspaceFile } from "../../types/protocol.ts";

export const FILE_VIEWER_MAX_TEXT_BYTES = 256 * 1024;
const EXTENSION_TO_LANGUAGE: Record<string, string> = {
    bash: "bash", c: "c", cc: "cpp", cpp: "cpp", css: "css", diff: "diff", go: "go", h: "c",
    htm: "html", html: "html", java: "java", js: "javascript", json: "json", jsx: "jsx", md: "markdown",
    mermaid: "mermaid", mmd: "mermaid", mjs: "javascript", py: "python", rb: "ruby", rs: "rust", sh: "bash",
    sql: "sql", svg: "xml", text: "log", toml: "toml", ts: "typescript", tsx: "tsx", txt: "log",
    xml: "xml", yaml: "yaml", yml: "yaml",
    // MatOpt additions for mathematical workspaces.
    tex: "latex", latex: "latex", r: "r", jl: "julia", ini: "ini", ipynb: "json", rst: "text",
};
export type PreviewKind = "image" | "pdf" | "html" | "markdown" | "latex" | "csv" | "plotly" | "source" | "binary";
export function fileExtension(path: string) { return path.split("/").pop()?.split(".").pop()?.toLowerCase() ?? ""; }
export function previewKind(file: WorkspaceFile): PreviewKind {
    const ext = fileExtension(file.path);
    if (file.kind === "image" || /^(png|jpe?g|gif|webp|bmp|svg|avif|apng|ico)$/.test(ext)) return "image";
    if (ext === "pdf") return "pdf";
    if (file.kind === "html" || /^(htm|html)$/.test(ext)) return "html";
    if (file.kind === "plotly") return "plotly";
    if (file.kind === "markdown" || /^(md|markdown|mdown)$/.test(ext)) return "markdown";
    if (/^(tex|latex)$/.test(ext)) return "latex";
    if (/^(csv|tsv)$/.test(ext)) return "csv";
    if (EXTENSION_TO_LANGUAGE[ext] || /^(text|code|json)$/.test(file.kind)
        || /^(txt|log|cfg|conf|env|gitignore|dockerfile|makefile)$/.test(ext)) return "source";
    return "binary";
}
export function codeLanguage(path: string) { return EXTENSION_TO_LANGUAGE[fileExtension(path)] ?? "text"; }
export function downloadUrl(url: string) { return `${url}${url.includes("?") ? "&" : "?"}download=true`; }
export function formatFileSize(size: number) {
    if (size < 1024) return `${size} B`;
    if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
    return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}
export interface TextSlice { text: string; bytes: number; truncated: boolean }
export async function readTextPreview(url: string, signal: AbortSignal, maxBytes = FILE_VIEWER_MAX_TEXT_BYTES): Promise<TextSlice> {
    const response = await fetch(url, { signal });
    if (!response.ok) throw new Error(`Could not load file (${response.status} ${response.statusText}).`);
    const reader = response.body?.getReader();
    if (!reader) return { text: "", bytes: 0, truncated: false };
    const decoder = new TextDecoder();
    const text: string[] = [];
    let bytes = 0;
    let truncated = false;
    const declaredSize = Number(response.headers.get("content-length") ?? NaN);
    try {
        while (bytes < maxBytes) {
            if (signal.aborted) throw new DOMException("Aborted", "AbortError");
            const { value, done } = await reader.read();
            if (done) break;
            const remaining = maxBytes - bytes;
            const chunk = value.subarray(0, remaining);
            text.push(decoder.decode(chunk, { stream: true }));
            bytes += chunk.byteLength;
            if (value.byteLength > remaining || bytes === maxBytes) {
                truncated = value.byteLength > remaining || declaredSize !== maxBytes;
                await reader.cancel();
                break;
            }
        }
        text.push(decoder.decode());
        return { text: text.join(""), bytes, truncated };
    } finally {
        // Cancellation also releases an unfinished HTTP stream when the selected file changes.
        await reader.cancel().catch(() => {});
        reader.releaseLock();
    }
}

export interface CsvPreview { rows: string[][]; truncated: boolean; columnsTruncated: boolean }
/** Quoted CSV/TSV, including doubled quotes and newlines inside cells; bounded DOM output. */
export function parseDelimited(text: string, delimiter = ",", maxRows = 201, maxColumns = 40): CsvPreview {
    const rows: string[][] = [];
    let row: string[] = [], field = "", quoted = false, columnsTruncated = false;
    const pushField = () => { if (row.length < maxColumns) row.push(field); else columnsTruncated = true; field = ""; };
    const pushRow = () => { pushField(); rows.push(row); row = []; };
    let index = 0;
    for (; index < text.length; index++) {
        const char = text[index];
        if (char === '"') {
            if (quoted && text[index + 1] === '"') { field += '"'; index++; }
            else if (quoted || field.length === 0) quoted = !quoted;
            else field += char;
        } else if (!quoted && char === delimiter) pushField();
        else if (!quoted && (char === "\n" || char === "\r")) {
            if (char === "\r" && text[index + 1] === "\n") index++;
            pushRow();
            if (rows.length >= maxRows) { index++; break; }
        } else field += char;
    }
    if (rows.length < maxRows && (field.length > 0 || row.length > 0)) pushRow();
    return { rows, truncated: index < text.length, columnsTruncated };
}

/** Extract mathematical fragments for KaTeX; this does not compile a LaTeX document. */
export function latexMathPreview(text: string): string {
    const expressions: Array<{ index: number; body: string }> = [];
    const patterns = [
        /\$\$([\s\S]*?)\$\$/g,
        /\\\[([\s\S]*?)\\\]/g,
        /\\begin\{(equation\*?|align\*?|gather\*?|displaymath)\}([\s\S]*?)\\end\{\1\}/g,
        /\\\(([\s\S]*?)\\\)/g,
        /(?<!\$)\$(?!\$)([^$\n]+)\$(?!\$)/g,
    ];
    for (const pattern of patterns) for (const match of text.matchAll(pattern)) {
        let body = match[2] ?? match[1];
        if (match[2] && match[1].startsWith("align")) body = `\\begin{aligned}${body}\\end{aligned}`;
        if (match[2] && match[1].startsWith("gather")) body = `\\begin{gathered}${body}\\end{gathered}`;
        expressions.push({ index: match.index ?? 0, body: body.replace(/\\label\{[^}]*\}/g, "").trim() });
    }
    if (!expressions.length) {
        const bare = text.replace(/(?<!\\)%[^\n]*/g, "").trim();
        // Formula files often contain the expression alone, without a math delimiter.
        // Keep documents and prose on the extraction path; this fallback does not compile them.
        const documentOrTextCommand = /\\(?:documentclass|usepackage|RequirePackage|newcommand|renewcommand|providecommand|def|input|include|title|author|date|maketitle|section|subsection|chapter|paragraph|tableofcontents|text(?:rm|sf|tt|bf|it)?|emph)\b/;
        const nonMathEnvironment = [...bare.matchAll(/\\(?:begin|end)\{([^}]+)\}/g)].some((match) =>
            !/^(?:matrix|pmatrix|bmatrix|Bmatrix|vmatrix|Vmatrix|cases|aligned|gathered|smallmatrix)$/.test(match[1]));
        const recognizableMath = /[=^_<>]|\\(?:[dt]?frac|sqrt|sum|prod|int|lim|sin|cos|tan|log|exp|hat|bar|vec|mathbb|mathbf|alpha|beta|gamma|delta|theta|lambda|mu|pi|sigma|phi|omega)\b/.test(bare);
        const prose = bare.replace(/\\(?:begin|end)\{[^}]+\}/g, "").replace(/\\[A-Za-z]+/g, "");
        if (bare && recognizableMath && !documentOrTextCommand.test(bare) && !nonMathEnvironment
            && !/[A-Za-z]{3,}/.test(prose) && !/\$|\\[\[\]()]/.test(bare)) {
            expressions.push({ index: 0, body: bare });
        }
    }
    return expressions.sort((a, b) => a.index - b.index).slice(0, 100).map(({ body }) => `$$\n${body}\n$$`).join("\n\n");
}
