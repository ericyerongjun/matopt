import "../../test/jsdom-env.mjs";
import assert from "node:assert/strict";
import { Buffer } from "node:buffer";
import { after, afterEach, test } from "node:test";
import { act, type ComponentProps } from "react";
import { createRoot, type Root } from "react-dom/client";
import MathInput from "./MathInput.tsx";
import { commandPresentation } from "./commandPresentation.ts";
import { TooltipProvider } from "../zcode/components/ui/tooltip.tsx";
import { ZCodeIntlProvider } from "../zcode/i18n/IntlProvider.tsx";

// jsdom does not provide browser sizing or a FileReader for Node's fetch Blob.
const originalReader = Object.getOwnPropertyDescriptor(globalThis, "FileReader");
class BlobReader {
  result: string | null = null;
  onloadend?: () => void;
  onerror?: () => void;
  readAsDataURL(blob: Blob) {
    void blob.arrayBuffer().then((bytes) => {
      this.result = `data:${blob.type};base64,${Buffer.from(bytes).toString("base64")}`;
      this.onloadend?.();
    }, () => this.onerror?.());
  }
}
Object.defineProperty(globalThis, "FileReader", { value: BlobReader, configurable: true });
Object.defineProperty(globalThis, "ResizeObserver", { value: class {
  observe() {} unobserve() {} disconnect() {}
}, configurable: true });
window.HTMLElement.prototype.scrollIntoView = () => {};
after(() => {
  if (originalReader) Object.defineProperty(globalThis, "FileReader", originalReader);
  else Reflect.deleteProperty(globalThis, "FileReader");
});

let root: Root | null = null;
let container: HTMLDivElement | null = null;
afterEach(() => {
  act(() => root?.unmount());
  root = null;
  container?.remove();
  container = null;
});

type Props = ComponentProps<typeof MathInput>;
function render(props: Props) {
  if (!container) {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  }
  act(() => root?.render(<ZCodeIntlProvider initialLocale="en-US"><TooltipProvider>
    <MathInput showDisclaimer={false} {...props} />
  </TooltipProvider></ZCodeIntlProvider>));
}
function textarea(): HTMLTextAreaElement {
  const node = document.querySelector('textarea[aria-label="Task instructions"]');
  assert.ok(node instanceof window.HTMLTextAreaElement);
  return node;
}
function button(label: string): HTMLButtonElement {
  const node = document.querySelector(`button[aria-label="${label}"]`);
  assert.ok(node instanceof HTMLButtonElement);
  return node;
}
function type(text: string) {
  const node = textarea();
  act(() => {
    Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value")?.set?.call(node, text);
    node.dispatchEvent(new window.Event("input", { bubbles: true }));
  });
}
function key(key: string, options: KeyboardEventInit = {}) {
  const event = new window.KeyboardEvent("keydown", { key, bubbles: true, cancelable: true, ...options });
  textarea().dispatchEvent(event);
  return event;
}

test("native composer uses real input group markup and sends on Enter without submitting IME/Shift+Enter", async () => {
  const submissions: string[] = [];
  render({ onSubmit: (text) => { submissions.push(text); }, toolbar: <span>Flash · High</span> });
  assert.ok(document.querySelector('[data-slot="input-group"]'));
  assert.ok(textarea().classList.contains("field-sizing-content"));
  assert.match(container?.textContent || "", /Flash · High/);
  assert.equal(button("Send").disabled, true);
  type("Solve x + 2 = 4");
  await act(async () => { key("Enter", { isComposing: true }); });
  await act(async () => { key("Enter", { shiftKey: true }); });
  assert.deepEqual(submissions, []);
  await act(async () => { key("Enter"); });
  assert.deepEqual(submissions, ["Solve x + 2 = 4"]);
  assert.equal(textarea().value, "");
});

test("external run controls keep one Stop action while the composer retains steering and Escape", async () => {
  const submissions: string[] = []; let stops = 0;
  render({ loading: true, stopControl: "external", onStop: () => { stops++; }, onSubmit: (text) => { submissions.push(text); } });
  assert.equal(document.querySelector('button[aria-label="Stop"]'), null);
  assert.equal(button("Steer").disabled, true);
  type("Keep the current assumptions");
  assert.equal(button("Steer").disabled, false);
  await act(async () => { button("Steer").click(); });
  assert.deepEqual(submissions, ["Keep the current assumptions"]);
  assert.equal(button("Steer").disabled, true);
  act(() => { key("Escape"); });
  assert.equal(stops, 1);
});

test("native command palette pins with Tab and submits canonical skill/tool bindings", async () => {
  const submissions: unknown[] = [];
  render({ commands: [
    { kind: "skill", name: "lambda-eda", description: "Explore data", source: "lambda" },
    { kind: "tool", name: "read_file", description: "Read a file", source: "matopt" },
  ], onSubmit: (text, files, skills, tools) => { submissions.push({ text, files, skills, tools }); } });
  type("/lambda");
  assert.ok(document.querySelector('[cmdk-root=""]'));
  act(() => { key("Tab"); });
  assert.equal(textarea().value, "");
  assert.ok(button("Remove Explore data"));
  type("/read-file inspect data");
  await act(async () => { button("Send").click(); });
  assert.deepEqual(submissions, [{ text: "/lambda-eda /read-file inspect data", files: [], skills: ["lambda-eda"], tools: ["read_file"] }]);
  assert.equal(document.querySelector('button[aria-label="Remove Explore data"]'), null);
});

test("starter presets fill an editable draft once, retaining files and tools without sending", async () => {
  const submissions: string[] = [];
  const props: Props = {
    commands: [{ kind: "tool", name: "read_file", description: "Read a file", source: "matopt" }],
    onSubmit: (text) => { submissions.push(text); },
  };
  render(props);
  type("/read-file");
  act(() => { key("Tab"); });
  const removeTool = button("Remove Read files");
  const upload = document.querySelector<HTMLInputElement>('input[aria-label="Upload files"]')!;
  act(() => {
    Object.defineProperty(upload, "files", { value: [new File(["x,y\n1,2"], "draft.csv", { type: "text/csv" })], configurable: true });
    upload.dispatchEvent(new window.Event("change", { bubbles: true }));
  });
  const removeFile = button("Remove draft.csv");
  const originalInput = textarea();
  await act(async () => {
    render({ ...props, draftPreset: { id: 1, text: "Analyze the attached data" } });
    await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
  });
  assert.equal(textarea(), originalInput);
  assert.equal(textarea().value, "Analyze the attached data");
  assert.equal(textarea().selectionStart, textarea().value.length);
  assert.equal(document.activeElement, textarea());
  assert.equal(button("Remove Read files"), removeTool);
  assert.equal(button("Remove draft.csv"), removeFile);
  assert.deepEqual(submissions, []);
  type("Analyze the attached data by month");
  render({ ...props, draftPreset: { id: 1, text: "Analyze the attached data" } });
  assert.equal(textarea().value, "Analyze the attached data by month");
  render({ ...props, draftPreset: { id: 2, text: "Research a topic" } });
  assert.equal(textarea().value, "Research a topic");
  assert.deepEqual(submissions, []);
});

test("a starter draft pins its skill without sending, and the next starter replaces that skill", async () => {
  const submissions: unknown[] = [];
  const props: Props = {
    commands: [
      { kind: "skill", name: "statistical-inference", description: "Tests and intervals", source: "matopt" },
      { kind: "skill", name: "numerical-optimization", description: "Numerical optimisation", source: "matopt" },
      { kind: "tool", name: "read_file", description: "Read a file", source: "matopt" },
    ],
    onSubmit: (text, files, skills, tools) => { submissions.push({ text, files, skills, tools }); },
  };
  render(props);
  type("/read-file");
  act(() => { key("Tab"); });
  await act(async () => {
    render({ ...props, draftPreset: { id: 1, text: "Compare the two groups", skills: ["statistical-inference"] } });
    await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
  });
  assert.equal(textarea().value, "Compare the two groups");
  assert.ok(button("Remove Statistical inference"));
  assert.ok(button("Remove Read files"));
  assert.deepEqual(submissions, []);
  await act(async () => {
    render({ ...props, draftPreset: { id: 2, text: "Minimize f(x, y)", skills: ["numerical-optimization"] } });
    await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
  });
  assert.equal(textarea().value, "Minimize f(x, y)");
  assert.equal(document.querySelector('button[aria-label="Remove Statistical inference"]'), null);
  assert.ok(button("Remove Numerical optimization"));
  assert.ok(button("Remove Read files"));
  await act(async () => { button("Send").click(); });
  assert.deepEqual(submissions, [{
    text: "/numerical-optimization /read-file Minimize f(x, y)",
    files: [],
    skills: ["numerical-optimization"],
    tools: ["read_file"],
  }]);
});

test("native file picker preserves binary upload data and releases attachment preview after send", async () => {
  let resolveSubmission: (value: { text: string; files: File[] }) => void = () => {};
  const submitted = new Promise<{ text: string; files: File[] }>((resolve) => { resolveSubmission = resolve; });
  render({ onSubmit: (text, files) => { resolveSubmission({ text, files }); } });
  const input = document.querySelector('input[aria-label="Upload files"]');
  assert.ok(input instanceof HTMLInputElement);
  const file = new File(["x,y\n1,2\n"], "data.csv", { type: "text/csv" });
  act(() => {
    Object.defineProperty(input, "files", { value: [file], configurable: true });
    input.dispatchEvent(new window.Event("change", { bubbles: true }));
  });
  assert.ok(button("Remove data.csv"));
  type("Analyze this CSV");
  let payload: { text: string; files: File[] } | undefined;
  await act(async () => {
    button("Send").click();
    payload = await submitted;
  });
  assert.equal(payload?.text, "Analyze this CSV");
  assert.equal(payload?.files[0].name, "data.csv");
  assert.equal(payload?.files[0].type, "text/csv");
  assert.equal(await payload?.files[0].text(), "x,y\n1,2\n");
  assert.equal(document.querySelector('button[aria-label="Remove data.csv"]'), null);
});

test("attachment action closes its modal menu after opening the picker, including picker cancellation", async () => {
  render({ onSubmit: () => {} });
  type("Keep the draft");
  const picker = document.querySelector<HTMLInputElement>('input[aria-label="Upload files"]')!;
  let pickerOpened = 0;
  picker.addEventListener("click", () => { pickerOpened++; });
  const openPicker = async () => {
    await act(async () => {
      const trigger = button("Add photos and files");
      trigger.focus();
      trigger.dispatchEvent(new window.KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true, cancelable: true }));
    });
    assert.equal(button("Add photos and files").getAttribute("aria-expanded"), "true");
    const item = Array.from(document.querySelectorAll<HTMLElement>('[role="menuitem"]')).find((node) => node.textContent?.includes("Add photos or files"));
    assert.ok(item);
    await act(async () => { item.click(); });
    assert.equal(button("Add photos and files").getAttribute("aria-expanded"), "false");
    assert.equal(textarea().closest('[aria-hidden="true"]'), null);
    assert.equal(document.querySelector('[role="menu"]'), null);
  };
  await openPicker();
  assert.equal(pickerOpened, 1);
  act(() => picker.dispatchEvent(new window.Event("cancel", { bubbles: true })));
  assert.equal(textarea().value, "Keep the draft");
  assert.equal(document.querySelectorAll('[aria-label^="Remove "]').length, 0);
  await openPicker();
  assert.equal(pickerOpened, 2);
  act(() => {
    Object.defineProperty(picker, "files", { value: [new File(["x,y\n1,2\n"], "picked.csv", { type: "text/csv" })], configurable: true });
    picker.dispatchEvent(new window.Event("change", { bubbles: true }));
  });
  assert.ok(button("Remove picked.csv"));
  assert.equal(textarea().value, "Keep the draft");
  assert.equal(textarea().closest('[aria-hidden="true"]'), null);
});

test("native Stop button and Escape cancel generation, while typed instructions steer and dialog Escape stays local", async () => {
  let stopped = 0;
  const submissions: string[] = [];
  render({ loading: true, onStop: () => { stopped++; }, onSubmit: (text) => { submissions.push(text); } });
  act(() => button("Stop").click());
  assert.equal(stopped, 1);
  act(() => { key("Escape"); });
  assert.equal(stopped, 2);
  const dialog = document.createElement("div");
  dialog.setAttribute("role", "dialog");
  document.body.appendChild(dialog);
  act(() => dialog.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true })));
  dialog.remove();
  assert.equal(stopped, 2);
  type("Try a simpler proof");
  assert.equal(textarea().placeholder, "Steer this turn…");
  assert.equal(button("Add photos and files").disabled, true);
  await act(async () => { key("Enter"); });
  assert.deepEqual(submissions, ["Try a simpler proof"]);
  assert.equal(stopped, 2);
});

test("disabled composer blocks sending and tools are available only through slash commands", async () => {
  let sent = 0;
  const props = { onSubmit: () => { sent++; } };
  render({ ...props, disabled: true });
  assert.equal(textarea().disabled, true);
  act(() => button("Send").click());
  assert.equal(sent, 0);
  render(props);
  assert.equal(document.querySelector('button[aria-label="Web search"]'), null);
  assert.equal(document.querySelector('button[aria-label="Browse skills and tools"]'), null);
  assert.equal(textarea().placeholder, "Ask anything, or type /");
});

test("slash selection replaces only the current token, never submits, and preserves attached files", async () => {
  const submissions: string[] = [];
  render({ commands: [{ kind: "tool", name: "web_search", description: "Search the web", source: "matopt" }], onSubmit: (text) => { submissions.push(text); } });
  const input = document.querySelector('input[aria-label="Upload files"]') as HTMLInputElement;
  act(() => {
    Object.defineProperty(input, "files", { value: [new File(["x"], "notes.txt", { type: "text/plain" })], configurable: true });
    input.dispatchEvent(new window.Event("change", { bubbles: true }));
  });
  type("Compare evidence /web-search carefully");
  // Place the caret at the end of the command and select it with Enter.
  act(() => { textarea().focus(); textarea().setSelectionRange(28, 28); document.dispatchEvent(new window.Event("selectionchange", { bubbles: true })); });
  assert.equal(textarea().getAttribute("aria-expanded"), "true");
  await act(async () => { key("Enter"); });
  assert.deepEqual(submissions, []);
  assert.equal(textarea().value, "Compare evidence  carefully");
  assert.ok(button("Remove Search the web"));
  assert.ok(button("Remove notes.txt"));
});

test("Escape and outside clicks close slash suggestions without erasing the draft or stopping generation", () => {
  let stopped = 0;
  render({ loading: true, onStop: () => { stopped++; }, commands: [{ kind: "tool", name: "web_search", description: "Search the web", source: "matopt" }], onSubmit: () => {} });
  type("Find sources /web");
  assert.equal(textarea().getAttribute("aria-expanded"), "true");
  act(() => { key("Escape"); });
  assert.equal(textarea().value, "Find sources /web");
  assert.equal(textarea().getAttribute("aria-expanded"), "false");
  assert.equal(stopped, 0);
  type("Find sources /web-");
  act(() => document.body.dispatchEvent(new window.MouseEvent("mousedown", { bubbles: true })));
  assert.equal(textarea().value, "Find sources /web-");
  assert.equal(textarea().getAttribute("aria-expanded"), "false");
});

test("exact Enter pins, selections deduplicate, and a rejected send retains the draft and commands", async () => {
  let submissions = 0;
  render({ commands: [{ kind: "tool", name: "web_search", description: "Search the web", source: "matopt" }], onSubmit: () => { submissions++; return false; } });
  type("/web-search");
  await act(async () => { key("Enter"); });
  assert.equal(submissions, 0);
  assert.ok(button("Remove Search the web"));
  type("/web-search");
  act(() => { key("Tab"); });
  assert.equal(document.querySelectorAll('button[aria-label="Remove Search the web"]').length, 1);
  type("Keep this draft");
  await act(async () => { key("Enter"); });
  assert.equal(submissions, 1);
  assert.equal(textarea().value, "Keep this draft");
  assert.ok(button("Remove Search the web"));
});

test("unavailable commands cannot be pinned", () => {
  render({ commands: [{ kind: "tool", name: "web_search", description: "Search the web", source: "matopt", available: false }], onSubmit: () => {} });
  type("/web");
  act(() => { key("Tab"); });
  assert.equal(document.querySelector('button[aria-label="Remove Search the web"]'), null);
  assert.match(document.body.textContent ?? "", /Unavailable/);
});

test("acceptance clears only submitted text, attachment IDs and command selections", async () => {
  let accepted!: (value: boolean) => void;
  let started!: () => void;
  const submissionStarted = new Promise<void>((resolve) => { started = resolve; });
  const submissions: Array<{ text: string; files: File[]; tools?: string[] }> = [];
  render({ commands: [
    { kind: "tool", name: "web_search", description: "Search the web", source: "matopt" },
    { kind: "tool", name: "python", description: "Compute", source: "matopt" },
  ], onSubmit: (text, files, _skills, tools) => {
    submissions.push({ text, files, tools }); started();
    return new Promise<boolean>((resolve) => { accepted = resolve; });
  } });
  const attach = (name: string) => {
    const picker = document.querySelector('input[aria-label="Upload files"]') as HTMLInputElement;
    act(() => {
      Object.defineProperty(picker, "files", { value: [new File([name], name, { type: "text/plain" })], configurable: true });
      picker.dispatchEvent(new window.Event("change", { bubbles: true }));
    });
  };
  type("/web-search"); act(() => { key("Tab"); }); type("First request"); attach("first.txt");
  await act(async () => { button("Send").click(); await submissionStarted; });
  act(() => button("Remove Search the web").click());
  type("/web-search"); act(() => { key("Tab"); });
  type("/python"); act(() => { key("Tab"); });
  type("Keep my newer draft"); attach("second.txt");
  await act(async () => { accepted(true); });
  assert.equal(textarea().value, "Keep my newer draft");
  assert.ok(button("Remove Search the web"));
  assert.ok(button("Remove Python analysis"));
  assert.equal(document.querySelector('button[aria-label="Remove first.txt"]'), null);
  assert.ok(button("Remove second.txt"));
  assert.equal(submissions[0].text, "/web-search First request");
  assert.deepEqual(submissions[0].files.map((file) => file.name), ["first.txt"]);
  assert.deepEqual(submissions[0].tools, ["web_search"]);
});

test("slash combobox points at cmdk's real list and active option IDs", () => {
  render({ commands: [
    { kind: "tool", name: "python", description: "Compute", source: "matopt" },
    { kind: "tool", name: "web_search", description: "Search the web", source: "matopt" },
  ], onSubmit: () => {} });
  type("/");
  const list = document.getElementById(textarea().getAttribute("aria-controls")!);
  assert.equal(list?.getAttribute("role"), "listbox");
  assert.equal(list?.getAttribute("aria-label"), "Skills and tools");
  assert.equal(document.getElementById(textarea().getAttribute("aria-activedescendant")!)?.getAttribute("data-value"), "tool:python");
  act(() => { key("ArrowDown"); });
  assert.equal(document.getElementById(textarea().getAttribute("aria-activedescendant")!)?.getAttribute("data-value"), "tool:web_search");
});

test("slash menu portals outside clipping containers, fits the visual viewport, and repositions on scroll", () => {
  const originalRect = window.HTMLElement.prototype.getBoundingClientRect;
  const originalViewport = Object.getOwnPropertyDescriptor(window, "visualViewport");
  let anchorTop = 100;
  Object.defineProperty(window, "visualViewport", { configurable: true, value: { width: 390, height: 380, offsetTop: 0, offsetLeft: 0, addEventListener() {}, removeEventListener() {} } });
  window.HTMLElement.prototype.getBoundingClientRect = function () {
    return this.hasAttribute("data-matopt-native-composer")
      ? { top: anchorTop, bottom: anchorTop + 80, left: 16, right: 374, width: 358, height: 80, x: 16, y: anchorTop, toJSON() {} }
      : originalRect.call(this);
  };
  try {
    render({ commands: [{ kind: "tool", name: "python", description: "Compute", source: "matopt" }], onSubmit: () => {} });
    type("/");
    const menu = document.querySelector<HTMLElement>("[data-matopt-command-menu]")!;
    assert.equal(menu.parentElement, document.body);
    assert.equal(menu.style.position, "fixed");
    assert.equal(menu.style.top, "188px");
    assert.equal(menu.style.maxHeight, "184px");
    assert.ok(Number.parseFloat(menu.style.top) + Number.parseFloat(menu.style.maxHeight) <= 372);
    anchorTop = 300;
    act(() => window.dispatchEvent(new window.Event("scroll")));
    assert.equal(menu.style.top, "8px");
    assert.equal(menu.style.maxHeight, "284px");
    const option = menu.querySelector<HTMLElement>('[role="option"]')!;
    act(() => { option.dispatchEvent(new window.MouseEvent("mousedown", { bubbles: true, cancelable: true })); option.click(); });
    assert.ok(button("Remove Python analysis"));
    assert.equal(document.querySelector("[data-matopt-command-menu]"), null);
  } finally {
    window.HTMLElement.prototype.getBoundingClientRect = originalRect;
    if (originalViewport) Object.defineProperty(window, "visualViewport", originalViewport);
    else Reflect.deleteProperty(window, "visualViewport");
  }
});

test("semantic command names are searchable and selected capsules keep canonical tool bindings", async () => {
  const submissions: unknown[] = [];
  render({ commands: [
    { kind: "tool", name: "python", description: "Compute with code in a persistent kernel", source: "matopt" },
    { kind: "skill", name: "lambda-eda", description: "Explore a dataset", source: "lambda" },
  ], onSubmit: (text, _files, skills, tools) => { submissions.push({ text, skills, tools }); } });
  type("/kernel");
  assert.equal(document.querySelector('[role="option"]')?.getAttribute("data-value"), "tool:python", "original catalog descriptions remain searchable");
  type("/analysis");
  const option = document.querySelector<HTMLElement>('[role="option"]')!;
  assert.equal(option.getAttribute("aria-label"), "Python analysis, tool");
  assert.equal(option.querySelector(".matopt-command-label")?.textContent, "Python analysis");
  assert.equal(option.querySelector(".matopt-command-description")?.textContent, "Calculate, analyze data, and make charts.");
  assert.ok(!option.textContent?.includes("kernel"));
  assert.equal(option.querySelector("svg")?.getAttribute("stroke-width"), "1.8");
  act(() => { key("Enter"); });
  assert.deepEqual(submissions, []);
  const chip = document.querySelector('[data-selected-command="tool:python"]')!;
  assert.equal(chip.querySelector(".matopt-command-chip-label")?.textContent, "Python analysis");
  assert.ok(!chip.textContent?.includes("/python"));
  assert.ok(button("Remove Python analysis"));
  type("/lambda"); act(() => { key("Tab"); });
  assert.equal(document.querySelector('[data-selected-command="skill:lambda-eda"] .matopt-command-chip-label')?.textContent, "Explore data");
  type("Explore the uploaded data");
  await act(async () => { button("Send").click(); });
  assert.deepEqual(submissions, [{ text: "/lambda-eda /python Explore the uploaded data", skills: ["lambda-eda"], tools: ["python"] }]);
});

test("growing the draft and adding attachments preserve the input, controls, and keyboard focus", () => {
  render({ onSubmit: () => {} });
  const input = textarea();
  const add = button("Add photos and files");
  const send = button("Send");
  assert.equal(input.rows, 1);
  assert.ok(container?.querySelector('[data-matopt-native-composer][data-empty="true"]'));
  act(() => input.focus());
  type("First line\nSecond line\nThird line");
  assert.equal(textarea(), input);
  assert.equal(document.activeElement, input);
  const picker = document.querySelector<HTMLInputElement>('input[aria-label="Upload files"]')!;
  act(() => {
    Object.defineProperty(picker, "files", { value: [new File(["data"], "data.csv", { type: "text/csv" })], configurable: true });
    picker.dispatchEvent(new window.Event("change", { bubbles: true }));
  });
  assert.equal(textarea(), input);
  assert.equal(document.activeElement, input);
  assert.equal(button("Add photos and files"), add);
  assert.equal(button("Send"), send);
  assert.ok(button("Remove data.csv"));
  type("");
  assert.equal(document.activeElement, input);
  assert.ok(container?.querySelector('[data-matopt-native-composer][data-empty="false"]'));
});

test("command labels preserve familiar acronyms and turn custom names into readable text", () => {
  assert.equal(commandPresentation({ kind: "tool", name: "parse_document" }).label, "Read a document");
  assert.equal(commandPresentation({ kind: "skill", name: "anthropic:frontend-design" }).label, "Frontend design");
  assert.equal(commandPresentation({ kind: "skill", name: "custom-pdf-review" }).label, "Custom PDF review");
});
