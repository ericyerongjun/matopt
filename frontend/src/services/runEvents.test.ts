import assert from "node:assert/strict";
import { test } from "node:test";
import { applyEvent, assistantFromStream, emptyStreamState, readApprovalRequest, readSubagent, readWorkspaceChanges } from "./stream.ts";
import { splitUnifiedDiff } from "./changes.ts";
import { shellSessionBadge } from "../components/transcriptBadges.ts";
import { projectAssistantPresentation } from "./presentation.ts";

const run = (events: Array<Record<string, unknown>>) => events.reduce((state, event, index) =>
    applyEvent(state, { run_id: "run-1", seq: index + 1, ...event, type: String(event.type) }), emptyStreamState());

test("subagent events keep one public row per agent and retain the final summary", () => {
    const state = run([
        { type: "subagent", id: "a1", path: "root/a1", nickname: "Ada", role: "researcher", kind: "started", status: "running", task: "Find sources" },
        { type: "subagent", id: "a2", path: "root/a2", nickname: "Bo", role: "coder", kind: "started", status: "running", task: "Fit model" },
        { type: "subagent", id: "a1", path: "root/a1", nickname: "Ada", role: "researcher", kind: "completed", status: "completed", task: "Find sources", summary: "Three sources agree." },
        { type: "subagent", id: "a1", path: "root/a1", nickname: "Ada", role: "researcher", kind: "closed", status: "closed", task: "Find sources" },
        { type: "subagent", kind: "started" },
        { type: "subagent", id: "bad", kind: "messaged" },
    ]);
    assert.deepEqual(state.subagents?.map((agent) => [agent.id, agent.kind, agent.summary]), [["a1", "closed", "Three sources agree."], ["a2", "started", undefined]]);
    assert.equal(readSubagent({ id: "x", kind: "started", mail: "private" })?.task, "");
    assert.equal("mail" in (readSubagent({ id: "x", kind: "started", mail: "private" }) ?? {}), false);
});

test("approval requests queue until resolved and a late duplicate does not reopen a decided card", () => {
    let state = run([
        { type: "approval_request", id: "ap1", tool: "bash", title: "Run rm -rf build", summary: "Deletes build", reason: "Writes outside sandbox", risk: "high" },
        { type: "approval_request", id: "ap2", tool: "write_file", title: "Write config", summary: "", reason: "", risk: "bogus" },
    ]);
    assert.deepEqual(state.approvals?.map((approval) => [approval.id, approval.risk]), [["ap1", "high"], ["ap2", "medium"]]);
    state = applyEvent(state, { type: "approval_resolved", run_id: "run-1", seq: 3, id: "ap1", decision: "allow", by: "user" });
    assert.deepEqual(state.approvals?.map((approval) => approval.id), ["ap2"]);
    state = applyEvent(state, { type: "approval_request", run_id: "run-1", seq: 4, id: "ap1", tool: "bash", title: "Again", summary: "", reason: "", risk: "low" });
    assert.deepEqual(state.approvals?.map((approval) => approval.id), ["ap2"]);
    assert.equal(applyEvent(state, { type: "approval_resolved", run_id: "run-1", seq: 5, id: "ap2", decision: "maybe", by: "user" }).approvals?.length, 1);
    state = applyEvent(state, { type: "done", run_id: "run-1", seq: 6 });
    assert.deepEqual(state.approvals, []);
    assert.equal(readApprovalRequest({ tool: "bash" }), undefined);
});

test("guardian reviews and workspace changes reach the assistant content with the turn", () => {
    const state = run([
        { type: "message_start", message_id: "m1" },
        { type: "guardian_review", call_id: "c1", tool: "bash", outcome: "deny", risk_level: "high", user_authorization: "none", rationale: "Deletes data." },
        { type: "guardian_review", call_id: "c2", outcome: "maybe" },
        { type: "workspace_changes", turn: 4, files: [{ path: "a.py", status: "M", added: 3, removed: 1 }, { path: "img.png", status: "A", binary: true }, { path: "x", status: "R" }, { status: "A" }] },
        { type: "message_end", message_id: "m1", content: { parts: [], status: "complete", turn: 4, model: "", provider: "", reasoning: "", usage: {}, plan: [], stats: { llm_calls: 0, tool_calls: 0 } } },
    ]);
    const content = assistantFromStream(state);
    assert.equal(content.turn, 4);
    assert.deepEqual(content.changes, { turn: 4, files: [{ path: "a.py", status: "M", added: 3, removed: 1 }, { path: "img.png", status: "A", binary: true }] });
    assert.deepEqual(content.guardian_reviews?.map((review) => [review.call_id, review.outcome]), [["c1", "deny"]]);
    assert.equal(readWorkspaceChanges({ turn: "4", files: [] }), undefined);
    const done = applyEvent(emptyStreamState(), { type: "done", turn: 7 });
    assert.equal(done.turn, 7);
});

test("message_start clears the previous turn's changes and reviews", () => {
    const state = run([
        { type: "workspace_changes", turn: 1, files: [{ path: "a", status: "A" }] },
        { type: "guardian_review", call_id: "c1", outcome: "allow" },
        { type: "message_start", message_id: "m2", turn: 2 },
    ]);
    assert.equal(state.changes, undefined); assert.equal(state.guardianReviews, undefined); assert.equal(state.turn, 2);
});

test("unified diffs split per file, including new, deleted and renamed files", () => {
    const diff = [
        "diff --git a/src/a.py b/src/a.py", "index 1..2 100644", "--- a/src/a.py", "+++ b/src/a.py", "@@ -1 +1 @@", "-x = 1", "+x = 2",
        "diff --git a/new.txt b/new.txt", "new file mode 100644", "--- /dev/null", "+++ b/new.txt", "@@ -0,0 +1 @@", "+hello",
        "diff --git a/old.txt b/old.txt", "deleted file mode 100644", "--- a/old.txt", "+++ /dev/null", "@@ -1 +0,0 @@", "-bye",
        "diff --git a/img.png b/img.png", "Binary files a/img.png and b/img.png differ", "",
    ].join("\n");
    const patches = splitUnifiedDiff(diff);
    assert.deepEqual([...patches.keys()], ["src/a.py", "new.txt", "old.txt", "img.png"]);
    assert.match(patches.get("src/a.py")!, /\+x = 2$/);
    assert.match(patches.get("old.txt")!, /-bye$/);
    assert.deepEqual([...splitUnifiedDiff("--- a/q\n+++ b/q\n@@ -1 +1 @@\n-a\n+b").keys()], ["q"]);
    assert.equal(splitUnifiedDiff("").size, 0);
});

test("background shell badges read only the exec header", () => {
    assert.deepEqual(shellSessionBadge({ tool: "bash", output: "Process running with session ID 7\nWall time: 1.0 seconds\nOutput:\nserving" }), { label: "Background · running", tone: "active" });
    assert.deepEqual(shellSessionBadge({ tool: "bash", output: "Process running with session ID 7\nOutput:\n" }, false), { label: "Background", tone: "neutral" });
    assert.deepEqual(shellSessionBadge({ tool: "bash_poll", output: "Exit code: 2\nWall time: 3.0 seconds\nOutput:\nfail" }), { label: "exit 2", tone: "danger" });
    assert.equal(shellSessionBadge({ tool: "bash", output: "Exit code: 0\nOutput:\nProcess running with session ID 9" }), null);
    assert.equal(shellSessionBadge({ tool: "python", output: "Process running with session ID 1" }), null);
    assert.equal(shellSessionBadge({ tool: "bash" }), null);
});

test("presentation keeps validated guardian reviews for the transcript", () => {
    const presented = projectAssistantPresentation({ parts: [], model: "", provider: "", reasoning: "", usage: {}, status: "complete", plan: [], stats: { llm_calls: 0, tool_calls: 0 },
        work: { status: "complete", tool_count: 1, verification: { status: "not_requested", rounds: 0 }, steps: [] },
        guardian_reviews: [{ call_id: "c1", tool: "bash", outcome: "allow", risk_level: "low", user_authorization: "high", rationale: "Read-only." }, { call_id: "", outcome: "allow" } as never] }, false);
    assert.deepEqual(presented.work?.reviews?.map((review) => review.call_id), ["c1"]);
});

test("mode events reach the answer, a switch records the previous mode, and saved content.mode is read", async () => {
    const { readMode } = await import("./stream.ts");
    let state = run([
        { type: "message_start", message_id: "m1" },
        { type: "mode", mode: "mat", source: "auto", reason: "Explanation request" },
        { type: "mode", mode: "opt", source: "auto", reason: "Needs to edit files", switched: true },
        { type: "mode", mode: "chat" },
    ]);
    assert.deepEqual(assistantFromStream(state).mode, { mode: "opt", source: "auto", reason: "Needs to edit files", switched: true, from: "mat" });
    state = applyEvent(state, { type: "message_start", run_id: "run-1", seq: 10, message_id: "m2" });
    assert.equal(state.mode, undefined);
    const saved = applyEvent(emptyStreamState(), { type: "message_end", content: { parts: [], status: "complete", mode: { mode: "mat", source: "user", reason: "" } } });
    assert.deepEqual(saved.mode, { mode: "mat", source: "user", reason: "" });
    assert.equal(readMode({ mode: "auto" }), undefined);
});
