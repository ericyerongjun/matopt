import "../../test/jsdom-env.mjs";
import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act, useEffect, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { useChat } from "./useChat.ts";
import MathInput from "../components/MathInput.tsx";
import { TooltipProvider } from "../zcode/components/ui/tooltip.tsx";
import { ZCodeIntlProvider } from "../zcode/i18n/IntlProvider.tsx";
import apiClient from "../services/api.ts";
import type { AssistantContent, ChatMessage, ResultBlock } from "../types/protocol.ts";

const originalFetch = globalThis.fetch;
const originalAdapter = apiClient.defaults.adapter;
let root: Root | null = null;
let container: HTMLDivElement | null = null;
let hook: ReturnType<typeof useChat>;
let submitted: Promise<boolean> | null = null;
let renderedMessages: ChatMessage[] = [];
beforeEach(() => { apiClient.defaults.adapter = async (config) => ({ data: { run: null }, config, headers: {}, status: 200, statusText: "OK" }); });
Object.defineProperty(globalThis, "ResizeObserver", { configurable: true, value: class { observe() {} unobserve() {} disconnect() {} } });
window.HTMLElement.prototype.scrollIntoView = () => {};
afterEach(() => {
  act(() => root?.unmount()); root = null; container?.remove(); container = null;
  globalThis.fetch = originalFetch; apiClient.defaults.adapter = originalAdapter; submitted = null;
});

function Harness({ activeId = "chat-1" }: { activeId?: string } = {}) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  renderedMessages = messages;
  useEffect(() => setMessages([]), [activeId]);
  hook = useChat({ activeId, messages, setMessages, setActiveId: () => {},
    createConversation: async () => ({ id: "chat-1", title: "Test" }), refreshList: async () => {},
    provider: "deepseek", model: "deepseek-flash", reasoning: "high", verify: false });
  return <ZCodeIntlProvider initialLocale="en-US"><TooltipProvider><MathInput loading={hook.streaming} showDisclaimer={false}
    commands={[{ kind: "tool", name: "web_search", description: "Search the web", source: "matopt" }]}
    onSubmit={(text, files, skills, tools) => { submitted = hook.sendMessage(text, files, skills, tools); return submitted; }} />
  </TooltipProvider></ZCodeIntlProvider>;
}
function render() {
  container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container);
  act(() => root?.render(<Harness />));
}
function input() { return document.querySelector<HTMLTextAreaElement>('textarea[aria-label="Task instructions"]')!; }
function type(value: string) {
  act(() => {
    Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value")!.set!.call(input(), value);
    input().dispatchEvent(new window.Event("input", { bubbles: true }));
  });
}
function key(key: string) { input().dispatchEvent(new window.KeyboardEvent("keydown", { key, bubbles: true, cancelable: true })); }
function frame(controller: ReadableStreamDefaultController<Uint8Array>, event: Record<string, unknown>) {
  controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(event)}\n\n`));
}

test("accepted sends clear promptly, steering stays available, and turn completion cannot erase the next draft", async () => {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  let request: Record<string, unknown> = {};
  const calls: Array<{ url?: string; body: Record<string, unknown> }> = [];
  globalThis.fetch = async (_url, options) => {
    request = JSON.parse(String(options?.body));
    return new Response(new ReadableStream<Uint8Array>({ start(stream) { controller = stream; } }), { headers: { "Content-Type": "text/event-stream" } });
  };
  apiClient.defaults.adapter = async (config) => {
    calls.push({ url: config.url, body: config.data ? JSON.parse(config.data) : {} });
    return { data: {}, config, headers: {}, status: 200, statusText: "OK" };
  };
  render(); type("Explain the result");
  await act(async () => { key("Enter"); });
  assert.equal(input().value, "Explain the result");
  assert.equal(hook.streaming, true);
  assert.equal(request.ui_version, 1);
  assert.equal(Object.hasOwn(request, "web_search"), false);
  await act(async () => {
    frame(controller, { type: "conversation", conversation_id: "chat-1" });
    frame(controller, { type: "message_start", message_id: "assistant-1" });
    assert.equal(await submitted, true);
  });
  assert.equal(hook.streaming, true);
  assert.equal(input().value, "");
  type("/web-search"); act(() => { key("Tab"); }); type("Find supporting sources");
  await act(async () => { key("Enter"); });
  assert.equal(await submitted, true);
  assert.equal(input().value, "");
  assert.deepEqual(calls.find((call) => call.url?.endsWith("/steer")), { url: "/chat/chat-1/steer", body: { message: "/web-search Find supporting sources", skills: [], tools: ["web_search"] } });
  type("Keep this draft for the next turn");
  await act(async () => { frame(controller, { type: "done" }); controller.close(); });
  assert.equal(hook.streaming, false);
  assert.equal(input().value, "Keep this draft for the next turn");
});

test("a request rejected before acceptance retains the draft and selected commands for retry", async () => {
  globalThis.fetch = async () => new Response(JSON.stringify({ detail: "Conversation is busy" }), { status: 409, headers: { "Content-Type": "application/json" } });
  render(); type("/web-search"); act(() => { key("Tab"); }); type("Find the latest source");
  await act(async () => { key("Enter"); });
  assert.equal(await submitted, false);
  assert.equal(input().value, "Find the latest source");
  assert.ok(document.querySelector('button[aria-label="Remove Search the web"]'));
  assert.equal(hook.streaming, false);
  assert.equal(hook.streamError, "Conversation is busy");
  assert.deepEqual(hook.overlay?.messages, []);
});

test("a late follow-up response from an abandoned run cannot relabel the next chat's live overlay", async () => {
  const controllers: Array<ReadableStreamDefaultController<Uint8Array>> = [];
  let releaseFollowUps: (() => void) | undefined;
  globalThis.fetch = async () => new Response(new ReadableStream<Uint8Array>({ start(controller) { controllers.push(controller); } }), { headers: { "Content-Type": "text/event-stream" } });
  apiClient.defaults.adapter = async (config) => {
    if (config.url === "/followups") return new Promise((resolve) => {
      releaseFollowUps = () => resolve({ data: { followups: ["Stale suggestion"] }, config, headers: {}, status: 200, statusText: "OK" });
    });
    return { data: {}, config, headers: {}, status: 200, statusText: "OK" };
  };
  render();
  let first!: Promise<boolean>;
  await act(async () => { first = hook.sendMessage("First question"); });
  await act(async () => {
    frame(controllers[0], { type: "conversation", conversation_id: "chat-1" });
    frame(controllers[0], { type: "message_start", message_id: "first-answer" });
    frame(controllers[0], { type: "text_delta", delta: "First answer" });
    frame(controllers[0], { type: "done" }); controllers[0].close();
    assert.equal(await first, true);
  });
  assert.ok(releaseFollowUps);
  assert.equal(hook.streaming, false, "optional follow-ups must not keep the turn busy");
  await act(async () => { await hook.abandon(); });
  act(() => root?.render(<Harness activeId="chat-2" />));
  let second!: Promise<boolean>;
  await act(async () => { second = hook.sendMessage("Second question"); });
  await act(async () => {
    frame(controllers[1], { type: "conversation", conversation_id: "chat-2" });
    frame(controllers[1], { type: "message_start", message_id: "second-answer" });
    assert.equal(await second, true);
  });
  await act(async () => { releaseFollowUps!(); });
  assert.equal(hook.overlay?.cid, "chat-2");
  assert.equal(hook.streaming, true);
  assert.equal(JSON.stringify(hook.overlay?.messages).includes("Stale suggestion"), false);
  assert.ok(hook.overlay?.messages.every((message) => message.conversation_id === "chat-2"));
  await act(async () => { frame(controllers[1], { type: "done" }); controllers[1].close(); });
});

function savedMessages(answerId = "answer-1"): ChatMessage[] {
  return [
    { id: "saved-user-1", conversation_id: "chat-1", seq: 1, role: "user", content: { text: "Question", attachments: [] }, created_at: 1 },
    { id: answerId, conversation_id: "chat-1", seq: 2, role: "assistant", created_at: 2,
      content: { parts: [{ type: "text", text: "Saved final answer" }, { type: "options", items: ["Saved suggestion"] }],
        presentation: "final", status: "complete", model: "model", provider: "deepseek", reasoning: "high", usage: {}, plan: [], stats: { llm_calls: 1, tool_calls: 0 } } },
  ];
}
function completedStream() {
  globalThis.fetch = async () => new Response([
    { type: "message_start", message_id: "answer-1" },
    { type: "final_start", message_id: "answer-1" },
    { type: "text_delta", delta: "Streamed final answer" },
    { type: "done", conversation_id: "chat-1", message_id: "answer-1" },
  ].map((event) => `data: ${JSON.stringify(event)}\n\n`).join(""));
}

test("completion reconciles canonical message IDs once while preserving the next composer draft", async () => {
  completedStream();
  let releaseRefresh!: () => void;
  apiClient.defaults.adapter = async (config) => new Promise((resolve) => {
    if (config.url?.endsWith("/run")) { resolve({ data: { run: null }, config, headers: {}, status: 200, statusText: "OK" }); return; }
    assert.equal(config.url, "/conversations/chat-1");
    releaseRefresh = () => resolve({ data: { id: "chat-1", messages: savedMessages(), running: false }, config, headers: {}, status: 200, statusText: "OK" });
  });
  render(); type("Question");
  await act(async () => { key("Enter"); });
  assert.equal(await submitted, true);
  assert.equal(hook.streaming, false);
  type("My next draft");
  await act(async () => { releaseRefresh(); });
  assert.deepEqual(hook.overlay?.messages.map((message) => message.id), ["saved-user-1", "answer-1"]);
  assert.equal(renderedMessages.length, 2);
  assert.equal(input().value, "My next draft");
  assert.equal(JSON.stringify(renderedMessages).includes("Streamed final answer"), false);
  assert.equal(JSON.stringify(renderedMessages).includes("Saved final answer"), true);
});

test("a delayed canonical refresh cannot replace another chat's messages", async () => {
  completedStream();
  let releaseRefresh!: () => void;
  apiClient.defaults.adapter = async (config) => new Promise((resolve) => {
    releaseRefresh = () => resolve({ data: { id: "chat-1", messages: savedMessages(), running: false }, config, headers: {}, status: 200, statusText: "OK" });
  });
  render();
  await act(async () => { await hook.sendMessage("Question"); });
  act(() => root?.render(<Harness activeId="chat-2" />));
  await act(async () => { releaseRefresh(); });
  assert.deepEqual(renderedMessages, []);
  assert.equal(hook.overlay, null);
});

test("a failed saved-state refresh preserves the final streamed answer without reporting a failed run", async () => {
  completedStream();
  apiClient.defaults.adapter = async () => { throw new Error("Read unavailable"); };
  render();
  await act(async () => { await hook.sendMessage("Question"); });
  assert.equal(hook.streaming, false);
  assert.equal(hook.streamError, null);
  assert.equal(JSON.stringify(hook.overlay?.messages).includes("Streamed final answer"), true);
});

test("abandoning during upload never starts the abandoned turn or restores its busy state", async () => {
  let releaseUpload!: () => void;
  const requests: string[] = [];
  globalThis.fetch = async (url) => {
    requests.push(String(url));
    return new Promise((resolve) => { releaseUpload = () => resolve(new Response(JSON.stringify({ files: [{ name: "sample.txt", path: "uploads/sample.txt" }] }), { headers: { "Content-Type": "application/json" } })); });
  };
  render();
  let pending!: Promise<boolean>;
  await act(async () => { pending = hook.sendMessage("Read this", [new File(["synthetic"], "sample.txt")]); });
  assert.equal(requests.length, 1);
  await act(async () => { await hook.abandon(); });
  type("Keep the new draft");
  await act(async () => { releaseUpload(); });
  assert.equal(await pending, false);
  assert.deepEqual(requests, ["/api/conversations/chat-1/files"]);
  assert.equal(hook.streaming, false);
  assert.equal(hook.overlay, null);
  assert.equal(input().value, "Keep the new draft");
});

test("navigation unsubscribes without stop and returning reattaches durable run", async () => {
  const urls: string[] = [];
  const apiUrls: string[] = [];
  const controllers: ReadableStreamDefaultController<Uint8Array>[] = [];
  let runExists = false;
  globalThis.fetch = async (url) => {
    urls.push(String(url));
    return new Response(new ReadableStream({ start(controller) { controllers.push(controller); } }));
  };
  apiClient.defaults.adapter = async (config) => {
    apiUrls.push(String(config.url));
    const data = config.url === "/chat/chat-1/run" && runExists
      ? { run: { id: "durable-1", conversation_id: "chat-1", message_id: "answer-1", status: "running", last_seq: 3 } }
      : config.url === "/conversations/chat-1" ? { messages: savedMessages().slice(0, 1) } : { run: null };
    return { data, config, headers: {}, status: 200, statusText: "OK" };
  };
  render();
  await act(async () => {
    const sent = hook.sendMessage("Keep working");
    await Promise.resolve();
    frame(controllers[0], { type: "message_start", message_id: "answer-1", run_id: "durable-1", seq: 1 });
    assert.equal(await sent, true);
  });
  runExists = true;
  await act(async () => { root?.render(<Harness activeId="chat-2" />); });
  assert.equal(hook.streaming, false);
  assert.equal(apiUrls.some((url) => url.endsWith("/stop")), false);
  await act(async () => { root?.render(<Harness activeId="chat-1" />); });
  assert.equal(hook.streaming, true);
  assert.equal(urls[1], "/api/runs/durable-1/events?after=0");
  await act(async () => {
    frame(controllers[1], { type: "message_start", message_id: "answer-1", run_id: "durable-1", seq: 1 });
    frame(controllers[1], { type: "final_start", run_id: "durable-1", seq: 2 });
    frame(controllers[1], { type: "text_delta", delta: "Recovered answer", run_id: "durable-1", seq: 3 });
    frame(controllers[1], { type: "done", run_id: "durable-1", seq: 4 });
  });
  assert.equal(hook.streaming, false);
  assert.equal(hook.overlay?.messages.filter((message) => message.role === "assistant").length, 1);
  assert.match(JSON.stringify(hook.overlay), /Recovered answer/);
});

test("reload recovers active run; controls wait for server acknowledgement", async () => {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const urls: string[] = [];
  globalThis.fetch = async (url) => {
    assert.equal(String(url), "/api/runs/reloaded-run/events?after=0");
    return new Response(new ReadableStream({ start(value) { controller = value; } }));
  };
  apiClient.defaults.adapter = async (config) => {
    urls.push(String(config.url));
    return { data: config.url?.endsWith("/run")
      ? { run: { id: "reloaded-run", conversation_id: "chat-1", message_id: "answer-1", status: "paused" } }
      : { messages: savedMessages().slice(0, 1) }, config, headers: {}, status: 200, statusText: "OK" };
  };
  await act(async () => { render(); });
  assert.equal(hook.streaming, true);
  assert.equal(hook.paused, true);
  await act(async () => { await hook.resume(); });
  assert.ok(urls.includes("/chat/chat-1/resume"));
  assert.equal(hook.controlStatus, "Resume requested");
  await act(async () => {
    frame(controller, { type: "message_start", run_id: "reloaded-run", seq: 1, message_id: "answer-1" });
  });
  assert.equal(hook.controlStatus, "Resume requested", "an intervening stream update cannot erase a pending control");
  await act(async () => {
    frame(controller, { type: "run_state", run_id: "reloaded-run", seq: 2, status: "running" });
  });
  await act(async () => { await hook.pause(); });
  assert.equal(hook.pauseRequested, true);
  assert.equal(hook.paused, false);
  await act(async () => { frame(controller, { type: "run_state", run_id: "reloaded-run", seq: 3, status: "paused" }); });
  assert.equal(hook.paused, true);
  await act(async () => { await hook.stop(); });
  assert.ok(urls.includes("/chat/chat-1/stop"));
  assert.equal(hook.streaming, true, "stop request is not a terminal acknowledgement");
  await act(async () => { frame(controller, { type: "usage", run_id: "reloaded-run", seq: 4, total_tokens: 42 }); });
  assert.equal(hook.controlStatus, "Stopping…", "late output cannot restore an earlier control message");
  await act(async () => { frame(controller, { type: "stopped", run_id: "reloaded-run", seq: 5 }); });
  assert.equal(hook.streaming, false);
});

test("switching chats during upload discards only the local submission and attaches the selected run", async () => {
  let releaseUpload!: () => void;
  const urls: string[] = [];
  let events!: ReadableStreamDefaultController<Uint8Array>;
  globalThis.fetch = async (url) => {
    urls.push(String(url));
    if (String(url).includes("/files")) return new Promise((resolve) => {
      releaseUpload = () => resolve(new Response(JSON.stringify({ files: [{ name: "sample.txt", path: "uploads/sample.txt" }] }), { headers: { "Content-Type": "application/json" } }));
    });
    return new Response(new ReadableStream({ start(controller) { events = controller; } }));
  };
  apiClient.defaults.adapter = async (config) => ({ data: config.url === "/chat/chat-2/run"
    ? { run: { id: "other-run", conversation_id: "chat-2", message_id: "other-answer", status: "running" } }
    : config.url === "/conversations/chat-2" ? { messages: [] } : { run: null }, config, headers: {}, status: 200, statusText: "OK" });
  render();
  let pending!: Promise<boolean>;
  await act(async () => { pending = hook.sendMessage("Read file", [new File(["source"], "sample.txt")]); });
  await act(async () => { root?.render(<Harness activeId="chat-2" />); });
  await act(async () => { releaseUpload(); });
  assert.equal(await pending, false);
  assert.equal(hook.streaming, true);
  assert.deepEqual(urls, ["/api/conversations/chat-1/files", "/api/runs/other-run/events?after=0"]);
  await act(async () => { frame(events, { type: "done", run_id: "other-run", seq: 1 }); });
});

test("retry after rejection starts a new submission rather than steering a nonexistent run", async () => {
  let attempts = 0;
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const urls: string[] = [];
  apiClient.defaults.adapter = async (config) => {
    urls.push(String(config.url));
    return { data: { run: null }, config, headers: {}, status: 200, statusText: "OK" };
  };
  globalThis.fetch = async () => {
    if (++attempts === 1) return new Response(JSON.stringify({ detail: "Temporarily unavailable" }), { status: 409 });
    return new Response(new ReadableStream({ start(value) { controller = value; } }));
  };
  render();
  await act(async () => { assert.equal(await hook.sendMessage("Try once"), false); });
  assert.equal(hook.streaming, false);
  await act(async () => {
    const pending = hook.sendMessage("Try again");
    await Promise.resolve();
    frame(controller, { type: "message_start", message_id: "retry-answer" });
    assert.equal(await pending, true);
  });
  assert.equal(attempts, 2);
  assert.equal(urls.some((url) => url.endsWith("/steer")), false);
  await act(async () => { frame(controller, { type: "done" }); });
});

test("partial revisions remain on the same assistant after steering and reconcile to the canonical final manifest", async () => {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  let finalMessages: ChatMessage[] = [];
  globalThis.fetch = async () => new Response(new ReadableStream({ start(value) { controller = value; } }));
  apiClient.defaults.adapter = async (config) => ({
    data: config.url === "/conversations/chat-1" ? { messages: finalMessages } : { run: null },
    config, headers: {}, status: 200, statusText: "OK",
  });
  const block: ResultBlock = { id: "answer-1:result:table", revision: 1, kind: "markdown", status: "provisional",
    content: "Initial public table", evidence_refs: ["results.csv"], created_at: 1 };
  const answer = () => hook.overlay!.messages.find((message) => message.id === "answer-1")!.content as AssistantContent;
  render();
  await act(async () => {
    const pending = hook.sendMessage("Calculate the table");
    await Promise.resolve();
    frame(controller, { type: "message_start", message_id: "answer-1", run_id: "run-1", seq: 1 });
    frame(controller, { type: "result_block", message_id: "answer-1", block, run_id: "run-1", seq: 2 });
    assert.equal(await pending, true);
  });
  await act(async () => { assert.equal(await hook.sendMessage("Use metres for the units."), true); });
  assert.equal(hook.overlay?.messages.at(-1)?.role, "user");
  assert.equal(answer().status, "streaming");
  assert.equal(hook.streaming, true);
  await act(async () => {
    frame(controller, { type: "result_block", message_id: "answer-1", block: { ...block, revision: 2, content: "Corrected table in metres" }, run_id: "run-1", seq: 3 });
    frame(controller, { type: "result_block", message_id: "answer-1", block, run_id: "run-1", seq: 2 });
    frame(controller, { type: "reasoning_delta", delta: "PRIVATE_WORKER_REASONING", run_id: "run-1", seq: 4 });
    frame(controller, { type: "final_start", message_id: "answer-1", run_id: "run-1", seq: 5 });
    frame(controller, { type: "text_delta", delta: "Final answer", run_id: "run-1", seq: 6 });
  });
  assert.equal(hook.overlay?.messages.filter((message) => message.role === "assistant").length, 1);
  assert.equal(hook.overlay?.messages.at(-1)?.role, "user");
  assert.equal(answer().status, "streaming");
  assert.deepEqual(answer().parts.map((part) => part.type), ["result_block", "text"]);
  assert.equal(answer().parts[0].type === "result_block" && answer().parts[0].block.revision, 2);
  assert.equal(JSON.stringify(hook.overlay).includes("PRIVATE_"), false);
  const finalContent: AssistantContent = { ...answer(), status: "complete", parts: [
    { type: "text", text: "Final answer in metres." },
    { type: "result_block", block: { ...block, revision: 3, status: "superseded", content: "Corrected table in metres" } },
    { type: "options", items: ["Show the derivation"] },
  ] };
  finalMessages = hook.overlay!.messages.map((message) => message.id === "answer-1" ? { ...message, content: finalContent } : message);
  type("Keep my next draft");
  await act(async () => {
    frame(controller, { type: "message_end", message_id: "answer-1", content: finalContent, run_id: "run-1", seq: 7 });
    frame(controller, { type: "done", message_id: "answer-1", conversation_id: "chat-1", run_id: "run-1", seq: 8 });
    controller.close();
  });
  assert.equal(hook.streaming, false);
  assert.deepEqual(answer().parts, finalContent.parts);
  assert.equal(hook.overlay?.messages.filter((message) => message.role === "assistant").length, 1);
  assert.equal(input().value, "Keep my next draft");
});
