import "../../../test/jsdom-env.mjs";
import assert from "node:assert/strict";
import { afterEach, mock, test } from "node:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { AssistantContent, ChatMessage } from "../../types/protocol.ts";
import { useRunSpeech } from "./useRunSpeech.ts";
import { useVoiceStore } from "./voiceStore.ts";

const answer = (id: string, status: AssistantContent["status"]): ChatMessage => ({ id, conversation_id: "c", seq: 1, role: "assistant", created_at: 1,
    content: { parts: [{ type: "text", text: id }], model: "", provider: "", reasoning: "", usage: {}, plan: [], stats: { llm_calls: 0, tool_calls: 0 }, status } });
function Probe({ running, messages }: { running: boolean; messages: ChatMessage[] }) { useRunSpeech(running, messages); return null; }

let root: Root | null = null;
afterEach(() => { act(() => root?.unmount()); root = null; mock.timers.reset(); useVoiceStore.setState({ mood: "idle" }); });
function start() {
    root = createRoot(document.createElement("div"));
    return (running: boolean, messages: ChatMessage[]) => act(() => root?.render(<Probe running={running} messages={messages} />));
}

test("streamed tokens do not re-run the start-of-run work", () => {
    const render = start();
    render(true, [answer("a", "streaming")]);
    assert.equal(useVoiceStore.getState().mood, "thinking");
    act(() => useVoiceStore.getState().setMood("listening"));
    render(true, [answer("a2", "streaming")]);
    assert.equal(useVoiceStore.getState().mood, "listening");
});

test("the smile settles even when saved history and follow-ups arrive after the run", () => {
    mock.timers.enable({ apis: ["setTimeout"] });
    const render = start();
    render(true, [answer("a", "streaming")]);
    render(false, [answer("a", "complete")]);
    assert.equal(useVoiceStore.getState().mood, "happy");
    render(false, [answer("saved", "complete")]);
    render(false, [answer("with-followups", "complete")]);
    act(() => mock.timers.tick(2600));
    assert.equal(useVoiceStore.getState().mood, "idle");
});
