import "../../test/jsdom-env.mjs";
import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { ApiRequestConfig, ApiResponse } from "../services/api.ts";
import apiClient from "../services/api.ts";
import { useConversations } from "./useConversations.ts";
import type { Conversation } from "../types/protocol.ts";

const originalAdapter = apiClient.defaults.adapter;
let root: Root | null = null, container: HTMLDivElement | null = null;
let hook: ReturnType<typeof useConversations>;
interface DeferredRequest {
    url: string; config: ApiRequestConfig;
    resolve: (response: ApiResponse) => void; reject: (error: Error) => void;
}
let requests: DeferredRequest[] = [];
beforeEach(() => window.history.replaceState(null, "", "/"));
afterEach(() => { act(() => root?.unmount()); root = null; container?.remove(); container = null; apiClient.defaults.adapter = originalAdapter; requests = []; });
function response(request: DeferredRequest, data: unknown): ApiResponse { return { data, config: request.config, headers: {}, status: 200, statusText: "OK" }; }
function Harness() { hook = useConversations(); return <output>{hook.activeId ?? "draft"}:{hook.loadingThread ? "loading" : "ready"}:{hook.error ?? ""}</output>; }
function mount() {
    apiClient.defaults.adapter = (config) => {
        if (config.method === "get" && config.url === "/conversations") return Promise.resolve({ data: { conversations: [] }, config, headers: {}, status: 200, statusText: "OK" });
        return new Promise<ApiResponse>((resolve, reject) => requests.push({ url: config.url ?? "", config, resolve, reject }));
    };
    container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container);
    act(() => root?.render(<Harness />));
}
/** A bounded tick flushes Axios interceptor promises. No act scope waits on an unresolved request. */
async function tick() {
    const actEnvironment = (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT;
    (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = false;
    try { await new Promise((resolve) => setTimeout(resolve, 0)); }
    finally { (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = actEnvironment; }
    act(() => {});
}
const conversation = (id: string): Conversation => ({ id, title: id, provider: null, model: null, reasoning: null, created_at: 1, updated_at: 1, meta: {} });

test("switching tasks discards a late prior response and error", async (context) => {
    context.mock.method(console, "error", () => {});
    mount(); await tick();
    act(() => hook.select("old")); await tick();
    const old = requests.at(-1)!;
    act(() => hook.select("current")); await tick();
    const current = requests.at(-1)!;
    assert.equal(old.url, "/conversations/old"); assert.equal(current.url, "/conversations/current");
    act(() => current.resolve(response(current, { ...conversation("current"), messages: [{ id: "current-message", role: "user", content: { text: "current" } }], running: false })));
    await tick();
    assert.equal(hook.activeId, "current"); assert.equal(hook.loadingThread, false);
    act(() => old.resolve(response(old, { ...conversation("old"), messages: [{ id: "old-message" }], running: true })));
    await tick();
    assert.equal(hook.messages[0].id, "current-message"); assert.equal(hook.running, false);
    assert.equal(hook.conversations.some((item) => item.id === "old"), false);

    act(() => hook.select("failed-old")); await tick();
    const failedOld = requests.at(-1)!;
    act(() => hook.select("newer")); await tick();
    const newer = requests.at(-1)!;
    act(() => newer.resolve(response(newer, { ...conversation("newer"), messages: [{ id: "newer-message" }], running: false })));
    await tick();
    act(() => failedOld.reject(new Error("STALE TASK FAILURE"))); await tick();
    assert.equal(hook.error, null); assert.equal(hook.messages[0].id, "newer-message");
});

test("New Chat clears loading immediately while the old GET remains unresolved", async () => {
    mount(); await tick();
    act(() => hook.select("old")); await tick();
    assert.equal(hook.loadingThread, true);
    const old = requests.at(-1)!;
    act(() => hook.newChat());
    assert.equal(hook.activeId, null); assert.equal(hook.loadingThread, false); assert.deepEqual(hook.messages, []);
    act(() => old.resolve(response(old, { ...conversation("old"), messages: [{ id: "old-message" }], running: true })));
    await tick();
    assert.equal(hook.activeId, null); assert.equal(hook.running, false); assert.deepEqual(hook.messages, []);
});

test("create clears thread loading without waiting for an unresolved prior GET", async () => {
    mount(); await tick();
    act(() => hook.select("old")); await tick();
    assert.equal(hook.loadingThread, true);
    const old = requests.at(-1)!;
    let creation: Promise<Conversation> | undefined;
    act(() => { creation = hook.create({ title: "new task" }); }); await tick();
    const post = requests.at(-1)!;
    assert.equal(post.config.method, "post");
    act(() => post.resolve(response(post, conversation("created")))); await tick();
    assert.equal((await creation!).id, "created");
    assert.equal(hook.activeId, "created"); assert.equal(hook.loadingThread, false); assert.deepEqual(hook.messages, []);
    act(() => old.resolve(response(old, { ...conversation("old"), messages: [{ id: "old-message" }], running: true })));
    await tick();
    assert.equal(hook.activeId, "created"); assert.equal(hook.running, false); assert.deepEqual(hook.messages, []);
});

test("a delayed create cannot reactivate a chat after New Chat", async () => {
    mount(); await tick();
    let creation!: Promise<Conversation>;
    act(() => { creation = hook.create(); }); await tick();
    const post = requests.at(-1)!;
    act(() => hook.newChat());
    act(() => post.resolve(response(post, conversation("created-late")))); await tick();
    assert.equal((await creation).id, "created-late");
    assert.equal(hook.activeId, null);
    assert.deepEqual(hook.messages, []);
    assert.equal(hook.conversations.some((item) => item.id === "created-late"), true);
});

test("selected chat survives a reload and restores its canonical running state", async () => {
    window.history.replaceState({ panel: "files" }, "", "/?view=workspace#results");
    mount(); await tick();
    act(() => hook.select("running-chat")); await tick();
    const selected = requests.at(-1)!;
    act(() => selected.resolve(response(selected, { ...conversation("running-chat"), messages: [], running: true })));
    await tick();
    assert.equal(window.location.search, "?view=workspace&chat=running-chat");
    assert.equal(window.location.hash, "#results");
    assert.deepEqual(window.history.state, { panel: "files" });

    // Remount is a page reload without changing its URL or saved server state.
    act(() => root?.unmount()); container?.remove(); root = null; container = null;
    mount();
    assert.equal(hook.activeId, "running-chat", "run recovery sees the saved chat on the initial render");
    assert.equal(hook.loadingThread, true);
    await tick();
    const restored = requests.at(-1)!;
    assert.equal(restored.url, "/conversations/running-chat");
    act(() => restored.resolve(response(restored, { ...conversation("running-chat"), messages: [{ id: "saved-question", role: "user", content: { text: "Continue the work" } }], running: true })));
    await tick();
    assert.equal(hook.running, true);
    assert.equal(hook.messages[0].id, "saved-question");
    assert.equal(hook.loadingThread, false);
    assert.equal(hook.error, null);
});

test("a restored chat cannot take over after navigating to New Chat", async () => {
    window.history.replaceState(null, "", "/?chat=old&view=workspace#results");
    mount(); await tick();
    const old = requests.at(-1)!;
    act(() => hook.newChat());
    assert.equal(window.location.search, "?view=workspace");
    assert.equal(window.location.hash, "#results");
    act(() => old.resolve(response(old, { ...conversation("old"), messages: [{ id: "old-message" }], running: true })));
    await tick();
    assert.equal(hook.activeId, null);
    assert.equal(hook.running, false);
    assert.deepEqual(hook.messages, []);
    assert.equal(window.location.search, "?view=workspace");
});

test("deleting the selected chat clears only its URL selection", async () => {
    window.history.replaceState(null, "", "/?chat=deleted&view=workspace#results");
    mount(); await tick();
    let deletion!: Promise<void>;
    act(() => { deletion = hook.remove("deleted"); }); await tick();
    const request = requests.at(-1)!;
    assert.equal(request.config.method, "delete");
    act(() => request.resolve(response(request, {}))); await tick(); await deletion;
    assert.equal(hook.activeId, null);
    assert.equal(window.location.search, "?view=workspace");
    assert.equal(window.location.hash, "#results");
});

test("an unavailable linked chat shows the load error without substituting a different conversation", async (context) => {
    context.mock.method(console, "error", () => {});
    window.history.replaceState(null, "", "/?chat=missing");
    mount(); await tick();
    const request = requests.at(-1)!;
    act(() => request.reject(new Error("Conversation not found"))); await tick();
    assert.equal(hook.error, "Conversation not found");
    assert.equal(hook.loadingThread, false);
    assert.equal(hook.running, false);
    assert.equal(hook.activeId, "missing");
    assert.deepEqual(hook.messages, []);
    assert.equal(window.location.search, "?chat=missing");
    act(() => hook.newChat());
    assert.equal(hook.error, null);
    assert.equal(window.location.search, "");
});
