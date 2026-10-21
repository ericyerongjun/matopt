/**
 * Push-to-talk voice input: hold Right ⌥ Option, or press the mic button.
 *
 * Browsers never see the fn/Globe key, so Right Option is the hold key. It
 * starts recording only after a short hold, and any other key pressed while
 * it is down cancels, so Option+letter characters keep working.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { transcribe } from "../services/speech.ts";
import { stopSpeaking } from "../features/voice/playback.ts";
import { useVoiceStore } from "../features/voice/voiceStore.ts";

export type VoiceState = "idle" | "recording" | "transcribing" | "error";

interface Recording {
    recorder: MediaRecorder; stream: MediaStream; chunks: Blob[]; started: number; via: "hotkey" | "button";
    context?: AudioContext; frame?: number; cancelled?: boolean;
}

const HOLD_DELAY_MS = 250;
const MIN_RECORDING_MS = 350;

function recorderMimeType(): string | undefined {
    if (typeof MediaRecorder === "undefined") return undefined;
    return ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"].find((type) => MediaRecorder.isTypeSupported(type));
}

export function isPushToTalkKey(event: Pick<KeyboardEvent, "code">): boolean {
    return event.code === "AltRight";
}

export function useVoiceInput({ language, disabled = false, onTranscript }: {
    language?: string | null;
    disabled?: boolean;
    onTranscript: (text: string, via: "hotkey" | "button") => void;
}) {
    const [state, setState] = useState<VoiceState>("idle");
    const [level, setLevel] = useState(0);
    const [error, setError] = useState<string | null>(null);
    const session = useRef<Recording | null>(null);
    const live = useRef({ language, onTranscript, disabled });
    live.current = { language, onTranscript, disabled };

    const release = useCallback(() => {
        const current = session.current;
        if (!current) return;
        if (current.frame) cancelAnimationFrame(current.frame);
        current.stream.getTracks().forEach((track) => track.stop());
        void current.context?.close().catch(() => undefined);
        setLevel(0);
    }, []);

    const start = useCallback(async (via: "hotkey" | "button") => {
        if (session.current || live.current.disabled) return;
        if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
            setError("This browser cannot record audio."); setState("error"); return;
        }
        setError(null);
        let stream: MediaStream;
        try {
            stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
        } catch {
            setError("Microphone access was blocked. Allow it in the browser's site settings."); setState("error"); return;
        }
        const mimeType = recorderMimeType();
        const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
        const current: Recording = { recorder, stream, chunks: [], started: Date.now(), via };
        session.current = current;
        recorder.ondataavailable = (event) => { if (event.data.size) current.chunks.push(event.data); };
        recorder.onstop = async () => {
            release();
            session.current = null;
            if (useVoiceStore.getState().mood === "listening") useVoiceStore.getState().setMood("idle");
            const blob = new Blob(current.chunks, { type: recorder.mimeType || mimeType || "audio/webm" });
            if (current.cancelled || Date.now() - current.started < MIN_RECORDING_MS || !blob.size) { setState("idle"); return; }
            setState("transcribing");
            try {
                const result = await transcribe(blob, live.current.language);
                if (result.text.trim()) live.current.onTranscript(result.text.trim(), current.via);
                setState("idle");
            } catch (err) {
                setError(err instanceof Error ? err.message : "Transcription failed."); setState("error");
            }
        };
        try {
            const context = new AudioContext();
            const analyser = context.createAnalyser();
            analyser.fftSize = 512;
            context.createMediaStreamSource(stream).connect(analyser);
            const samples = new Uint8Array(analyser.fftSize);
            current.context = context;
            const tick = () => {
                analyser.getByteTimeDomainData(samples);
                let sum = 0;
                for (const value of samples) sum += ((value - 128) / 128) ** 2;
                setLevel(Math.min(1, Math.sqrt(sum / samples.length) * 4));
                current.frame = requestAnimationFrame(tick);
            };
            tick();
        } catch { /* The level meter is decorative; recording continues without it. */ }
        recorder.start();
        stopSpeaking();  // Talking over the tutor interrupts it.
        useVoiceStore.getState().setMood("listening");
        setState("recording");
    }, [release]);

    const stop = useCallback((cancel = false) => {
        const current = session.current;
        if (!current) return;
        current.cancelled = cancel;
        if (current.recorder.state !== "inactive") current.recorder.stop();
    }, []);

    const toggle = useCallback(() => {
        if (session.current) stop();
        else void start("button");
    }, [start, stop]);

    // Hold Right Option to talk.
    useEffect(() => {
        let timer: ReturnType<typeof setTimeout> | null = null;
        let held = false;
        const down = (event: KeyboardEvent) => {
            if (isPushToTalkKey(event)) {
                if (event.repeat || held || live.current.disabled) return;
                held = true;
                timer = setTimeout(() => { timer = null; void start("hotkey"); }, HOLD_DELAY_MS);
            } else if (held) {
                // Option+key types a character: this was not push-to-talk.
                if (timer) { clearTimeout(timer); timer = null; }
                if (session.current?.via === "hotkey") stop(true);
                held = false;
            }
        };
        const up = (event: KeyboardEvent) => {
            if (!isPushToTalkKey(event) || !held) return;
            held = false;
            if (timer) { clearTimeout(timer); timer = null; return; }
            if (session.current?.via === "hotkey") stop();
        };
        const blur = () => { held = false; if (timer) clearTimeout(timer); if (session.current?.via === "hotkey") stop(true); };
        window.addEventListener("keydown", down, true);
        window.addEventListener("keyup", up, true);
        window.addEventListener("blur", blur);
        return () => {
            window.removeEventListener("keydown", down, true);
            window.removeEventListener("keyup", up, true);
            window.removeEventListener("blur", blur);
            if (timer) clearTimeout(timer);
        };
    }, [start, stop]);

    useEffect(() => () => { stop(true); }, [stop]);

    return { state, level, error, toggle, stop, clearError: () => { setError(null); setState("idle"); } };
}
