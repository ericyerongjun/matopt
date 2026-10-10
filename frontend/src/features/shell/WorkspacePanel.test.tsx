import '../../../test/diffs-dom-env.mjs';
import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';
import { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { WorkspacePanel, type WorkspaceView } from './WorkspacePanel.tsx';
import { RunControls } from './RunControls.tsx';
let root: Root | null = null; let container: HTMLDivElement | null = null;
afterEach(() => { act(() => root?.unmount()); root = null; container?.remove(); container = null; });
function mount(node: React.ReactNode) { container = document.createElement('div'); document.body.append(container); root = createRoot(container); act(() => root?.render(node)); return container; }
function Harness() {
  const [view, setView] = useState<WorkspaceView>('results');
  return <WorkspacePanel view={view} onViewChange={setView} onClose={() => {}} results={<input id="result-parameter" defaultValue="10" />} files={<textarea id="file-document" defaultValue="Document" />} resultCount={2} fileCount={3} />;
}
test('Results and Files tabs preserve both mounted views and keyboard selection', () => {
  const dom = mount(<Harness />);
  const input = dom.querySelector<HTMLInputElement>('#result-parameter')!; const file = dom.querySelector<HTMLTextAreaElement>('#file-document')!;
  input.value = '25'; file.value = 'Edited document';
  const tabs = dom.querySelectorAll<HTMLButtonElement>('[role=tab]');
  assert.equal(dom.querySelector('[data-pane-heading]'), tabs[0]);
  act(() => tabs[0].dispatchEvent(new window.KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true, cancelable: true })));
  assert.equal(tabs[1].getAttribute('aria-selected'), 'true'); assert.equal(document.activeElement, tabs[1]);
  assert.equal(dom.querySelector('[data-pane-heading]'), tabs[1]);
  assert.equal(input.closest<HTMLElement>('[role=tabpanel]')?.hidden, true);
  act(() => tabs[1].dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Home', bubbles: true, cancelable: true })));
  assert.equal(document.activeElement, tabs[0]); assert.equal(dom.querySelector('#result-parameter'), input); assert.equal(input.value, '25');
  assert.equal(dom.querySelector('#file-document'), file); assert.equal(file.value, 'Edited document');
});
test('run controls distinguish pending requests, applied pause, reconnecting and stopping', () => {
  let pauses = 0, resumes = 0, stops = 0;
  const base = { paused: false, pauseRequested: false, reconnecting: false, status: null as string | null, onPause: () => pauses++, onResume: () => resumes++, onStop: () => stops++ };
  const dom = mount(<RunControls {...base} />); const buttons = () => [...dom.querySelectorAll<HTMLButtonElement>('button')];
  act(() => buttons()[0].click()); assert.equal(pauses, 1);
  act(() => root?.render(<RunControls {...base} pauseRequested status="Pause requested; finishing the current action" />));
  assert.ok(buttons()[0].disabled); assert.equal(buttons()[0].textContent, 'Pausing…'); assert.equal(buttons()[1].disabled, false);
  act(() => root?.render(<RunControls {...base} paused />)); act(() => buttons()[0].click()); assert.equal(resumes, 1);
  act(() => root?.render(<RunControls {...base} paused status="Resume requested" />)); assert.ok(buttons()[0].disabled);
  act(() => root?.render(<RunControls {...base} reconnecting />)); assert.ok(buttons()[0].disabled); assert.match(dom.textContent!, /Reconnecting/);
  act(() => buttons()[1].click()); assert.equal(stops, 1);
  act(() => root?.render(<RunControls {...base} status="Stopping…" />)); assert.ok(buttons().every((button) => button.disabled));
});
