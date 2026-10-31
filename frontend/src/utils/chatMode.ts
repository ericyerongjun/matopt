/** Persist the composer's Auto / Mat / Opt choice. */
import type { ChatMode } from "../types/protocol.ts";

export const CHAT_MODE_STORAGE_KEY = "matopt.chatMode";
export const DEFAULT_CHAT_MODE: ChatMode = "auto";

export function normalizeChatMode(value: unknown): ChatMode {
    return value === "mat" || value === "opt" || value === "auto" ? value : DEFAULT_CHAT_MODE;
}

/** Storage can throw (private mode, disabled cookies); fall back to Auto. */
export function loadChatMode(): ChatMode {
    try { return normalizeChatMode(localStorage.getItem(CHAT_MODE_STORAGE_KEY)); } catch { return DEFAULT_CHAT_MODE; }
}

export function setChatMode(value: ChatMode): ChatMode {
    const mode = normalizeChatMode(value);
    try { localStorage.setItem(CHAT_MODE_STORAGE_KEY, mode); } catch { /* Storage is optional. */ }
    return mode;
}
