/**
 * useChat — manages chat conversation state.
 */

import { useState, useCallback } from "react";
import { sendChatMessage } from "../services/chat";
import type { Message } from "../types/message";

let _nextId = 1;
function uid(): string {
    return `msg-${_nextId++}-${Date.now()}`;
}

export function useChat() {
    const [messages, setMessages] = useState<Message[]>([]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const sendMessage = useCallback(
        async (content: string) => {
            const userMsg: Message = {
                id: uid(),
                role: "user",
                content,
                timestamp: Date.now(),
            };
            setMessages((prev) => [...prev, userMsg]);
            setLoading(true);
            setError(null);

            try {
                const history = [...messages, userMsg].map((m) => ({
                    role: m.role,
                    content: m.content,
                }));
                const resp = await sendChatMessage({ messages: history });
                const assistantMsg: Message = {
                    id: resp.id,
                    role: "assistant",
                    content: resp.content,
                    timestamp: Date.now(),
                    toolCalls: resp.tool_calls,
                };
                setMessages((prev) => [...prev, assistantMsg]);
            } catch (err: unknown) {
                const msg = err instanceof Error ? err.message : "Chat failed";
                setError(msg);
            } finally {
                setLoading(false);
            }
        },
        [messages]
    );

    const clearChat = useCallback(() => {
        setMessages([]);
        setError(null);
    }, []);

    return { messages, loading, error, sendMessage, clearChat };
}
