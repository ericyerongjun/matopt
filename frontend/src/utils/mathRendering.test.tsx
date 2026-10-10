import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { describe, it } from "node:test";
import { JSDOM } from "jsdom";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import MarkdownRenderer from "../components/MarkdownRenderer.tsx";
import { ZCodeIntlProvider } from "../zcode/i18n/IntlProvider.js";

function rendered(source: string) {
    const html = renderToStaticMarkup(<ZCodeIntlProvider initialLocale="en-US"><MarkdownRenderer content={source} conversationId="math-regression" /></ZCodeIntlProvider>);
    return new JSDOM(html);
}

describe("MatOpt math with native ZCode prose", () => {
    it("renders the complete spaced regression equation without inserting inline math into it", () => {
        const body = String.raw`\hat a=\frac{\sum_i(x_i-\bar x)(y_i-\bar y)}{\sum_i(x_i-\bar x)^2}=2,\qquad \hat b=\bar y-\hat a\bar x=1.`;
        for (const formula of [body, body.replace(",\\qquad", ",\n\\qquad")]) {
            const dom = rendered(`$$ ${formula} $$`);
            try {
                const doc = dom.window.document;
                assert.equal(doc.querySelector(".katex-error"), null);
                assert.equal(doc.querySelectorAll(".katex").length, 1);
                assert.equal(doc.querySelectorAll(".katex-display").length, 1);
                assert.equal(doc.querySelector("annotation")?.textContent, formula);
                assert.ok(doc.querySelector(".mfrac"));
            } finally { dom.window.close(); }
        }
    });

    it("renders legacy display delimiters, aligned equations, fractions, roots and macros as accessible KaTeX", () => {
        const dom = rendered("\\[\\boxed{\\frac{1}{2}+\\sqrt{x}}\\]\n\n\\begin{align}x &= y+1\\\\ z &= 2\\end{align}\n\nLet $x\\in\\R$.");
        try {
            const doc = dom.window.document;
            assert.equal(doc.querySelectorAll(".katex").length, 3);
            assert.equal(doc.querySelectorAll(".katex-display").length, 2);
            assert.equal(doc.querySelectorAll("math").length, 3);
            assert.ok(doc.querySelector(".mfrac"));
            assert.ok(doc.querySelector(".sqrt"));
            assert.ok(doc.querySelector(".fbox"));
            assert.equal(doc.querySelector(".katex-error"), null);
        } finally { dom.window.close(); }
    });

    it("keeps currency ranges and shell variables visible instead of turning them into formulas", () => {
        const source = "Prices $5-$10 and $5 to $10; use $HOME or $PATH.";
        const dom = rendered(source);
        try {
            assert.equal(dom.window.document.querySelector(".katex"), null);
            assert.equal(dom.window.document.querySelector("p")?.textContent, source);
        } finally { dom.window.close(); }
    });

    it("renders numeric-list math next to literal grouped dollar amounts", () => {
        const dom = rendered("The indices are $1,2,3,7,8,9,10$ and $0.1, 0.2, 0.3$. Prices are $1,000 and $2,500.50; range $1,000-$2,000.");
        try {
            const doc = dom.window.document;
            assert.deepEqual([...doc.querySelectorAll("annotation")].map((node) => node.textContent), ["1,2,3,7,8,9,10", "0.1, 0.2, 0.3"]);
            assert.match(doc.querySelector("p")?.textContent || "", /Prices are \$1,000 and \$2,500\.50; range \$1,000-\$2,000\./);
            assert.equal(doc.querySelector(".katex-error"), null);
        } finally { dom.window.close(); }
    });

    it("does not pair dangling currency dollars with inline code across a Markdown boundary", () => {
        const source = "A budget of $20 or $50 remains ordinary currency. Code such as `$x$` stays literal.";
        const dom = rendered(source);
        try {
            assert.equal(dom.window.document.querySelector(".katex"), null);
            assert.equal(dom.window.document.querySelector("code")?.textContent, "$x$");
            assert.equal(dom.window.document.querySelector("p")?.textContent, source.replace(/`/g, ""));
        } finally { dom.window.close(); }
    });

    it("keeps incomplete inline math literal and restores it when the next streaming chunk closes it", () => {
        const incomplete = rendered("Budget $20. Draft $x");
        const complete = rendered("Budget $20. Draft $x^2$ and literal `$y$`.");
        try {
            assert.equal(incomplete.window.document.querySelector(".katex"), null);
            assert.equal(incomplete.window.document.querySelector("p")?.textContent, "Budget $20. Draft $x");
            assert.equal(complete.window.document.querySelectorAll(".katex").length, 1);
            assert.equal(complete.window.document.querySelector("annotation")?.textContent, "x^2");
            assert.equal(complete.window.document.querySelector("code")?.textContent, "$y$");
        } finally { incomplete.window.close(); complete.window.close(); }
    });

    it("keeps malformed assistant math readable without throwing", () => {
        const dom = rendered("Bad $\\frac{1}$ then valid $x^2$.");
        try {
            assert.ok(dom.window.document.querySelector(".katex-error"));
            assert.ok(dom.window.document.querySelector(".katex"));
        } finally { dom.window.close(); }
    });

    it("uses the literal native response typography and accessible KaTeX markup", () => {
        const dom = rendered("\\[\\boxed{\\frac{1}{2}+\\sqrt{x}}\\]");
        try {
            const doc = dom.window.document;
            assert.ok(doc.querySelector(".text-ui-base.leading-\\[1\\.75\\].tracking-wide"));
            assert.ok(doc.querySelector(".katex .katex-mathml math"));
            assert.ok(doc.querySelector(".katex .katex-html[aria-hidden=\"true\"]"));
            const native = readFileSync(new URL("../zcode/styles.css", import.meta.url), "utf8");
            assert.ok(native.includes('@import "katex/dist/katex.min.css"'));
        } finally { dom.window.close(); }
    });
});
