import '../../../test/jsdom-env.mjs';
import assert from 'node:assert/strict';
import { afterEach, beforeEach, test } from 'node:test';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import apiClient, { type ApiRequestConfig } from '../../services/api.ts';
import { SettingsDialog } from './SettingsDialog.tsx';

// Radix focus scopes walk the tree with NodeFilter, which the shared jsdom globals omit.
Object.assign(globalThis, { NodeFilter: window.NodeFilter });

let root: Root | null = null; let container: HTMLDivElement | null = null;
const originalAdapter = apiClient.defaults.adapter;
let requests: ApiRequestConfig[] = [];
let routes: Record<string, (config: ApiRequestConfig) => unknown> = {};
const notFound = () => { throw Object.assign(new Error('Not Found'), { status: 404 }); };
const settings = {
  features: [
    { key: 'memories', name: 'Memories', description: 'Remember across chats.', stage: 'beta', enabled: false, live: true, origin: 'default' },
    { key: 'code_mode', name: 'Code mode', description: 'Tools inside Python.', stage: 'experimental', enabled: true, live: false, origin: 'user' },
  ],
  security: { sandbox: { mode: 'workspace-write', network: 'disabled', backend: 'seatbelt', available: true }, hooks: { enabled: true, path: '/etc/matopt/hooks.json', events: ['pre_tool_use'] },
    exec_rules: { path: '/etc/matopt/rules', rules: 3 }, approvals_reviewer: 'guardian', redact_tool_output: true },
  instructions: { operator: { chars: 1200, sources: ['/srv/project/AGENTS.md'] } },
  mcp: { servers: [{ name: 'wolfram', state: 'connected', tools: 4, resources: 1 }, { name: 'broken', state: 'failed', tools: 0, resources: 0, error: 'spawn failed' }] },
  config: { path: '/home/u/.matopt/config.toml', exists: true, profile: 'dev' },
};

beforeEach(() => {
  requests = [];
  routes = {
    'get /settings': () => settings,
    'put /features/memories': (config) => ({ ...settings.features[0], enabled: JSON.parse(String(config.data)).enabled }),
    'get /plugins': () => ({ plugins: [{ id: 'p1', name: 'Plotting', version: '1.0', description: 'Charts', enabled: true, skills: ['plot'], mcp_servers: [], hooks: [], errors: [] }] }),
    'post /plugins/p1/disable': () => ({ plugin: { id: 'p1', name: 'Plotting', enabled: false }, restart_required: true }),
    'get /memories': () => ({ enabled: true, summary: 'Prefers SI units.', memory_md: '', notes: [{ id: 'n1', title: '', text: 'Use NumPy.', created_at: 1 }], conversations_learned: 2 }),
    'post /memories/notes': (config) => ({ id: 'n2', title: '', text: JSON.parse(String(config.data)).text, created_at: 2 }),
    'delete /memories': () => ({ cleared: true }),
    'get /migration/detect': () => ({ items: [
      { id: 'i1', source: 'claude_code', scope: 'user', item_type: 'mcp_server', path: '~/.claude.json', summary: 'MCP server “github”', count: 1, conflicts: [] },
      { id: 'i2', source: 'cursor', scope: 'project', item_type: 'skill', path: '.cursor/rules', summary: 'Rules', count: 2, conflicts: ['rules'] }] }),
    'post /migration/import': (config) => ({ results: JSON.parse(String(config.data)).items.map((id: string) => ({ id, status: 'imported', detail: '' })) }),
  };
  apiClient.defaults.adapter = async (config) => {
    requests.push(config);
    const route = routes[`${config.method} ${config.url}`];
    if (!route) notFound();
    return { data: route!(config), status: 200, config };
  };
});
afterEach(() => { act(() => root?.unmount()); root = null; container?.remove(); container = null; apiClient.defaults.adapter = originalAdapter; });

async function settle() { for (let i = 0; i < 5; i++) await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); }); }
async function mount(node: React.ReactNode) { container = document.createElement('div'); document.body.append(container); root = createRoot(container); act(() => root?.render(node)); await settle(); }
const dialog = () => document.querySelector<HTMLElement>('[role=dialog]')!;
const tab = (name: string) => [...dialog().querySelectorAll<HTMLButtonElement>('[role=tab]')].find((button) => button.textContent === name)!;
const panel = () => dialog().querySelector<HTMLElement>('[role=tabpanel]:not([hidden])')!;
const button = (scope: HTMLElement, text: string | RegExp) => [...scope.querySelectorAll<HTMLButtonElement>('button')].find((entry) => typeof text === 'string' ? entry.textContent === text : text.test(entry.textContent ?? ''))!;
function type(element: HTMLInputElement | HTMLTextAreaElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(element), 'value')!.set!;
  act(() => { setter.call(element, value); element.dispatchEvent(new window.Event('input', { bubbles: true })); });
}

test('features show stages, restart notes and toggle through PUT /features/{key}', async () => {
  await mount(<SettingsDialog open onOpenChange={() => {}} />);
  assert.equal(tab('Features').getAttribute('aria-selected'), 'true');
  const switches = panel().querySelectorAll<HTMLButtonElement>('[role=switch]');
  assert.equal(switches.length, 2);
  assert.equal(switches[0].getAttribute('aria-checked'), 'false');
  assert.match(panel().textContent!, /Beta/); assert.match(panel().textContent!, /Experimental/); assert.match(panel().textContent!, /Applies after restart/);
  act(() => switches[0].click()); await settle();
  const put = requests.find((request) => request.method === 'put')!;
  assert.equal(put.url, '/features/memories'); assert.deepEqual(JSON.parse(String(put.data)), { enabled: true });
  assert.equal(panel().querySelectorAll('[role=switch]')[0].getAttribute('aria-checked'), 'true');
});

test('security is read-only and sections are keyboard navigable', async () => {
  await mount(<SettingsDialog open onOpenChange={() => {}} />);
  act(() => tab('Features').dispatchEvent(new window.KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true })));
  assert.equal(tab('Security').getAttribute('aria-selected'), 'true'); assert.equal(document.activeElement, tab('Security'));
  const text = panel().textContent!;
  assert.match(text, /set by the operator/); assert.match(text, /workspace-write/); assert.match(text, /Automatic review/); assert.match(text, /3 rules/);
  assert.equal(panel().querySelectorAll('input, [role=switch], select').length, 0);
  act(() => tab('Security').dispatchEvent(new window.KeyboardEvent('keydown', { key: 'End', bubbles: true })));
  assert.match(panel().textContent!, /AGENTS\.md/);
});

test('integrations list MCP state, change plugins and import detected items', async () => {
  await mount(<SettingsDialog open onOpenChange={() => {}} initialSection="integrations" />);
  const view = panel();
  assert.match(view.textContent!, /wolfram/); assert.match(view.textContent!, /Connected/); assert.match(view.textContent!, /spawn failed/);
  const plugin = view.querySelector<HTMLButtonElement>('[aria-label="Enable Plotting"]')!;
  act(() => plugin.click()); await settle();
  assert.ok(requests.some((request) => request.method === 'post' && request.url === '/plugins/p1/disable'));
  assert.match(view.textContent!, /Restart the MatOpt server/);
  act(() => button(view, 'Find importable settings').click()); await settle();
  const boxes = view.querySelectorAll<HTMLInputElement>('input[type=checkbox]');
  assert.equal(boxes.length, 2); assert.equal(boxes[0].checked, true); assert.equal(boxes[1].checked, false, 'conflicting items start unselected');
  assert.match(view.textContent!, /Claude Code/); assert.match(view.textContent!, /Conflicts with existing/);
  act(() => button(view, 'Import 1 selected').click()); await settle();
  const imported = requests.find((request) => request.url === '/migration/import')!;
  assert.deepEqual(JSON.parse(String(imported.data)), { items: ['i1'] });
  assert.match(view.querySelector('.matopt-import-result')!.textContent!, /Imported/);
});

test('memories add notes and clear only after confirmation', async () => {
  await mount(<SettingsDialog open onOpenChange={() => {}} initialSection="memories" />);
  const view = panel();
  assert.match(view.textContent!, /Prefers SI units/); assert.match(view.textContent!, /Use NumPy/);
  const field = view.querySelector<HTMLTextAreaElement>('textarea')!;
  type(field, 'Plot in SVG.');
  act(() => button(view, 'Add').click()); await settle();
  assert.match(view.textContent!, /Plot in SVG/);
  act(() => button(view, /Clear all memories/).click());
  assert.equal(requests.some((request) => request.method === 'delete' && request.url === '/memories'), false);
  act(() => button(view, 'Clear all').click()); await settle();
  assert.ok(requests.some((request) => request.method === 'delete' && request.url === '/memories'));
});

test('a server without the settings API explains what is unavailable', async () => {
  routes = {};
  await mount(<SettingsDialog open onOpenChange={() => {}} />);
  assert.match(panel().textContent!, /Features are not available on this server/);
  act(() => tab('Memories').click());
  assert.match(panel().textContent!, /Memories are not available on this server/);
});
