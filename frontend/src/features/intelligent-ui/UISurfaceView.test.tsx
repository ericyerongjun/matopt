import "../../../test/jsdom-env.mjs";
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { UICapabilities, UISurface } from "../../types/ui.ts";
import { ZCodeIntlProvider } from "../../zcode/i18n/IntlProvider.js";
import { TooltipProvider } from "../../zcode/components/ui/tooltip.js";
import { UiWorkspaceProvider, useUiWorkspace } from "./UiWorkspace.tsx";
import { projectAssistantPresentation } from "../../services/presentation.ts";
import type { AssistantContent } from "../../types/protocol.ts";
import { MiniAppFrame } from "./MiniAppFrame.tsx";
import { ArtifactView, safeHtmlArtifactSource } from "../../components/ArtifactView.tsx";

Object.defineProperty(window.HTMLCanvasElement.prototype, "getContext", { configurable: true, value: () => null });
const { UISurfaceView, UiCanvas } = await import("./UISurfaceView.tsx");
const originalFetch = globalThis.fetch;
const capabilities: UICapabilities = { version: 1, native: true, mini_apps: true, node_types: ["number", "markdown", "button", "tabs", "stack"] };
let root: Root | null = null;
let container: HTMLDivElement | null = null;
afterEach(() => { act(() => root?.unmount()); root = null; container?.remove(); container = null; globalThis.fetch = originalFetch; });

function example(overrides: Partial<UISurface> = {}): UISurface {
    return { version: 1, id: "taylor", revision: 1, kind: "native", title: "Taylor explorer", fallback: "An approximation around zero.", status: "committed", state: { degree: 5, view: "curve" },
        nodes: [
            { id: "degree", type: "number", props: { label: "Degree", stateKey: "degree", min: 1, max: 12, value: 3 } },
            { id: "value", type: "markdown", props: { text: { $state: "degree" } } },
            { id: "compute", type: "button", props: { label: "Compute", actionId: "recompute" } },
        ], actions: [{ id: "recompute", label: "Recompute", handler_version: 1, input_schema: { type: "object", properties: { degree: { type: "number" } }, required: ["degree"], additionalProperties: false } }], ...overrides };
}
function wrap(surface: UISurface, cid: string | null = "conversation", busy = false) {
    return <ZCodeIntlProvider initialLocale="en-US"><TooltipProvider><UiWorkspaceProvider conversationId={cid} conversationBusy={busy} capabilities={capabilities}>
        <UISurfaceView surface={surface} /><UiCanvas />
    </UiWorkspaceProvider></TooltipProvider></ZCodeIntlProvider>;
}
function mount(surface: UISurface, cid: string | null = "conversation") {
    container = document.createElement("div"); document.body.append(container); root = createRoot(container);
    act(() => root!.render(wrap(surface, cid))); return container;
}
function setValue(input: HTMLInputElement, value: string) {
    act(() => { Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value")!.set!.call(input, value); input.dispatchEvent(new window.Event("input", { bubbles: true })); });
}
const tick = async () => { for (let count = 0; count < 4; count++) await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); }); };
function button(dom: Element, label: string) { return Array.from(dom.querySelectorAll<HTMLButtonElement>("button")).find((node) => node.textContent === label)!; }
function sse(surface: UISurface) { return new Response(`data: ${JSON.stringify({ type: "ui_commit", surface })}\n\ndata: {"type":"done"}\n\n`, { headers: { "content-type": "text/event-stream" } }); }

test("inline and canvas share control state and preserve focus through progressive revisions", () => {
    const surface = example(); const dom = mount(surface);
    const input = dom.querySelector<HTMLInputElement>('input[type="number"]')!;
    act(() => dom.querySelector<HTMLButtonElement>('button[title="Open in canvas"]')!.click());
    assert.equal(dom.querySelectorAll('input[type="number"]').length, 2);
    act(() => input.focus()); setValue(input, "7");
    assert.deepEqual(Array.from(dom.querySelectorAll<HTMLInputElement>('input[type="number"]')).map((node) => node.value), ["7", "7"]);
    assert.equal(document.activeElement, input);
    act(() => root!.render(wrap({ ...surface, revision: 2, title: "Taylor approximation", state: { degree: 6, view: "curve" } })));
    assert.equal(dom.querySelector<HTMLInputElement>('input[type="number"]'), input);
    assert.equal(input.value, "7"); assert.equal(document.activeElement, input);
});

test("preview controls are inert and a committed update enables the same nodes", () => {
    const dom = mount(example({ status: "preview" }));
    const input = dom.querySelector<HTMLInputElement>('input[type="number"]')!;
    assert.equal(input.disabled, true); assert.equal(button(dom, "Compute").disabled, true); assert.equal(button(dom, "Export").disabled, true);
    act(() => root!.render(wrap(example({ revision: 2 }))));
    assert.equal(dom.querySelector('input[type="number"]'), input); assert.equal(input.disabled, false);
    assert.equal(button(dom, "Compute").disabled, false); assert.equal(button(dom, "Export").disabled, false);
});

let inspectedWorkspace: ReturnType<typeof useUiWorkspace>;
function InspectWorkspace() { inspectedWorkspace = useUiWorkspace(); return <UiCanvas />; }
function authoritativeTurns(contents: AssistantContent[], busy = false) {
    const surfaces = contents.flatMap((content) => projectAssistantPresentation(content, content.status === "streaming").surfaces);
    return <ZCodeIntlProvider initialLocale="en-US"><TooltipProvider><UiWorkspaceProvider conversationId="conversation" conversationBusy={busy} capabilities={capabilities} surfaceIds={surfaces.map((surface) => surface.id)}>
        {surfaces.map((surface) => <UISurfaceView key={surface.id} surface={surface} />)}<InspectWorkspace />
    </UiWorkspaceProvider></TooltipProvider></ZCodeIntlProvider>;
}
function surfaceTurn(surfaces: UISurface[], streaming = false): AssistantContent {
    return { parts: surfaces.map((surface) => ({ type: "ui", surface })), model: "test", provider: "test", reasoning: "high", usage: {}, status: streaming ? "streaming" : "complete", presentation: streaming ? undefined : "final", plan: [], stats: { llm_calls: 1, tool_calls: 1 }, work: { status: streaming ? "running" : "complete", tool_count: 1, verification: { status: "not_requested", rounds: 0 }, steps: [], items: [] } };
}
function mountTurns(contents: AssistantContent[], busy = false) {
    container = document.createElement("div"); document.body.append(container); root = createRoot(container);
    act(() => root!.render(authoritativeTurns(contents, busy))); return container;
}

test("canonical completion withdraws an open native preview without removing saved earlier-turn surfaces", () => {
    const earlier = example({ id: "earlier", title: "Earlier result" });
    const preview = example({ status: "preview" });
    const dom = mountTurns([surfaceTurn([earlier]), surfaceTurn([preview], true)], true);
    act(() => dom.querySelector<HTMLButtonElement>('[data-ui-surface="taylor"] button[title="Open in canvas"]')!.click());
    assert.equal(dom.querySelectorAll('[data-ui-surface="taylor"]').length, 2);
    act(() => root!.render(authoritativeTurns([surfaceTurn([earlier]), surfaceTurn([])])));
    assert.equal(dom.querySelector('[data-ui-surface="taylor"]'), null);
    assert.equal(inspectedWorkspace.selection, null);
    assert.equal(inspectedWorkspace.records.taylor, undefined);
    assert.ok(inspectedWorkspace.records.earlier);
    assert.ok(dom.querySelector('[data-ui-surface="earlier"]'));
    act(() => { inspectedWorkspace.registerSurface(preview); inspectedWorkspace.openSurface(preview); });
    assert.equal(inspectedWorkspace.records.taylor, undefined, "stale registrations cannot restore a withdrawn preview");
    assert.equal(inspectedWorkspace.selection, null);
});

test("an accepted same-ID final revision preserves canvas selection, edited parameters and view state", () => {
    const initial = example();
    const dom = mountTurns([surfaceTurn([initial], true)], true);
    act(() => dom.querySelector<HTMLButtonElement>('button[title="Open in canvas"]')!.click());
    const input = dom.querySelector<HTMLInputElement>('input[type="number"]')!;
    setValue(input, "7");
    act(() => inspectedWorkspace.updateViewState(initial.id, "result-tabs", "second"));
    const accepted = { ...initial, revision: 2, title: "Checked result", state: { degree: 6, view: "curve" } };
    act(() => root!.render(authoritativeTurns([surfaceTurn([accepted])], true)));
    assert.deepEqual(inspectedWorkspace.selection, { kind: "surface", surfaceId: initial.id });
    assert.equal(inspectedWorkspace.records.taylor.surface.revision, 2);
    assert.equal(inspectedWorkspace.records.taylor.state.degree, 7);
    assert.equal(inspectedWorkspace.records.taylor.viewState["result-tabs"], "second");
    assert.equal(dom.querySelector<HTMLInputElement>('input[type="number"]'), input);
    assert.equal(dom.querySelectorAll('[data-ui-surface="taylor"][data-ui-revision="2"]').length, 2);
});

test("withdrawing another turn preserves an open earlier surface and an open file selection", () => {
    const earlier = example({ id: "earlier", title: "Earlier result" });
    const preview = example({ status: "preview" });
    const dom = mountTurns([surfaceTurn([earlier]), surfaceTurn([preview], true)], true);
    act(() => dom.querySelector<HTMLButtonElement>('[data-ui-surface="earlier"] button[title="Open in canvas"]')!.click());
    act(() => root!.render(authoritativeTurns([surfaceTurn([earlier]), surfaceTurn([])])));
    assert.deepEqual(inspectedWorkspace.selection, { kind: "surface", surfaceId: "earlier" });
    assert.equal(dom.querySelectorAll('[data-ui-surface="earlier"]').length, 2);
    const artifact = { kind: "markdown", name: "notes.md", data: "Saved notes" };
    act(() => inspectedWorkspace.openArtifact(artifact));
    act(() => root!.render(authoritativeTurns([surfaceTurn([])])));
    assert.equal(inspectedWorkspace.selection?.kind, "artifact");
    assert.ok(dom.textContent?.includes("Saved notes"));
});

test("withdrawal aborts a surface action and ignores its late result without disrupting accepted records", async () => {
    let respond!: (response: Response) => void;
    let signal: AbortSignal | undefined;
    globalThis.fetch = async (_url, init) => { signal = init?.signal as AbortSignal; return new Promise((resolve) => { respond = resolve; }); };
    const earlier = example({ id: "earlier", title: "Earlier result" });
    const dom = mountTurns([surfaceTurn([earlier]), surfaceTurn([example()])]);
    act(() => button(dom.querySelector('[data-ui-surface="taylor"]')!, "Compute").click()); await tick();
    act(() => root!.render(authoritativeTurns([surfaceTurn([earlier]), surfaceTurn([])])));
    assert.equal(signal?.aborted, true);
    await act(async () => respond(sse(example({ revision: 99, title: "Withdrawn computation" })))); await tick();
    assert.equal(inspectedWorkspace.records.taylor, undefined);
    assert.ok(inspectedWorkspace.records.earlier);
    assert.ok(!dom.textContent?.includes("Withdrawn computation"));
});

test("numeric controls permit clearing and typing negative values without saving an accidental zero", () => {
    const surface = example({ nodes: [{ id: "coefficient", type: "number", props: { label: "Coefficient", stateKey: "degree", min: -12, max: 12, value: 3 } }] });
    const dom = mount(surface);
    const input = dom.querySelector<HTMLInputElement>('input[type="number"]')!;
    act(() => input.focus()); setValue(input, "");
    assert.equal(input.value, "");
    act(() => dom.querySelector<HTMLButtonElement>('button[title="Open in canvas"]')!.click());
    assert.equal(dom.querySelectorAll<HTMLInputElement>('input[type="number"]')[1].value, "5");
    setValue(input, "-3");
    assert.deepEqual(Array.from(dom.querySelectorAll<HTMLInputElement>('input[type="number"]')).map((node) => node.value), ["-3", "-3"]);
});

test("control saves wait for the chat run and resume automatically when it finishes", async () => {
    const requests: unknown[] = [];
    globalThis.fetch = async (_url, init) => { requests.push(JSON.parse(String(init?.body))); return Response.json({ surface: example({ revision: 2, state: { degree: 8, view: "curve" }, computed_state: { degree: 5, view: "curve" } }) }); };
    const dom = mount(example());
    act(() => root!.render(wrap(example(), "conversation", true)));
    setValue(dom.querySelector<HTMLInputElement>('input[type="number"]')!, "8");
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 450)); });
    assert.equal(requests.length, 0); assert.equal(button(dom, "Compute").disabled, true);
    assert.ok(dom.textContent?.includes("Saves when the current task finishes"));
    act(() => root!.render(wrap(example(), "conversation", false))); await tick();
    assert.equal(requests.length, 1); assert.equal(button(dom, "Compute").disabled, false);
    assert.ok(dom.textContent?.includes("Parameters changed. Recompute to update this result."));
    assert.ok(!dom.textContent?.includes("Unsaved parameters"));
});

test("an action on one surface defers another surface's save and resumes it after completion", async () => {
    let finishAction!: (response: Response) => void;
    const requests: Array<{ url: string; body: Record<string, unknown> }> = [];
    const second = example({ id: "other", title: "Second result" });
    globalThis.fetch = async (url, init) => {
        const body = JSON.parse(String(init?.body)); requests.push({ url: String(url), body });
        if (String(url).endsWith("/actions")) return new Promise((resolve) => { finishAction = resolve; });
        return Response.json({ surface: { ...second, revision: 2, state: { degree: 9, view: "curve" } } });
    };
    container = document.createElement("div"); document.body.append(container); root = createRoot(container);
    act(() => root!.render(<ZCodeIntlProvider initialLocale="en-US"><TooltipProvider><UiWorkspaceProvider conversationId="conversation" capabilities={capabilities}>
        <UISurfaceView surface={example()} /><UISurfaceView surface={second} />
    </UiWorkspaceProvider></TooltipProvider></ZCodeIntlProvider>));
    const firstView = container.querySelector('[data-ui-surface="taylor"]')!;
    const secondView = container.querySelector('[data-ui-surface="other"]')!;
    act(() => button(firstView, "Compute").click()); await tick();
    setValue(secondView.querySelector<HTMLInputElement>('input[type="number"]')!, "9");
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 450)); });
    assert.equal(requests.length, 1); assert.equal(button(secondView, "Compute").disabled, true);
    await act(async () => finishAction(sse(example({ revision: 2 })))); await tick();
    assert.equal(requests.length, 2); assert.match(requests[1].url, /other\/state$/);
    assert.deepEqual(requests[1].body.state, { degree: 9, view: "curve" });
    assert.equal(button(secondView, "Compute").disabled, false);
});

test("stored computed parameters retain a stale-result notice after reload", () => {
    const dom = mount(example({ state: { degree: 8, view: "curve" }, computed_state: { degree: 5, view: "curve" } }));
    assert.ok(dom.textContent?.includes("Parameters changed. Recompute to update this result."));
});

test("tabs share their keyboard selection with the canvas", () => {
    const surface = example({ nodes: [{ id: "views", type: "tabs", props: { label: "Result views" }, children: [
        { id: "first", type: "markdown", props: { label: "First view", text: "First data" } },
        { id: "second", type: "markdown", props: { label: "Second view", text: "Second data" } },
    ] }] });
    const dom = mount(surface);
    const tab = dom.querySelector<HTMLButtonElement>('[role="tab"]')!;
    act(() => { tab.focus(); tab.dispatchEvent(new window.KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true, cancelable: true })); });
    act(() => dom.querySelector<HTMLButtonElement>('button[title="Open in canvas"]')!.click());
    assert.deepEqual(Array.from(dom.querySelectorAll('[role="tab"][aria-selected="true"]')).map((node) => node.textContent), ["Second view", "Second view"]);
});

test("disabled server capabilities render readable fallbacks without controls or executable frames", async () => {
    globalThis.fetch = async () => Response.json({ ...capabilities, native: false, mini_apps: false });
    container = document.createElement("div"); document.body.append(container); root = createRoot(container);
    act(() => root!.render(<ZCodeIntlProvider initialLocale="en-US"><TooltipProvider><UiWorkspaceProvider conversationId="conversation">
        <UISurfaceView surface={example()} /><UISurfaceView surface={example({ id: "mini", title: "Simulation", kind: "mini_app", app: { html: "<script>window.test=true</script>" }, nodes: [] })} />
    </UiWorkspaceProvider></TooltipProvider></ZCodeIntlProvider>));
    await tick();
    assert.equal(container.querySelector("iframe"), null); assert.equal(container.querySelector("input"), null);
    assert.ok(container.textContent?.includes("An approximation around zero.")); assert.ok(container.textContent?.includes("Interactive views are disabled"));
});

test("a failed progressive surface shows its fallback directly rather than hiding it in a disclosure", () => {
    const dom = mount(example({ status: "error", nodes: [], actions: [], fallback: "At degree 5 the error is below 0.002." }));
    assert.ok(dom.textContent?.includes("At degree 5 the error is below 0.002."));
    assert.equal(dom.querySelector("details"), null); assert.equal(dom.querySelector("input"), null);
    assert.ok(dom.textContent?.includes("could not be completed"));
});

test("an explicit computation flushes parameter edits then sends only declared inputs and adopts the committed revision", async () => {
    const requests: Array<{ url: string; body: Record<string, unknown> }> = [];
    globalThis.fetch = async (url, init) => {
        const body = JSON.parse(String(init?.body)); requests.push({ url: String(url), body });
        if (String(url).endsWith("/state")) return Response.json({ surface: example({ revision: 2, state: { degree: 7, view: "curve" } }) });
        return sse(example({ revision: 3, state: { degree: 7, view: "curve" }, title: "Computed approximation" }));
    };
    const dom = mount(example()); setValue(dom.querySelector<HTMLInputElement>('input[type="number"]')!, "7");
    act(() => button(dom, "Compute").click()); await tick();
    assert.equal(requests.length, 2); assert.match(requests[0].url, /\/state$/);
    assert.deepEqual(requests[0].body, { revision: 1, state: { degree: 7, view: "curve" } });
    assert.equal(requests[1].body.revision, 2); assert.deepEqual(requests[1].body.values, { degree: 7 });
    assert.match(String(requests[1].body.idempotency_key), /^[a-f0-9-]+$/);
    assert.equal(dom.querySelector("[data-ui-surface]")?.getAttribute("data-ui-revision"), "3");
    assert.ok(dom.textContent?.includes("Computed approximation")); assert.equal(button(dom, "Compute").disabled, false);
});

test("failed actions preserve the preceding result and expose a recoverable error", async () => {
    globalThis.fetch = async () => new Response('data: {"type":"ui_error","error":"Missing dataset.csv. Upload it again."}\n\n', { headers: { "content-type": "text/event-stream" } });
    const dom = mount(example()); act(() => button(dom, "Compute").click()); await tick();
    assert.equal(dom.querySelector("[data-ui-surface]")?.getAttribute("data-ui-revision"), "1");
    assert.equal(dom.querySelector<HTMLInputElement>('input[type="number"]')?.value, "5");
    assert.ok(dom.querySelector('[role="alert"]')?.textContent?.includes("Missing dataset.csv"));
    assert.ok(button(dom, "Reload saved result"));
});

test("a late action response cannot overwrite a different conversation", async () => {
    let respond!: (response: Response) => void;
    let signal: AbortSignal | undefined;
    globalThis.fetch = async (_url, init) => { signal = init?.signal as AbortSignal; return new Promise((resolve) => { respond = resolve; }); };
    const dom = mount(example()); act(() => button(dom, "Compute").click()); await tick();
    act(() => root!.render(wrap(example({ title: "Another conversation", state: { degree: 2 } }), "second")));
    assert.equal(signal?.aborted, true);
    await act(async () => respond(sse(example({ revision: 99, title: "Stale computation" })))); await tick();
    assert.ok(dom.textContent?.includes("Another conversation")); assert.ok(!dom.textContent?.includes("Stale computation"));
    assert.equal(dom.querySelector<HTMLInputElement>('input[type="number"]')?.value, "2");
});

test("mini-app messages require the exact frame window as well as its channel", () => {
    const changes: unknown[] = [];
    container = document.createElement("div"); document.body.append(container); root = createRoot(container);
    act(() => root!.render(<MiniAppFrame surfaceId="app" title="Local simulation" html="<p>Simulation</p>" state={{ n: 1 }} onStateChange={(state) => changes.push(state)} />));
    const frame = container.querySelector("iframe")!;
    assert.equal(frame.getAttribute("sandbox"), "allow-scripts"); assert.equal(frame.hasAttribute("srcdoc"), false);
    const channel = new URLSearchParams(new URL(frame.src).hash.slice(1)).get("channel");
    const data = { type: "matopt:state", version: 1, channel, surfaceId: "app", state: { n: 2 } };
    act(() => window.dispatchEvent(new window.MessageEvent("message", { data, source: window })));
    assert.deepEqual(changes, []);
    act(() => window.dispatchEvent(new window.MessageEvent("message", { data, source: frame.contentWindow! })));
    assert.deepEqual(changes, [{ n: 2 }]);
});

test("legacy HTML artifacts never receive a same-origin active iframe or unrestricted file URL", async () => {
    const fetches: string[] = [];
    globalThis.fetch = async (url) => { fetches.push(String(url)); return new Response("<h1>Safe frame</h1><script>window.example=1</script>"); };
    container = document.createElement("div"); document.body.append(container); root = createRoot(container);
    act(() => root!.render(<ArtifactView artifact={{ kind: "html_file", name: "app.html", path: "app.html" }} conversationId="one" />));
    await tick();
    assert.equal(fetches[0], "http://localhost/api/files/one/app.html");
    const frame = container.querySelector("iframe")!;
    assert.equal(new URL(frame.src).origin, "http://localhost:3001"); assert.equal(frame.getAttribute("sandbox"), "allow-scripts"); assert.equal(frame.hasAttribute("srcdoc"), false);
    assert.equal(safeHtmlArtifactSource("/api/conversations", "one"), null);
    assert.equal(safeHtmlArtifactSource("/api/files/two/app.html", "one"), null);
    assert.equal(safeHtmlArtifactSource("https://example.com/steal", "one"), null);
});
