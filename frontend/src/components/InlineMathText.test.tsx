import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import { InlineMathText, splitInlineMath } from "./InlineMathText.tsx";

describe("InlineMathText", () => {
    it("splits dollar and paren delimited math from plain text", () => {
        assert.deepEqual(splitInlineMath("Why is $\\kappa_2$ large near \\(x=1\\)?"), [
            { kind: "text", value: "Why is " }, { kind: "math", value: "\\kappa_2" },
            { kind: "text", value: " large near " }, { kind: "math", value: "x=1" }, { kind: "text", value: "?" },
        ]);
        assert.deepEqual(splitInlineMath("Costs $5 and $"), [{ kind: "text", value: "Costs $5 and $" }]);
    });

    it("renders math with KaTeX and escapes ordinary text", () => {
        const html = renderToStaticMarkup(<InlineMathText text={"Eigenvalues of $H(1,1)$ <b>now</b>"} />);
        assert.match(html, /class="katex"/);
        assert.doesNotMatch(html, /\$H\(1,1\)\$/);
        assert.match(html, /&lt;b&gt;now&lt;\/b&gt;/);
    });
});
