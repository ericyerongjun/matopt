import '../../test/jsdom-env.mjs';
import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';
import { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { AnswerModeBadge, ModeSwitch } from './ModeSwitch.tsx';
import { CHAT_MODE_STORAGE_KEY, loadChatMode, setChatMode } from '../utils/chatMode.ts';
import { useChat } from '../hooks/useChat.ts';
import apiClient from '../services/api.ts';
import type { ChatMessage, ChatMode } from '../types/protocol.ts';

// The shared jsdom globals expose storage only on window.
if (!('localStorage' in globalThis)) Object.assign(globalThis, { localStorage: window.localStorage });
let root: Root | null = null; let container: HTMLDivElement | null = null;
const originalFetch = globalThis.fetch; const originalAdapter = apiClient.defaults.adapter;
afterEach(() => { act(() => root?.unmount()); root = null; container?.remove(); container = null; globalThis.fetch = originalFetch; apiClient.defaults.adapter = originalAdapter; window.localStorage.clear(); });
function mount(node: React.ReactNode) { container = document.createElement('div'); document.body.append(container); root = createRoot(container); act(() => root?.render(node)); return container; }

function Persisted() {
  const [mode, setMode] = useState(loadChatMode);
  return <ModeSwitch value={mode} onChange={(next) => setMode(setChatMode(next))} />;
}

test('the mode switch is a keyboard radiogroup that persists the choice', () => {
  assert.equal(loadChatMode(), 'auto');
  const view = mount(<Persisted />);
  const radios = () => [...view.querySelectorAll<HTMLButtonElement>('[role=radio]')];
  assert.equal(view.querySelector('[role=radiogroup]')!.getAttribute('aria-label'), 'Mode');
  assert.deepEqual(radios().map((radio) => [radio.firstChild!.textContent, radio.getAttribute('aria-checked'), radio.tabIndex]),
    [['Auto', 'true', 0], ['Mat', 'false', -1], ['Opt', 'false', -1]]);
  assert.match(radios()[1].title, /never changes your files/);
  act(() => radios()[0].dispatchEvent(new window.KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true, cancelable: true })));
  assert.equal(radios()[1].getAttribute('aria-checked'), 'true'); assert.equal(document.activeElement, radios()[1]);
  assert.equal(window.localStorage.getItem(CHAT_MODE_STORAGE_KEY), 'mat');
  act(() => radios()[2].click());
  assert.equal(loadChatMode(), 'opt');
  window.localStorage.setItem(CHAT_MODE_STORAGE_KEY, 'bogus'); assert.equal(loadChatMode(), 'auto');
});

let hook: ReturnType<typeof useChat>;
function ChatHarness({ mode }: { mode: ChatMode }) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  hook = useChat({ activeId: 'chat-1', messages, setMessages, setActiveId: () => {}, createConversation: async () => ({ id: 'chat-1', title: 'T' }),
    refreshList: async () => {}, provider: null, model: null, reasoning: null, verify: false, mode });
  return null;
}

test('every new turn sends the selected mode in the stream request', async () => {
  apiClient.defaults.adapter = async (config) => ({ data: { run: null }, config, status: 200 });
  const bodies: Array<Record<string, unknown>> = [];
  globalThis.fetch = async (_url, options) => {
    bodies.push(JSON.parse(String(options?.body)));
    const text = ['conversation', 'done'].map((type) => `data: ${JSON.stringify({ type, conversation_id: 'chat-1', message_id: 'm1' })}\n\n`).join('');
    return new Response(text, { headers: { 'Content-Type': 'text/event-stream' } });
  };
  mount(<ChatHarness mode="mat" />);
  await act(async () => { await hook.sendMessage('Explain eigenvalues'); });
  act(() => root?.render(<ChatHarness mode="opt" />));
  await act(async () => { await hook.sendMessage('Refactor fit.py'); });
  assert.deepEqual(bodies.map((body) => body.mode), ['mat', 'opt']);
});

test('answer badges show the mode, automatic choice and a mid-turn switch with the reason', () => {
  const view = mount(<AnswerModeBadge mode={{ mode: 'mat', source: 'user', reason: '' }} />);
  assert.equal(view.textContent!.replace(/Chosen.*/, ''), 'Mat');
  act(() => root?.render(<AnswerModeBadge mode={{ mode: 'opt', source: 'auto', reason: 'Needs to edit files', switched: true, from: 'mat' }} />));
  const badge = view.querySelector<HTMLElement>('.matopt-mode-badge')!;
  assert.equal(badge.querySelector('.matopt-mode-auto')!.textContent, 'Auto');
  assert.match(badge.textContent!, /Mat → Opt/);
  assert.match(badge.title, /Chosen automatically · switched during this answer · Needs to edit files/);
  assert.equal(document.getElementById(badge.getAttribute('aria-describedby')!)!.textContent, badge.title);
});
