import "../../test/jsdom-env.mjs";
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { window } from "../../test/jsdom-env.mjs";
import type { AxiosResponse, InternalAxiosRequestConfig } from "axios";
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

test("stale provider/model/medium preferences migrate to Flash/high and arbitrary selections stay rejected", async () => {
    Object.defineProperty(globalThis, "localStorage", { value: window.localStorage, configurable: true });
    window.localStorage.setItem("matopt.provider", "legacy"); window.localStorage.setItem("matopt.model", "old-model");
    window.localStorage.setItem("matopt.reasoning", "medium"); window.localStorage.setItem("matopt.verify", "1");
    let request: { config: InternalAxiosRequestConfig; resolve: (value: AxiosResponse) => void } | undefined;
    apiClient.defaults.adapter = (config) => new Promise<AxiosResponse>((resolve) => { request = { config, resolve }; });
    container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container);
    act(() => root?.render(<Harness />)); await tick();
    assert.equal(request?.config.url, "/providers"); assert.equal(hook.loading, true);
    act(() => request!.resolve({ data: catalog, config: request!.config, headers: {}, status: 200, statusText: "OK" })); await tick();
    assert.equal(hook.loading, false); assert.equal(hook.provider, "deepseek"); assert.equal(hook.model, "deepseek-flash");
    assert.equal(hook.reasoning, "high"); assert.equal(hook.verify, true); assert.equal(hook.selectedLabel, "DeepSeek V4.1 Flash");
    assert.deepEqual(hook.catalog?.providers.map((provider) => [provider.id, provider.models.map((entry) => entry.id)]), [["deepseek", ["deepseek-flash"]]]);
    assert.deepEqual(hook.allowedLevels.map((level) => level.id), ["off", "low", "high", "max"]);
    assert.equal(window.localStorage.getItem("matopt.provider"), "deepseek");
    assert.equal(window.localStorage.getItem("matopt.model"), "deepseek-flash"); assert.equal(window.localStorage.getItem("matopt.reasoning"), "high");
    act(() => { hook.select("legacy", "old-model"); hook.select("deepseek", "other-deepseek"); hook.selectReasoning("medium"); });
    assert.equal(hook.provider, "deepseek"); assert.equal(hook.model, "deepseek-flash"); assert.equal(hook.reasoning, "high");
    assert.equal(window.localStorage.getItem("matopt.reasoning"), "high");
});
