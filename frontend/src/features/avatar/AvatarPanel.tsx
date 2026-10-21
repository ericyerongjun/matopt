import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { AudioLines, ImageUp, RotateCcw, Square, Volume2, VolumeX, X } from "lucide-react";
import { clearCustomAvatar, loadCustomAvatar, saveCustomAvatar } from "./customAvatar.ts";
import { stopSpeaking } from "../voice/playback.ts";
import { useVoiceStore } from "../voice/voiceStore.ts";
import "./avatar.css";

const AvatarStage = lazy(() => import("./AvatarStage.tsx"));

const CAPTIONS = { idle: "", listening: "Listening…", thinking: "Thinking…", speaking: "Speaking…", happy: "" } as const;

/** Docked tutor card: avatar, live state and the voice switches. */
export function AvatarPanel() {
    const { mood, speaking, speakReplies, conversation, setPreference } = useVoiceStore();
    const [customUrl, setCustomUrl] = useState<string | null>(null);
    const [notice, setNotice] = useState<string | null>(null);
    const picker = useRef<HTMLInputElement>(null);
    useEffect(() => {
        let url: string | null = null;
        void loadCustomAvatar().then((file) => { if (file) { url = URL.createObjectURL(file); setCustomUrl(url); } });
        return () => { if (url) URL.revokeObjectURL(url); };
    }, []);
    const choose = async (file: File | undefined) => {
        if (!file) return;
        try {
            await saveCustomAvatar(file);
            setCustomUrl((previous) => { if (previous) URL.revokeObjectURL(previous); return URL.createObjectURL(file); });
            setNotice(null);
        } catch (error) {
            setNotice(error instanceof Error ? error.message : "That avatar could not be used.");
        }
    };
    const reset = () => {
        void clearCustomAvatar();
        setCustomUrl((previous) => { if (previous) URL.revokeObjectURL(previous); return null; });
    };
    const caption = notice ?? (speaking ? CAPTIONS.speaking : CAPTIONS[mood]);
    return <aside className="matopt-avatar-panel" aria-label="Voice tutor">
        <Suspense fallback={<div className="matopt-avatar-stage" data-status="loading"><span className="matopt-avatar-note">Loading avatar…</span></div>}>
            <AvatarStage key={customUrl ?? "default"} url={customUrl ?? undefined} />
        </Suspense>
        <p className="matopt-avatar-caption" role="status" aria-live="polite">{caption}</p>
        <input ref={picker} type="file" accept=".vrm" hidden onChange={(event) => { void choose(event.target.files?.[0]); event.target.value = ""; }} />
        <div className="matopt-avatar-controls">
            <button type="button" aria-pressed={speakReplies} title={speakReplies ? "Stop reading replies aloud" : "Read replies aloud"}
                onClick={() => { setPreference("speakReplies", !speakReplies); if (speakReplies) stopSpeaking(); }}>
                {speakReplies ? <Volume2 aria-hidden="true" /> : <VolumeX aria-hidden="true" />}<span>Speak</span>
            </button>
            <button type="button" aria-pressed={conversation} title="Hands-free: what you say is sent, and replies are spoken"
                onClick={() => { const next = !conversation; setPreference("conversation", next); if (next) setPreference("speakReplies", true); }}>
                <AudioLines aria-hidden="true" /><span>Talk</span>
            </button>
            <button type="button" className="matopt-avatar-icon" aria-label="Choose avatar (.vrm)" title="Use your own .vrm avatar" onClick={() => picker.current?.click()}><ImageUp aria-hidden="true" /></button>
            {customUrl && <button type="button" className="matopt-avatar-icon" aria-label="Use the default avatar" title="Use the default avatar" onClick={reset}><RotateCcw aria-hidden="true" /></button>}
            {speaking && <button type="button" className="matopt-avatar-icon" aria-label="Stop speaking" title="Stop speaking" onClick={stopSpeaking}><Square aria-hidden="true" /></button>}
            <button type="button" className="matopt-avatar-icon" aria-label="Close avatar" title="Close avatar"
                onClick={() => { stopSpeaking(); setPreference("avatarOpen", false); }}><X aria-hidden="true" /></button>
        </div>
    </aside>;
}
