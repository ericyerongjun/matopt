/** Long-term memories: consolidated summary plus user-authored notes. */
import apiClient from "./api.ts";

export interface MemoryNote { id: string; title: string; text: string; created_at: number }
export interface MemorySnapshot {
    enabled: boolean;
    summary: string;
    memory_md: string;
    notes: MemoryNote[];
    conversations_learned: number;
}

const record = (value: unknown): Record<string, unknown> => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const str = (value: unknown) => typeof value === "string" ? value : "";

export function readNote(value: unknown): MemoryNote | null {
    const note = record(value);
    if (!str(note.id)) return null;
    return { id: str(note.id), title: str(note.title), text: str(note.text), created_at: typeof note.created_at === "number" ? note.created_at : 0 };
}

export function readMemories(value: unknown): MemorySnapshot {
    const data = record(value);
    return { enabled: data.enabled !== false, summary: str(data.summary), memory_md: str(data.memory_md),
        notes: (Array.isArray(data.notes) ? data.notes : []).map(readNote).filter((note): note is MemoryNote => Boolean(note)),
        conversations_learned: typeof data.conversations_learned === "number" ? data.conversations_learned : 0 };
}

export const memoriesService = {
    async get(signal?: AbortSignal) { return readMemories((await apiClient.get("/memories", { signal })).data); },
    async addNote(text: string) { return readNote((await apiClient.post("/memories/notes", { text })).data); },
    async updateNote(id: string, text: string) { return readNote((await apiClient.put(`/memories/notes/${encodeURIComponent(id)}`, { text })).data); },
    async deleteNote(id: string) { await apiClient.delete(`/memories/notes/${encodeURIComponent(id)}`); },
    async clear() { await apiClient.delete("/memories"); },
    async refresh() { await apiClient.post("/memories/refresh"); },
    async setConversationMode(conversationId: string, mode: "enabled" | "disabled") {
        await apiClient.put(`/conversations/${encodeURIComponent(conversationId)}/memory`, { mode });
    },
};
