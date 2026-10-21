/**
 * Shared voice state: language, spoken replies, hands-free conversation,
 * the avatar panel, and the live speech level the avatar lip-syncs to.
 */
import { create } from "zustand";
import { DEFAULT_TUTOR, isTutorId, type TutorId } from "../avatar/tutors.ts";

export type Viseme = { aa: number; ih: number; ou: number; ee: number; oh: number };
export const SILENT: Viseme = { aa: 0, ih: 0, ou: 0, ee: 0, oh: 0 };

interface Preferences {
    /** Reply/voice language code, or null to follow the user. */
    language: string | null;
    speakReplies: boolean;
    conversation: boolean;
    avatarOpen: boolean;
    /** Which built-in tutor the avatar panel shows. */
    tutor: TutorId;
}

interface VoiceStore extends Preferences {
    speaking: boolean;
    viseme: Viseme;
    /** "thinking" while the agent works, "happy" briefly after an answer. */
    mood: "idle" | "listening" | "thinking" | "speaking" | "happy";
    setPreference: <K extends keyof Preferences>(key: K, value: Preferences[K]) => void;
    setSpeech: (speaking: boolean, viseme?: Viseme) => void;
    setMood: (mood: VoiceStore["mood"]) => void;
}

const KEY = "matopt.voice";
const DEFAULTS: Preferences = { language: null, speakReplies: false, conversation: false, avatarOpen: false, tutor: DEFAULT_TUTOR };

function load(): Preferences {
    try {
        const saved = JSON.parse(localStorage.getItem(KEY) || "{}");
        return {
            language: typeof saved.language === "string" ? saved.language : null,
            speakReplies: saved.speakReplies === true,
            conversation: saved.conversation === true,
            avatarOpen: saved.avatarOpen === true,
            tutor: isTutorId(saved.tutor) ? saved.tutor : DEFAULT_TUTOR,
        };
    } catch {
        return DEFAULTS;
    }
}

export const useVoiceStore = create<VoiceStore>((set, get) => ({
    ...load(),
    speaking: false,
    viseme: SILENT,
    mood: "idle",
    setPreference: (key, value) => {
        set({ [key]: value } as Partial<VoiceStore>);
        const { language, speakReplies, conversation, avatarOpen, tutor } = get();
        try { localStorage.setItem(KEY, JSON.stringify({ language, speakReplies, conversation, avatarOpen, tutor })); } catch { /* Preferences are optional. */ }
    },
    setSpeech: (speaking, viseme = SILENT) => set({ speaking, viseme }),
    setMood: (mood) => set({ mood }),
}));

/** Best-effort language of a reply when the user has not chosen one. */
export function detectLanguage(text: string): string {
    if (/[぀-ヿ]/.test(text)) return "ja";
    if (/[一-鿿]/.test(text)) return "zh";
    if (/[ऀ-ॿ]/.test(text)) return "hi";
    return "en";
}
