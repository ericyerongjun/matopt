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

    it("wraps bare T=500 and 1/2 as inline math, leaves fences alone", () => {
        const src = "At T=500 and T=2000, score is 1/2 draw.\n```python\nx=500\n```\n";
        const out = normalizeAgentMarkdown(src);
        assert.ok(out.includes("$T=500$"));
        assert.ok(out.includes("$T=2000$"));
        assert.ok(out.includes("$1/2$"));
        assert.ok(out.includes("x=500"));
    });
});
