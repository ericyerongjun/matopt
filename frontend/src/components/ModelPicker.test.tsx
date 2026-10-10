import '../../test/jsdom-env.mjs';
import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import ModelPicker from './ModelPicker.tsx';
import type { ReasoningLevel, ReasoningStop } from '../types/protocol.ts';
let root: Root | null = null; let container: HTMLDivElement | null = null;
afterEach(() => { act(() => root?.unmount()); root = null; container?.remove(); container = null; });
const levels: ReasoningStop[] = [{ id: 'off', label: 'Off' }, { id: 'low', label: 'Low' }, { id: 'medium', label: 'Medium' }, { id: 'high', label: 'High' }, { id: 'max', label: 'Max' }];
function mount(disabled = false) {
    const selected: ReasoningLevel[] = []; const models: string[] = [];
    container = document.createElement('div'); document.body.append(container); root = createRoot(container);
    act(() => root?.render(<ModelPicker catalog={null} provider="deepseek" model="deepseek-flash" label="DeepSeek V4.1 Flash"
        levels={levels} reasoning="high" disabled={disabled} onSelect={(_p, m) => models.push(m)} onReasoningChange={(level) => selected.push(level)} />));
    return { selected, models, trigger: container.querySelector<HTMLButtonElement>('button')! };
}
const tick = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
function press(target: Element, key: string) { act(() => target.dispatchEvent(new window.KeyboardEvent('keydown', { key, bubbles: true }))); }

test('native effort menu opens from the keyboard, excludes medium and selects the real radio item', async () => {
    const { trigger, selected, models } = mount();
    act(() => trigger.focus()); press(trigger, 'Enter'); await tick();
    const menu = document.querySelector('[role="menu"]'); assert.ok(menu);
    const items = Array.from(menu.querySelectorAll<HTMLElement>('[role="menuitemradio"]'));
    assert.deepEqual(items.map((item) => item.textContent), ['Off', 'Low', 'High', 'Max']);
    assert.equal(items[2].getAttribute('aria-checked'), 'true');
    act(() => items[1].click()); await tick();
    assert.deepEqual(selected, ['low']); assert.deepEqual(models, []);
    assert.equal(document.querySelector('[role="menu"]'), null);
});

test('native effort menu closes on Escape and returns focus to its trigger', async () => {
    const { trigger } = mount(); act(() => trigger.focus()); press(trigger, 'Enter'); await tick();
    const menu = document.querySelector('[role="menu"]')!; assert.ok(menu);
    press(menu, 'Escape'); await tick();
    assert.equal(document.querySelector('[role="menu"]'), null); assert.equal(document.activeElement, trigger);
});

test('disabled Flash trigger cannot open the native menu', async () => {
    const { trigger, selected } = mount(true); assert.equal(trigger.disabled, true);
    act(() => trigger.click()); await tick();
    assert.equal(document.querySelector('[role="menu"]'), null); assert.deepEqual(selected, []);
});

test('starting a turn closes an already open effort menu and disables its trigger', async () => {
    const { trigger } = mount(); act(() => trigger.focus()); press(trigger, 'Enter'); await tick();
    assert.ok(document.querySelector('[role="menu"]'));
    act(() => root?.render(<ModelPicker catalog={null} provider="deepseek" model="deepseek-flash" label="Flash"
        levels={levels} reasoning="high" disabled onSelect={() => {}} onReasoningChange={() => {}} />));
    await tick();
    assert.equal(document.querySelector('[role="menu"]'), null);
    assert.equal(container!.querySelector<HTMLButtonElement>('button')!.disabled, true);
});
