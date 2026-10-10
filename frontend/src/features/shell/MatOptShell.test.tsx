import "../../../test/jsdom-env.mjs";
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MatOptShell } from "./MatOptShell.tsx";

let root: Root | null = null;
let container: HTMLDivElement | null = null;
let width = 1280;
Object.defineProperty(window, "matchMedia", { configurable: true, value: (query: string) => ({
  matches: width <= Number(query.match(/max-width: (\d+)/)?.[1] ?? 0), media: query,
  addEventListener() {}, removeEventListener() {},
}) });
afterEach(() => {
  act(() => root?.unmount()); root = null; container?.remove(); container = null; width = 1280;
});

function Harness({ remountOpener = false }: { remountOpener?: boolean }) {
  const [sidebar, setSidebar] = useState(false);
  const [pane, setPane] = useState(false);
  return <MatOptShell sidebarOpen={sidebar} paneOpen={pane} onCloseSidebar={() => setSidebar(false)} onClosePane={() => setPane(false)}
    sidebar={<><button id="sidebar-first" onClick={() => setSidebar(false)}>Close sidebar</button><button id="sidebar-last">Last action</button></>}
    header={(!remountOpener || !sidebar) && <button id="open-sidebar" aria-label="Open sidebar" onClick={() => setSidebar(true)}>Open sidebar</button>}
    conversation={<><div data-testid="chat-view"><p>Keep this conversation mounted.</p></div><textarea id="draft" defaultValue="Keep my draft" /><button id="open-pane" onClick={() => setPane(true)}>Open in canvas</button></>}
    sidePane={<><h2 data-pane-heading tabIndex={-1}>Canvas</h2><button id="close-pane" onClick={() => setPane(false)}>Back to chat</button></>} />;
}
function render(remountOpener = false) {
  container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container);
  act(() => root?.render(<Harness remountOpener={remountOpener} />));
}
function button(id: string) { return document.getElementById(id) as HTMLButtonElement; }
function press(id: string) { act(() => { button(id).focus(); button(id).click(); }); }

test("canvas starts closed and opening it preserves the conversation, draft and scroll", () => {
  render();
  const chat = document.querySelector('[data-testid="chat-view"]') as HTMLElement;
  const draft = document.getElementById("draft");
  chat.scrollTop = 80;
  assert.equal(document.querySelector<HTMLElement>(".matopt-side-pane")?.hidden, true);
  press("open-pane");
  assert.equal(document.querySelector<HTMLElement>(".matopt-side-pane")?.hidden, false);
  assert.equal(document.getElementById("draft"), draft);
  assert.equal(document.querySelector('[data-testid="chat-view"]'), chat);
  assert.equal(chat.scrollTop, 80);
  press("close-pane");
  assert.equal(document.activeElement?.id, "open-pane");
});

test("mobile sidebar traps keyboard focus and returns it to the opener", () => {
  width = 390; render(); press("open-sidebar");
  assert.equal(document.querySelector(".matopt-sidebar")?.getAttribute("aria-modal"), "true");
  assert.ok(document.querySelector(".matopt-main")?.hasAttribute("inert"));
  assert.equal(document.activeElement?.id, "sidebar-first");
  act(() => {
    button("sidebar-last").focus();
    button("sidebar-last").dispatchEvent(new window.KeyboardEvent("keydown", { key: "Tab", bubbles: true, cancelable: true }));
  });
  assert.equal(document.activeElement?.id, "sidebar-first");
  act(() => button("sidebar-first").dispatchEvent(new window.KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true })));
  assert.equal(document.querySelector<HTMLElement>(".matopt-sidebar")?.hidden, true);
  assert.equal(document.activeElement?.id, "open-sidebar");
  assert.equal(document.querySelector(".matopt-main")?.hasAttribute("inert"), false);
});

test("mobile canvas focuses its heading and keeps hidden chat controls inert until returning", () => {
  width = 390; render(); press("open-pane");
  assert.equal(document.activeElement?.textContent, "Canvas");
  assert.ok(document.querySelector(".matopt-conversation")?.hasAttribute("inert"));
  press("close-pane");
  assert.equal(document.querySelector(".matopt-conversation")?.hasAttribute("inert"), false);
  assert.equal(document.activeElement?.id, "open-pane");
  assert.equal((document.getElementById("draft") as HTMLTextAreaElement).value, "Keep my draft");
});

test("mobile sidebar restores focus when the compact header remounts its opener", () => {
  width = 390; render(true); press("open-sidebar");
  assert.equal(document.getElementById("open-sidebar"), null);
  act(() => button("sidebar-first").dispatchEvent(new window.KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true })));
  assert.equal(document.activeElement, document.getElementById("open-sidebar"));
});

function PersistentHarness() {
  const [pane, setPane] = useState(true);
  const [inspector, setInspector] = useState(false);
  return <MatOptShell sidebarOpen={false} paneOpen={pane} inspectorOpen={inspector} onCloseSidebar={() => {}} onClosePane={() => setPane(false)} onCloseInspector={() => setInspector(false)}
    sidebar={null} header={<><button id="toggle-workspace" onClick={() => setPane((value) => !value)}>Workspace</button><button id="open-activity" onClick={() => setInspector(true)}>Activity</button></>}
    conversation={<textarea id="conversation-draft" defaultValue="Draft survives" />}
    sidePane={<><h2 data-pane-heading tabIndex={-1}>Workspace</h2><input id="result-value" defaultValue="42" /><div id="result-scroll">Long result</div></>}
    inspector={<><h2 data-pane-heading tabIndex={-1}>Activity</h2><button id="close-activity" onClick={() => setInspector(false)}>Close activity</button></>} />;
}
function persistent() {
  container = document.createElement('div'); document.body.append(container); root = createRoot(container);
  act(() => root?.render(<PersistentHarness />));
}

test('activity inspector and closing the workspace retain the exact result nodes, edits and scroll', () => {
  persistent();
  const result = document.getElementById('result-value') as HTMLInputElement;
  const scroll = document.getElementById('result-scroll')!;
  result.value = '84'; scroll.scrollTop = 125;
  press('open-activity');
  assert.equal(document.activeElement?.textContent, 'Activity');
  assert.equal(document.querySelector<HTMLElement>('.matopt-side-pane')?.hidden, true);
  assert.ok(document.querySelector('.matopt-side-pane')?.hasAttribute('inert'));
  assert.equal(document.getElementById('result-value'), result);
  press('close-activity'); assert.equal(document.activeElement?.id, 'open-activity');
  press('toggle-workspace'); assert.equal(document.querySelector<HTMLElement>('.matopt-side-pane')?.hidden, true);
  press('toggle-workspace');
  assert.equal(document.getElementById('result-value'), result); assert.equal(result.value, '84'); assert.equal(scroll.scrollTop, 125);
});

test('splitter keyboard controls are bounded and saved for a remounted workspace', () => {
  window.localStorage.removeItem('matopt.workspaceWidth'); persistent();
  const splitter = document.querySelector<HTMLElement>('[role=separator]')!;
  const key = (value: string) => act(() => splitter.dispatchEvent(new window.KeyboardEvent('keydown', { key: value, bubbles: true, cancelable: true })));
  key('ArrowLeft'); assert.equal(splitter.getAttribute('aria-valuenow'), '52');
  key('End'); key('ArrowLeft'); assert.equal(splitter.getAttribute('aria-valuenow'), '68');
  key('Home'); key('ArrowRight'); assert.equal(splitter.getAttribute('aria-valuenow'), '32');
  assert.equal(window.localStorage.getItem('matopt.workspaceWidth'), '32');
  act(() => root?.unmount()); root = createRoot(container!); act(() => root?.render(<PersistentHarness />));
  assert.equal(document.querySelector('[role=separator]')?.getAttribute('aria-valuenow'), '32');
  window.localStorage.removeItem('matopt.workspaceWidth');
});

test('narrow activity keeps the selected workspace inert and restores it on Escape', () => {
  width = 554; persistent(); press('open-activity');
  assert.equal(document.querySelector('.matopt-shell')?.getAttribute('data-compact'), 'true');
  assert.ok(document.querySelector('.matopt-side-pane')?.hasAttribute('inert'));
  assert.ok(document.querySelector('.matopt-conversation')?.hasAttribute('inert'));
  assert.equal(document.querySelector<HTMLElement>('[role=separator]')?.hidden, true);
  act(() => document.querySelector('.matopt-inspector')?.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })));
  assert.equal(document.querySelector<HTMLElement>('.matopt-inspector')?.hidden, true);
  assert.equal(document.querySelector('.matopt-side-pane')?.hasAttribute('inert'), false);
  assert.equal(document.querySelector<HTMLElement>('.matopt-side-pane')?.hidden, false);
});
