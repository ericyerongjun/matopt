import assert from "node:assert/strict";
import { describe, it } from "node:test";
import type { Artifact, AssistantContent, ChatMessage, Part } from "../types/protocol";
import { selectOutputArtifacts } from "./outputArtifacts.ts";

function assistant(parts: Part[], id = "a1"): ChatMessage {
    const content: AssistantContent = {
        parts,
        model: "test",
        provider: "test",
        reasoning: "",
        usage: {},
        status: "complete",
        plan: [],
        stats: { llm_calls: 1, tool_calls: parts.filter((p) => p.type === "tool_call").length },
    };
    return {
        id,
        conversation_id: "c1",
        seq: 1,
        role: "assistant",
        content,
        created_at: 0,
    };
}

function tool(
    artifacts: Artifact[],
    extra: Partial<Extract<Part, { type: "tool_call" }>> = {},
): Extract<Part, { type: "tool_call" }> {
    return {
        type: "tool_call",
        id: extra.id ?? "t1",
        name: extra.name ?? "python",
        arguments: {},
        status: extra.status ?? "completed",
        phase: extra.phase,
        result: {
            ok: true,
            output: "ok",
            artifacts,
            error: null,
            meta: {},
        },
    };
}

function image(name: string, extras: Partial<Artifact> = {}): Artifact {
    return { kind: "image", name, path: extras.path ?? `figures/${name}`, digest: extras.digest, ...extras };
}

describe("selectOutputArtifacts", () => {
    it("keeps figures from the latest assistant turn only", () => {
        const older = assistant([tool([image("fig_001.png", { digest: "aaa" })])], "old");
        const newer = assistant([tool([image("fig_002.png", { digest: "bbb" })])], "new");
        const picked = selectOutputArtifacts([older, newer]);
        assert.equal(picked.length, 1);
        assert.equal(picked[0].name, "fig_002.png");
    });

    it("collapses identical digest and path, keeping the last write", () => {
        const parts = [
            tool([image("fig_001.png", { digest: "same", path: "figures/fig_001.png" })], { id: "t1" }),
            tool(
                [
                    image("fig_001.png", { digest: "same", path: "figures/fig_001.png" }),
                    image("fig_002.png", { digest: "other", path: "figures/fig_002.png" }),
                ],
                { id: "t2" },
            ),
        ];
        const picked = selectOutputArtifacts([assistant(parts)]);
        assert.equal(picked.length, 2);
        assert.deepEqual(
            picked.map((a) => a.path),
            ["figures/fig_001.png", "figures/fig_002.png"],
        );
    });

    it("drops verify/revise recaptures so the checker cannot spam Output", () => {
        const parts = [
            tool([image("fig_001.png", { digest: "aaa" })], { id: "ans", phase: "answer" }),
            tool([image("fig_001b.png", { digest: "zzz" })], { id: "ver", phase: "verify" }),
            tool([image("fig_001c.png", { digest: "yyy" })], { id: "rev", phase: "revise" }),
        ];
        const picked = selectOutputArtifacts([assistant(parts)]);
        assert.equal(picked.length, 1);
        assert.equal(picked[0].name, "fig_001.png");
    });

    it("keeps distinct inline HTML tables that have no path or digest", () => {
        const tables: Artifact[] = [
            { kind: "html", name: "table", data: "<table>a</table>" },
            { kind: "html", name: "table", data: "<table>b</table>" },
        ];
        const picked = selectOutputArtifacts([assistant([tool(tables)])]);
        assert.equal(picked.length, 2);
    });
});
