/**
 * useChat — manages multi-conversation chat state (ChatGPT-style).
 *
 * Each conversation gets its own message thread. The hook exposes the
 * active conversation, a conversation list for the sidebar, and helpers
 * to create / switch / delete conversations.
 */

import { useState, useCallback, useMemo } from "react";
import { sendChatMessage } from "../services/chat";
import type { Message } from "../types/message";
import type { Conversation } from "../types/conversation";

let _nextId = 1;
function uid(prefix = "msg"): string {
    return `${prefix}-${_nextId++}-${Date.now()}`;
}

function titleFromContent(content: string): string {
    const cleaned = content.replace(/\$[^$]*\$/g, "math").replace(/\s+/g, " ");
    return cleaned.length > 40 ? cleaned.slice(0, 40) + "…" : cleaned;
}

export function useChat() {
    const [conversations, setConversations] = useState<Conversation[]>([]);
    const [activeId, setActiveId] = useState<string | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const activeConversation = useMemo(
        () => conversations.find((c) => c.id === activeId) ?? null,
        [conversations, activeId]
    );

    const messages = activeConversation?.messages ?? [];

    // ── helpers ─────────────────────────────────────────────────────────

    const createConversation = useCallback((): string => {
        const id = uid("conv");
        const conv: Conversation = {
            id,
            title: "New chat",
            messages: [],
            createdAt: Date.now(),
            updatedAt: Date.now(),
        };
        setConversations((prev) => [conv, ...prev]);
        setActiveId(id);
        setError(null);
        return id;
    }, []);

    const switchConversation = useCallback((id: string) => {
        setActiveId(id);
        setError(null);
    }, []);

    const deleteConversation = useCallback(
        (id: string) => {
            setConversations((prev) => prev.filter((c) => c.id !== id));
            if (activeId === id) {
                setActiveId(null);
            }
        },
        [activeId]
    );

    const renameConversation = useCallback((id: string, title: string) => {
        setConversations((prev) =>
            prev.map((c) => (c.id === id ? { ...c, title } : c))
        );
    }, []);

    // ── send message ────────────────────────────────────────────────────

    const sendMessage = useCallback(
        async (content: string) => {
            let convId = activeId;

            // Auto-create conversation if none active
            if (!convId) {
                convId = uid("conv");
                const conv: Conversation = {
                    id: convId,
                    title: titleFromContent(content),
                    messages: [],
                    createdAt: Date.now(),
                    updatedAt: Date.now(),
                };
                setConversations((prev) => [conv, ...prev]);
                setActiveId(convId);
            }

            const userMsg: Message = {
                id: uid(),
                role: "user",
                content,
                timestamp: Date.now(),
            };

            // Append user message & update title if first message
            setConversations((prev) =>
                prev.map((c) => {
                    if (c.id !== convId) return c;
                    const isFirst = c.messages.length === 0;
                    return {
                        ...c,
                        title: isFirst ? titleFromContent(content) : c.title,
                        messages: [...c.messages, userMsg],
                        updatedAt: Date.now(),
                    };
                })
            );

            setLoading(true);
            setError(null);

            try {
                // Build history from the current conversation
                const currentConv = conversations.find(
                    (c) => c.id === convId
                );
                const history = [
                    ...(currentConv?.messages ?? []),
                    userMsg,
                ].map((m) => ({
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

                setConversations((prev) =>
                    prev.map((c) =>
                        c.id === convId
                            ? {
                                ...c,
                                messages: [...c.messages, assistantMsg],
                                updatedAt: Date.now(),
                            }
                            : c
                    )
                );
            } catch (err: unknown) {
                const msg =
                    err instanceof Error ? err.message : "Chat failed";
                setError(msg);
            } finally {
                setLoading(false);
            }
        },
        [activeId, conversations]
    );

    const clearChat = useCallback(() => {
        if (!activeId) return;
        setConversations((prev) =>
            prev.map((c) =>
                c.id === activeId ? { ...c, messages: [], updatedAt: Date.now() } : c
            )
        );
        setError(null);
    }, [activeId]);

    return {
        // Conversation list (for sidebar)
        conversations,
        activeId,
        createConversation,
        switchConversation,
        deleteConversation,
        renameConversation,
        // Active chat
        messages,
        loading,
        error,
        sendMessage,
        clearChat,
    };
}
