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

    it("renders a standalone one-line double-dollar expression as display math", () => {
        const source = "Before\n\n$$\\int_0^1 x^2\\,dx=\\frac{1}{3}$$\n\nAfter";
        const normalized = "Before\n\n$$\n\\int_0^1 x^2\\,dx=\\frac{1}{3}\n$$\n\nAfter";
        assert.equal(preprocessLaTeX(source), normalized);
        assert.equal(preprocessLaTeX(normalized), normalized);
        const literal = "```markdown\n$$x^2$$\n```";
        assert.equal(preprocessLaTeX(literal), literal);
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

    it("preserves inline code, tilde fences, longer fences and unfinished streamed code", () => {
        for (const src of [
            "Write `\\(x\\)` and ``$ x $`` literally.",
            "~~~shell\nprice='$5-$10'\n\\(code\\)\n~~~\n",
            "````markdown\n```latex\n\\frac{1}{2}\n```\n````\n",
            "```python\nprint('\\\\(x\\\\)')\n$ x $",
        ]) assert.equal(preprocessLaTeX(src), src);
    });

    it("imports ZCode's dollar guards for prices and environment variables without hiding math", () => {
        assert.equal(preprocessLaTeX("Prices $5-$10; variables $HOME and $PATH."), "Prices \\$5-\\$10; variables \\$HOME and \\$PATH.");
        assert.equal(preprocessLaTeX("Prices $5 to $10."), "Prices \\$5 to \\$10.");
        assert.equal(preprocessLaTeX("Use $x$, $x+y$, and $\\frac{1}{2}$."), "Use $x$, $x+y$, and $\\frac{1}{2}$.");
        assert.equal(preprocessLaTeX("Literal \\$5 then $x$."), "Literal \\$5 then $x$.");
    });

    it("escapes dangling prose dollars at code boundaries and reevaluates unfinished inline math", () => {
        assert.equal(preprocessLaTeX("Budget $20 or $50. Code `$x$`."), "Budget \\$20 or \\$50. Code `$x$`.");
        assert.equal(preprocessLaTeX("Draft $x"), "Draft \\$x");
        assert.equal(preprocessLaTeX("Draft $x^2$"), "Draft $x^2$");
        assert.equal(preprocessLaTeX("$$\n\\frac{1}{2}"), "$$\n\\frac{1}{2}");
    });

    it("recognizes delimited numeric lists without reclassifying grouped currency", () => {
        assert.equal(preprocessLaTeX("Indices $1,2,3,7,8,9,10$ and $0.1, 0.2, 0.3$."), "Indices $1,2,3,7,8,9,10$ and $0.1, 0.2, 0.3$.");
        assert.equal(preprocessLaTeX("Prices $1,000 and $2,500.50; range $1,000-$2,000."), "Prices \\$1,000 and \\$2,500.50; range \\$1,000-\\$2,000.");
        assert.equal(preprocessLaTeX("Literal `$1,2,3$` and ```code``` stay source."), "Literal `$1,2,3$` and ```code``` stay source.");
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
