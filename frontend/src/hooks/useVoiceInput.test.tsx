import '../../test/jsdom-env.mjs';
import assert from 'node:assert/strict';
import { afterEach, beforeEach, test } from 'node:test';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import apiClient from '../services/api.ts';
import { useVoiceInput } from './useVoiceInput.ts';

class FakeRecorder {
  static isTypeSupported = (type: string) => type === 'audio/webm';
  state = 'inactive';
  mimeType = 'audio/webm';
  ondataavailable: ((event: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;
  start() { this.state = 'recording'; }
  stop() { this.state = 'inactive'; this.ondataavailable?.({ data: new Blob(['audio'], { type: 'audio/webm' }) }); void this.onstop?.(); }
}

let root: Root | null = null; let dom: HTMLDivElement | null = null;
const originalAdapter = apiClient.defaults.adapter;
let heard: string[] = []; let requests = 0; let clock = 0;
const realNow = Date.now;

beforeEach(() => {
  heard = []; requests = 0; clock = 1_000;
  Date.now = () => clock;
  Object.assign(globalThis, { MediaRecorder: FakeRecorder });
  Object.defineProperty(globalThis.navigator, 'mediaDevices', { configurable: true,
    value: { getUserMedia: async () => ({ getTracks: () => [{ stop() {} }] }) } });
  apiClient.defaults.adapter = async (config) => { requests += 1; return { data: { text: 'hello world', language: 'en', language_probability: 1, duration: 1 }, status: 200, config }; };
});
afterEach(() => { act(() => root?.unmount()); root = null; dom?.remove(); dom = null; apiClient.defaults.adapter = originalAdapter; Date.now = realNow; });

function Probe() {
  const voice = useVoiceInput({ language: 'en', onTranscript: (text, via) => heard.push(`${via}:${text}`) });
  return <span data-state={voice.state} />;
}
const settle = () => act(async () => { for (let i = 0; i < 5; i += 1) await new Promise((resolve) => setTimeout(resolve, 0)); });
const key = (type: 'keydown' | 'keyup', code: string) => window.dispatchEvent(new window.KeyboardEvent(type, { code, key: code === 'AltRight' ? 'Alt' : 'e' }));
const wait = (ms: number) => act(async () => { await new Promise((resolve) => setTimeout(resolve, ms)); });

test('holding Right Option records and releasing transcribes into the composer', async () => {
  dom = document.createElement('div'); document.body.append(dom); root = createRoot(dom);
  act(() => root?.render(<Probe />));
  key('keydown', 'AltRight');
  await wait(300); await settle();
  assert.equal(dom.querySelector('span')?.getAttribute('data-state'), 'recording');
  clock += 1_000;
  key('keyup', 'AltRight');
  await settle();
  assert.deepEqual(heard, ['hotkey:hello world']);
  assert.equal(dom.querySelector('span')?.getAttribute('data-state'), 'idle');
});

test('a quick tap or Option+key typing never records or transcribes', async () => {
  dom = document.createElement('div'); document.body.append(dom); root = createRoot(dom);
  act(() => root?.render(<Probe />));
  key('keydown', 'AltRight'); key('keyup', 'AltRight');
  await wait(300);
  key('keydown', 'AltRight'); key('keydown', 'KeyE'); key('keyup', 'KeyE');
  await wait(300); key('keyup', 'AltRight'); await settle();
  assert.equal(requests, 0);
  assert.deepEqual(heard, []);
});
