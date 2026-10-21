import { lazy, Suspense } from "react";
import { AudioLines, Square, Volume2, VolumeX, X } from "lucide-react";
import { stopSpeaking } from "../voice/playback.ts";
import { useVoiceStore } from "../voice/voiceStore.ts";
import "./avatar.css";

const AvatarStage = lazy(() => import("./AvatarStage.tsx"));

const CAPTIONS = { idle: "", listening: "Listening…", thinking: "Thinking…", speaking: "Speaking…", happy: "" } as const;

/** Docked tutor card: avatar, live state and the voice switches. */
export function AvatarPanel() {
    const { mood, speaking, speakReplies, conversation, setPreference } = useVoiceStore();
    const caption = speaking ? CAPTIONS.speaking : CAPTIONS[mood];
    return <aside className="matopt-avatar-panel" aria-label="Voice tutor">
        <Suspense fallback={<div className="matopt-avatar-stage" data-status="loading"><span className="matopt-avatar-note">Loading avatar…</span></div>}>
            <AvatarStage />
        </Suspense>
        <p className="matopt-avatar-caption" role="status" aria-live="polite">{caption}</p>
        <div className="matopt-avatar-controls">
            <button type="button" aria-pressed={speakReplies} title={speakReplies ? "Stop reading replies aloud" : "Read replies aloud"}
                onClick={() => { setPreference("speakReplies", !speakReplies); if (speakReplies) stopSpeaking(); }}>
                {speakReplies ? <Volume2 aria-hidden="true" /> : <VolumeX aria-hidden="true" />}<span>Speak</span>
            </button>
            <button type="button" aria-pressed={conversation} title="Hands-free: what you say is sent, and replies are spoken"
                onClick={() => { const next = !conversation; setPreference("conversation", next); if (next) setPreference("speakReplies", true); }}>
                <AudioLines aria-hidden="true" /><span>Talk</span>
            </button>
            {speaking && <button type="button" className="matopt-avatar-icon" aria-label="Stop speaking" title="Stop speaking" onClick={stopSpeaking}><Square aria-hidden="true" /></button>}
            <button type="button" className="matopt-avatar-icon" aria-label="Close avatar" title="Close avatar"
                onClick={() => { stopSpeaking(); setPreference("avatarOpen", false); }}><X aria-hidden="true" /></button>
        </div>
    </aside>;
}
