import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { containsMath, preprocessLaTeX } from "./formulaHelpers.ts";

describe("preprocessLaTeX", () => {
    it("leaves same-line $$...$$ intact (the previous blank-line rewriter split these)", () => {
        const src = "The identity is $$E = mc^2$$ in special relativity.";
        assert.equal(preprocessLaTeX(src), src);
    });

    it("leaves multiline $$ blocks intact", () => {
        const src = "See\n$$\n\\int_0^1 x^2\\,dx\n$$\nfor the area.";
        assert.equal(preprocessLaTeX(src), src);
    });

    it("converts \\[ \\] display delimiters to a $$ fence", () => {
        const out = preprocessLaTeX("A display \\[E = mc^2\\] equation.");
        assert.equal(out, "A display \n$$\nE = mc^2\n$$\n equation.");
    });

    it("converts \\( \\) inline delimiters to $", () => {
        const out = preprocessLaTeX("Inline \\(x^2 + y^2\\) math.");
        assert.equal(out, "Inline $x^2 + y^2$ math.");
    });

    it("trims spaces inside single-dollar delimiters", () => {
        const out = preprocessLaTeX("Let $ x + y $ be a sum.");
        assert.equal(out, "Let $x + y$ be a sum.");
    });

    it("does not treat code-fence dollars as math", () => {
        const src = "Use `$x$` in prose but not here:\n```python\nprice = '$5'\n```\n";
        const out = preprocessLaTeX(src);
        assert.ok(out.includes("price = '$5'"));
        assert.equal(out, src);
    });

    it("promotes ```latex fences to display math", () => {
        const src = "```latex\n\\frac{1}{2}\n```";
        const out = preprocessLaTeX(src);
        assert.equal(out.trim(), "$$\n\\frac{1}{2}\n$$");
    });

    it("rewrites align environments to aligned and wraps them", () => {
        const src = "\\begin{align}\na &= b \\\\\nc &= d\n\\end{align}";
        const out = preprocessLaTeX(src);
        assert.ok(out.includes("\\begin{aligned}"));
        assert.match(out, /^\n\$\$\n\\begin\{aligned\}/);
        assert.match(out, /\\end\{aligned\}\n\$\$\n$/);
        assert.ok(!out.includes("\\begin{align}"));
    });

    it("turns same-line $$ with internal newlines into a $$ fence", () => {
        const src = "$$\\begin{aligned}\na &= b\n\\end{aligned}$$";
        const out = preprocessLaTeX(src);
        assert.equal(out, "\n$$\n\\begin{aligned}\na &= b\n\\end{aligned}\n$$\n");
    });

    it("wraps a bare \\boxed answer", () => {
        const out = preprocessLaTeX("Therefore \\boxed{\\dfrac{1}{2}}.");
        assert.equal(out, "Therefore $$\\boxed{\\dfrac{1}{2}}$$.");
    });

    it("leaves \\boxed already inside math alone", () => {
        const src = "Answer $x=\\boxed{1}$ done.";
        assert.equal(preprocessLaTeX(src), src);
    });
});

describe("containsMath", () => {
    it("detects dollar and begin-align forms", () => {
        assert.equal(containsMath("nope"), false);
        assert.equal(containsMath("see $x$"), true);
        assert.equal(containsMath("\\begin{align} a \\end{align}"), true);
    });
});
