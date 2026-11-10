import "../../../test/jsdom-env.mjs";
import assert from "node:assert/strict";
import { test } from "node:test";
import { act, Profiler } from "react";
import { createRoot } from "react-dom/client";
import { AvatarPanel } from "./AvatarPanel.tsx";
import { LanguagePicker } from "../voice/LanguagePicker.tsx";
import { useVoiceStore } from "../voice/voiceStore.ts";

test("lip-sync frames do not re-render subscribers of the voice preferences", () => {
    let commits = 0;
    const host = document.createElement("div"); document.body.append(host);
    const root = createRoot(host);
    act(() => root.render(<Profiler id="voice" onRender={() => { commits += 1; }}><AvatarPanel /><LanguagePicker /></Profiler>));
    act(() => useVoiceStore.getState().setSpeech(true, { aa: 0, ih: 0, ou: 0, ee: 0, oh: 0 }));
    const afterSpeaking = commits;
    for (let frame = 1; frame <= 10; frame += 1) act(() => useVoiceStore.getState().setSpeech(true, { aa: frame / 10, ih: 0, ou: 0, ee: 0, oh: 0 }));
    assert.equal(commits, afterSpeaking);
    act(() => useVoiceStore.getState().setSpeech(false));
    act(() => root.unmount());
    host.remove();
});
