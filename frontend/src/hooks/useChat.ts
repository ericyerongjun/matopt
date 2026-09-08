/**
 * Drive one agent turn: upload attachments, POST /api/chat/stream, reduce
 * SSE events into an assistant message of ordered parts.
 *
 * Live parts are stored in an overlay keyed by conversation id so switching
 * threads mid-run does not clobber another conversation's messages. The
 * assistant snapshot is not in SQLite until `message_end`.
 */

import { useCallback, useRef, useState, type Dispatch, type SetStateAction } from "react";
import { fetchFollowUps } from "../services/followups";
import { steerRun, stopRun } from "../services/conversations";
import { textFromAssistant, isAssistantContent } from "../types/protocol";
import { uploadFiles } from "../services/files";
import {
    applyEvent,
    assistantFromStream,
    emptyStreamState,
    streamChat,
    type StreamState,
} from "../services/stream";
import type { Attachment, ChatMessage, UserContent } from "../types/protocol";

interface Options {
    activeId: string | null;
    messages: ChatMessage[];
    setMessages: Dispatch<SetStateAction<ChatMessage[]>>;
    setActiveId: (id: string | null) => void;
    createConversation: (opts: {
        provider?: string | null;
        model?: string | null;
        reasoning?: string | null;
    }) => Promise<{ id: string; title: string }>;
    refreshList: () => Promise<void>;
    provider: string | null;
    model: string | null;
    reasoning: string | null;
    verify: boolean;
    webSearch?: boolean;
    onArtifacts?: () => void;
}

function uid(prefix: string) {
    return `${prefix}${Math.random().toString(16).slice(2)}${Date.now().toString(16)}`;
}

export function useChat(options: Options) {
    const [streaming, setStreaming] = useState(false);
    const [statusLine, setStatusLine] = useState<string | null>(null);
    const [streamError, setStreamError] = useState<string | null>(null);
    const [overlay, setOverlay] = useState<{ cid: string; messages: ChatMessage[] } | null>(null);
    const abortRef = useRef<AbortController | null>(null);
    const cidRef = useRef<string | null>(null);
    const overlayRef = useRef<ChatMessage[]>([]);
    const optionsRef = useRef(options);
    optionsRef.current = options;

    const sendMessage = useCallback(async (text: string, files: File[] = [], skillNames: string[] = []) => {
        const opts = optionsRef.current;
        const trimmed = text.trim();
        if (!trimmed) return;
        if (abortRef.current) {
            const cid = cidRef.current || opts.activeId;
            if (cid) {
                try {
                    await steerRun(cid, trimmed);
                } catch (err: unknown) {
                    setStreamError(err instanceof Error ? err.message : "Could not steer");
                }
            }
            return;
        }

        setStreamError(null);
        setStatusLine(null);

        let cid = opts.activeId;
        try {
            if (!cid) {
                const conv = await opts.createConversation({
                    provider: opts.provider,
                    model: opts.model,
                    reasoning: opts.reasoning,
                });
                cid = conv.id;
            }
            if (!cid) throw new Error("Could not create conversation");

            let attachments: Attachment[] = [];
            if (files.length) {
                setStatusLine("Uploading files…");
                const uploaded = await uploadFiles(cid, files);
                attachments = uploaded.map((f) => ({
                    name: f.name,
                    path: f.path,
                    url: f.url,
                    size: f.size,
                    kind: f.kind,
                }));
            }

            const userMsg: ChatMessage = {
                id: uid("u_"),
                conversation_id: cid,
                seq: opts.messages.length + 1,
                role: "user",
                content: { text: trimmed, attachments } satisfies UserContent,
                created_at: Date.now() / 1000,
            };
            const assistantId = uid("m_");
            const assistantMsg: ChatMessage = {
                id: assistantId,
                conversation_id: cid,
                seq: opts.messages.length + 2,
                role: "assistant",
                content: assistantFromStream(emptyStreamState()),
                created_at: Date.now() / 1000,
            };

            const starting = [...opts.messages, userMsg, assistantMsg];
            overlayRef.current = starting;
            setOverlay({ cid, messages: starting });
            if (!opts.activeId || opts.activeId === cid) opts.setMessages(starting);
            cidRef.current = cid;
            setStreaming(true);

            const abort = new AbortController();
            abortRef.current = abort;
            let state: StreamState = { ...emptyStreamState(), conversationId: cid, messageId: assistantId };

            const patchAssistant = (next: StreamState) => {
                state = next;
                setStatusLine(next.statusLine);
                overlayRef.current = overlayRef.current.map((m) =>
                    m.id === assistantId || m.id === next.messageId
                        ? {
                              ...m,
                              id: next.messageId || m.id,
                              conversation_id: next.conversationId || m.conversation_id,
                              content: assistantFromStream(next),
                          }
                        : m
                );
                const streamCid = next.conversationId || cid;
                if (!streamCid) return;
                setOverlay({ cid: streamCid, messages: overlayRef.current });
                if (optionsRef.current.activeId === streamCid) {
                    optionsRef.current.setMessages(overlayRef.current);
                }
                if (next.conversationId && !optionsRef.current.activeId) {
                    optionsRef.current.setActiveId(next.conversationId);
                    cidRef.current = next.conversationId;
                }
            };

            await streamChat(
                {
                    conversation_id: cid,
                    message: trimmed,
                    attachments,
                    provider: opts.provider,
                    model: opts.model,
                    reasoning: opts.reasoning,
                    verify: opts.verify,
                    skills: skillNames.length ? skillNames : undefined,
                    web_search: Boolean(opts.webSearch),
                },
                (event) => {
                    const next = applyEvent(state, event);
                    patchAssistant(next);
                    if (event.type === "tool_result") optionsRef.current.onArtifacts?.();
                },
                abort.signal
            );

            const last = [...overlayRef.current].reverse().find((m) => m.role === "assistant");
            if (last && isAssistantContent(last.content) && !last.content.parts.some((p) => p.type === "options")) {
                const reply = textFromAssistant(last.content);
                if (reply) {
                    try {
                        const { followups } = await fetchFollowUps({
                            content: reply,
                            provider: opts.provider ?? undefined,
                            model: opts.model ?? undefined,
                        });
                        if (followups?.length) {
                            const next = applyEvent(state, { type: "options", items: followups });
                            patchAssistant(next);
                        }
                    } catch {
                        /* chips are optional */
                    }
                }
            }
            void optionsRef.current.refreshList();
            window.setTimeout(() => void optionsRef.current.refreshList(), 4000);
            optionsRef.current.onArtifacts?.();
        } catch (err: unknown) {
            if ((err as Error).name === "AbortError") {
                /* stop button */
            } else {
                const msg = err instanceof Error ? err.message : "Chat failed";
                setStreamError(msg);
            }
        } finally {
            abortRef.current = null;
            cidRef.current = null;
            setStreaming(false);
            setStatusLine(null);
        }
    }, []);

    const stop = useCallback(async () => {
        const cid = cidRef.current || optionsRef.current.activeId;
        abortRef.current?.abort();
        if (cid) {
            try {
                await stopRun(cid);
            } catch {
                /* stream will end anyway */
            }
        }
    }, []);

    return { sendMessage, stop, streaming, statusLine, streamError, setStreamError, overlay };
}
