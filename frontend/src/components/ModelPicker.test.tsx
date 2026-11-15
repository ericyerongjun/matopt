import '../../test/jsdom-env.mjs';
import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import ModelPicker from './ModelPicker.tsx';
let root: Root | null = null; let container: HTMLDivElement | null = null;
afterEach(() => { act(() => root?.unmount()); root = null; container?.remove(); container = null; });
function mount(disabled = false, ultrafast = true) {
    const toggles: boolean[] = [];
    container = document.createElement('div'); document.body.append(container); root = createRoot(container);
    act(() => root?.render(<ModelPicker label="DeepSeek V4.1 Flash" ultrafast={ultrafast}
        disabled={disabled} onUltrafastChange={(value) => toggles.push(value)} />));
    return { toggles, trigger: container.querySelector<HTMLButtonElement>('button')! };
}
const tick = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
function press(target: Element, key: string) { act(() => target.dispatchEvent(new window.KeyboardEvent('keydown', { key, bubbles: true }))); }

test('keyboard menu offers only the demo toggle and no manual effort choices', async () => {
    const { trigger, toggles } = mount();
    assert.match(trigger.textContent!, /Ultrafast/);
    act(() => trigger.focus()); press(trigger, 'Enter'); await tick();
    const menu = document.querySelector('[role="menu"]'); assert.ok(menu);
    assert.equal(menu.querySelectorAll('[role="menuitemradio"]').length, 0);
    const items = menu.querySelectorAll<HTMLElement>('[role="menuitemcheckbox"]');
    assert.equal(items.length, 1);
    assert.equal(items[0].textContent, 'Ultrafast Demo');
    assert.equal(items[0].getAttribute('aria-checked'), 'true');
    act(() => items[0].click()); await tick();
    assert.deepEqual(toggles, [false]);
    assert.equal(document.querySelector('[role="menu"]'), null);
});

test('response menu closes on Escape and returns focus to its trigger', async () => {
    const { trigger } = mount(); act(() => trigger.focus()); press(trigger, 'Enter'); await tick();
    const menu = document.querySelector('[role="menu"]')!; assert.ok(menu);
    press(menu, 'Escape'); await tick();
    assert.equal(document.querySelector('[role="menu"]'), null); assert.equal(document.activeElement, trigger);
});

test('disabled Flash trigger cannot open the native menu', async () => {
    const { trigger, toggles } = mount(true); assert.equal(trigger.disabled, true);
    act(() => trigger.click()); await tick();
    assert.equal(document.querySelector('[role="menu"]'), null); assert.deepEqual(toggles, []);
});

test('starting a turn closes an already open response menu and disables its trigger', async () => {
    const { trigger } = mount(); act(() => trigger.focus()); press(trigger, 'Enter'); await tick();
    assert.ok(document.querySelector('[role="menu"]'));
    act(() => root?.render(<ModelPicker label="Flash" ultrafast disabled onUltrafastChange={() => {}} />));
    await tick();
    assert.equal(document.querySelector('[role="menu"]'), null);
    assert.equal(container!.querySelector<HTMLButtonElement>('button')!.disabled, true);
});

test('turning demo off shows automatic mode and permits re-enabling the profile', async () => {
    const { trigger, toggles } = mount(false, false);
    assert.match(trigger.textContent!, /Auto/);
    act(() => trigger.focus()); press(trigger, 'Enter'); await tick();
    const toggle = document.querySelector<HTMLElement>('[role="menuitemcheckbox"]')!;
    assert.equal(toggle.getAttribute('aria-checked'), 'false');
    assert.match(document.querySelector('[role="menu"]')!.textContent!, /configured default/);
    act(() => toggle.click()); await tick();
    assert.deepEqual(toggles, [true]);
});
