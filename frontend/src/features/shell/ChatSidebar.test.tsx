import "../../../test/jsdom-env.mjs";
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { act, type ComponentProps } from "react";
import { createRoot, type Root } from "react-dom/client";
import { ChatSidebar } from "./ChatSidebar.tsx";
import { ZCodeIntlProvider } from "../../zcode/i18n/IntlProvider.tsx";
import type { Conversation } from "../../types/protocol.ts";

Object.defineProperty(globalThis, "NodeFilter", { value: window.NodeFilter, configurable: true });

let root: Root | null = null;
let container: HTMLDivElement | null = null;
afterEach(() => { act(() => root?.unmount()); root = null; container?.remove(); container = null; });
type Props = ComponentProps<typeof ChatSidebar>;
const defaults = { activeId: null, loading: false, onNewChat() {}, onSelect() {}, async onRename() {}, async onDelete() {}, onFiles() {}, onClose() {} };
function render(props: Partial<Props> & Pick<Props, "conversations">) {
  if (!container) { container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container); }
  act(() => root?.render(<ZCodeIntlProvider initialLocale="en-US"><ChatSidebar {...defaults} {...props} /></ZCodeIntlProvider>));
}
function chat(id: string, days = 0, running = false): Conversation {
  const date = new Date(); date.setHours(12, 0, 0, 0); date.setDate(date.getDate() - days);
  return { id, title: `${id} analysis`, updated_at: +date / 1000, created_at: +date / 1000, provider: null, model: null, reasoning: null, meta: {}, running };
}
function namedButton(label: string): HTMLButtonElement {
  const button = Array.from(document.querySelectorAll<HTMLButtonElement>("button")).find((node) => node.getAttribute("aria-label") === label || node.textContent?.trim() === label);
  assert.ok(button, label); return button;
}
function row(id: string): HTMLElement { const node = document.querySelector<HTMLElement>(`[data-task-item-key="${id}"]`); assert.ok(node); return node; }
function changeInput(input: HTMLInputElement, text: string) {
  act(() => {
    Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value")?.set?.call(input, text);
    input.dispatchEvent(new window.Event("input", { bubbles: true }));
  });
}
async function openAction(id: string, action: string) {
  const trigger = row(id).querySelector<HTMLButtonElement>('button[aria-label="Task actions"]')!;
  await act(async () => { trigger.focus(); trigger.dispatchEvent(new window.KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true, cancelable: true })); });
  const item = Array.from(document.querySelectorAll<HTMLElement>('[role="menuitem"]')).find((node) => node.textContent === action);
  assert.ok(item); await act(async () => item.click());
}

test("navigation groups only real running tasks and retains native selection and workspace actions", () => {
  const selected: string[] = []; let files = 0; let created = 0; let closed = 0;
  render({ conversations: [chat("old", 12), chat("today"), chat("working", 4, true), chat("yesterday", 1)], activeId: "today", onSelect: (id) => selected.push(id), onFiles: () => { files++; }, onNewChat: () => { created++; }, onClose: () => { closed++; } });
  assert.deepEqual(Array.from(document.querySelectorAll("[data-chat-group]")).map((node) => node.getAttribute("data-chat-group")), ["running", "today", "yesterday", "older"]);
  assert.deepEqual(Array.from(document.querySelectorAll("[data-task-item-key]")).map((node) => node.getAttribute("data-task-item-key")), ["working", "today", "yesterday", "old"]);
  assert.ok(row("today").classList.contains("bg-selected"));
  act(() => row("old").dispatchEvent(new window.KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true })));
  act(() => namedButton("Workspace files").click());
  act(() => namedButton("MatOpt home").click());
  act(() => namedButton("Collapse sidebar").click());
  assert.deepEqual(selected, ["old"]); assert.equal(files, 1); assert.equal(created, 1); assert.equal(closed, 1);
});

test("search filters real titles and Escape restores focus without closing the sidebar", () => {
  let closed = 0;
  render({ conversations: [chat("forecast"), chat("algebra")], onClose: () => { closed++; } });
  act(() => namedButton("Search chats").click());
  const input = document.querySelector<HTMLInputElement>('[aria-label="Search chat history"]')!;
  changeInput(input, "forecast");
  assert.equal(document.querySelectorAll("[data-task-item-key]").length, 1);
  assert.ok(row("forecast"));
  assert.ok(document.querySelector('[data-chat-group="search"]'));
  act(() => input.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true })));
  assert.equal(document.querySelector('[aria-label="Search chat history"]'), null);
  assert.equal(document.activeElement, namedButton("Search chats"));
  assert.equal(document.querySelectorAll("[data-task-item-key]").length, 2);
  assert.equal(closed, 0);
});

test("only the known paused run gets a static Paused group, including search, and its row survives resume", () => {
  const conversations = [chat("paused", 0, true), chat("other-running", 0, true), chat("finished")];
  render({ conversations, activeId: "paused", pausedConversationId: "paused" });
  const pausedRow = row("paused");
  assert.deepEqual(Array.from(document.querySelectorAll("[data-chat-group]")).map((node) => node.getAttribute("data-chat-group")), ["running", "paused", "today"]);
  assert.equal(document.querySelector('[data-chat-group="paused"] h2')?.textContent, "Paused");
  assert.ok(document.querySelector('[data-chat-group="paused"] .mo-navigation-paused-icon'));
  assert.equal(pausedRow.querySelector(".animate-spin"), null);
  assert.ok(row("other-running").querySelector(".animate-spin"));
  act(() => namedButton("Search chats").click());
  const input = document.querySelector<HTMLInputElement>('[aria-label="Search chat history"]')!;
  changeInput(input, "paused");
  assert.equal(document.querySelector('[data-chat-group="paused"] h2')?.textContent, "Paused");
  assert.equal(row("paused"), pausedRow);
  assert.equal(pausedRow.querySelector(".animate-spin"), null);
  render({ conversations, activeId: "paused", pausedConversationId: null });
  assert.equal(row("paused"), pausedRow);
  assert.ok(pausedRow.querySelector(".animate-spin"));
  assert.equal(document.querySelector('[data-chat-group="paused"]'), null);
});

test("a running task keeps its rename draft when it moves into recent history; rename and delete keep their callbacks", async () => {
  const renames: unknown[] = []; const deletions: string[] = [];
  const callbacks = { onRename: async (id: string, title: string) => { renames.push([id, title]); }, onDelete: async (id: string) => { deletions.push(id); } };
  render({ conversations: [chat("working", 0, true), chat("other")], ...callbacks });
  const original = row("working");
  await openAction("working", "Rename");
  const input = document.querySelector<HTMLInputElement>('[role="dialog"] input')!;
  assert.ok(input);
  changeInput(input, "Keep this renamed title");
  render({ conversations: [chat("working"), chat("other")], ...callbacks });
  assert.equal(row("working"), original);
  assert.equal(document.querySelector('[role="dialog"] input'), input);
  assert.equal(input.value, "Keep this renamed title");
  await act(async () => namedButton("Confirm").click());
  assert.deepEqual(renames, [["working", "Keep this renamed title"]]);
  await openAction("working", "Delete");
  await act(async () => namedButton("Delete").click());
  assert.deepEqual(deletions, ["working"]);
});
