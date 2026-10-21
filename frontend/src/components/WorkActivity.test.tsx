import '../../test/jsdom-env.mjs';
import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { WorkDisclosure, ActivityPanel, SafeWorkTimeline } from './WorkActivity.tsx';
import type { PresentedWork } from '../services/presentation.ts';
let root: Root | null = null; let dom: HTMLDivElement | null = null;
afterEach(() => { act(() => root?.unmount()); root = null; dom?.remove(); dom = null; });
const work = (): PresentedWork => ({ status: 'running', toolCount: 7, verification: { status: 'not_requested', rounds: 0 }, steps: [], startedAt: Date.now() / 1000,
  progress: { id: 'latest', seq: 12, text: 'Three sources are ready; checking the remaining calculations.', created_at: 12, phase: 'work' },
  milestones: Array.from({ length: 8 }, (_, index) => ({ id: `update-${index}`, seq: index, text: `Completed milestone ${index}`, created_at: index, phase: 'work' as const })),
  items: Array.from({ length: 7 }, (_, index) => ({ id: `call-${index}`, parent_id: null, seq: index, kind: 'tool' as const, phase: 'work' as const, label: `Read source ${index + 1}`, purpose: 'Find evidence.', status: 'running' as const, started_at: 10, updated_at: 11, finished_at: null, attempt: 1, outputs: [] })) });
function mount(node: React.ReactNode) { dom = document.createElement('div'); document.body.append(dom); root = createRoot(dom); act(() => root?.render(node)); return dom; }
test('parallel actions show three by default while expansion preserves each identity', () => {
  const value = work(); const view = mount(<WorkDisclosure work={value} />);
  assert.equal(view.querySelectorAll('.matopt-active-actions > li').length, 3);
  assert.equal(view.querySelector('.matopt-more-actions')?.textContent, '4 more active actions');
  act(() => view.querySelector<HTMLButtonElement>('.matopt-more-actions')!.click());
  assert.equal(view.querySelectorAll('.matopt-active-actions > li').length, 7);
  act(() => root?.render(<WorkDisclosure work={{ ...value, progress: { ...value.progress!, text: 'The sources are ready.' } }} />));
  assert.equal(view.querySelectorAll('.matopt-active-actions > li').length, 7);
  assert.deepEqual([...view.querySelectorAll('.matopt-active-actions > li')].map((item) => item.getAttribute('data-activity-id')), value.items.map((item) => item.id));
});
test('milestones are bounded and a manually opened work record stays open after completion', () => {
  const value = work(); const view = mount(<WorkDisclosure work={value} />);
  act(() => view.querySelector<HTMLButtonElement>('.matopt-work-trigger')!.click());
  assert.equal(view.querySelectorAll('.matopt-milestones > li').length, 6);
  assert.match(view.querySelector('.matopt-milestones')!.textContent!, /Completed milestone 2/);
  act(() => root?.render(<WorkDisclosure work={{ ...value, status: 'complete', durationMs: 9000, items: value.items.map((item) => ({ ...item, status: 'completed', finished_at: 19 })) }} />));
  assert.equal(view.querySelector('.matopt-work-trigger')?.getAttribute('aria-expanded'), 'true');
  assert.equal(view.querySelector('.matopt-active-actions'), null); assert.equal(view.querySelector('.matopt-work-spinning'), null);
  assert.match(view.textContent!, /Worked for 9s/);
});
test('terminal summaries distinguish elapsed work from failure and stopping', () => {
  const value = work(); const view = mount(<WorkDisclosure work={{ ...value, status: 'error', durationMs: 38000 }} />);
  assert.equal(view.querySelector('.matopt-work-trigger')?.getAttribute('aria-label'), 'Couldn’t finish · 38s · 7 actions');
  act(() => root?.render(<WorkDisclosure work={{ ...value, status: 'stopped', durationMs: 38000 }} />));
  assert.equal(view.querySelector('.matopt-work-trigger')?.getAttribute('aria-label'), 'Stopped after 38s · 7 actions');
  act(() => root?.render(<WorkDisclosure work={{ ...value, status: 'error', startedAt: undefined }} />));
  assert.equal(view.querySelector('.matopt-work-trigger')?.getAttribute('aria-label'), 'Couldn’t finish · 7 actions', 'unknown durations are omitted');
});
test('activity prioritizes the current turn and filters do not repeat milestone notices', () => {
  const earlier = { ...work(), status: 'complete' as const, durationMs: 1000 };
  const current = work(); const view = mount(<ActivityPanel turns={[{ id: 'earlier', work: earlier }, { id: 'current', work: current }]} conversationId="test" />);
  assert.equal(view.querySelector('h3')?.textContent, 'Current taskIn progress');
  const failures = [...view.querySelectorAll<HTMLButtonElement>('.matopt-activity-filters button')].find((button) => button.textContent === 'Failures')!;
  act(() => failures.click()); assert.equal(view.querySelector('.matopt-milestones'), null); assert.match(view.textContent!, /No failed actions/);
});
test('Activity keeps milestone history collapsed without losing the latest public update or manual history expansion', () => {
  const value = work();
  const view = mount(<ActivityPanel turns={[{ id: 'current', work: value }]} conversationId="test" />);
  const history = view.querySelector<HTMLDetailsElement>('.matopt-update-history')!;
  assert.equal(history.open, false);
  assert.equal(view.querySelector('.matopt-inspector-current')?.textContent, value.progress?.text);
  assert.equal(history.querySelectorAll('li').length, 6);
  act(() => history.querySelector('summary')!.click());
  assert.equal(history.open, true);
  const completed = { ...value, status: 'complete' as const, durationMs: 9000 };
  act(() => root?.render(<ActivityPanel turns={[{ id: 'current', work: completed }]} conversationId="test" />));
  assert.equal(view.querySelector('.matopt-update-history'), history, 'completion preserves the same history disclosure');
  assert.equal(history.open, true);
  assert.equal(view.querySelector('.matopt-inspector-current'), null, 'a completed task does not present stale running commentary as current');
});

function nestedWork(): PresentedWork {
  const value = work(); const item = value.items[0];
  return { ...value, items: [
    { ...item, id: 'phase', kind: 'phase', label: 'Review documents', status: 'completed', finished_at: 20 },
    { ...item, id: 'agent', parent_id: 'phase', kind: 'agent', label: 'Compare sources', status: 'completed', finished_at: 20 },
    { ...item, id: 'failed', parent_id: 'agent', label: 'Read unavailable source', status: 'failed', finished_at: 15, summary: 'The source was unavailable.' },
    { ...item, id: 'ready', parent_id: 'agent', label: 'Read available source', status: 'completed', finished_at: 20, outputs: [{ kind: 'file', label: 'Extracted text', path: 'source.txt' }] },
    { ...item, id: 'unrelated', label: 'Calculate estimate' },
  ] };
}
const detailsFor = (view: Element, id: string) => view.querySelector<HTMLDetailsElement>(`[data-activity-id="${id}"] > details`)!;
const toggle = (details: HTMLDetailsElement) => act(() => details.querySelector<HTMLElement>(':scope > summary')!.click());

test('collapsing an activity branch hides descendants together and retains their expansion after updates', () => {
  const value = nestedWork(); const view = mount(<SafeWorkTimeline work={value} />);
  const phase = detailsFor(view, 'phase'); const agent = detailsFor(view, 'agent'); const failed = detailsFor(view, 'failed');
  assert.equal(phase.open, false); assert.equal(agent.open, false);
  assert.equal(agent.closest('[data-activity-id="phase"] > details'), phase, 'children belong to the parent disclosure content');
  toggle(phase); toggle(agent); toggle(failed);
  assert.equal(failed.open, true);
  toggle(phase);
  assert.equal(phase.open, false); assert.equal(agent.open, true); assert.equal(failed.open, true);
  act(() => root?.render(<SafeWorkTimeline work={{ ...value, status: 'complete', items: value.items.map(item => ({ ...item, updated_at: 30 })) }} />));
  assert.equal(detailsFor(view, 'phase'), phase); assert.equal(phase.open, false);
  toggle(phase);
  assert.equal(detailsFor(view, 'failed'), failed); assert.equal(failed.open, true, 'a parent collapse does not discard child choices');
});

test('filters retain and reveal matching actions beneath contextual ancestors, without unrelated siblings', () => {
  const value = nestedWork(); const view = mount(<SafeWorkTimeline work={value} filter="failures" />);
  assert.deepEqual([...view.querySelectorAll('[data-activity-id]')].map(node => node.getAttribute('data-activity-id')), ['phase', 'agent', 'failed']);
  assert.deepEqual([...view.querySelectorAll('[data-activity-context]')].map(node => node.getAttribute('data-activity-id')), ['phase', 'agent']);
  assert.equal(detailsFor(view, 'phase').open, true); assert.equal(detailsFor(view, 'agent').open, true);
  toggle(detailsFor(view, 'agent'));
  act(() => root?.render(<SafeWorkTimeline work={value} filter="outputs" conversationId="fixture" />));
  assert.deepEqual([...view.querySelectorAll('[data-activity-id]')].map(node => node.getAttribute('data-activity-id')), ['phase', 'agent', 'ready']);
  assert.equal(detailsFor(view, 'agent').open, true, 'another filter has its own disclosure choice');
  assert.equal(view.querySelector('a')?.getAttribute('href'), '/api/files/fixture/source.txt');
  act(() => root?.render(<SafeWorkTimeline work={value} filter="failures" />));
  assert.equal(detailsFor(view, 'agent').open, false, 'returning to a filter preserves the manual collapse');
  act(() => root?.render(<SafeWorkTimeline work={value} filter="all" />));
  assert.equal(detailsFor(view, 'phase').open, false, 'revealing a filtered match never expands the All view');
});

test('orphaned, cross-phase, and cyclic parent references keep every public action inspectable once', () => {
  const value = nestedWork(); const item = value.items[0];
  value.items = [
    { ...item, id: 'root', parent_id: null },
    { ...item, id: 'orphan', parent_id: 'missing', kind: 'tool' },
    { ...item, id: 'cross-phase', phase: 'check', parent_id: 'root', kind: 'tool' },
    { ...item, id: 'cycle-a', parent_id: 'cycle-b', kind: 'agent' },
    { ...item, id: 'cycle-b', parent_id: 'cycle-a', kind: 'tool' },
  ];
  const view = mount(<SafeWorkTimeline work={value} />);
  const ids = [...view.querySelectorAll('[data-activity-id]')].map(node => node.getAttribute('data-activity-id'));
  assert.equal(ids.length, value.items.length); assert.equal(new Set(ids).size, value.items.length);
  assert.equal(view.querySelector('[data-activity-id="root"] [data-activity-id="cross-phase"]'), null);
});

test('Active includes queued and paused actions without mislabeling them as running', () => {
  const value = work(); value.items = value.items.slice(0, 3).map((item, index) => ({ ...item, status: index === 0 ? 'queued' : index === 1 ? 'paused' : 'completed' }));
  const view = mount(<ActivityPanel turns={[{ id: 'current', work: value }]} conversationId="test" />);
  const active = [...view.querySelectorAll<HTMLButtonElement>('.matopt-activity-filters button')].find(button => button.textContent === 'Active')!;
  act(() => active.click());
  assert.equal(active.getAttribute('aria-pressed'), 'true');
  assert.deepEqual([...view.querySelectorAll('[data-activity-id]')].map(node => node.getAttribute('data-activity-status')), ['queued', 'paused']);
  assert.match(detailsFor(view, 'call-0').textContent!, /Queued/); assert.match(detailsFor(view, 'call-1').textContent!, /Paused/);
});

test('filtered histories show only matching turns and one global empty state while retaining disclosure choices', () => {
  const current = work(); const earlier = nestedWork();
  const view = mount(<ActivityPanel turns={[{ id: 'earlier', work: earlier }, { id: 'current', work: current }]} conversationId="test" />);
  const rows = [...view.querySelectorAll<HTMLLIElement>('.matopt-activity-turns > li')];
  toggle(detailsFor(rows[0], 'call-0'));
  const clickFilter = (name: string) => act(() => [...view.querySelectorAll<HTMLButtonElement>('.matopt-activity-filters button')].find(button => button.textContent === name)!.click());
  clickFilter('Failures');
  assert.equal(rows[0].hidden, true); assert.equal(rows[1].hidden, false);
  assert.equal(rows[1].querySelector('h3')?.textContent, 'Earlier turn 1In progress', 'a matching earlier turn does not become the current task');
  assert.equal(detailsFor(rows[1], 'phase').open, true, 'the match retains its context path');
  clickFilter('All');
  assert.equal(detailsFor(rows[0], 'call-0').open, true, 'a hidden turn retains its per-filter manual disclosure choice');
  const noOutputs = [current, { ...earlier, items: earlier.items.map(item => ({ ...item, outputs: [] })) }];
  act(() => root?.render(<ActivityPanel turns={noOutputs.map((work, index) => ({ id: index ? 'earlier' : 'current', work }))} conversationId="test" />));
  clickFilter('Outputs');
  assert.equal(view.querySelectorAll('.matopt-public-activity > .matopt-work-empty[role="status"]').length, 1);
  assert.equal(view.querySelector('.matopt-public-activity > .matopt-work-empty')?.textContent, 'No outputs yet.');
  assert.equal(view.querySelector<HTMLOListElement>('.matopt-activity-turns')?.hidden, true);
});
test('a running card shows the agent plan as an inline checklist and drops replaced steps', () => {
  const milestone = (id: string, label: string, status: 'completed' | 'running' | 'queued' | 'cancelled') => ({ id, parent_id: null, seq: 0, kind: 'phase' as const, phase: 'work' as const, label, purpose: 'Task milestone', status, started_at: 1, updated_at: 1, finished_at: null, attempt: 1, outputs: [] });
  const value = { ...work(), items: [milestone('t1', 'Derive the gradient', 'completed'), milestone('t2', 'Run BFGS', 'running'), milestone('t3', 'Plot convergence', 'queued'), milestone('t4', 'Old step', 'cancelled')] };
  const view = mount(<WorkDisclosure work={value} />);
  const steps = [...view.querySelectorAll('.matopt-work-plan > li')];
  assert.deepEqual(steps.map((step) => [step.textContent, step.getAttribute('data-activity-status')]), [['Derive the gradient', 'completed'], ['Run BFGS', 'running'], ['Plot convergence', 'queued']]);
  act(() => root?.render(<WorkDisclosure work={{ ...value, status: 'complete', durationMs: 5000 }} />));
  assert.equal(view.querySelector('.matopt-work-plan'), null, 'the finished card collapses to its summary');
});
