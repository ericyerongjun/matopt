import "../../../test/jsdom-env.mjs";
import "../../../test/diffs-dom-env.mjs";
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { TooltipProvider } from "../../zcode/components/ui/tooltip.js";
import { ZCodeIntlProvider } from "../../zcode/i18n/IntlProvider.js";
import { WorkspaceWorkbench } from "./WorkspaceWorkbench.tsx";
import { FilePreview } from "./FilePreview.tsx";
import { FILE_VIEWER_MAX_TEXT_BYTES } from "./preview.ts";
import { useWorkspaceFiles } from "../../hooks/useWorkspaceFiles.ts";
import type { WorkspaceFile } from "../../types/protocol.ts";

const originalFetch = globalThis.fetch;
const originalResizeObserver = Object.getOwnPropertyDescriptor(globalThis, 'ResizeObserver')!;
let root: Root | null = null, container: HTMLDivElement | null = null;
let shortViewport = false;
Object.defineProperty(window, "matchMedia", { configurable: true, value: (query: string) => ({ matches: shortViewport, media: query }) });
afterEach(() => { act(() => root?.unmount()); root = null; container?.remove(); container = null; globalThis.fetch = originalFetch; Object.defineProperty(globalThis, 'ResizeObserver', originalResizeObserver); shortViewport = false; });
function mount(element: ReactNode) { container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container); act(() => root?.render(<ZCodeIntlProvider initialLocale="en-US"><TooltipProvider>{element}</TooltipProvider></ZCodeIntlProvider>)); }
const file = (path: string, url = `/api/files/one/${path}`): WorkspaceFile => ({ path, name: path.split("/").pop()!, kind: "text", size: 20, mtime: 1, url });
function fileClick(name: string) {
    const button = [...document.querySelectorAll<HTMLButtonElement>('[role="treeitem"]')].find((item) => item.textContent === name);
    assert.ok(button); act(() => button.click());
}
async function settle() { await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); }); }
function visibleSource() {
 const preview=document.querySelector('.file-preview__source');
 return [...(preview?.querySelectorAll('diffs-container') ?? [])].map(element => element.shadowRoot?.textContent ?? element.textContent ?? '').join('');
}
async function sourceContains(text: string) {
 for (let tries=0; tries<100 && !visibleSource().includes(text); tries++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)); });
 assert.ok(visibleSource().includes(text), `native source contains ${text}: ${visibleSource().slice(-200)}`);
}

test("document tabs open actual source, close inactive tabs without changing selection, and download originals", async () => {
    globalThis.fetch = async (url) => new Response(`content from ${url}`);
    const files = [file("data/a.txt"), file("data/b.txt")];
    mount(<WorkspaceWorkbench conversationId="one" files={files} onRefresh={() => {}} />);
    fileClick("a.txt"); await settle();
    await sourceContains("data/a.txt");
    fileClick("b.txt"); await settle();
    assert.equal(document.querySelectorAll('[role="tab"]').length, 2);
    const firstTab = document.querySelector(".workbench-document-tab")!;
    act(() => firstTab.dispatchEvent(new MouseEvent("auxclick", { bubbles: true, button: 1 })));
    assert.equal(document.querySelectorAll('[role="tab"]').length, 1);
    assert.match(document.querySelector('[role="tab"][aria-selected="true"]')?.textContent ?? "", /b.txt/);
    assert.equal(document.querySelector<HTMLAnchorElement>('a[aria-label="Download b.txt"]')?.getAttribute("href"), "/api/files/one/data/b.txt?download=true");
    act(() => root?.render(<WorkspaceWorkbench conversationId="two" files={files} onRefresh={() => {}} />));
    assert.equal(document.querySelectorAll('[role="tab"]').length, 0);
    assert.equal(document.querySelector(".file-preview__source"), null);
});

test("browsing files retains the selected document and returns without another fetch", async () => {
    shortViewport = true;
    const requests: string[] = [];
    globalThis.fetch = async (url) => { requests.push(String(url)); return new Response("value,result\nx,42"); };
    mount(<WorkspaceWorkbench conversationId="one" files={[file("table.csv")]} onRefresh={() => {}} />);
    fileClick("table.csv"); await settle();
    const preview = document.querySelector(".file-preview")!;
    assert.match(preview.textContent ?? "", /x42/);
    assert.equal(document.activeElement, document.querySelector('[role="tab"][aria-selected="true"]'));
    const browse = document.querySelector<HTMLButtonElement>('[aria-label="Browse workspace files"]')!;
    act(() => browse.click());
    assert.equal(document.querySelector('.matopt-workbench')?.getAttribute('data-browsing-files'), 'true');
    assert.equal(document.querySelector('[role="tab"][aria-selected="true"]')?.textContent, 'table.csv');
    assert.equal(document.querySelector(".file-preview"), preview);
    assert.equal(document.activeElement, document.querySelector('[role="treeitem"][aria-selected="true"]'));
    fileClick("table.csv"); await settle();
    assert.equal(document.querySelector('.matopt-workbench')?.getAttribute('data-browsing-files'), 'false');
    assert.equal(document.querySelector(".file-preview"), preview);
    assert.equal(document.activeElement, document.querySelector('[role="tab"][aria-selected="true"]'));
    assert.deepEqual(requests, ["/api/files/one/table.csv"]);
    act(() => document.querySelector<HTMLButtonElement>('[aria-label="Close table.csv"]')!.click());
    assert.equal(document.querySelector('.matopt-workbench')?.getAttribute('data-has-document'), 'false');
    assert.equal(document.activeElement, document.querySelector('[role="treeitem"]'));
});

test("a constrained desktop dock restores focus and document state when browsing or resizing", async () => {
    let resize: ResizeObserverCallback | undefined;
    let observed: Element | undefined;
    let disconnected = false;
    Object.defineProperty(globalThis, 'ResizeObserver', { configurable: true, value: class {
        constructor(callback: ResizeObserverCallback) { resize = callback; }
        observe(element: Element) { observed = element; }
        unobserve() {}
        disconnect() { disconnected = true; }
    } });
    const notifyWidth = (width: number) => act(() => resize?.([{ contentRect: { width } } as ResizeObserverEntry], {} as ResizeObserver));
    const requests: string[] = [];
    globalThis.fetch = async (url) => { requests.push(String(url)); return new Response("value,result\nx,42"); };
    mount(<WorkspaceWorkbench conversationId="one" files={[file("table.csv")]} onRefresh={() => {}} />);
    assert.equal(observed, document.querySelector('.matopt-workbench'));
    notifyWidth(900);
    fileClick("table.csv"); await settle();
    const preview = document.querySelector('.file-preview')!;
    const activeTab = document.querySelector<HTMLElement>('[role="tab"][aria-selected="true"]')!;
    const tree = document.querySelector<HTMLElement>('[role="treeitem"][aria-selected="true"]')!;
    act(() => tree.focus());
    notifyWidth(377);
    assert.equal(document.activeElement, activeTab, 'a newly hidden explorer must not retain keyboard focus');
    act(() => document.querySelector<HTMLButtonElement>('[aria-label="Browse workspace files"]')!.click());
    assert.equal(document.activeElement, tree);
    assert.equal(document.querySelector('.file-preview'), preview);
    act(() => document.querySelector<HTMLButtonElement>('[aria-label="Return to table.csv"]')!.click());
    assert.equal(document.activeElement, activeTab);
    assert.equal(document.querySelector('.matopt-workbench')?.getAttribute('data-browsing-files'), 'false');
    notifyWidth(0);
    act(() => document.querySelector<HTMLButtonElement>('[aria-label="Browse workspace files"]')!.click());
    assert.equal(document.activeElement, tree, 'temporarily hiding the Files tab preserves its compact layout');
    assert.equal(document.querySelector('.file-preview'), preview);
    assert.deepEqual(requests, ["/api/files/one/table.csv"]);
    act(() => root?.unmount()); root = null;
    assert.equal(disconnected, true);
});

test("file preview aborts stale requests and ignores old responses after a task switch", async () => {
    const pending: Array<{ signal?: AbortSignal | null; resolve: (value: Response) => void }> = [];
    globalThis.fetch = async (_url, options) => new Promise<Response>((resolve) => pending.push({ signal: options?.signal, resolve }));
    mount(<FilePreview file={file("a.txt")} conversationId="one" />);
    assert.equal(pending.length, 1);
    act(() => root?.render(<ZCodeIntlProvider initialLocale="en-US"><TooltipProvider><FilePreview file={file("b.txt")} conversationId="two" /></TooltipProvider></ZCodeIntlProvider>));
    assert.equal(pending[0].signal?.aborted, true); assert.equal(pending.length, 2);
    pending[0].resolve(new Response("STALE FIRST TASK")); await settle();
    assert.doesNotMatch(document.body.textContent ?? "", /STALE FIRST TASK/);
    pending[1].resolve(new Response("CURRENT TASK")); await settle();
    await sourceContains("CURRENT TASK");
    assert.doesNotMatch(visibleSource(), /STALE FIRST TASK/);
});

test("PDF pages use bounded image previews, navigate and report unreadable pages", async () => {
    const requests: string[] = [];
    globalThis.fetch = async (url) => {
        requests.push(String(url));
        return String(url).endsWith("page=1")
            ? new Response(new Blob(["page one"], { type: "image/png" }), { headers: { "X-PDF-Pages": "2" } })
            : Response.json({ detail: "This PDF page could not be rendered." }, { status: 422 });
    };
    mount(<FilePreview file={file("uploads/scan example.pdf")} conversationId="one" />);
    await settle();
    assert.equal(document.querySelector("iframe"), null);
    assert.equal(document.querySelector(".file-preview__pdf img")?.getAttribute("alt"), "scan example.pdf, page 1 of 2");
    const buttons = [...document.querySelectorAll<HTMLButtonElement>(".file-preview__pdf-pages button")];
    assert.equal(buttons[0].disabled, true); assert.equal(buttons[1].disabled, false);
    act(() => buttons[1].click()); await settle();
    assert.deepEqual(requests, ["/api/files/one/pdf-preview/uploads/scan%20example.pdf?page=1", "/api/files/one/pdf-preview/uploads/scan%20example.pdf?page=2"]);
    assert.match(document.querySelector('[role="alert"]')?.textContent ?? "", /could not be rendered/);
    assert.equal(document.querySelector(".file-preview__pdf img"), null);
    act(() => buttons[0].click()); await settle();
    assert.ok(document.querySelector(".file-preview__pdf img"));
});

test("HTML files open a sandboxed preview with one Preview/Code switch and preserve the original source", async () => {
    const source = '<!doctype html><html><body><h1>Dashboard</h1><script>window.previewOnly = true;</script></body></html>';
    const requests: string[] = [];
    globalThis.fetch = async (url) => { requests.push(String(url)); return new Response(source); };
    mount(<FilePreview file={file("results/dashboard.html")} conversationId="one" />);
    await settle();
    const rich = document.querySelector('[data-rich-source="html"]')!;
    assert.ok(rich); assert.equal(rich.getAttribute("data-rich-view"), "preview");
    const frame = rich.querySelector("iframe")!;
    assert.ok(frame); assert.equal(frame.getAttribute("sandbox"), "allow-scripts");
    assert.notEqual(new URL(frame.src).origin, window.location.origin);
    assert.equal(frame.hasAttribute("srcdoc"), false);
    assert.equal(document.querySelectorAll(".file-preview__modes button").length, 0);
    const views = Array.from(rich.querySelectorAll<HTMLButtonElement>('[role="group"][aria-label="HTML view"] button'));
    assert.deepEqual(views.map((button) => button.textContent), ["Preview", "Code"]);
    act(() => views[1].click());
    assert.equal(rich.getAttribute("data-rich-view"), "code");
    assert.equal(rich.querySelector("iframe"), null);
    assert.equal(rich.querySelector('pre[aria-label="HTML source"] code')?.textContent, source);
    act(() => views[0].click());
    assert.ok(rich.querySelector("iframe"));
    assert.deepEqual(requests, ["/api/files/one/results/dashboard.html"]);
});

test("truncated HTML remains readable source without an executable preview and downloads the complete original", async () => {
    const source = `<!doctype html><html><body>${"x".repeat(FILE_VIEWER_MAX_TEXT_BYTES)}</body></html>`;
    let cancelled = false;
    globalThis.fetch = async () => new Response(new ReadableStream({
        start(controller) { controller.enqueue(new TextEncoder().encode(source)); },
        cancel() { cancelled = true; },
    }));
    mount(<FilePreview file={{ ...file("results/large.htm"), size: source.length }} conversationId="one" />);
    await settle();
    const rich = document.querySelector('[data-rich-source="html"]')!;
    assert.ok(rich); assert.equal(rich.getAttribute("data-rich-view"), "code");
    assert.equal(rich.querySelector("iframe"), null);
    assert.equal(rich.querySelector<HTMLButtonElement>('[role="group"] button')?.disabled, true);
    assert.equal(rich.querySelector('pre[aria-label="HTML source"] code')?.textContent, source.slice(0, FILE_VIEWER_MAX_TEXT_BYTES));
    assert.equal(cancelled, true);
    assert.match(document.querySelector(".file-preview__footer")?.textContent ?? "", /Preview limited to 256.0 KB/);
    assert.equal(document.querySelector<HTMLAnchorElement>(".file-preview__footer a")?.getAttribute("href"), "/api/files/one/results/large.htm?download=true");
});

test("workspace listing aborts old tasks and keeps an out-of-order response out of the current task", async () => {
    const pending: Array<{ signal?: AbortSignal | null; resolve: (value: Response) => void }> = [];
    globalThis.fetch = async (_url, options) => new Promise<Response>((resolve) => pending.push({ signal: options?.signal, resolve }));
    function Listing({ id }: { id: string }) { const { files } = useWorkspaceFiles(id); return <div>{files.map((entry) => entry.name).join(",")}</div>; }
    mount(<Listing id="one" />);
    act(() => root?.render(<Listing id="two" />));
    assert.equal(pending[0].signal?.aborted, true);
    pending[1].resolve(Response.json({ files: [file("current.txt")] })); await settle();
    assert.match(document.body.textContent ?? "", /current.txt/);
    pending[0].resolve(Response.json({ files: [file("stale.txt")] })); await settle();
    assert.doesNotMatch(document.body.textContent ?? "", /stale.txt/);
});

test("CSV renders a real table and explorer folders collapse", async () => {
    globalThis.fetch = async () => new Response("variable,value\nx,42\ny,7");
    const csv = { ...file("results/table.csv"), kind: "text" };
    mount(<WorkspaceWorkbench conversationId="one" files={[csv]} onRefresh={() => {}} />);
    fileClick("table.csv"); await settle();
    assert.equal(document.querySelectorAll(".file-preview__table tbody tr").length, 2);
    assert.match(document.querySelector(".file-preview__table")?.textContent ?? "", /variablevalue.*x42.*y7/);
    const collapse = document.querySelector<HTMLButtonElement>('button[aria-label="Collapse folders"]')!;
    act(() => collapse.click());
    assert.equal(document.querySelectorAll('[role="treeitem"]').length, 1);
    assert.match(document.querySelector('[role="treeitem"]')?.textContent ?? "", /results/);
});
