/** The avatar thinks while this chat's run works, smiles when it ends, and reads the answer if asked. */
import { useEffect, useRef } from "react";
import { isAssistantContent, textFromAssistant, type ChatMessage } from "../../types/protocol.ts";
import { speak, stopSpeaking } from "./playback.ts";
import { useVoiceStore } from "./voiceStore.ts";

const HAPPY_MS = 2500;

/** Acts on run start and end only: per-token work would cut off Read aloud and cancel the settle timer. */
export function useRunSpeech(attachedRun: boolean, messages: ChatMessage[]): void {
    const latest = useRef(messages);
    latest.current = messages;
    const wasRunning = useRef(false);
    useEffect(() => {
        const store = useVoiceStore.getState();
        if (attachedRun) { stopSpeaking(); store.setMood("thinking"); wasRunning.current = true; return; }
        if (!wasRunning.current) return;
        wasRunning.current = false;
        const last = [...latest.current].reverse().find((message) => message.role === "assistant");
        const content = last && isAssistantContent(last.content) ? last.content : null;
        store.setMood(content?.status === "complete" ? "happy" : "idle");
        const settle = setTimeout(() => { if (useVoiceStore.getState().mood === "happy") useVoiceStore.getState().setMood("idle"); }, HAPPY_MS);
        if (content?.status === "complete" && store.speakReplies) {
            const text = textFromAssistant(content);
            if (text.trim()) void speak(text).catch(() => undefined);
        }
        return () => clearTimeout(settle);
    }, [attachedRun]);
}
