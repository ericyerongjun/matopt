/**
 * Server-backed conversation list + the active thread's persisted messages.
 * In-flight streams overlay via `drafts` so switching chats mid-run doesn't
 * lose the live assistant message (it isn't in SQLite until message_end).
 */

import { useCallback, useEffect, useState } from "react";
import {
    createConversation as apiCreate,
    deleteConversation as apiDelete,
    getConversation,
    listConversations,
    updateConversation,
} from "../services/conversations";
import type { ChatMessage, Conversation } from "../types/protocol";

export function useConversations() {
    const [conversations, setConversations] = useState<Conversation[]>([]);
    const [activeId, setActiveId] = useState<string | null>(null);
    const [messages, setMessages] = useState<ChatMessage[]>([]);
    const [running, setRunning] = useState(false);
    const [loadingList, setLoadingList] = useState(true);
    const [loadingThread, setLoadingThread] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const refreshList = useCallback(async () => {
        try {
            const items = await listConversations();
            setConversations(items);
        } catch (err: unknown) {
            setError(err instanceof Error ? err.message : "Failed to load conversations");
        } finally {
            setLoadingList(false);
        }
    }, []);

    useEffect(() => {
        void refreshList();
    }, [refreshList]);

    const loadThread = useCallback(async (id: string) => {
        setLoadingThread(true);
        setError(null);
        try {
            const conv = await getConversation(id);
            setMessages(conv.messages ?? []);
            setRunning(Boolean(conv.running));
            setConversations((prev) => {
                const others = prev.filter((c) => c.id !== id);
                const { messages: _m, ...rest } = conv;
                return [rest, ...others].sort((a, b) => b.updated_at - a.updated_at);
            });
        } catch (err: unknown) {
            setError(err instanceof Error ? err.message : "Failed to load conversation");
            setMessages([]);
        } finally {
            setLoadingThread(false);
        }
    }, []);

    const select = useCallback(
        (id: string | null) => {
            setActiveId(id);
            if (!id) {
                setMessages([]);
                setRunning(false);
                return;
            }
            void loadThread(id);
        },
        [loadThread]
    );

    const create = useCallback(
        async (opts: { title?: string; provider?: string | null; model?: string | null; reasoning?: string | null } = {}) => {
            const conv = await apiCreate(opts);
            setConversations((prev) => [conv, ...prev.filter((c) => c.id !== conv.id)]);
            setActiveId(conv.id);
            setMessages([]);
            setRunning(false);
            return conv;
        },
        []
    );

    const remove = useCallback(
        async (id: string) => {
            await apiDelete(id);
            setConversations((prev) => prev.filter((c) => c.id !== id));
            if (activeId === id) {
                setActiveId(null);
                setMessages([]);
                setRunning(false);
            }
        },
        [activeId]
    );

    const rename = useCallback(async (id: string, title: string) => {
        const conv = await updateConversation(id, { title });
        setConversations((prev) => prev.map((c) => (c.id === id ? { ...c, ...conv } : c)));
    }, []);

    const newChat = useCallback(() => {
        setActiveId(null);
        setMessages([]);
        setRunning(false);
        setError(null);
    }, []);

    return {
        conversations,
        activeId,
        messages,
        setMessages,
        running,
        setRunning,
        loadingList,
        loadingThread,
        error,
        setError,
        refreshList,
        loadThread,
        select,
        create,
        remove,
        rename,
        newChat,
        setActiveId,
        setConversations,
    };
}
