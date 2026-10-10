import apiClient from "./api.ts";
import type { Conversation, ChatMessage } from "../types/protocol";

export async function listConversations(limit = 200): Promise<Conversation[]> {
    const { data } = await apiClient.get<{ conversations: Conversation[] }>("/conversations", {
        params: { limit },
    });
    return data.conversations;
}

export async function createConversation(body: {
    title?: string;
    provider?: string | null;
    model?: string | null;
    reasoning?: string | null;
} = {}): Promise<Conversation> {
    const { data } = await apiClient.post<Conversation>("/conversations", body);
    return data;
}

export async function getConversation(id: string, options?: { signal?: AbortSignal; timeout?: number }): Promise<Conversation & { messages: ChatMessage[]; running: boolean }> {
    const { data } = await apiClient.get<Conversation & { messages: ChatMessage[]; running: boolean }>(
        `/conversations/${id}`, options
    );
    return data;
}

export async function updateConversation(
    id: string,
    body: { title?: string; provider?: string | null; model?: string | null; reasoning?: string | null }
): Promise<Conversation> {
    const { data } = await apiClient.patch<Conversation>(`/conversations/${id}`, body);
    return data;
}

export async function deleteConversation(id: string): Promise<void> {
    await apiClient.delete(`/conversations/${id}`);
}

export async function restartKernel(id: string): Promise<void> {
    await apiClient.post(`/conversations/${id}/kernel/restart`);
}

export async function stopRun(id: string): Promise<void> {
    await apiClient.post(`/chat/${id}/stop`);
}

export async function steerRun(
    id: string,
    message: string,
    extras: { skills?: string[]; tools?: string[] } = {}
): Promise<void> {
    await apiClient.post(`/chat/${id}/steer`, {
        message,
        skills: extras.skills ?? [],
        tools: extras.tools ?? [],
    });
}

function filenameFromDisposition(header: string | null, fallback: string): string {
    if (!header) return fallback;
    const star = /filename\*=UTF-8''([^;]+)/i.exec(header);
    if (star) return decodeURIComponent(star[1]);
    const plain = /filename="([^"]+)"/i.exec(header) || /filename=([^;]+)/i.exec(header);
    return plain ? plain[1].replace(/"/g, "") : fallback;
}

export async function exportConversation(id: string, format: "markdown" | "ipynb"): Promise<void> {
    const res = await fetch(`/api/conversations/${id}/export?format=${format}`);
    if (!res.ok) {
        throw new Error(`Export failed (${res.status})`);
    }
    const blob = await res.blob();
    const name = filenameFromDisposition(
        res.headers.get("content-disposition"),
        format === "ipynb" ? "matopt.ipynb" : "matopt.md"
    );
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    a.click();
    URL.revokeObjectURL(url);
}
