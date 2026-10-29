import assert from "node:assert/strict";
import { test } from "node:test";
import type { ActivityItem, AssistantContent, Part } from "../types/protocol.ts";
import type { UISurface } from "../types/ui.ts";
import { projectAssistantPresentation, referencedByAnswer } from "./presentation.ts";

function content(parts: Part[], extra: Partial<AssistantContent> = {}): AssistantContent {
    return { parts, model: "test", provider: "test", reasoning: "high", usage: {}, status: "complete", plan: [], stats: { llm_calls: 1, tool_calls: 0 }, ...extra };
}
const tool = (extra: Partial<Extract<Part, { type: "tool_call" }>> = {}): Extract<Part, { type: "tool_call" }> => ({
    type: "tool_call", id: "call", name: "python", arguments: { code: "PRIVATE_TOOL_CODE" }, status: "completed",
    result: { ok: true, output: "PRIVATE_STDOUT", error: null, artifacts: [], meta: {} }, ...extra,
});

test("legacy presentation selects the last revised answer and never carries internal text into its work summary", () => {
    const result = projectAssistantPresentation(content([
        { type: "reasoning", text: "PRIVATE_REASONING" }, { type: "text", text: "PRIVATE_TOOL_NARRATION" }, tool(),
        { type: "text", text: "Older full answer" }, { type: "phase", phase: "verify", round: 1 },
        { type: "text", phase: "verify", text: "PRIVATE_VERIFY_TEXT" },
        { type: "verification", verdict: "incorrect", report: "PRIVATE_REPORT", round: 1 },
        { type: "monitor", level: "warning", summary: "PRIVATE_AGENT_MESSAGE" },
        { type: "phase", phase: "revise", round: 1 }, { type: "text", phase: "revise", text: "Final revised answer" },
    ], { plan: [{ content: "PRIVATE_PLAN", status: "completed" }] }));
    assert.equal(result.answer, "Final revised answer");
    assert.deepEqual(result.earlierDrafts, []);
    assert.equal(result.work?.toolCount, 1);
    assert.ok(!JSON.stringify(result).includes("PRIVATE_"));
    assert.deepEqual(result.work?.steps.map((step) => step.label), ["Computing", "Checking answer", "Correcting answer"]);
});

test("legacy streaming and interrupted tool narration stay hidden until a final answer exists", () => {
    const parts: Part[] = [{ type: "text", text: "I will inspect the data" }, tool()];
    assert.equal(projectAssistantPresentation(content(parts)).answer, "");
    const complete = [...parts, { type: "text", text: "The result is 2." } as Part];
    assert.equal(projectAssistantPresentation(content(complete), true).answer, "");
    assert.equal(projectAssistantPresentation(content(complete)).answer, "The result is 2.");
    assert.equal(projectAssistantPresentation(content(complete, { status: "streaming" })).answer, "");
});

test("public final text streams while raw parts and unrecognized work labels remain hidden", () => {
    const result = projectAssistantPresentation(content([
        { type: "text", text: "The answer " }, { type: "text", text: "is 2." },
        { type: "reasoning", text: "PRIVATE_REASONING" }, { type: "text", phase: "verify", text: "PRIVATE_VERIFIER" }, tool(),
    ], {
        presentation: "final", status: "streaming",
        work: { status: "running", tool_count: 1, verification: { status: "correct", rounds: 1 }, steps: [
            { label: "Computing", status: "complete" }, { label: "PRIVATE_AGENT_PROMPT", status: "running" },
        ] },
    }), true);
    assert.equal(result.answer, "The answer \n\nis 2.");
    assert.equal(result.work?.steps[1].label, "Working");
    assert.ok(!JSON.stringify(result).includes("PRIVATE_"));
});

test("only committed surfaces and intended artifacts reach the final presentation", () => {
    const surface: UISurface = { version: 1, id: "s1", revision: 1, kind: "native", title: "Result", fallback: "Result fallback", status: "preview", nodes: [], state: {}, actions: [] };
    const artifact = { kind: "image", name: "plot.png", path: "plot.png" };
    const result = projectAssistantPresentation(content([
        { type: "ui", surface }, { type: "ui", surface: { ...surface, revision: 2, status: "committed" } },
        { type: "ui", surface: { ...surface, revision: 3, status: "committed" } },
        { type: "artifact", artifact }, { type: "artifact", artifact },
        tool({ result: { ok: true, output: "", error: null, artifacts: [{ kind: "image", name: "internal.png", path: "internal.png" }], meta: {} } }),
    ], { presentation: "final" }));
    assert.deepEqual(result.surfaces.map((item) => item.revision), [3]);
    assert.deepEqual(result.artifacts, [artifact]);
});

test("stored HOME caches and bytecode never surface as results", () => {
    const kept = { kind: "file", name: "result.txt", path: "result.txt" };
    const result = projectAssistantPresentation(content([
        { type: "artifact", artifact: { kind: "file", name: "os.cpython-39.pyc", path: "Library/Caches/com.apple.python/lib/os.cpython-39.pyc" } },
        { type: "artifact", artifact: { kind: "file", name: "mod.pyc", path: "pkg/mod.pyc" } },
        { type: "artifact", artifact: { kind: "file", name: "x.json", path: ".cache/x.json" } },
        { type: "artifact", artifact: kept },
    ], { presentation: "final" }));
    assert.deepEqual(result.artifacts, [kept]);
});

test("earlier drafts require explicit public approval and never replace the final answer", () => {
    const saved = content([{ type: "text", phase: "revise", text: "Q4 correction" }], { earlier_drafts: ["Full Q1–Q4 draft"] });
    assert.deepEqual(projectAssistantPresentation(saved).earlierDrafts, []);
    const projected = projectAssistantPresentation({ ...saved, presentation: "final" });
    assert.equal(projected.answer, "Q4 correction");
    assert.deepEqual(projected.earlierDrafts, ["Full Q1–Q4 draft"]);
});

test("terminal messages cannot retain stale running work or expose OCR scratch artifacts", () => {
    const result = projectAssistantPresentation(content([
        tool({ name: "parse_document", result: { ok: true, output: "", error: null, artifacts: [{ kind: "file", name: "internal-ocr.md", path: "internal-ocr.md" }], meta: {} } }),
        { type: "text", text: "Final answer" },
    ]));
    assert.equal(result.work?.steps[0].label, "Reading uploaded file");
    assert.deepEqual(result.artifacts, []);
    const stopped = projectAssistantPresentation(content([], {
        presentation: "final", status: "stopped",
        work: { status: "running", tool_count: 1, verification: { status: "not_requested", rounds: 0 }, steps: [{ label: "Computing", status: "running" }] },
    }));
    assert.equal(stopped.work?.status, "stopped");
    assert.equal(stopped.work?.steps[0].status, "error");
});

test("later output data replaces earlier copies of the same artifact", () => {
    const result = projectAssistantPresentation(content([
        { type: "artifact", artifact: { kind: "plotly", name: "plot", path: "plot.json", data: "old" } },
        { type: "artifact", artifact: { kind: "plotly", name: "plot", path: "plot.json", data: "new" } },
    ], { presentation: "final" }));
    assert.equal(result.artifacts.length, 1);
    assert.equal(result.artifacts[0].data, "new");
});

test("public activity keeps same-tool calls distinct and picks the latest sequence without carrying private metadata", () => {
    const item: ActivityItem = { id: "call-1", parent_id: "work", seq: 2, kind: "tool", phase: "work", label: "Computing", purpose: "Check the first calculation.", status: "running", started_at: 10, updated_at: 10, finished_at: null, attempt: 1, outputs: [] };
    const withPrivatePayload = { ...item, arguments: { secret: "PRIVATE_ARGUMENT" }, output: "PRIVATE_OUTPUT", reasoning: "PRIVATE_REASONING" };
    const projected = projectAssistantPresentation(content([], { status: "streaming", work: { status: "running", tool_count: 2, verification: { status: "not_requested", rounds: 0 }, steps: [], items: [withPrivatePayload, { ...item, id: "call-2", seq: 3 }, { ...item, seq: 4, status: "completed", finished_at: 12 }], current_progress: { id: "p1", seq: 5, text: "The first calculation is complete. The second is still running.", created_at: 12, phase: "work" } } }), true);
    assert.deepEqual(projected.work?.items.map((entry) => [entry.id, entry.status]), [["call-2", "running"], ["call-1", "completed"]]);
    assert.equal(projected.work?.progress?.text, "The first calculation is complete. The second is still running.");
    assert.ok(!JSON.stringify(projected).includes("PRIVATE_"));
});

test("progress uses only measured values and interrupted activities never keep a running indicator", () => {
    const item: ActivityItem = { id: "ocr", parent_id: null, seq: 1, kind: "tool", phase: "work", label: "Reading uploaded file", purpose: "Read the uploaded pages.", status: "running", started_at: 10, updated_at: 12, finished_at: null, attempt: 1, outputs: [], progress: { completed: 3, total: 2, unit: "pages" } };
    const projected = projectAssistantPresentation(content([], { status: "stopped", work: { status: "running", tool_count: 1, verification: { status: "not_requested", rounds: 0 }, steps: [], items: [item] } }));
    assert.equal(projected.work?.items[0].status, "cancelled");
    assert.deepEqual(projected.work?.items[0].progress, { completed: 3, unit: "pages" });
});

test("validated native previews can update during work but incomplete HTML previews stay hidden", () => {
    const native: UISurface = { version: 1, id: "native", revision: 1, kind: "native", title: "Calculation", fallback: "Calculating", status: "preview", nodes: [], state: {}, actions: [] };
    const html: UISurface = { ...native, id: "html", kind: "mini_app", app: { html: "<div>Partial" } };
    const projected = projectAssistantPresentation(content([{ type: "ui", surface: native }, { type: "ui", surface: html }, { type: "ui", surface: { ...native, revision: 2 } }], { status: "streaming", work: { status: "running", tool_count: 1, verification: { status: "not_requested", rounds: 0 }, steps: [], items: [] } }), true);
    assert.deepEqual(projected.surfaces.map((surface) => [surface.id, surface.revision]), [["native", 2]]);
    assert.equal(projected.answer, "");
});

test("an acknowledged safe-boundary pause removes running animation without completing the item", () => {
    const item: ActivityItem = { id: "final", parent_id: null, seq: 1, kind: "phase", phase: "final", label: "Preparing final answer", purpose: "Write the checked answer.", status: "running", started_at: 10, updated_at: 10, finished_at: null, attempt: 1, outputs: [] };
    const projected = projectAssistantPresentation(content([], { status: "streaming", work: { status: "paused", tool_count: 0, verification: { status: "correct", rounds: 1 }, steps: [], items: [item], current_progress: { id: "paused", seq: 2, text: "Paused at a safe boundary.", created_at: 15, phase: "final" } } }), true);
    assert.equal(projected.work?.items[0].status, "paused");
    assert.equal(projected.work?.items[0].updated_at, 15);
    assert.equal(projected.work?.items[0].finished_at, null);
});

test("public result blocks are independent of worker text, keep literal kind and settle by revision", () => {
    const block = { id: "result-1", revision: 1, kind: "markdown" as const, status: "provisional" as const, content: "**A useful partial result**", evidence_refs: ["result.csv"], created_at: 1 };
    const parts: Part[] = [{ type: "text", text: "PRIVATE_WORKER" }, { type: "reasoning", text: "PRIVATE_REASONING" },
        { type: "result_block", block: { ...block, private_trace: "PRIVATE_TRACE" } as typeof block }];
    const live = projectAssistantPresentation(content(parts, { status: "streaming" }), true);
    assert.equal(live.answer, "");
    assert.deepEqual(live.resultBlocks, [block]);
    assert.equal(JSON.stringify(live).includes("PRIVATE_"), false);
    const finalParts: Part[] = [{ type: "text", text: "Final answer" }, ...parts.slice(2),
        { type: "result_block", block: { ...block, revision: 2, status: "superseded" } },
        { type: "result_block", block: { ...block, id: "source", kind: "source", status: "accepted", content: "```markdown\n**literal**\n```" } }];
    const final = projectAssistantPresentation(content(finalParts, { presentation: "final" }));
    assert.equal(final.answer, "Final answer");
    assert.equal(final.earlierResults.length, 1);
    assert.equal(final.earlierResults[0].revision, 2);
    assert.equal(final.resultBlocks[0].kind, "source");
    const withdrawn = projectAssistantPresentation(content([...finalParts, { type: "result_block", block: { ...block, revision: 3, status: "withdrawn" } }], { presentation: "final" }));
    assert.equal(withdrawn.earlierResults.length, 0);
    assert.equal(withdrawn.resultBlocks.some((entry) => entry.id === block.id), false);
});

test("stopped partial results remain visibly provisional and malformed saved blocks cannot enter presentation", () => {
    const block = { id: "partial", revision: 1, kind: "markdown" as const, status: "provisional" as const, content: "Known result", evidence_refs: [], created_at: 1 };
    for (const status of ["stopped", "error"] as const) {
        const view = projectAssistantPresentation(content([{ type: "result_block", block }], { status }));
        assert.equal(view.resultBlocks[0].status, "provisional");
        assert.equal(view.earlierResults.length, 0);
    }
    const malformed = { ...block, kind: "reasoning", content: "PRIVATE_INVALID" } as unknown as typeof block;
    assert.equal(projectAssistantPresentation(content([{ type: "result_block", block: malformed }])).resultBlocks.length, 0);
});

test("only files the answer points to are delivered, and an embedded figure is not repeated", () => {
    const answer = "![Surface](paraboloid.png)\n\nThe report is [here](outputs/report.pdf).";
    assert.equal(referencedByAnswer({ kind: "image", name: "fig_001.png", path: "figures/fig_001.png" } as never, answer), false);
    assert.equal(referencedByAnswer({ kind: "image", name: "paraboloid.png", path: "paraboloid.png" } as never, answer), false);
    assert.equal(referencedByAnswer({ kind: "file", name: "report.pdf", path: "outputs/report.pdf" } as never, answer), true);
    assert.equal(referencedByAnswer({ kind: "image", name: "a.png", path: "figures/a.png" } as never, "Saved to `figures/a.png`."), true);
    assert.equal(referencedByAnswer({ kind: "html", name: "table" } as never, answer), false);
});
