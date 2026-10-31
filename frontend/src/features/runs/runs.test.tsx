import '../../../test/jsdom-env.mjs';
import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import apiClient from '../../services/api.ts';
import { ApprovalCard } from './ApprovalCard.tsx';
import { AgentsPanel, mergeAgents } from './AgentsPanel.tsx';
import { JobsPanel } from './JobsPanel.tsx';
import { WorkTranscript } from '../../components/WorkActivity.tsx';

let root: Root | null = null; let container: HTMLDivElement | null = null;
const originalAdapter = apiClient.defaults.adapter;
afterEach(() => { act(() => root?.unmount()); root = null; container?.remove(); container = null; apiClient.defaults.adapter = originalAdapter; });
async function settle() { for (let i = 0; i < 4; i++) await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); }); }
async function mount(node: React.ReactNode) { container = document.createElement('div'); document.body.append(container); root = createRoot(container); act(() => root?.render(node)); await settle(); return container; }
const approval = { id: 'ap1', tool: 'bash', title: 'Delete the build folder', summary: 'rm -rf build', reason: 'Writes outside the workspace.', risk: 'high' as const };

test('the approval card shows the request and sends one decision at a time', async () => {
  const decisions: string[] = []; let release: () => void = () => {};
  const view = await mount(<ApprovalCard approval={approval} onDecide={(decision) => { decisions.push(decision); return new Promise<void>((resolve) => { release = resolve; }); }} />);
  const group = view.querySelector('[role=group]')!;
  assert.equal(group.getAttribute('aria-labelledby'), view.querySelector('h3')!.id);
  assert.match(group.textContent!, /Delete the build folder/); assert.match(group.textContent!, /Run a command/); assert.match(group.textContent!, /High risk/);
  assert.match(group.textContent!, /Writes outside the workspace/);
  const [deny, allow] = [...view.querySelectorAll<HTMLButtonElement>('button')];
  assert.equal(deny.textContent, 'Deny'); assert.equal(allow.textContent, 'Allow');
  act(() => allow.click());
  assert.deepEqual(decisions, ['allow']); assert.ok([...view.querySelectorAll<HTMLButtonElement>('button')].every((button) => button.disabled));
  await act(async () => { release(); }); await settle();
  assert.match(view.querySelector('[role=status]')!.textContent!, /Allowed/);
});

test('a failed decision is reported and can be retried', async () => {
  let attempts = 0;
  const view = await mount(<ApprovalCard approval={approval} onDecide={async () => { attempts++; throw new Error('Run finished'); }} />);
  act(() => view.querySelectorAll<HTMLButtonElement>('button')[0].click()); await settle();
  assert.equal(view.querySelector('[role=alert]')!.textContent, 'Run finished');
  act(() => view.querySelectorAll<HTMLButtonElement>('button')[0].click()); await settle();
  assert.equal(attempts, 2);
});

test('agents merge fetched records with live events and show only public summaries', async () => {
  apiClient.defaults.adapter = async (config) => ({ status: 200, config, data: { agents: [
    { id: 'a1', parent_id: null, path: 'root/a1', nickname: 'Ada', role: 'researcher', status: 'running', task: 'Find sources', summary: '', created_at: 1, updated_at: 1 },
  ] } });
  const view = await mount(<AgentsPanel conversationId="c1" running={false} live={[{ id: 'a1', path: 'root/a1', nickname: 'Ada', role: 'researcher', kind: 'completed', status: 'completed', task: 'Find sources', summary: 'Two sources agree.' },
    { id: 'a2', path: 'root/a2', nickname: '', role: 'coder', kind: 'started', status: 'running', task: 'Fit the model' }]} />);
  const rows = [...view.querySelectorAll('li')];
  assert.equal(rows.length, 2);
  assert.match(rows[0].textContent!, /Ada.*researcher.*Done.*Find sources.*Two sources agree/);
  assert.match(rows[1].textContent!, /root\/a2.*coder.*Running.*Fit the model/);
  assert.equal(rows[1].querySelector('.matopt-runs-summary'), null);
  assert.equal(mergeAgents([{ id: 'x', parent_id: null, path: '', nickname: '', role: '', status: 'completed', task: '', summary: 'Done', created_at: 0, updated_at: 0 }],
    [{ id: 'x', path: '', nickname: '', role: '', kind: 'started', status: 'running', task: '' }])[0].status, 'completed');
});

test('jobs list running and finished background commands, and a missing endpoint is explained', async () => {
  apiClient.defaults.adapter = async (config) => ({ status: 200, config, data: { jobs: [
    { session_id: 3, command: 'python serve.py', running: true, exit_code: null, started_at: 0, duration_ms: 65000 },
    { session_id: 'b', command: 'make test', running: false, exit_code: 2, started_at: 0, duration_ms: 1000 },
  ] } });
  const view = await mount(<JobsPanel conversationId="c1" running={false} />);
  const rows = [...view.querySelectorAll('li')];
  assert.match(rows[0].textContent!, /python serve\.py.*Running.*Session 3 · 1m 5s/);
  assert.match(rows[1].textContent!, /make test.*Exit 2/);
  act(() => root?.unmount()); root = null;
  apiClient.defaults.adapter = async () => { throw Object.assign(new Error('Not Found'), { status: 404 }); };
  const missing = await mount(<JobsPanel conversationId="c1" running={false} />);
  assert.match(missing.textContent!, /not available on this server/);
});

test('the transcript marks background commands and guardian outcomes for their call', async () => {
  const view = await mount(<WorkTranscript live entries={[
    { id: 'action:c1', kind: 'action', order: [1, 1], tool: 'bash', title: 'Ran a command', output: 'Process running with session ID 4\nWall time: 0.5 seconds\nOutput:\nlistening', status: 'done' },
    { id: 'action:c2', kind: 'action', order: [1, 2], tool: 'bash', title: 'Ran a command', output: 'Exit code: 0\nOutput:\nok', status: 'done' },
  ]} reviews={[{ call_id: 'c2', tool: 'bash', outcome: 'deny', risk_level: 'high', user_authorization: 'none', rationale: 'Touches system files.' }]} />);
  const rows = [...view.querySelectorAll('.matopt-tx-action')];
  assert.equal(rows[0].querySelector('.matopt-tx-badge')!.textContent, 'Background · running');
  assert.equal(rows[1].querySelector('.matopt-tx-badge')!.textContent, 'Review denied');
  assert.match(rows[1].querySelector('.matopt-tx-guardian')!.textContent!, /high risk: Touches system files/);
});
