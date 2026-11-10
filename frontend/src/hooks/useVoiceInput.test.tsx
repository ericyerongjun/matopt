import '../../test/jsdom-env.mjs';
import assert from 'node:assert/strict';
import { afterEach, beforeEach, test } from 'node:test';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import apiClient from '../services/api.ts';
import { isPushToTalkKey, useVoiceInput } from './useVoiceInput.ts';
import { useVoiceStore } from '../features/voice/voiceStore.ts';

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
afterEach(() => { act(() => root?.unmount()); root = null; dom?.remove(); dom = null; apiClient.defaults.adapter = originalAdapter; Date.now = realNow; useVoiceStore.setState({ pushToTalk: true }); });

function Probe() {
  const voice = useVoiceInput({ language: 'en', onTranscript: (text, via) => heard.push(`${via}:${text}`) });
  toggle = voice.toggle;
  return <span data-state={voice.state} />;
}
let toggle: () => void = () => {};
/** A microphone request the test answers by hand, as the browser's permission prompt does. */
function slowMicrophone() {
  const stopped: number[] = []; const grants: Array<() => void> = [];
  Object.defineProperty(globalThis.navigator, 'mediaDevices', { configurable: true, value: { getUserMedia: () => new Promise((resolve) => {
    const id = grants.length;
    grants.push(() => resolve({ getTracks: () => [{ stop() { stopped.push(id); } }] }));
  }) } });
  return { stopped, grants };
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

const mount = () => { dom = document.createElement('div'); document.body.append(dom); root = createRoot(dom); act(() => root?.render(<Probe />)); };
const state = () => dom?.querySelector('span')?.getAttribute('data-state');

test('releasing the key while the permission prompt is open never leaves the microphone recording', async () => {
  const mic = slowMicrophone(); mount();
  key('keydown', 'AltRight'); await wait(300);
  key('keyup', 'AltRight'); await settle();
  mic.grants[0](); await settle();
  assert.equal(state(), 'idle');
  assert.deepEqual(mic.stopped, [0], 'the granted stream must be released');
});

test('a composer that unmounts while the microphone is opening releases the stream', async () => {
  const mic = slowMicrophone(); mount();
  key('keydown', 'AltRight'); await wait(300);
  act(() => root?.unmount()); root = null;
  mic.grants[0](); await settle();
  assert.deepEqual(mic.stopped, [0]);
});

test('a second click while the microphone is opening cancels it instead of opening another stream', async () => {
  const mic = slowMicrophone(); mount();
  await act(async () => { toggle(); });
  await act(async () => { toggle(); });
  assert.equal(mic.grants.length, 1);
  mic.grants[0](); await settle();
  assert.equal(state(), 'idle');
  assert.deepEqual(mic.stopped, [0]);
});

test('AltGr keeps its typing role outside Apple keyboards, and the same key still works on a Mac', () => {
  assert.equal(isPushToTalkKey({ code: 'AltRight', key: 'AltGraph' }, false), false);
  assert.equal(isPushToTalkKey({ code: 'AltRight', key: 'Alt' }, false), true);
  assert.equal(isPushToTalkKey({ code: 'AltRight', key: 'AltGraph' }, true), true);
  assert.equal(isPushToTalkKey({ code: 'AltRight', key: 'Alt', ctrlKey: true }, true), false);
});

test('the hold-to-talk key can be turned off', async () => {
  useVoiceStore.setState({ pushToTalk: false }); mount();
  key('keydown', 'AltRight'); await wait(300); await settle();
  assert.equal(state(), 'idle');
});
