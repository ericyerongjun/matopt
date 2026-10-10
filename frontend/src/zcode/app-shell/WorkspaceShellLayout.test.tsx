import "../../../test/jsdom-env.mjs";
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { act, useCallback, useState, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { ZCodeIntlProvider } from "@/i18n/IntlProvider.js";
import { TooltipProvider } from "@/components/ui/tooltip.js";
import { WorkspaceShellLayout } from "./WorkspaceShellLayout.tsx";
import { WorkspaceSidebar } from "@/WorkspaceSidebar.js";
import { ControlHintTooltip } from "@/ControlHintTooltip.js";
import { Button } from "@/components/ui/button.js";

// Only the mobile host seam is exercised here; desktop split geometry is checked in browser QA.
Object.defineProperty(window, "matchMedia", { configurable: true, value: () => ({ matches: true, addEventListener() {}, removeEventListener() {} }) });
Object.defineProperty(globalThis, "localStorage", { configurable: true, value: window.localStorage });
class ResizeObserverStub { observe() {} unobserve() {} disconnect() {} }
Object.defineProperty(globalThis, "ResizeObserver", { configurable: true, value: ResizeObserverStub });
Object.defineProperty(window, "ResizeObserver", { configurable: true, value: ResizeObserverStub });

let root: Root | null = null;
let container: HTMLDivElement | null = null;
afterEach(() => { act(() => root?.unmount()); root = null; container?.remove(); container = null; window.localStorage.clear(); });
function mount(element: ReactNode) {
  container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container);
  render(element);
}
function render(element: ReactNode) { act(() => root?.render(<ZCodeIntlProvider initialLocale="en-US"><TooltipProvider>{element}</TooltipProvider></ZCodeIntlProvider>)); }
const shell = (preview: boolean, sidebar = false, onDismissSidebar?: () => void) => <WorkspaceShellLayout isSidebarVisible={sidebar} isSidePaneVisible={preview} onDismissSidebar={onDismissSidebar}
  sidebar={<p>History sidebar</p>} header={<p>Agent header</p>} conversation={<p>Agent conversation</p>} sidePane={<p>Workspace preview</p>} topOverlay={<p>Native caption tools</p>} />;

test("mobile host renders one native pane and never a cramped horizontal split", () => {
  mount(shell(false));
  assert.match(container!.textContent!, /Agent conversation/);
  assert.doesNotMatch(container!.textContent!, /Workspace preview/);
  assert.equal(container!.querySelectorAll('[role="separator"]').length, 0);
  assert.ok(container!.querySelector('[data-workspace-conversation-frame]')?.classList.contains("bg-background"));
  render(shell(true));
  assert.match(container!.textContent!, /Workspace preview/);
  assert.doesNotMatch(container!.textContent!, /Agent conversation|Native caption tools/);
  assert.equal(container!.querySelectorAll('[role="separator"]').length, 0);
});

test("mobile sidebar overlays the native frame and collapsed history cannot receive focus", () => {
  mount(shell(false, true));
  const sidebar = container!.querySelector('[data-workspace-sidebar-panel]')!;
  assert.ok(sidebar.classList.contains("absolute"));
  assert.ok(sidebar.classList.contains("max-w-[85%]"));
  assert.match(sidebar.textContent!, /History sidebar/);
  render(shell(false, false));
  assert.equal(sidebar.querySelector("aside")?.getAttribute("aria-hidden"), "true");
  assert.equal(sidebar.textContent, "");
});

test("desktop-to-mobile preview keeps a sidebar dismissal control and Escape available", () => {
  let dismissed = 0;
  mount(shell(true, true, () => dismissed++));
  assert.match(container!.querySelector('[data-workspace-sidebar-panel]')!.textContent!, /Native caption tools/);
  assert.ok(container!.querySelector('#content')!.hasAttribute("inert"));
  act(() => container!.querySelector<HTMLButtonElement>('button[aria-label="Close sidebar"]')!.click());
  assert.equal(dismissed, 1);
  act(() => window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
  assert.equal(dismissed, 2);
});

function EscapeDrawer({ stopAtChild = false }: { stopAtChild?: boolean }) {
  const [visible, setVisible] = useState(true);
  const dismiss = useCallback(() => setVisible(false), []);
  return <WorkspaceShellLayout isSidebarVisible={visible} isSidePaneVisible onDismissSidebar={dismiss}
    sidebar={<p>History sidebar</p>} header={<p>Agent header</p>} conversation={<p>Agent conversation</p>} sidePane={<p>Workspace preview</p>}
    topOverlay={<ControlHintTooltip open title="Toggle sidebar"><Button aria-label="Toggle sidebar" onKeyDown={stopAtChild ? (event) => { event.preventDefault(); event.stopPropagation(); } : undefined}>Toggle</Button></ControlHintTooltip>} />;
}

for (const stopAt of ["child", "document"] as const) {
  test(`one Escape dismisses a drawer with native caption tooltip despite ${stopAt} interception`, () => {
    const intercept = (event: KeyboardEvent) => { event.preventDefault(); event.stopPropagation(); };
    if (stopAt === "document") document.addEventListener("keydown", intercept, true);
    try {
      mount(<EscapeDrawer stopAtChild={stopAt === "child"} />);
      assert.ok(container!.querySelector('#content')!.hasAttribute("inert"));
      const toggle = container!.querySelector<HTMLButtonElement>('button[aria-label="Toggle sidebar"]')!;
      act(() => { toggle.focus(); toggle.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true })); });
      assert.equal(container!.querySelector('#sidebar')!.getAttribute("role"), null);
      assert.equal(container!.querySelector('#content')!.hasAttribute("inert"), false);
    } finally { document.removeEventListener("keydown", intercept, true); }
  });
}

for (const role of ["menu", "dialog"] as const) {
  test(`nested open ${role} handles Escape first, then the mobile drawer dismisses`, () => {
    mount(<EscapeDrawer />);
    const nested = document.createElement("div"); nested.setAttribute("role", role); nested.dataset.state = "open";
    nested.addEventListener("keydown", (event) => { event.preventDefault(); event.stopPropagation(); nested.remove(); });
    document.body.appendChild(nested);
    act(() => nested.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true })));
    assert.equal(container!.querySelector('#sidebar')!.getAttribute("role"), "dialog");
    const toggle = container!.querySelector<HTMLButtonElement>('button[aria-label="Toggle sidebar"]')!;
    act(() => toggle.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true })));
    assert.equal(container!.querySelector('#sidebar')!.getAttribute("role"), null);
    assert.equal(container!.querySelector('#content')!.hasAttribute("inert"), false);
  });
}

test("native new-task source has one keyboard target and session rows select their real IDs", () => {
  let creates = 0; const selected: string[] = [];
  mount(<WorkspaceSidebar conversations={[{ id: "session-a", title: "Optimization experiment", created_at: 1, updated_at: 1, provider: null, model: null, reasoning: null, meta: {} }]}
    activeId={null} onCreateTask={() => creates++} onSelectTask={(id) => selected.push(id)} onRenameTask={async () => {}} onDeleteTask={async () => {}} onOpenFiles={() => {}} />);
  const newTask = container!.querySelector<HTMLElement>('[role="button"]')!;
  assert.equal(container!.querySelectorAll('[role="button"]').length, 1);
  assert.ok(newTask.classList.contains("rounded-lg"));
  act(() => newTask.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, key: "Enter" })));
  assert.equal(creates, 1);
  const row = container!.querySelector<HTMLElement>('li[data-task-item-key="session-a"]')!;
  act(() => row.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, key: " " })));
  assert.deepEqual(selected, ["session-a"]);
});
