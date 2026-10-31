import '../../../test/jsdom-env.mjs';
import assert from 'node:assert/strict';
import { afterEach, beforeEach, test } from 'node:test';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import apiClient, { type ApiRequestConfig } from '../../services/api.ts';
import { clearTurnChangeCache } from '../../services/changes.ts';
import type { AssistantContent } from '../../types/protocol.ts';
import { ChangesView } from './ChangesView.tsx';
import { TurnChangesChip } from './TurnChangesChip.tsx';
import { WorkspacePanel } from '../shell/WorkspacePanel.tsx';

let root: Root | null = null; let container: HTMLDivElement | null = null;
const originalAdapter = apiClient.defaults.adapter;
let requests: ApiRequestConfig[] = [];
const diff = {
  turn: 3, truncated: true,
  files: [{ path: 'src/fit.py', status: 'M', added: 2, removed: 1 }, { path: 'plot.png', status: 'A', binary: true }, { path: 'notes.md', status: 'A', untracked: true }, { path: 'old.csv', status: 'D', removed: 4 }],
  diff: ['diff --git a/src/fit.py b/src/fit.py', '--- a/src/fit.py', '+++ b/src/fit.py', '@@ -1,2 +1,3 @@', ' import numpy', '-x = 1', '+x = 2', '+y = 3',
    'diff --git a/old.csv b/old.csv', 'deleted file mode 100644', '--- a/old.csv', '+++ /dev/null', '@@ -1,4 +0,0 @@', '-a', '-b', '-c', '-d'].join('\n'),
};
beforeEach(() => {
  requests = []; clearTurnChangeCache();
  apiClient.defaults.adapter = async (config) => {
    requests.push(config);
    if (config.url === '/conversations/c1/turns/3/diff') return { data: diff, status: 200, config };
    throw Object.assign(new Error('Not Found'), { status: 404 });
  };
});
afterEach(() => { act(() => root?.unmount()); root = null; container?.remove(); container = null; apiClient.defaults.adapter = originalAdapter; });
async function settle() { for (let i = 0; i < 4; i++) await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); }); }
async function mount(node: React.ReactNode) { container = document.createElement('div'); document.body.append(container); root = createRoot(container); act(() => root?.render(node)); await settle(); return container; }
const content = (changes: Partial<AssistantContent> = {}): AssistantContent => ({ parts: [], model: '', provider: '', reasoning: '', usage: {}, status: 'complete', plan: [], stats: { llm_calls: 0, tool_calls: 0 }, ...changes });

test('the changes view lists A/M/D files with counts, notices and accessible per-file diffs', async () => {
  const view = await mount(<ChangesView conversationId="c1" turn={3} highlight={false} />);
  assert.equal(requests[0].url, '/conversations/c1/turns/3/diff');
  const rows = [...view.querySelectorAll<HTMLLIElement>('.matopt-change-file')];
  assert.deepEqual(rows.map((row) => row.querySelector('.matopt-change-path')!.textContent), ['src/fit.py', 'plot.png', 'notes.md', 'old.csv']);
  assert.deepEqual(rows.map((row) => row.querySelector('.matopt-change-status .sr-only')!.textContent), ['Modified', 'Added', 'Added', 'Deleted']);
  assert.match(view.querySelector('.matopt-changes-summary')!.textContent!, /4 files changed\+2−5/);
  assert.match(view.textContent!, /truncated/);
  const open = (row: HTMLLIElement) => act(() => { const details = row.querySelector('details')!; details.open = true; details.dispatchEvent(new window.Event('toggle')); });
  open(rows[0]);
  const pre = rows[0].querySelector('pre.matopt-diff')!;
  assert.equal(pre.getAttribute('aria-label'), 'Changes to src/fit.py');
  assert.deepEqual([...pre.querySelectorAll('.matopt-diff-line')].map((line) => line.getAttribute('data-kind')), ['hunk', 'context', 'del', 'add', 'add']);
  assert.match(pre.textContent!, /Added: \+y = 3/);
  open(rows[1]); assert.match(rows[1].textContent!, /Binary file/);
  open(rows[2]); assert.match(rows[2].textContent!, /untracked/i);
  open(rows[3]); assert.match(rows[3].querySelector('pre')!.textContent!, /Removed: -d/);
});

test('the changes view explains unavailable servers and empty selection', async () => {
  const view = await mount(<ChangesView conversationId="c1" turn={9} highlight={false} />);
  assert.match(view.textContent!, /not available on this server/);
  act(() => root?.render(<ChangesView conversationId="c1" turn={null} />));
  assert.match(view.textContent!, /No changes selected/);
});

test('the footer chip uses live changes, lazily fetches saved turns once, and hides without changes', async () => {
  const opened: number[] = [];
  const view = await mount(<TurnChangesChip content={content({ turn: 5, changes: { turn: 5, files: [{ path: 'a', status: 'A', added: 1 }] } })} conversationId="c1" streaming={false} onOpen={(turn) => opened.push(turn)} />);
  assert.equal(requests.length, 0);
  assert.equal(view.querySelector('button')!.getAttribute('aria-label'), '1 file changed. Review changes');
  act(() => view.querySelector('button')!.click()); assert.deepEqual(opened, [5]);
  act(() => root?.render(<TurnChangesChip content={content({ turn: 3 })} conversationId="c1" streaming={false} onOpen={() => {}} />)); await settle();
  assert.match(view.textContent!, /4 files changed/);
  act(() => root?.render(<><TurnChangesChip content={content({ turn: 3 })} conversationId="c1" streaming={false} onOpen={() => {}} /><TurnChangesChip content={content({ turn: 3 })} conversationId="c1" streaming={false} onOpen={() => {}} /></>)); await settle();
  assert.equal(requests.length, 1, 'summaries are cached per turn');
  act(() => root?.render(<TurnChangesChip content={content({ turn: 9 })} conversationId="c1" streaming={false} onOpen={() => {}} />)); await settle();
  assert.equal(view.querySelector('button'), null);
});

test('the workspace Changes tab joins arrow-key navigation and stays mounted', async () => {
  const views: string[] = [];
  const view = await mount(<WorkspacePanel view="results" onViewChange={(next) => views.push(next)} onClose={() => {}} results={<p>r</p>} files={<p>f</p>} changes={<input id="changes-probe" />} changeCount={4} />);
  const tabs = [...view.querySelectorAll<HTMLButtonElement>('[role=tab]')];
  assert.deepEqual(tabs.map((tab) => tab.textContent), ['Results', 'Files', 'Changes4']);
  assert.ok(view.querySelector('#changes-probe')?.closest<HTMLElement>('[role=tabpanel]')?.hidden);
  for (const key of ['ArrowLeft', 'End', 'ArrowRight']) act(() => tabs[0].dispatchEvent(new window.KeyboardEvent('keydown', { key, bubbles: true, cancelable: true })));
  assert.deepEqual(views, ['changes', 'changes', 'files']);
});
