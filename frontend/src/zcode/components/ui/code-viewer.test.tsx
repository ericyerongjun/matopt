import '../../../../test/diffs-dom-env.mjs';
import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';

const { CodeViewer } = await import('./code-viewer.tsx');
let root: Root | null = null, container: HTMLDivElement | null = null;
afterEach(() => { act(() => root?.unmount()); root = null; container?.remove(); container = null; });
function mount(code: string, language: string, theme?: 'github-light' | 'github-dark') {
    container = document.createElement('div'); document.body.append(container); root = createRoot(container);
    act(() => root?.render(<CodeViewer code={code} language={language} theme={theme} />));
}
function source() { return container?.querySelector('diffs-container')?.shadowRoot?.querySelector('[data-content]')?.textContent ?? ''; }
async function settleSource(expected: string) {
    for (let tries = 0; tries < 100 && !source().includes(expected); tries++) {
        await act(async () => { await new Promise((resolve) => setTimeout(resolve, 10)); });
    }
    assert.ok(source().includes(expected), `native code source includes ${expected}: ${source()}`);
}

test('native viewer inserts actual highlighted code into its shadow root', async () => {
    mount('print(1)\nprint(2)', 'python', 'github-light');
    await settleSource('print(2)');
    const shadow = container!.querySelector('diffs-container')!.shadowRoot!;
    assert.equal(shadow.querySelectorAll('[data-content] [data-line]').length, 2);
    assert.ok(shadow.querySelector('[data-content] [data-line] span[style*="color"]'));
    assert.equal(shadow.querySelector('[data-gutter] [data-line-number-content]')?.textContent, '1');
});

test('native viewer changes actual source when file contents or theme change', async () => {
    mount('old source', 'text', 'github-light'); await settleSource('old source');
    act(() => root?.render(<CodeViewer code="new source" language="text" theme="github-dark" wrapLongLines />));
    await settleSource('new source'); assert.ok(!source().includes('old source'));
    assert.equal(container!.querySelector('diffs-container')!.shadowRoot!.querySelector('pre')?.getAttribute('data-overflow'), 'wrap');
});
