import "../../../test/jsdom-env.mjs";
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { TableOverflow } from "./TableOverflow.tsx";

const originalObserver = Object.getOwnPropertyDescriptor(globalThis, 'ResizeObserver');
let root: Root | null = null;
let container: HTMLDivElement | null = null;
const observers: Array<{ callback: ResizeObserverCallback; disconnected: boolean }> = [];
afterEach(async () => {
    await act(async () => { await Promise.resolve(); root?.unmount(); }); root = null; container?.remove(); container = null;
    if (originalObserver) Object.defineProperty(globalThis, 'ResizeObserver', originalObserver);
    else Reflect.deleteProperty(globalThis, 'ResizeObserver');
    observers.length = 0;
});
function table(kind: 'native' | 'markdown', rows = 1, label = 'Existing data label', description?: string) {
    const content = <table className={kind === 'native' ? 'ui-table' : undefined} data-streamdown={kind === 'markdown' ? 'table' : undefined}><thead><tr><th>Model</th><th>Accuracy</th><th>Standard error</th></tr></thead><tbody>{Array.from({ length: rows }, (_, i) => <tr key={i}><td>Linear</td><td>0.847</td><td>0.021</td></tr>)}</tbody></table>;
    return kind === 'native' ? <div className="ui-table-scroll" role="region" aria-label={label} aria-describedby={description} tabIndex={0}>{content}</div>
        : <div data-markdown-table-frame=""><div><div className="markdown-viewport">{content}</div></div><div data-markdown-table-virtual-scroll-track="" /></div>;
}
function mount(kind: 'native' | 'markdown') {
    Object.defineProperty(globalThis, 'ResizeObserver', { configurable: true, value: class {
        entry: { callback: ResizeObserverCallback; disconnected: boolean };
        constructor(callback: ResizeObserverCallback) { this.entry = { callback, disconnected: false }; observers.push(this.entry); }
        observe() {} unobserve() {} disconnect() { this.entry.disconnected = true; }
    } });
    container = document.createElement('div'); document.body.append(container); root = createRoot(container);
    act(() => root!.render(<TableOverflow label="Comparison data table">{table(kind)}</TableOverflow>));
    const viewport = container.querySelector<HTMLElement>(kind === 'native' ? '.ui-table-scroll' : '.markdown-viewport')!;
    const dimensions = { width: 300, content: 300 };
    Object.defineProperties(viewport, { clientWidth: { get: () => dimensions.width }, scrollWidth: { get: () => dimensions.content } });
    const resize = () => act(() => { for (const entry of observers) if (!entry.disconnected) entry.callback([], {} as ResizeObserver); });
    return { viewport, dimensions, resize };
}
function key(viewport: HTMLElement, value: string) {
    const event = new KeyboardEvent('keydown', { key: value, bubbles: true, cancelable: true });
    act(() => viewport.dispatchEvent(event)); return event;
}

test('native table hints reflect overflow and scroll boundaries, without replacing table data', async () => {
    const { viewport, dimensions, resize } = mount('native');
    const actualTable = viewport.querySelector('table');
    resize(); assert.equal(container!.querySelector('.ui-table-overflow-cue'), null);
    dimensions.content = 720; resize();
    assert.match(container!.querySelector('.ui-table-overflow-cue')?.textContent ?? '', /Scroll for more columns/);
    assert.equal(viewport.getAttribute('aria-label'), 'Existing data label');
    assert.ok(viewport.getAttribute('aria-describedby'));
    const left = container!.querySelector<HTMLButtonElement>('[aria-label="Scroll table left"]')!;
    const right = container!.querySelector<HTMLButtonElement>('[aria-label="Scroll table right"]')!;
    assert.equal(left.disabled, true); assert.equal(right.disabled, false);
    act(() => right.click()); assert.equal(viewport.scrollLeft, 225); assert.equal(left.disabled, false);
    act(() => viewport.focus());
    assert.equal(key(viewport, 'End').defaultPrevented, true); assert.equal(viewport.scrollLeft, 420); assert.equal(right.disabled, true);
    key(viewport, 'Home'); assert.equal(viewport.scrollLeft, 0);
    key(viewport, 'ArrowRight'); assert.equal(viewport.scrollLeft, 48);
    assert.equal(document.activeElement, viewport);
    assert.equal(viewport.querySelector('table'), actualTable); assert.equal(viewport.querySelectorAll('th').length, 3);
    dimensions.width = 800; resize();
    assert.equal(container!.querySelector('.ui-table-overflow-cue'), null);
    assert.equal(viewport.getAttribute('aria-describedby'), null);
    assert.equal(viewport.getAttribute('aria-label'), 'Existing data label');
    assert.equal(viewport.tabIndex, 0, 'retain the native renderer’s existing focus contract');
});

test('Markdown table overflow becomes a named keyboard region and survives hidden tabs and row updates', async () => {
    const { viewport, dimensions, resize } = mount('markdown');
    const actualTable = viewport.querySelector('table');
    dimensions.content = 500; resize();
    assert.equal(viewport.getAttribute('role'), 'region'); assert.equal(viewport.getAttribute('aria-label'), 'Comparison data table'); assert.equal(viewport.tabIndex, 0);
    const description = viewport.getAttribute('aria-describedby');
    assert.match(document.getElementById(description!)?.textContent ?? '', /Scroll/);
    key(viewport, 'End'); assert.equal(viewport.scrollLeft, 200);
    dimensions.width = 0; resize(); assert.equal(viewport.scrollLeft, 200);
    assert.equal(container!.querySelectorAll('.ui-table-overflow-cue').length, 1);
    dimensions.width = 300; resize();
    await act(async () => { root!.render(<TableOverflow label="Comparison data table">{table('markdown', 2)}</TableOverflow>); await new Promise(resolve => setTimeout(resolve, 0)); });
    assert.equal(viewport.querySelector('table'), actualTable); assert.equal(viewport.querySelectorAll('tbody tr').length, 2);
    assert.equal(container!.querySelectorAll('.ui-table-overflow-cue').length, 1); assert.equal(viewport.scrollLeft, 200);
    dimensions.width = 600; resize();
    assert.equal(container!.querySelector('.ui-table-overflow-cue'), null); assert.equal(viewport.hasAttribute('tabindex'), false); assert.equal(viewport.hasAttribute('role'), false);
    act(() => root!.unmount()); root = null;
    assert.ok(observers.every(entry => entry.disconnected));
});

test('a native table revision retains its new label and unrelated description tokens', async () => {
    const { viewport, dimensions, resize } = mount('native');
    const actualTable = viewport.querySelector('table');
    dimensions.content = 500; resize();
    const hintId = viewport.getAttribute('aria-describedby')!;
    await act(async () => { root!.render(<TableOverflow label="Comparison data table">{table('native', 2, 'Updated result label', 'revision-description')}</TableOverflow>); await new Promise(resolve => setTimeout(resolve, 0)); });
    assert.equal(viewport.querySelector('table'), actualTable);
    assert.equal(viewport.getAttribute('aria-label'), 'Updated result label');
    assert.equal(viewport.getAttribute('aria-describedby'), `revision-description ${hintId}`);
    dimensions.width = 600; resize();
    assert.equal(viewport.getAttribute('aria-label'), 'Updated result label');
    assert.equal(viewport.getAttribute('aria-describedby'), 'revision-description');
    assert.equal(viewport.getAttribute('role'), 'region');
    dimensions.width = 300; resize();
    act(() => root!.unmount()); root = null;
    assert.equal(viewport.getAttribute('aria-label'), 'Updated result label');
    assert.equal(viewport.getAttribute('aria-describedby'), 'revision-description');
});

test('Markdown keyboard and arrow limits reuse its outer virtual track geometry', () => {
    const { viewport, dimensions, resize } = mount('markdown');
    dimensions.content = 500;
    viewport.querySelector('table')!.getBoundingClientRect = () => ({ width: 500 }) as DOMRect;
    container!.querySelector<HTMLElement>('[data-markdown-table-virtual-scroll-track]')!.getBoundingClientRect = () => ({ width: 302 }) as DOMRect;
    resize(); key(viewport, 'End');
    assert.equal(viewport.scrollLeft, 198, 'the existing Markdown renderer clamps to table minus outer track width');
    assert.equal(container!.querySelector<HTMLButtonElement>('[aria-label="Scroll table right"]')!.disabled, true);
    key(viewport, 'Home'); assert.equal(viewport.scrollLeft, 0);
});
