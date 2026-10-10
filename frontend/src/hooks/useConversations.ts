/**
 * Server-backed conversation list + the active thread's persisted messages.
 * In-flight streams overlay via `drafts` so switching chats mid-run doesn't
 * lose the live assistant message (it isn't in SQLite until message_end).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
    createConversation as apiCreate,
    deleteConversation as apiDelete,
    getConversation,
    listConversations,
    updateConversation,
} from "../services/conversations.ts";
import type { ChatMessage, Conversation } from "../types/protocol.ts";

function selectedChatFromUrl(): string | null {
    if (typeof window === "undefined") return null;
    const id = new URL(window.location.href).searchParams.get("chat");
    // Conversation IDs are opaque path segments, never API paths or queries.
    return id && /^[a-zA-Z0-9_-]+$/.test(id) ? id : null;
}

function persistSelectedChat(id: string | null) {
    if (typeof window === "undefined") return;
    const url = new URL(window.location.href);
    if (id) url.searchParams.set("chat", id);
    else url.searchParams.delete("chat");
    window.history.replaceState(window.history.state, "", url);
}

export function useConversations() {
    const [conversations, setConversations] = useState<Conversation[]>([]);
    const [activeId, updateActiveId] = useState<string | null>(selectedChatFromUrl);
    const activeIdRef = useRef<string | null>(activeId);
    const loadGeneration = useRef(0);
    const setActiveId = useCallback((id: string | null) => {
        activeIdRef.current = id;
        loadGeneration.current += 1;
        persistSelectedChat(id);
        updateActiveId(id);
    }, []);
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
        const generation = ++loadGeneration.current;
        setLoadingThread(true);
        setError(null);
        try {
            const conv = await getConversation(id);
            if (generation !== loadGeneration.current || activeIdRef.current !== id) return;
            setMessages(conv.messages ?? []);
            setRunning(Boolean(conv.running));
            setConversations((prev) => {
                const others = prev.filter((c) => c.id !== id);
                const { messages: _m, ...rest } = conv;
                return [rest, ...others].sort((a, b) => b.updated_at - a.updated_at);
            });
        } catch (err: unknown) {
            if (generation !== loadGeneration.current || activeIdRef.current !== id) return;
            setError(err instanceof Error ? err.message : "Failed to load conversation");
            setMessages([]);
            setRunning(false);
        } finally {
            if (generation === loadGeneration.current) setLoadingThread(false);
        }
    }, []);

    const select = useCallback(
        (id: string | null) => {
            setActiveId(id);
            setMessages([]);
            setRunning(false);
            setError(null);
            if (!id) {
                setMessages([]);
                setRunning(false);
                setLoadingThread(false);
                return;
            }
            void loadThread(id);
        },
        [loadThread, setActiveId]
    );

    useEffect(() => {
        const id = activeIdRef.current;
        if (id) void loadThread(id);
        const restoreSelection = () => select(selectedChatFromUrl());
        window.addEventListener("popstate", restoreSelection);
        return () => {
            loadGeneration.current += 1;
            window.removeEventListener("popstate", restoreSelection);
        };
    }, [loadThread, select]);

    const create = useCallback(
        async (opts: { title?: string; provider?: string | null; model?: string | null; reasoning?: string | null } = {}) => {
            const generation = loadGeneration.current;
            const conv = await apiCreate(opts);
            setConversations((prev) => [conv, ...prev.filter((c) => c.id !== conv.id)]);
            // Creating a chat may finish after the user has navigated away.
            // Keep the saved conversation, but never take over their new view.
            if (generation !== loadGeneration.current) return conv;
            setActiveId(conv.id);
            setMessages([]);
            setRunning(false);
            setLoadingThread(false);
            return conv;
        },
        [setActiveId]
    );

    const remove = useCallback(
        async (id: string) => {
            await apiDelete(id);
            setConversations((prev) => prev.filter((c) => c.id !== id));
            if (activeIdRef.current === id) {
                setActiveId(null);
                setMessages([]);
                setRunning(false);
                setLoadingThread(false);
            }
        },
        [setActiveId]
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
        setLoadingThread(false);
    }, [setActiveId]);

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
