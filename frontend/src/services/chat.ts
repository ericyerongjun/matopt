/**
 * Chat service — send messages to the backend LLM.
 */

import apiClient from "./api";
import type { ChatRequestBody, ChatResponseBody } from "./types";

export async function sendChatMessage(
    body: ChatRequestBody
): Promise<ChatResponseBody> {
    const { data } = await apiClient.post<ChatResponseBody>("/chat", body);
    return data;
}

/**
 * Stream chat via SSE.
 *
 * TODO: Implement proper EventSource / fetch-based SSE reader here.
 * For now, falls back to the non-streaming endpoint.
 */
export async function streamChatMessage(
    body: ChatRequestBody,
    onChunk: (text: string) => void
): Promise<void> {
    const resp = await sendChatMessage({ ...body, stream: false });
    onChunk(resp.content);
}
