import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { downloadUrl, latexMathPreview, parseDelimited, previewKind, readTextPreview } from "./preview.ts";
import { buildWorkspaceTree, flattenWorkspaceFileTreeRows } from "./tree.ts";
import type { WorkspaceFile } from "../../types/protocol.ts";

const originalFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = originalFetch; });
const file = (path: string, kind = "text"): WorkspaceFile => ({ path, name: path.split("/").pop()!, kind, url: `/api/files/one/${path}`, size: 20, mtime: 1 });

test("upstream tree compacts directory chains and preserves directory-first order and depth", () => {
    const tree = buildWorkspaceTree([file("z.txt"), file("data/results/tables/a.csv"), file("data/results/tables/b.csv"), file("a.txt")]);
    const rows = flattenWorkspaceFileTreeRows({ rootPath: "", childrenByDirectory: tree.childrenByDirectory, expandedPaths: tree.directoryPaths,
        loadedDirectoryPaths: tree.directoryPaths, loadingDirectoryPaths: new Set(), errorByDirectory: new Map(), flattenEmptyDirectories: true });
    assert.deepEqual(rows.map((row) => [row.name, row.depth]), [["data/results/tables", 0], ["a.csv", 1], ["b.csv", 1], ["a.txt", 0], ["z.txt", 0]]);
    assert.deepEqual(rows[0].compactedPaths, ["data", "data/results", "data/results/tables"]);
    const collapsed = flattenWorkspaceFileTreeRows({ rootPath: "", childrenByDirectory: tree.childrenByDirectory, expandedPaths: new Set(),
        loadedDirectoryPaths: tree.directoryPaths, loadingDirectoryPaths: new Set(), errorByDirectory: new Map(), flattenEmptyDirectories: true });
    assert.deepEqual(collapsed.map((row) => row.path), ["data/results/tables", "a.txt", "z.txt"]);
});

test("CSV handles quoted delimiters, multiline fields, escaped quotes and bounded rows/columns", () => {
    const parsed = parseDelimited('name,value,note\r\n"a,b",2,"first\nsecond"\r\n"a""b",3,done\r\n');
    assert.deepEqual(parsed.rows, [["name", "value", "note"], ["a,b", "2", "first\nsecond"], ['a"b', "3", "done"]]);
    assert.equal(parsed.truncated, false);
    const limited = parseDelimited("a,b,c\n1,2,3\n4,5,6", ",", 2, 2);
    assert.deepEqual(limited.rows, [["a", "b"], ["1", "2"]]);
    assert.equal(limited.truncated, true); assert.equal(limited.columnsTruncated, true);
    assert.deepEqual(parseDelimited("a\tb\n1\t2", "\t").rows, [["a", "b"], ["1", "2"]]);
});

test("preview reads bounded bytes and cancels the remaining HTTP body", async () => {
    let cancelled = false;
    globalThis.fetch = async () => new Response(new ReadableStream({
        start(controller) { controller.enqueue(new TextEncoder().encode("0123456789")); },
        cancel() { cancelled = true; },
    }));
    const value = await readTextPreview("/large.txt", new AbortController().signal, 5);
    assert.deepEqual(value, { text: "01234", bytes: 5, truncated: true });
    assert.equal(cancelled, true);
});

test("preview stream preserves multibyte text and reports HTTP failures", async () => {
    const bytes = new TextEncoder().encode("αβγ");
    globalThis.fetch = async () => new Response(new ReadableStream({ start(controller) {
        controller.enqueue(bytes.subarray(0, 3)); controller.enqueue(bytes.subarray(3)); controller.close();
    } }), { headers: { "content-length": String(bytes.length) } });
    assert.deepEqual(await readTextPreview("/unicode.txt", new AbortController().signal, bytes.length), { text: "αβγ", bytes: 6, truncated: false });
    globalThis.fetch = async () => new Response("missing", { status: 404, statusText: "Not Found" });
    await assert.rejects(readTextPreview("/missing", new AbortController().signal), /404 Not Found/);
});

test("file modes and LaTeX formulas retain real source/download semantics", () => {
    assert.equal(previewKind(file("report.tex")), "latex");
    assert.equal(previewKind(file("table.csv")), "csv");
    assert.equal(previewKind(file("chart.png", "image")), "image");
    assert.equal(previewKind(file("archive.zip", "file")), "binary");
    assert.equal(downloadUrl("/api/files/one/a.csv?version=1"), "/api/files/one/a.csv?version=1&download=true");
    const formulas = latexMathPreview(String.raw`\documentclass{article}\begin{document}Let $x=1$. \[y=x^2\] \begin{align}a&=b\\c&=d\end{align}\end{document}`);
    assert.match(formulas, /x=1/); assert.match(formulas, /y=x\^2/); assert.match(formulas, /\\begin\{aligned\}/);
    assert.doesNotMatch(formulas, /documentclass|begin\{document\}/);
});

test("HTML files use the sandboxed preview mode while SVG files remain inert images", () => {
    assert.equal(previewKind(file("results/dashboard.html")), "html");
    assert.equal(previewKind(file("results/DASHBOARD.HTM")), "html");
    assert.equal(previewKind(file("results/dashboard", "html")), "html");
    assert.equal(previewKind(file("results/plot.svg")), "image");
});

test("a bare mathematical LaTeX file previews its fraction without document delimiters", () => {
    const fraction = String.raw`\hat\beta_1=\frac{\sum_i(x_i-\bar x)(y_i-\bar y)}{\sum_i(x_i-\bar x)^2}=2`;
    assert.equal(latexMathPreview(fraction), `$$\n${fraction}\n$$`);
    assert.equal(latexMathPreview("x^2+y^2=1"), "$$\nx^2+y^2=1\n$$");
});

test("full documents, preambles and prose do not become a bare formula", () => {
    assert.equal(latexMathPreview(String.raw`\documentclass{article}\begin{document}\frac{1}{2}\end{document}`), "");
    assert.equal(latexMathPreview(String.raw`\usepackage{amsmath}\frac{1}{2}`), "");
    assert.equal(latexMathPreview(String.raw`\textbf{Result:} \frac{1}{2}`), "");
    assert.equal(latexMathPreview(String.raw`The estimate is \frac{1}{2}.`), "");
    const document = String.raw`\documentclass{article}\begin{document}The estimate is \[\frac{1}{2}\].\end{document}`;
    assert.equal(latexMathPreview(document), "$$\n\\frac{1}{2}\n$$");
});
