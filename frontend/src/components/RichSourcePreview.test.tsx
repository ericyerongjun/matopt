import "../../test/jsdom-env.mjs";
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import MarkdownRenderer from "./MarkdownRenderer.tsx";
import { RichSourcePreview } from "./RichSourcePreview.tsx";
import { closedRichFences, MAX_RICH_SOURCE_LENGTH, richSourcePreviewIssue, standaloneRichSource } from "../utils/richSource.ts";
import { ZCodeIntlProvider } from "../zcode/i18n/IntlProvider.js";
import { TooltipProvider } from "../zcode/components/ui/tooltip.js";
import { UiWorkspaceProvider } from "../features/intelligent-ui/UiWorkspace.tsx";

Object.defineProperty(window.HTMLCanvasElement.prototype, "getContext", { configurable: true, value: () => null });
const { ArtifactView } = await import("./ArtifactView.tsx");

const originalFetch = globalThis.fetch;
let root: Root | null = null;
let container: HTMLDivElement | null = null;
afterEach(() => { act(() => root?.unmount()); root = null; container?.remove(); container = null; globalThis.fetch = originalFetch; });

function mount(element: React.ReactNode) {
    container = document.createElement("div"); document.body.append(container); root = createRoot(container);
    act(() => root!.render(<ZCodeIntlProvider initialLocale="en-US"><TooltipProvider>{element}</TooltipProvider></ZCodeIntlProvider>));
    return container;
}
function button(label: string): HTMLButtonElement {
    const result = [...container!.querySelectorAll("button")].find((node) => node.textContent === label);
    assert.ok(result, `${label} control exists`); return result;
}
async function tick() { await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); }); }
const html = '<section><h1>Example</h1><script>window.example = "isolated";</script></section>';
const svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20"><circle cx="10" cy="10" r="6" /></svg>';

test("HTML source stays literal until Preview is selected and is sent only to the isolated frame", () => {
    const doc = mount(<MarkdownRenderer content={`\`\`\`html\n${html}\n\`\`\``} />);
    assert.equal(doc.querySelector("iframe"), null);
    assert.equal(doc.querySelector("pre code")?.textContent, html);
    assert.equal(doc.querySelector("h1, script"), null);
    act(() => button("Preview").click());
    const frame = doc.querySelector("iframe")!;
    assert.ok(frame);
    assert.equal(new URL(frame.src).origin, "http://localhost:3001");
    assert.equal(frame.getAttribute("sandbox"), "allow-scripts");
    assert.equal(frame.hasAttribute("srcdoc"), false);
    const sent: Record<string, unknown>[] = [];
    frame.contentWindow!.postMessage = (value: unknown) => { sent.push(value as Record<string, unknown>); };
    const params = new URLSearchParams(new URL(frame.src).hash.slice(1));
    act(() => window.dispatchEvent(new window.MessageEvent("message", { source: frame.contentWindow, data: {
        type: "matopt:ready", version: 1, channel: params.get("channel"), surfaceId: params.get("surface"),
    } })));
    assert.equal(sent.find((message) => message.type === "matopt:init")?.html, html);
    assert.equal(doc.querySelector("h1, script"), null);
    act(() => button("Code").click());
    assert.equal(doc.querySelector("iframe"), null);
    assert.equal(doc.querySelector("pre code")?.textContent, html);
});

for (const [language, source] of [["html-preview", html], ["svg-preview", svg]] as const) {
    test(`${language} opts into Preview while retaining the exact source`, () => {
        const doc = mount(<MarkdownRenderer content={`\`\`\`${language}\n${source}\n\`\`\``} />);
        assert.ok(doc.querySelector(language === "html-preview" ? "iframe" : 'img[src^="data:image/svg+xml"]'));
        assert.equal(button("Preview").getAttribute("aria-pressed"), "true");
        act(() => button("Code").click());
        assert.equal(doc.querySelector("pre code")?.textContent, source);
    });
}

test("SVG code examples keep their literal source and do not inject SVG into the host", () => {
    const doc = mount(<MarkdownRenderer content={`\`\`\`svg\n${svg}\n\`\`\``} />);
    assert.equal(doc.querySelector("iframe"), null);
    assert.equal(doc.querySelector("pre code")?.textContent, svg);
    assert.equal(doc.querySelector("svg[viewBox='0 0 20 20']"), null);
});

for (const [label, source, streaming] of [
    ["unfinished fence", `\`\`\`html-preview\n${html}`, false],
    ["unfinished tags", "```html-preview\n<div><span>Draft</span>\n```", false],
    ["streamed output", `\`\`\`html-preview\n${html}\n\`\`\``, true],
] as const) {
    test(`${label} remains readable code and cannot execute`, () => {
        const doc = mount(<MarkdownRenderer content={source} streaming={streaming} />);
        assert.equal(doc.querySelector("iframe"), null);
        assert.equal(button("Preview").disabled, true);
        assert.ok(doc.querySelector("pre code")?.textContent);
        assert.ok(doc.querySelector('[role="status"]'));
    });
}

test("complete standalone documents preview, but mixed prose and fenced Markdown remain Markdown", () => {
    const documentSource = `<!doctype html><html><head><title>Demo</title></head><body>${html}</body></html>`;
    const doc = mount(<MarkdownRenderer content={documentSource} />);
    assert.ok(doc.querySelector("iframe"));
    const fenced = new DOMParser().parseFromString(renderToStaticMarkup(<ZCodeIntlProvider initialLocale="en-US"><TooltipProvider><MarkdownRenderer
        content={`Before the example.\n\n\`\`\`markdown\n${documentSource}\n\`\`\`\n\n**After**`} /></TooltipProvider></ZCodeIntlProvider>), "text/html");
    assert.equal(fenced.querySelector("iframe"), null);
    assert.equal(fenced.querySelector("strong")?.textContent, "After");
    assert.equal(fenced.querySelector("[data-rich-source]"), null);
    assert.equal(standaloneRichSource(`Before\n${documentSource}`), null);
    assert.equal(standaloneRichSource(`${documentSource}\nAfter`), null);
});

test("incomplete, invalid, and oversized rich documents retain readable source with an explanation", () => {
    assert.ok(richSourcePreviewIssue('<svg><g></svg>', "svg"));
    assert.ok(richSourcePreviewIssue('<section title="unfinished>', "html"));
    assert.equal(richSourcePreviewIssue('<style>p { color: red }</style><p>Text<br>More</p><script>if (1 < 2) { window.x = 1; }</script>', "html"), null);
    assert.equal(closedRichFences("````markdown\n```html-preview\n<div>Example</div>\n```\n````").size, 0);
    const source = `<div>${"x".repeat(MAX_RICH_SOURCE_LENGTH)}</div>`;
    const doc = mount(<RichSourcePreview source={source} kind="html" defaultView="preview" />);
    assert.equal(doc.querySelector("iframe"), null);
    assert.equal(button("Preview").disabled, true);
    assert.equal(doc.querySelector("pre code")?.textContent, source);
    assert.match(doc.textContent || "", /too large to preview/);
});

test("typed Markdown artifacts render tables and equations and preserve their source on demand", () => {
    const source = "## Results\n\n| x | y |\n|---|---|\n| 1 | 2 |\n\nThe answer is $x^2$.\n";
    const doc = mount(<ArtifactView artifact={{ kind: "markdown", name: "results.md", data: source }} />);
    assert.equal(doc.querySelector("h2")?.textContent, "Results");
    assert.equal(doc.querySelector("tbody td")?.textContent, "1");
    assert.equal(doc.querySelector("annotation")?.textContent, "x^2");
    act(() => button("Code").click());
    assert.equal(doc.querySelector("pre code")?.textContent, source);
    assert.equal(doc.querySelector("table, .katex"), null);
});

test("inline SVG artifacts use an inert image and keep copyable source", () => {
    const doc = mount(<ArtifactView artifact={{ kind: "image", mime: "image/svg+xml", name: "diagram.svg", data: svg }} />);
    assert.ok(doc.querySelector('img[src^="data:image/svg+xml"]'));
    assert.equal(doc.querySelector("iframe"), null);
    act(() => button("Code").click());
    assert.equal(doc.querySelector("pre code")?.textContent, svg);
    assert.ok(doc.querySelector('button[aria-label="Copy source"]'));
});

test("exported SVG documents preserve XML declarations, doctypes, comments, and CDATA in Code", () => {
    const exported = '<?xml version="1.0" encoding="utf-8" standalone="no"?>\n<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">\n<!-- Created with a scientific plotting tool -->\n<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 100 100"><defs><style type="text/css"><![CDATA[*{stroke-linejoin:round}]]></style></defs><g id="axes_1"><path d="M0 0 L100 100" stroke="black" /></g></svg>\n';
    assert.equal(richSourcePreviewIssue(exported, "svg"), null);
    assert.equal(standaloneRichSource(exported), "svg");
    const doc = mount(<ArtifactView artifact={{ kind: "image", mime: "image/svg+xml", name: "figure.svg", data: exported }} />);
    const image = doc.querySelector('img[src^="data:image/svg+xml"]')!;
    assert.ok(image);
    assert.equal(doc.querySelector("g#axes_1, style, iframe"), null);
    const imageSource = decodeURIComponent(image.getAttribute("src")!.split(",").slice(1).join(","));
    assert.equal(imageSource, exported);
    act(() => button("Code").click());
    assert.equal(doc.querySelector("pre code")?.textContent, exported);
});

test("inline SVG without xmlns adds the image namespace only in its preview copy", () => {
    const original = '<svg viewBox="0 0 20 20"><circle cx="10" cy="10" r="6" /></svg>';
    const doc = mount(<RichSourcePreview source={original} kind="svg" defaultView="preview" />);
    const image = doc.querySelector('img[src^="data:image/svg+xml"]')!;
    assert.ok(image);
    const imageSource = decodeURIComponent(image.getAttribute("src")!.split(",").slice(1).join(","));
    const parsed = new DOMParser().parseFromString(imageSource, "image/svg+xml");
    assert.equal(parsed.documentElement.namespaceURI, "http://www.w3.org/2000/svg");
    assert.equal(parsed.querySelector("parsererror"), null);
    act(() => button("Code").click());
    assert.equal(doc.querySelector("pre code")?.textContent, original);
});

test("SVG image loading failures fall back to the original source", () => {
    const doc = mount(<RichSourcePreview source={svg} kind="svg" defaultView="preview" />);
    const image = doc.querySelector("img")!;
    act(() => image.dispatchEvent(new window.Event("error")));
    assert.equal(doc.querySelector("img"), null);
    assert.equal(doc.querySelector("pre code")?.textContent, svg);
    assert.match(doc.textContent || "", /image could not be previewed/);
});

test("disabled interactive capability preserves source without starting a frame", () => {
    const doc = mount(<UiWorkspaceProvider conversationId="one" capabilities={{ version: 1, native: true, mini_apps: false, node_types: [] }}>
        <ArtifactView artifact={{ kind: "html", name: "example.html", data: html }} />
    </UiWorkspaceProvider>);
    assert.equal(doc.querySelector("iframe"), null);
    assert.equal(doc.querySelector("pre code")?.textContent, html);
    assert.equal(button("Preview").disabled, true);
});

test("Markdown and SVG files load only from their conversation and render the loaded output", async () => {
    const requested: string[] = [];
    globalThis.fetch = async (url) => { requested.push(String(url)); return new Response(svg); };
    const doc = mount(<ArtifactView artifact={{ kind: "file", name: "figure.svg", path: "outputs/figure.svg" }} conversationId="one" />);
    await tick();
    assert.deepEqual(requested, ["http://localhost/api/files/one/outputs/figure.svg"]);
    assert.ok(doc.querySelector('img[src^="data:image/svg+xml"]'));
    act(() => root!.render(<ArtifactView artifact={{ kind: "markdown", name: "remote.md", url: "https://example.com/remote.md" }} conversationId="one" />));
    await tick();
    assert.equal(requested.length, 1);
    assert.equal(doc.querySelector("iframe"), null);
    assert.match(doc.textContent || "", /no accessible conversation file/);
});

for (const [name, response] of [
    ["HTTP error", () => new Response("Unavailable", { status: 404 })],
    ["oversized header", () => new Response("too much", { headers: { "Content-Length": String(MAX_RICH_SOURCE_LENGTH + 1) } })],
    ["oversized streamed body", () => new Response("x".repeat(MAX_RICH_SOURCE_LENGTH + 1))],
] as const) {
    test(`${name} displays a preview failure and retains the original download link`, async () => {
        globalThis.fetch = async () => response();
        const doc = mount(<ArtifactView artifact={{ kind: "html_file", name: "app.html", path: "app.html" }} conversationId="one" />);
        await tick();
        assert.equal(doc.querySelector("iframe"), null);
        assert.ok(doc.querySelector('[role="status"]'));
        assert.equal(doc.querySelector("a[download]")?.getAttribute("href"), "/api/files/one/app.html");
    });
}
