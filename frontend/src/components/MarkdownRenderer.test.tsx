import "../../test/diffs-dom-env.mjs";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { describe, it } from "node:test";
import { JSDOM } from "jsdom";
import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { ZCodeIntlProvider } from "../zcode/i18n/IntlProvider.js";
import { TooltipProvider } from "../zcode/components/ui/tooltip.js";

Object.defineProperty(window.HTMLCanvasElement.prototype, "getContext", { configurable: true, value: () => null });
const { default: MarkdownRenderer } = await import("./MarkdownRenderer.tsx");

function render(source: string, streaming = false) {
    return new JSDOM(renderToStaticMarkup(
        <ZCodeIntlProvider initialLocale="en-US"><TooltipProvider><MarkdownRenderer content={source} streaming={streaming} /></TooltipProvider></ZCodeIntlProvider>,
    ));
}

describe("literal native ZCode Markdown renderer", () => {
    it("uses native heading, list, quote and table components in the actual response", () => {
        const dom = render("## Results\n\n- First\n- Second\n\n> Evidence\n\n| x | y |\n|---|---|\n| 1 | 2 |");
        try {
            const doc = dom.window.document;
            assert.equal(doc.querySelector("h2")?.textContent, "Results");
            assert.ok(doc.querySelector('ul[data-markdown-list="unordered"]'));
            assert.equal(doc.querySelector("blockquote[data-markdown-blockquote]")?.textContent?.trim(), "Evidence");
            assert.ok(doc.querySelector("[data-markdown-table-frame]"));
            assert.ok(doc.querySelector("[data-markdown-table-toolbar]"));
            assert.equal(doc.querySelector("tbody td")?.textContent, "1");
        } finally { dom.window.close(); }
    });

    it("retains native code framing and copy/wrap controls", () => {
        const dom = render("```python\nprint(1)\n```");
        try {
            const doc = dom.window.document;
            assert.ok(doc.querySelector('[data-language="python"]'));
            assert.ok(doc.querySelector('button[aria-label="Copy code"]'));
            assert.ok(doc.querySelector('button[aria-label="Wrap lines"]'));
            // @pierre/diffs inserts the code into its shadow root after mount.
            assert.ok(doc.querySelector("diffs-container"));
        } finally { dom.window.close(); }
    });

    it("keeps scientific fence contents inside the native frame with a real chart loading slot", () => {
        const dom = render('```plotly\n{"data":[{"x":[1],"y":[2]}]}\n```');
        try {
            const doc = dom.window.document;
            assert.ok(doc.querySelector('[data-language="plotly"]'));
            assert.ok(doc.querySelector('button[aria-label="Copy code"]'));
            assert.equal(doc.querySelector('button[aria-label="Wrap lines"]'), null);
            assert.ok(doc.body.textContent?.includes("Loading chart…"));
        } finally { dom.window.close(); }
    });

    it("uses a shipped native icon for every supported scientific fence language", () => {
        for (const language of ["plotly", "chartjs", "chart"]) {
            const dom = render(`\`\`\`${language}\n{"data":[]}\n\`\`\``);
            try {
                const frame = dom.window.document.querySelector(`[data-language="${language}"]`);
                assert.ok(frame, `native ${language} frame exists`);
                const src = frame.querySelector("img")?.getAttribute("src");
                assert.equal(src, "/material-icons/json.svg");
                const asset = readFileSync(new URL("../../public" + src, import.meta.url), "utf8");
                assert.match(asset, /<svg\b/);
            } finally { dom.window.close(); }
        }
    });

    for (const streaming of [false, true]) {
        it(`keeps an image, unlabeled tree, table, math, and following prose separate in ${streaming ? "streaming" : "static"} rendering`, () => {
            const source = [
                "![Decision tree](https://example.com/tree.png)", "", "```",
                "root", "|-- left: class A", "+-- right: class B", "```", "",
                "| Node | Count |", "|---|---|", "| left | 2 |", "",
                "$$", String.raw`P(A)=\frac{2}{3}`, "$$", "",
                String.raw`**Q5** The final probability is $\frac{2}{3}$.`,
            ].join("\n");
            const dom = render(source, streaming);
            try {
                const doc = dom.window.document;
                const frames = [...doc.querySelectorAll('[data-language="text"]')].filter((node) => !node.parentElement?.closest('[data-language]'));
                assert.equal(frames.length, 1, "only the tree is a plain-text code block");
                assert.equal(doc.querySelectorAll("table").length, 1, "the table after the closing fence must be parsed as GFM");
                assert.equal(doc.querySelector("tbody td")?.textContent, "left");
                assert.equal(doc.querySelectorAll(".katex").length, 2, "display and inline math after the tree remain math");
                const final = doc.querySelector("strong");
                assert.equal(final?.textContent, "Q5");
                assert.equal(final?.closest('[data-language], pre, code'), null, "following prose cannot be captured by the closing fence");
                assert.ok(final?.parentElement?.textContent?.includes("The final probability is"));
            } finally { dom.window.close(); }
        });

        it(`preserves an explicit Markdown source fence as literal code in ${streaming ? "streaming" : "static"} rendering`, () => {
            const source = [
                "![Reference](https://example.com/reference.png)", "", "```markdown",
                "| Node | Count |", "|---|---|", "| left | 2 |", "",
                "$$", String.raw`P(A)=\frac{2}{3}`, "$$", "",
                "**Literal Markdown source**", "```", "", "**Outside the example**",
            ].join("\n");
            const dom = render(source, streaming);
            try {
                const doc = dom.window.document;
                const frames = [...doc.querySelectorAll('[data-language="markdown"]')].filter((node) => !node.parentElement?.closest('[data-language]'));
                assert.equal(frames.length, 1);
                assert.equal(doc.querySelectorAll("table").length, 0);
                assert.equal(doc.querySelectorAll(".katex").length, 0);
                assert.deepEqual([...doc.querySelectorAll("strong")].map((node) => node.textContent), ["Outside the example"]);
            } finally { dom.window.close(); }
        });
    }

    it("updates one mounted response through incomplete fences, tables, and math deltas", () => {
        const container = document.createElement("div");
        document.body.append(container);
        const root = createRoot(container);
        const update = (content: string, streaming = true) => act(() => root.render(
            <ZCodeIntlProvider initialLocale="en-US"><TooltipProvider><MarkdownRenderer content={content} streaming={streaming} /></TooltipProvider></ZCodeIntlProvider>,
        ));
        try {
            let content = "Here is the calculation.\n\n```python\nprint(";
            update(content);
            const response = container.querySelector('[data-response-streaming]');
            assert.equal(container.querySelectorAll('[data-language="python"]').length > 0, true);
            assert.equal(container.querySelector("table"), null);
            content += "2)\n``";
            update(content);
            assert.equal(container.querySelector("table"), null);
            content += "`\n\n| Question | Result |\n|---|";
            update(content);
            content += "---|\n| Q4 | 2 |\n\nThe probability is $\\frac{2}{";
            update(content);
            assert.equal(container.querySelector("tbody td")?.textContent, "Q4");
            content += "3}$ and the answer is **checked**.";
            update(content);
            assert.equal(container.querySelector('[data-response-streaming]'), response, "deltas reuse the existing response container");
            assert.equal(container.querySelector("annotation")?.textContent, String.raw`\frac{2}{3}`);
            assert.equal(container.querySelector("strong")?.textContent, "checked");
            assert.equal(container.querySelector("strong")?.closest("pre,code,[data-language]"), null);
            update(content, false);
            assert.equal(container.querySelector('[data-response-streaming]')?.getAttribute("aria-busy"), "false");
            assert.equal(container.querySelectorAll("table").length, 1);
            assert.equal(container.querySelectorAll(".katex-error").length, 0);
        } finally { act(() => root.unmount()); container.remove(); }
    });

    it("does not execute progressively received HTML before the completed answer settles", () => {
        const container = document.createElement("div");
        document.body.append(container);
        const root = createRoot(container);
        const update = (content: string, streaming = true) => act(() => root.render(
            <ZCodeIntlProvider initialLocale="en-US"><TooltipProvider><MarkdownRenderer content={content} streaming={streaming} /></TooltipProvider></ZCodeIntlProvider>,
        ));
        try {
            let content = "```html-preview\n<section><h1>Result";
            update(content);
            assert.equal(container.querySelector("iframe,h1,script"), null);
            content += "</h1><script>window.example=1;</script></section>\n```";
            update(content);
            assert.equal(container.querySelector("iframe,h1,script"), null);
            assert.ok(container.querySelector("pre code")?.textContent?.includes("window.example=1"));
            update(content, false);
            const frame = container.querySelector("iframe");
            assert.ok(frame);
            assert.equal(frame.getAttribute("sandbox"), "allow-scripts");
            assert.equal(frame.hasAttribute("srcdoc"), false);
            assert.equal(container.querySelector("h1,script"), null);
        } finally { act(() => root.unmount()); container.remove(); }
    });

    it("keeps rendering Markdown while a tagged display equation streams in", () => {
        const answer = "# Stein's method\n\n$Z\\sim N(0,1)$ if and only if\n\n$$\n\\mathbb{E}[f'(Z)] = \\mathbb{E}[Zf(Z)]\n\\tag{1}\n$$\n\nfor every absolutely continuous $f$, and again\n\n$$\nW \\overset{d}{=} Z \\tag{2}\n$$\n\n## Next\n\nDone with $\\E[W]$.";
        const container = document.createElement("div");
        document.body.append(container);
        const root = createRoot(container);
        const warn = console.warn, error = console.error;
        const failures: unknown[] = [];
        console.warn = (...args: unknown[]) => failures.push(args);
        console.error = (...args: unknown[]) => failures.push(args);
        try {
            for (let length = 8; length <= answer.length; length += 8) {
                act(() => root.render(<ZCodeIntlProvider initialLocale="en-US"><TooltipProvider><MarkdownRenderer content={answer.slice(0, length)} streaming /></TooltipProvider></ZCodeIntlProvider>));
            }
            act(() => root.render(<ZCodeIntlProvider initialLocale="en-US"><TooltipProvider><MarkdownRenderer content={answer} streaming /></TooltipProvider></ZCodeIntlProvider>));
            assert.deepEqual(failures, []);
            assert.equal(container.querySelector("h1")?.textContent, "Stein's method");
            assert.equal(container.querySelectorAll(".katex-display").length, 2);
            assert.ok(!container.textContent?.includes("$$"));
        } finally {
            console.warn = warn; console.error = error;
            act(() => root.unmount()); container.remove();
        }
    });
});
