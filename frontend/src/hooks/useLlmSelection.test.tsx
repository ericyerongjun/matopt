import "../../test/jsdom-env.mjs";
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import apiClient from "../services/api.ts";
import { useLlmSelection } from "./useLlmSelection.ts";
import type { ProviderCatalog, ProviderModel } from "../types/protocol.ts";

const originalAdapter = apiClient.defaults.adapter;
const originalStorage = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
let root: Root | null = null, container: HTMLDivElement | null = null;
let hook: ReturnType<typeof useLlmSelection>;
afterEach(() => {
    act(() => root?.unmount()); root = null; container?.remove(); container = null;
    apiClient.defaults.adapter = originalAdapter; window.localStorage.clear();
    if (originalStorage) Object.defineProperty(globalThis, "localStorage", originalStorage);
    else delete (globalThis as { localStorage?: Storage }).localStorage;
});
function Harness() { hook = useLlmSelection(); return <output>{hook.provider}:{hook.model}:{hook.reasoning}</output>; }
async function tick() {
    const state = (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT;
    (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = false;
    try { await new Promise((resolve) => setTimeout(resolve, 0)); }
    finally { (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = state; }
    act(() => {});
}
const model = (id: string): ProviderModel => ({ id, label: id === "deepseek-flash" ? "DeepSeek V4.1 Flash" : id,
    family: "deepseek", description: "model", context_window: 1_000_000, max_output: 64_000,
    reasoning_levels: ["off", "low", "high", "max"], default_reasoning: "high", tags: [] });
const catalog: ProviderCatalog = { default_provider: "legacy", default_model: "old-model", default_reasoning: "medium", verify_available: true,
    levels: [{ id: "off", label: "Off" }, { id: "low", label: "Low" }, { id: "medium", label: "Medium" }, { id: "high", label: "High" }, { id: "max", label: "Max" }],
    providers: [
        { id: "legacy", label: "Legacy", configured: true, env_key: "LEGACY", docs_url: "", default_model: "old-model", models: [model("old-model")] },
        { id: "deepseek", label: "DeepSeek", configured: true, env_key: "DEEPSEEK", docs_url: "", default_model: "deepseek-flash", models: [model("deepseek-flash"), model("other-deepseek")] },
    ] };

async function mountSelection() {
    Object.defineProperty(globalThis, "localStorage", { value: window.localStorage, configurable: true });
    apiClient.defaults.adapter = async (config) => ({ data: catalog, config, headers: {}, status: 200, statusText: "OK" });
    container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container);
    act(() => root?.render(<Harness />));
    for (let i = 0; hook.loading && i < 50; i++) await tick();
}

test("fresh selection defaults to Ultrafast without a manual reasoning override", async () => {
    await mountSelection();
    assert.equal(hook.loading, false);
    assert.equal(hook.ultrafast, true);
    assert.equal(hook.reasoning, null);
    assert.equal(hook.provider, "deepseek");
    assert.equal(hook.model, "deepseek-flash");
    assert.equal(hook.selectedLabel, "DeepSeek V4.1 Flash");
});

test("legacy effort and model preferences cannot override automatic model selection", async () => {
    window.localStorage.setItem("matopt.provider", "legacy");
    window.localStorage.setItem("matopt.model", "old-model");
    window.localStorage.setItem("matopt.reasoning", "max");
    window.localStorage.setItem("matopt.ultrafast", "0");
    window.localStorage.setItem("matopt.verify", "1");
    await mountSelection();
    assert.equal(hook.reasoning, null);
    assert.equal(hook.verify, true);
    assert.equal(hook.ultrafast, true);
    assert.equal(window.localStorage.getItem("matopt.reasoning"), null);
    assert.equal(window.localStorage.getItem("matopt.provider"), "deepseek");
    assert.equal(window.localStorage.getItem("matopt.model"), "deepseek-flash");
    act(() => hook.selectUltrafast(false));
    assert.equal(hook.reasoning, null);
    assert.equal(hook.ultrafast, false);
    assert.equal(window.localStorage.getItem("matopt.ultrafast"), "0");
    act(() => root?.unmount()); root = null; container?.remove();
    await mountSelection();
    assert.equal(hook.ultrafast, false);
    assert.equal(hook.reasoning, null);
    act(() => hook.selectUltrafast(true));
    assert.equal(window.localStorage.getItem("matopt.ultrafast"), "1");
});

test("unavailable local storage preserves the default and leaves the toggle usable", async () => {
    Object.defineProperty(globalThis, "localStorage", { configurable: true, get() { throw new Error("Storage denied"); } });
    apiClient.defaults.adapter = async (config) => ({ data: catalog, config, headers: {}, status: 200, statusText: "OK" });
    container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container);
    act(() => root?.render(<Harness />));
    for (let i = 0; hook.loading && i < 50; i++) await tick();
    assert.equal(hook.ultrafast, true);
    assert.equal(hook.reasoning, null);
    act(() => { hook.selectUltrafast(false); hook.selectVerify(true); });
    assert.equal(hook.ultrafast, false);
    assert.equal(hook.verify, true);
});
