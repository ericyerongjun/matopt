import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { normalizeAgentMarkdown } from "./markdownNormalize.ts";

describe("normalizeAgentMarkdown", () => {
    it("lifts a pipe table out of a numbered list so GFM can parse it", () => {
        const src = [
            "2. **Strength** depends on budget.",
            "   | opponent | 10 | 50 |",
            "   |---|---|---|",
            "   | random | 0.885 | 0.990 |",
            "   | perfect | 0.492 | 0.500 |",
            "Then a conclusion.",
        ].join("\n");
        const out = normalizeAgentMarkdown(src);
        assert.ok(out.includes("\n| opponent | 10 | 50 |\n"));
        assert.ok(out.includes("\n|---|---|---|\n"));
        assert.ok(!out.includes("   | opponent"));
    });

    it("joins consecutive figure lines into one paragraph", () => {
        const src = "See:\n![a](figures/a.png)\n\n![b](figures/b.png)\nDone.";
        const out = normalizeAgentMarkdown(src);
        assert.ok(out.includes("![a](figures/a.png) ![b](figures/b.png)"));
    });

    it("preserves the boundary between an output image, an ASCII diagram and rendered results", () => {
        const source = [
            "![Decision tree](figures/tree.png)", "", "```", "A", "├── F → −", "└── T → +", "```", "",
            "| Index | Prediction | True | Correct? |", "|:---:|:---:|:---:|:---:|", "| 13 | − | − | ✓ |", "",
            String.raw`Validation accuracy $=4/6=\boxed{0.6667}$.`, "", "**Q5 — Naive Bayes**", "",
        ].join("\n");
        assert.equal(normalizeAgentMarkdown(source), source);
        assert.equal(normalizeAgentMarkdown(normalizeAgentMarkdown(source)), source);
    });

    it("groups images without consuming trailing fence boundaries, including unfinished streams", () => {
        for (const fence of ["```", "~~~text", "````python"]) {
            for (const gap of ["\n", "\n\n", "\n\n\n"]) {
                const source = `![first](a.png)\n\n![second](b.png)${gap}${fence}\nLiteral $x$ and **source**`;
                assert.equal(normalizeAgentMarkdown(source), `![first](a.png) ![second](b.png)${gap}${fence}\nLiteral $x$ and **source**`);
            }
        }
    });

    it("leaves explicitly fenced Markdown source and its internal image boundaries literal", () => {
        const source = ["````markdown", "# Copy this source", "![figure](figure.png)", "", "```text", "A → B", "```", "",
            "| x | y |", "|---|---|", "| 1 | 2 |", "", "$x^2$ and **bold**", "````", ""].join("\n");
        assert.equal(normalizeAgentMarkdown(source), source);
    });

    it("wraps bare T=500 and 1/2 as inline math, leaves fences alone", () => {
        const src = "At T=500 and T=2000, score is 1/2 draw.\n```python\nx=500\n```\n";
        const out = normalizeAgentMarkdown(src);
        assert.ok(out.includes("$T=500$"));
        assert.ok(out.includes("$T=2000$"));
        assert.ok(out.includes("$1/2$"));
        assert.ok(out.includes("x=500"));
    });

    it("preserves complete and unfinished display math, including spaced delimiters", () => {
        const formula = String.raw`$$ \hat a=\frac{\sum_i(x_i-\bar x)(y_i-\bar y)}{\sum_i(x_i-\bar x)^2}=2,\qquad \hat b=\bar y-\hat a\bar x=1. $$`;
        for (const source of [formula, formula.replace(",\\qquad", ",\n\\qquad"), String.raw`$$ \bar x=1`, String.raw`\[ \bar x=1. \]`, String.raw`\begin{equation}x=1\end{equation}`, "$ x=1 $"]) {
            assert.equal(normalizeAgentMarkdown(source), source);
        }
    });

    it("keeps inline code and tilde, longer and unfinished fences literal", () => {
        for (const source of [
            "Use `x=1` and ``1/2`` as code.",
            "~~~python\nx=1\n1/2\n~~~\n",
            "````markdown\n```python\nx=1\n```\n1/2\n````\n",
            "```python\nx=1\n1/2",
            "~~~python\nx=1\n1/2",
        ]) assert.equal(normalizeAgentMarkdown(source), source);
    });
});
