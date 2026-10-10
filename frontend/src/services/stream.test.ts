import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { applyEvent, assistantFromStream, emptyStreamState, streamChat } from "./stream.ts";
import { observeRun } from "./runs.ts";
import type { UISurface } from "../types/ui.ts";
import type { ResultBlock } from "../types/protocol.ts";

const surface = (changes: Partial<UISurface> = {}): UISurface => ({
    version: 1, id: "taylor", revision: 1, kind: "native", title: "Taylor approximation",
    fallback: "The cubic approximation to sin(x) is x − x³/6.", status: "preview",
    nodes: [{ id: "formula", type: "equation", props: { latex: "x-x^3/6" } }], state: { degree: 3 }, actions: [], ...changes,
});
const originalFetch = globalThis.fetch;
const resultBlock = (changes: Partial<ResultBlock> = {}): ResultBlock => ({ id: "answer:result:table", revision: 1, kind: "markdown", status: "provisional", content: "| Input | Value |\n| --- | --- |\n| x | 2 |", evidence_refs: ["result.csv"], created_at: 1, ...changes });
afterEach(() => { globalThis.fetch = originalFetch; });

test("document-reading progress clears when the assistant resumes reasoning or answering", () => {
    const reading = applyEvent(emptyStreamState(), { type: "status", message: "Reading scan.pdf · page 1/1" });
    assert.equal(applyEvent(reading, { type: "reasoning_delta", delta: "" }).statusLine, reading.statusLine);
    assert.equal(applyEvent(reading, { type: "reasoning_delta", delta: "The page says" }).statusLine, null);
    assert.deepEqual(applyEvent(reading, { type: "reasoning_delta", delta: "Private reasoning" }).parts, []);
    assert.equal(applyEvent(reading, { type: "text_delta", delta: "The equation is" }).statusLine, null);
});

test("public progress rejects arbitrary labels and final publication replaces internal drafts", () => {
    let state = applyEvent(emptyStreamState(), { type: "text_delta", delta: "Private working draft" });
    state = applyEvent(state, { type: "work", work: { status: "running", tool_count: 2,
        verification: { status: "correct", rounds: 1 }, steps: [{ label: "Computing", status: "complete" }, { label: "secret agent message", status: "running" }] } });
    assert.deepEqual(state.work?.steps.map((step) => step.label), ["Computing", "Working"]);
    state = applyEvent(state, { type: "final_start", message_id: "final-1" });
    assert.equal(assistantFromStream(state).presentation, "final");
    assert.equal(state.parts.length, 0);
    state = applyEvent(state, { type: "text_delta", delta: "The final " });
    state = applyEvent(state, { type: "text_delta", delta: "answer." });
    state = applyEvent(state, { type: "artifact", artifact: { kind: "file", name: "result.csv", path: "result.csv" } });
    assert.equal(state.parts.length, 2);
    state = applyEvent(state, { type: "message_end", message_id: "final-1", content: {
        ...assistantFromStream(state), parts: [{ type: "text", text: "The final answer." }, { type: "reasoning", text: "Do not expose" }],
        notice: "Check could not confirm this result.", status: "complete",
    } });
    state = applyEvent(state, { type: "done" });
    assert.deepEqual(state.parts, [{ type: "text", text: "The final answer." }]);
    assert.equal(state.work?.status, "complete");
    assert.equal(assistantFromStream(state).notice, "Check could not confirm this result.");
});

test("legacy tool and verifier events never retain internal payloads and a stopped turn cannot become complete", () => {
    let state = applyEvent(emptyStreamState(), { type: "tool_call_start", id: "t1", name: "python" });
    for (const type of ["tool_call_delta", "tool_call_ready", "tool_progress", "reasoning_delta", "monitor"]) {
        state = applyEvent(state, { type, id: "t1", delta: "private", arguments: { secret: "private" }, summary: "private" });
    }
    state = applyEvent(state, { type: "tool_result", id: "t1", output: "private", error: "private", meta: { secret: "private" } });
    state = applyEvent(state, { type: "verification", verdict: "uncertain", report: "private" });
    assert.equal(JSON.stringify(state).includes("private"), false);
    state = applyEvent(state, { type: "stopped" });
    assert.equal(applyEvent(state, { type: "done" }).status, "stopped");
});

test("streamed surfaces keep their ordered slot, reject stale revisions, and commit actions only once", () => {
    let state = applyEvent(emptyStreamState(), { type: "text_delta", delta: "Explore this approximation." });
    state = applyEvent(state, { type: "ui_start", surface: surface() });
    state = applyEvent(state, { type: "text_delta", delta: "The error grows away from zero." });
    state = applyEvent(state, { type: "ui_patch", surface: surface({ revision: 2 }) });
    state = applyEvent(state, { type: "ui_commit", surface: surface({ revision: 2, status: "committed" }) });
    const committed = state;
    assert.deepEqual(state.parts.map((part) => part.type), ["text", "ui", "text"]);
    assert.equal(applyEvent(state, { type: "ui_patch", surface: surface({ revision: 1 }) }), committed);
    assert.equal(applyEvent(state, { type: "ui_patch", surface: surface({ revision: 2 }) }), committed);
    state = applyEvent(state, { type: "ui_error", surface_id: "taylor", revision: 2, error: "interrupted" });
    assert.deepEqual(state.parts, committed.parts);
});

test("stopping drops unfinished controls but retains committed results and fallback explanations", () => {
    let state = applyEvent(emptyStreamState(), { type: "ui_commit", surface: surface({ status: "committed" }) });
    state = applyEvent(state, { type: "ui_start", surface: surface({ id: "unfinished" }) });
    state = applyEvent(state, { type: "stopped" });
    assert.equal(state.status, "stopped");
    assert.ok(state.parts[0].type === "ui" && state.parts[0].surface.status === "committed");
    assert.ok(state.parts[1].type === "ui" && state.parts[1].surface.status === "error");
    if (state.parts[1].type === "ui") {
        assert.deepEqual(state.parts[1].surface.nodes, []);
        assert.match(state.parts[1].surface.fallback, /cubic/);
    }
});

test("unsupported UI payloads do not replace a readable response", () => {
    const state = applyEvent(emptyStreamState(), { type: "text_delta", delta: "Readable answer" });
    assert.equal(applyEvent(state, { type: "ui_commit", surface: { ...surface(), version: 99 } }), state);
    assert.equal(applyEvent(state, { type: "ui_patch", surface: "{incomplete" }), state);
});

test("UI failure before the first valid preview still has a readable fallback", () => {
    const state = applyEvent(emptyStreamState(), { type: "ui_error", surface_id: "bad", revision: 1, error: "invalid nodes", fallback: "The derivative of x² is 2x." });
    assert.deepEqual(state.parts, [{ type: "text", text: "The derivative of x² is 2x.", phase: "answer" }]);
});

test("SSE accepts split CRLF frames and sends capability and per-turn tools", async () => {
    let body: Record<string, unknown> = {};
    globalThis.fetch = async (_url, init) => {
        body = JSON.parse(String(init?.body));
        const encoder = new TextEncoder();
        const chunks = ['data: {"type":"text_delta","delta":"Hello"}\r\n\r', '\ndata: {"type":"done"}\r\n\r\n'];
        return new Response(new ReadableStream({ start(controller) { for (const chunk of chunks) controller.enqueue(encoder.encode(chunk)); controller.close(); } }));
    };
    const events: string[] = [];
    assert.equal(await streamChat({ message: "Search", tools: ["web_search"], web_search: true, ui_version: 1 }, (event) => events.push(event.type)), "done");
    assert.deepEqual(events, ["text_delta", "done"]);
    assert.equal(body.ui_version, 1);
    assert.deepEqual(body.tools, ["web_search"]);
});

test("unexpected end of SSE is an interruption, never a false successful completion", async () => {
    globalThis.fetch = async () => new Response('data: {"type":"text_delta","delta":"Partial"}\n\n');
    await assert.rejects(streamChat({ message: "Hello" }, () => {}), /connection ended before/);
});

test("sequenced replay deduplicates final text and usage; repeated final_start preserves output", () => {
    const events = [
        { type: "message_start", message_id: "answer", run_id: "run-1", seq: 1 },
        { type: "text_delta", delta: "PRIVATE WORKING TEXT", run_id: "run-1", seq: 2 },
        { type: "final_start", message_id: "answer", run_id: "run-1", seq: 3 },
        { type: "text_delta", delta: "Answer", run_id: "run-1", seq: 4 },
        { type: "usage", completion_tokens: 10, run_id: "run-1", seq: 5 },
    ];
    let state = events.reduce(applyEvent, emptyStreamState());
    state = events.reduce(applyEvent, state);
    state = applyEvent(state, { type: "final_start", message_id: "answer", run_id: "run-1", seq: 6 });
    assert.equal(JSON.stringify(state).includes("PRIVATE WORKING TEXT"), false);
    assert.deepEqual(state.parts, [{ type: "text", text: "Answer", phase: "answer" }]);
    assert.deepEqual(state.usage, { completion_tokens: 10 });
    assert.equal(applyEvent(state, { type: "text_delta", delta: "Wrong run", run_id: "run-2", seq: 8 }), state);
});

test("parallel tools stay independent, fields are allowlisted, and previews survive final_start", () => {
    const item = { id: "t1", parent_id: null, seq: 1, kind: "tool", phase: "work", label: "Computing", purpose: "Check calculation",
        status: "running", started_at: 1, updated_at: 1, finished_at: null, attempt: 1, outputs: [], arguments: "PRIVATE" };
    let state = applyEvent(emptyStreamState(), { type: "activity", item });
    state = applyEvent(state, { type: "activity", item: { ...item, id: "t2", seq: 2 } });
    state = applyEvent(state, { type: "activity", item: { ...item, seq: 3, status: "completed", summary: "Calculation completed" } });
    state = applyEvent(state, { type: "progress", update: { id: "p1", seq: 4, text: "Checking the second calculation", created_at: 2, phase: "work", reasoning: "PRIVATE" } });
    assert.deepEqual(state.work?.items?.map(({ id, status }) => ({ id, status })), [{ id: "t1", status: "completed" }, { id: "t2", status: "running" }]);
    assert.equal(state.work?.current_progress?.text, "Checking the second calculation");
    assert.equal(JSON.stringify(state).includes("PRIVATE"), false);
    state = applyEvent(state, { type: "ui_start", surface: surface() });
    state = applyEvent(state, { type: "final_start" });
    assert.equal(state.parts[0].type, "ui");
});

test("pause confirms only at safe boundary and steering acknowledgement is explicit", () => {
    let state = applyEvent(emptyStreamState(), { type: "control_ack", action: "pause", status: "queued" });
    assert.equal(state.pauseRequested, true);
    assert.equal(Boolean(state.paused), false);
    state = applyEvent(state, { type: "run_state", status: "paused" });
    assert.equal(state.paused, true);
    assert.equal(state.pauseRequested, false);
    state = applyEvent(state, { type: "control_ack", action: "steer", status: "queued" });
    assert.equal(state.controlStatus, "Instruction queued");
    state = applyEvent(state, { type: "steer_applied" });
    assert.equal(state.controlStatus, "Instruction applied");
    state = applyEvent(state, { type: "run_state", status: "running" });
    assert.equal(state.paused, false);
});

test("disconnect replays after last cursor and deduplicates repeated events", async () => {
    const calls: string[] = [];
    globalThis.fetch = async (url) => {
        calls.push(String(url));
        const events = calls.length === 1 ? [
            { type: "conversation", run_id: "durable", seq: 1 },
            { type: "final_start", run_id: "durable", seq: 2 },
            { type: "text_delta", run_id: "durable", seq: 3, delta: "Hello " },
        ] : [
            { type: "text_delta", run_id: "durable", seq: 3, delta: "Hello " },
            { type: "text_delta", run_id: "durable", seq: 4, delta: "world" },
            { type: "done", run_id: "durable", seq: 5 },
        ];
        return new Response(events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(""));
    };
    let state = emptyStreamState();
    const connections: boolean[] = [];
    await observeRun({ body: { message: "Hello", client_run_id: "idempotent" }, signal: new AbortController().signal,
        onEvent: (event) => { state = applyEvent(state, event); }, onConnection: (value) => connections.push(value) });
    assert.deepEqual(calls, ["/api/chat/stream", "/api/runs/durable/events?after=3"]);
    assert.deepEqual(state.parts, [{ type: "text", text: "Hello world", phase: "answer" }]);
    assert.equal(state.status, "complete");
    assert.ok(connections.includes(true));
});

test("lost initial response retries identical submission identity", async () => {
    const bodies: string[] = [];
    globalThis.fetch = async (_url, options) => {
        bodies.push(String(options?.body));
        if (bodies.length === 1) throw new TypeError("Connection lost before headers");
        return new Response('data: {"type":"done","run_id":"recovered","seq":1}\n\n');
    };
    await observeRun({ body: { message: "Only execute once", client_run_id: "stable-id" }, signal: new AbortController().signal, onEvent: () => {} });
    assert.equal(bodies.length, 2);
    assert.equal(bodies[0], bodies[1]);
});

test("aborting subscriber cancels reader without sending server stop", async () => {
    const abort = new AbortController();
    const urls: string[] = [];
    let cancelled = false;
    globalThis.fetch = async (url) => {
        urls.push(String(url));
        return new Response(new ReadableStream({ cancel() { cancelled = true; } }));
    };
    const pending = observeRun({ runId: "still-running", signal: abort.signal, onEvent: () => {} });
    await new Promise((resolve) => setTimeout(resolve, 0));
    abort.abort();
    await assert.rejects(pending, { name: "AbortError" });
    assert.deepEqual(urls, ["/api/runs/still-running/events?after=0"]);
    assert.equal(cancelled, true);
});

test("interrupted replay settles pending activities without rewriting completed work", () => {
    const item = { id: "t1", parent_id: null, seq: 1, kind: "tool", phase: "work", label: "Computing", purpose: "Check calculation",
        status: "running", started_at: 1, updated_at: 1, finished_at: null, attempt: 1, outputs: [] };
    let state = applyEvent(emptyStreamState(), { type: "activity", item });
    state = applyEvent(state, { type: "activity", item: { ...item, id: "t2", status: "completed", finished_at: 2 } });
    state = applyEvent(state, { type: "error", code: "interrupted", message: "The server restarted." });
    assert.deepEqual(state.work?.items?.map(({ status }) => status), ["failed", "completed"]);
    assert.equal(state.status, "error");
});

test("explicit partial results stream before final text, keep only public fields and respect message identity", () => {
    let state = applyEvent(emptyStreamState(), { type: "message_start", message_id: "answer", run_id: "run", seq: 1 });
    state = applyEvent(state, { type: "text_delta", delta: "PRIVATE_WORKER", run_id: "run", seq: 2 });
    state = applyEvent(state, { type: "reasoning_delta", delta: "PRIVATE_REASONING", run_id: "run", seq: 3 });
    state = applyEvent(state, { type: "result_block", message_id: "answer", block: { ...resultBlock(), reasoning: "PRIVATE_REASONING", tool_arguments: "PRIVATE_ARGUMENTS", stdout: "PRIVATE_STDOUT", agent_messages: "PRIVATE_AGENT" }, run_id: "run", seq: 4 });
    assert.deepEqual(state.parts, [{ type: "result_block", block: resultBlock() }]);
    assert.equal(state.presentation, undefined);
    assert.equal(JSON.stringify(state).includes("PRIVATE_"), false);
    const parts = state.parts;
    state = applyEvent(state, { type: "result_block", message_id: "other", block: resultBlock({ id: "other", content: "WRONG_CHAT" }), run_id: "run", seq: 5 });
    assert.equal(state.parts, parts);
    for (const invalid of [{ id: "" }, { id: "x".repeat(161) }, { revision: 0 }, { revision: 1.5 }, { revision: Number.MAX_SAFE_INTEGER + 1 },
        { kind: "reasoning" }, { status: "internal" }, { content: {} }, { created_at: Infinity }, { created_at: -1 }, { created_at: "1" },
        { evidence_refs: [42] }, { evidence_refs: Array(51).fill("result.csv") }, { evidence_refs: ["x".repeat(501)] }, { content: "x".repeat(50_001) }]) {
        state = applyEvent(state, { type: "result_block", message_id: "answer", block: { ...resultBlock(), ...invalid } });
        assert.equal(state.parts, parts);
    }
});

test("partial revisions and withdrawal tombstones cannot regress on replay; final manifest remains authoritative", () => {
    let state = applyEvent(emptyStreamState(), { type: "message_start", message_id: "answer" });
    const publish = (block: ResultBlock) => { state = applyEvent(state, { type: "result_block", message_id: "answer", block }); };
    publish(resultBlock());
    publish(resultBlock({ revision: 2, content: "Corrected result" }));
    const revised = state;
    publish(resultBlock()); publish(resultBlock({ revision: 2, content: "DUPLICATE_MUTATION" }));
    assert.equal(state, revised);
    state = applyEvent(state, { type: "final_start", message_id: "answer" });
    assert.equal(state.parts[0].type, "result_block");
    state = applyEvent(state, { type: "text_delta", delta: "The final answer." });
    const canonical = { ...assistantFromStream(state), status: "complete", parts: [
        { type: "text", text: "The final answer." },
        { type: "result_block", block: resultBlock({ revision: 3, content: "Corrected result", status: "superseded" }) },
        { type: "reasoning", text: "PRIVATE_FINAL" },
    ] };
    state = applyEvent(state, { type: "message_end", message_id: "answer", content: canonical });
    assert.equal(state.parts.length, 2);
    assert.equal(state.parts[1].type === "result_block" && state.parts[1].block.status, "superseded");
    const completed = state;
    publish(resultBlock({ revision: 4, content: "LATE_MUTATION" }));
    assert.equal(state, completed);
    assert.equal(JSON.stringify(state).includes("PRIVATE_"), false);

    state = applyEvent(emptyStreamState(), { type: "message_start", message_id: "answer" });
    publish(resultBlock({ revision: 5, status: "withdrawn", content: "" }));
    publish(resultBlock({ revision: 4, content: "WITHDRAWN_TEXT" }));
    assert.equal(state.parts.length, 1);
    assert.equal(state.parts[0].type === "result_block" && state.parts[0].block.status, "withdrawn");
});

test("stopping retains explicitly provisional results and progress history is bounded, deduplicated and allowlisted", () => {
    let state = applyEvent(emptyStreamState(), { type: "message_start", message_id: "answer" });
    state = applyEvent(state, { type: "result_block", message_id: "answer", block: resultBlock() });
    for (let seq = 1; seq <= 15; seq++) state = applyEvent(state, { type: "progress", update: {
        id: `update-${seq}`, seq, text: `Public milestone ${seq}`, created_at: seq, phase: "work", reasoning: "PRIVATE_TRACE",
    } });
    assert.equal(state.work?.milestones?.length, 12);
    assert.equal(state.work?.milestones?.[0].id, "update-4");
    state = applyEvent(state, { type: "progress", update: { id: "update-15", seq: 14, text: "STALE", created_at: 14, phase: "work" } });
    assert.equal(state.work?.current_progress?.text, "Public milestone 15");
    assert.equal(state.work?.milestones?.at(-1)?.text, "Public milestone 15");
    state = applyEvent(state, { type: "stopped" });
    assert.equal(state.parts[0].type === "result_block" && state.parts[0].block.status, "provisional");
    assert.equal(JSON.stringify(state).includes("PRIVATE_"), false);
    assert.equal(state.status, "stopped");
});
