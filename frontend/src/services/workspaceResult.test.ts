import assert from "node:assert/strict";
import { test } from "node:test";
import type { ChatMessage, Part } from "../types/protocol.ts";
import { latestResultText } from "./workspaceResult.ts";

const assistant = (parts: Part[], id = "assistant"): ChatMessage => ({
    id, conversation_id: "task", seq: 1, role: "assistant", created_at: 1,
    content: { parts, model: "test", provider: "test", reasoning: "off", usage: {}, status: "complete", plan: [], stats: { llm_calls: 1, tool_calls: 0 } },
});
const tool: Part = { type: "tool_call", id: "call", name: "python", arguments: {}, result: null, status: "completed", phase: "answer" };

test("latest result selects the final text after tools and excludes its preface", () => {
    const messages = [assistant([{ type: "text", text: "I will compute the integral.", phase: "answer" }, tool,
        { type: "text", text: "The integral is 42.", phase: "answer" }])];
    assert.equal(latestResultText(messages), "The integral is 42.");
});

test("verification text and reports cannot replace the answer", () => {
    const messages = [assistant([{ type: "text", text: "The answer is 42.", phase: "answer" },
        { type: "phase", phase: "verify", round: 1 }, { type: "text", text: "<verdict>correct</verdict>", phase: "verify" },
        { type: "verification", verdict: "correct", report: "The computation is consistent.", round: 1 }])];
    assert.equal(latestResultText(messages), "The answer is 42.");
    assert.equal(latestResultText([assistant([{ type: "text", text: "Verifier only", phase: "verify" }])]), "");
});

test("the latest revision round supplies the result instead of an earlier answer or draft", () => {
    const messages = [assistant([{ type: "text", text: "Initial answer", phase: "answer" },
        { type: "phase", phase: "revise", round: 1 }, { type: "text", text: "First revision", phase: "revise" },
        { type: "phase", phase: "verify", round: 2 }, { type: "text", text: "Still incorrect", phase: "verify" },
        { type: "phase", phase: "revise", round: 2 }, { type: "text", text: "I will fix the sign.", phase: "revise" }, tool,
        { type: "text", text: "Corrected final answer", phase: "revise" }, { type: "text", text: "  \n ", phase: "revise" }])];
    assert.equal(latestResultText(messages), "Corrected final answer");
});

test("an unanswered latest assistant message never falls back to an old result", () => {
    const old = assistant([{ type: "text", text: "Old result" }], "old");
    assert.equal(latestResultText([old, assistant([tool], "new")]), "");
    assert.equal(latestResultText([old, assistant([], "new")]), "");
    assert.equal(latestResultText([]), "");
});

test("legacy text without a phase remains eligible and preserves Markdown whitespace", () => {
    const text = "    code block\n\n";
    assert.equal(latestResultText([assistant([{ type: "text", text }])]), text);
});

test("an interactive-only answer copies its accessible text summary", () => {
    const surface = { version: 1 as const, id: "example", revision: 1, kind: "native" as const, title: "Result", fallback: "The mean is 2.", status: "committed" as const, nodes: [], state: {}, actions: [] };
    assert.equal(latestResultText([assistant([{ type: "ui", surface }])]), "The mean is 2.");
});
