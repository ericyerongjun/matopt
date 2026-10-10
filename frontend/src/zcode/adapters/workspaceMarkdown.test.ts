import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { prepareWorkspaceMarkdown } from "./workspaceMarkdown.ts";

describe("MatOpt workspace URLs for native ZCode Markdown", () => {
    it("maps workspace links and images to actual same-origin HTTP file URLs", () => {
        assert.equal(
            prepareWorkspaceMarkdown("[data](results/data.csv)\n![plot](figures/a.png)\n", "task-1", "http://localhost:5173"),
            "[data](http://localhost:5173/api/files/task-1/results/data.csv)\n![plot](http://localhost:5173/api/files/task-1/figures/a.png)\n",
        );
    });

    it("preserves external URLs and literal Markdown examples inside code", () => {
        const source = "[site](https://example.com)\n`[data](results/a.csv)`\n~~~python\nimage = '![p](p.png)'\n~~~\n[data](results/a.csv)";
        assert.equal(
            prepareWorkspaceMarkdown(source, "task-2", "https://matopt.test"),
            source.replace(/\[data\]\(results\/a.csv\)$/, "[data](https://matopt.test/api/files/task-2/results/a.csv)"),
        );
    });

    it("protects an unfinished code fence and keeps paths unchanged without a conversation", () => {
        const source = "```markdown\n[example](results/data.csv)";
        assert.equal(prepareWorkspaceMarkdown(source, "task-1", "https://matopt.test"), source);
        assert.equal(prepareWorkspaceMarkdown("[relative](a.csv)", null, "https://matopt.test"), "[relative](a.csv)");
    });

    it("converts existing files API sources without changing other root links", () => {
        assert.equal(
            prepareWorkspaceMarkdown("![a](/api/files/task-1/a.png) [home](/)", null, "https://matopt.test"),
            "![a](https://matopt.test/api/files/task-1/a.png) [home](/)",
        );
    });

    it("routes workspace scheme files and images to the actual files API without leaking the scheme into its path", () => {
        assert.equal(
            prepareWorkspaceMarkdown("[report](workspace://reports/result.md) ![plot](workspace://figures/a.png)", "task-3", "https://matopt.test"),
            "[report](https://matopt.test/api/files/task-3/reports/result.md) ![plot](https://matopt.test/api/files/task-3/figures/a.png)",
        );
        assert.equal(prepareWorkspaceMarkdown("`[report](workspace://reports/result.md)`", "task-3", "https://matopt.test"), "`[report](workspace://reports/result.md)`");
    });
});
