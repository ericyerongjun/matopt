/** Foreground subscription to a durable agent run. Navigation only unsubscribes. */
import { useCallback, useEffect, useRef, useState, type Dispatch, type SetStateAction } from "react";
import { fetchFollowUps } from "../services/followups.ts";
import { getConversation, steerRun, stopRun } from "../services/conversations.ts";
import { controlRun, latestRun, observeRun } from "../services/runs.ts";
import { textFromAssistant, isAssistantContent } from "../types/protocol.ts";
import { uploadFiles } from "../services/files.ts";
import { applyEvent, assistantFromStream, emptyStreamState, type StreamState, type ChatStreamBody } from "../services/stream.ts";
import type { Attachment, ChatMessage, StreamEvent, UserContent } from "../types/protocol";

interface Options {
    activeId: string | null;
    messages: ChatMessage[];
    setMessages: Dispatch<SetStateAction<ChatMessage[]>>;
    setActiveId: (id: string | null) => void;
    createConversation: (opts: { provider?: string | null; model?: string | null; reasoning?: string | null }) => Promise<{ id: string; title: string }>;
    refreshList: () => Promise<void>;
    provider: string | null;
    model: string | null;
    reasoning: string | null;
    verify: boolean;
    /** Reply language chosen in the UI; null lets the model follow the user's language. */
    language?: string | null;
    onArtifacts?: () => void;
}
interface Subscription {
    cid: string;
    abort: AbortController;
    state: StreamState;
    messages: ChatMessage[];
    assistantId: string;
    accepted: boolean;
}
function uid(prefix: string) { return `${prefix}${crypto.randomUUID()}`; }

export function useChat(options: Options) {
    const [streaming, setStreaming] = useState(false);
    const [statusLine, setStatusLine] = useState<string | null>(null);
    const [streamError, setStreamError] = useState<string | null>(null);
    const [overlay, setOverlay] = useState<{ cid: string; messages: ChatMessage[] } | null>(null);
    const [detached, setDetached] = useState(false);
    const [reconnecting, setReconnecting] = useState(false);
    const [paused, setPaused] = useState(false);
    const [pauseRequested, setPauseRequested] = useState(false);
    const [controlStatus, setControlStatus] = useState<string | null>(null);
    const current = useRef<Subscription | null>(null);
    const generation = useRef(0);
    const preparing = useRef(false);
    const preparingCid = useRef<string | null>(null);
    const optionsRef = useRef(options);
    optionsRef.current = options;

    const unsubscribe = useCallback(() => {
        ++generation.current;
        current.current?.abort.abort();
        current.current = null;
        preparing.current = false;
        setStreaming(false); setReconnecting(false); setPaused(false); setPauseRequested(false);
        setStatusLine(null); setControlStatus(null);
    }, []);

    const publish = useCallback((session: Subscription) => {
        if (current.current !== session || session.abort.signal.aborted) return;
        setStatusLine(session.state.statusLine);
        setPaused(Boolean(session.state.paused));
        setPauseRequested(Boolean(session.state.pauseRequested));
        setControlStatus(session.state.controlStatus ?? null);
        const answer: ChatMessage = {
            id: session.state.messageId || session.assistantId, conversation_id: session.cid,
            seq: session.messages.length + 1, role: "assistant", created_at: Date.now() / 1000,
            content: assistantFromStream(session.state),
        };
        let found = false;
        session.messages = session.messages.map((message) => {
            if (message.id !== session.assistantId && message.id !== answer.id) return message;
            found = true;
            return { ...message, id: answer.id, content: answer.content };
        });
        if (!found) session.messages = [...session.messages, answer];
        setOverlay({ cid: session.cid, messages: session.messages });
        if (optionsRef.current.activeId === session.cid) optionsRef.current.setMessages(session.messages);
    }, []);

    const monitor = useCallback(async (session: Subscription, body?: ChatStreamBody, onAccepted?: (accepted: boolean) => void) => {
        const original = session.messages.filter((message) => message.id !== session.assistantId);
        try {
            await observeRun({ body, runId: session.state.runId, after: session.state.lastSeq,
                signal: session.abort.signal,
                onConnection: (value) => { if (current.current === session) setReconnecting(value); },
                onEvent: (event: StreamEvent) => {
                    if (current.current !== session) return;
                    if (!session.accepted && ["message_start", "conversation", "run_state", "work"].includes(event.type)) {
                        session.accepted = true; onAccepted?.(true);
                        // The server titles a new chat alongside its first turn; pick it up while work continues.
                        if (!original.some((message) => message.role === "assistant")) {
                            for (const delay of [3000, 8000]) {
                                setTimeout(() => { if (!session.abort.signal.aborted) void optionsRef.current.refreshList(); }, delay);
                            }
                        }
                    }
                    session.state = applyEvent(session.state, event);
                    publish(session);
                    if (event.type === "error") setStreamError(String(event.message ?? "The run failed"));
                    if (event.type === "artifact" || event.type === "ui_commit") optionsRef.current.onArtifacts?.();
                },
            });
            if (current.current !== session) return session.accepted;
            setStreaming(false); setReconnecting(false); setStatusLine(null); setPaused(false); setPauseRequested(false);
            if (!session.accepted) { onAccepted?.(false); return false; }
            // Completion refresh updates only this turn. It never remounts the composer.
            try {
                const saved = await getConversation(session.cid, { signal: session.abort.signal, timeout: 10000 });
                if (current.current !== session) return true;
                const answer = saved.messages?.find((message) => message.id === session.state.messageId);
                if (answer && isAssistantContent(answer.content) && answer.content.status !== "streaming") {
                    session.messages = saved.messages;
                    setOverlay({ cid: session.cid, messages: saved.messages });
                    if (optionsRef.current.activeId === session.cid) optionsRef.current.setMessages(saved.messages);
                }
            } catch { /* The authoritative terminal event remains usable offline. */ }
            if (current.current !== session) return true;
            const last = session.messages.find((message) => message.id === session.state.messageId);
            if (last && isAssistantContent(last.content) && last.content.status === "complete" && !last.content.parts.some((part) => part.type === "options")) {
                const reply = textFromAssistant(last.content);
                if (reply) void fetchFollowUps({ conversation_id: session.cid, content: reply,
                    provider: optionsRef.current.provider ?? undefined, model: optionsRef.current.model ?? undefined,
                }).then(({ followups }) => {
                    if (current.current !== session || !followups?.length) return;
                    session.messages = session.messages.map((message) => message.id === last.id && isAssistantContent(message.content)
                        ? { ...message, content: { ...message.content, parts: [...message.content.parts.filter((part) => part.type !== "options"), { type: "options", items: followups }] } }
                        : message);
                    setOverlay({ cid: session.cid, messages: session.messages });
                    if (optionsRef.current.activeId === session.cid) optionsRef.current.setMessages(session.messages);
                }).catch(() => {});
            }
            void optionsRef.current.refreshList(); optionsRef.current.onArtifacts?.();
            return true;
        } catch (error) {
            if (current.current !== session || session.abort.signal.aborted) return session.accepted;
            const message = error instanceof Error ? error.message : "Could not connect to the run";
            setStreamError(message);
            // Connection failure is not evidence that execution failed.
            if (!session.accepted) {
                session.messages = original.filter((entry) => !body || entry.id !== original.at(-1)?.id);
                setOverlay({ cid: session.cid, messages: session.messages });
                if (optionsRef.current.activeId === session.cid) optionsRef.current.setMessages(session.messages);
                onAccepted?.(false);
                setStreaming(false); setReconnecting(false); setStatusLine(null);
                current.current = null;
            }
            return session.accepted;
        } finally {
            if (current.current === session) { setStreaming(false); setReconnecting(false); setStatusLine(null); }
        }
    }, [publish]);

    useEffect(() => {
        const cid = options.activeId;
        if (current.current?.cid === cid || (preparing.current && (!preparingCid.current || preparingCid.current === cid))) return;
        unsubscribe();
        setOverlay(null);
        setDetached(false); setStreamError(null);
        if (!cid) { setOverlay(null); return; }
        const mine = generation.current;
        const abort = new AbortController();
        void latestRun(cid, abort.signal).then(async (run) => {
            if (!run || abort.signal.aborted || generation.current !== mine || current.current || preparing.current || optionsRef.current.activeId !== cid) return;
            const saved = await getConversation(cid, { signal: abort.signal });
            if (abort.signal.aborted || generation.current !== mine || current.current || preparing.current || optionsRef.current.activeId !== cid) return;
            // Completed turns already represented in canonical history need no replay.
            if (["completed", "stopped", "failed", "interrupted"].includes(run.status)
                && saved.messages?.some((message) => message.id === run.message_id && isAssistantContent(message.content) && message.content.status !== "streaming")) return;
            const session: Subscription = { cid, abort: new AbortController(), accepted: true,
                assistantId: run.message_id || uid("m_"), messages: saved.messages ?? [],
                state: { ...emptyStreamState(), conversationId: cid, messageId: run.message_id, runId: run.id, paused: run.status === "paused" } };
            current.current = session; setStreaming(true); setPaused(run.status === "paused");
            publish(session);
            void monitor(session);
        }).catch((error) => {
            if (!abort.signal.aborted && generation.current === mine && (error as { response?: { status?: number } }).response?.status !== 404)
                setStreamError("Could not check the active run. Reopen this chat to reconnect.");
        });
        return () => abort.abort();
    }, [options.activeId, monitor, publish, unsubscribe]);

    useEffect(() => () => { generation.current += 1; current.current?.abort.abort(); current.current = null; }, []);

    const runTurn = useCallback(async (text: string, files: File[], skills: string[], tools: string[], accepted: (value: boolean) => void) => {
        const opts = optionsRef.current;
        const trimmed = text.trim();
        if (!trimmed) return false;
        const previous = current.current;
        if (previous?.state.status === "streaming" && previous.cid === opts.activeId && !previous.abort.signal.aborted) {
            const user: ChatMessage = { id: uid("u_"), conversation_id: previous.cid, seq: previous.messages.length + 1, role: "user",
                content: { text: trimmed, attachments: [] } satisfies UserContent, created_at: Date.now() / 1000 };
            previous.messages = [...previous.messages, user]; previous.state.controlStatus = "Instruction queued"; publish(previous);
            try {
                await steerRun(previous.cid, trimmed, { skills, tools });
                return true;
            } catch (error) {
                if (current.current === previous) {
                    previous.messages = previous.messages.filter((message) => message.id !== user.id); previous.state.controlStatus = null; publish(previous);
                    setStreamError(error instanceof Error ? error.message : "Could not steer");
                }
                return false;
            }
        }
        unsubscribe(); preparing.current = true; preparingCid.current = opts.activeId; setDetached(false); setStreamError(null);
        const mine = generation.current;
        let cid = opts.activeId;
        try {
            if (!cid) cid = (await opts.createConversation({ provider: opts.provider, model: opts.model, reasoning: opts.reasoning })).id;
            if (generation.current !== mine) return false;
            preparingCid.current = cid;
            let attachments: Attachment[] = [];
            if (files.length) {
                setStatusLine("Uploading files…");
                attachments = (await uploadFiles(cid, files)).map(({ name, path, url, size, kind }) => ({ name, path, url, size, kind }));
            }
            if (generation.current !== mine || (optionsRef.current.activeId !== cid && (opts.activeId || optionsRef.current.activeId))) return false;
            const user: ChatMessage = { id: uid("u_"), conversation_id: cid, seq: opts.messages.length + 1, role: "user",
                content: { text: trimmed, attachments }, created_at: Date.now() / 1000 };
            const session: Subscription = { cid, abort: new AbortController(), accepted: false, assistantId: uid("m_"),
                state: { ...emptyStreamState(), conversationId: cid }, messages: [...opts.messages, user] };
            session.state.messageId = session.assistantId;
            current.current = session; preparing.current = false; setStreaming(true); publish(session);
            if (!opts.activeId) optionsRef.current.setActiveId(cid);
            return await monitor(session, { conversation_id: cid, client_run_id: crypto.randomUUID(), message: trimmed, attachments,
                provider: opts.provider, model: opts.model, reasoning: opts.reasoning, verify: opts.verify,
                skills: skills.length ? skills : undefined, tools: tools.length ? tools : undefined, ui_version: 1,
                language: opts.language || undefined }, accepted);
        } catch (error) {
            if (generation.current === mine) setStreamError(error instanceof Error ? error.message : "Could not start the run");
            return false;
        } finally {
            if (generation.current === mine) { preparing.current = false; setStatusLine(null); }
        }
    }, [monitor, publish, unsubscribe]);

    const sendMessage = useCallback((text: string, files: File[] = [], skills: string[] = [], tools: string[] = []) =>
        new Promise<boolean>((resolve) => { void runTurn(text, files, skills, tools, resolve).then(resolve, () => resolve(false)); }), [runTurn]);

    const stop = useCallback(async () => {
        const session = current.current;
        const cid = session?.cid || optionsRef.current.activeId;
        if (!cid) return;
        if (session) session.state.controlStatus = "Stopping…";
        setControlStatus("Stopping…");
        try { await stopRun(cid); } catch {
            if (current.current === session) {
                if (session) session.state.controlStatus = null;
                setControlStatus(null); setStreamError("Could not stop the run. Please try again.");
            }
        }
    }, []);
    const pause = useCallback(async () => {
        const session = current.current;
        if (!session) return;
        session.state.pauseRequested = true; session.state.controlStatus = "Pause requested; finishing the current action";
        setPauseRequested(true); setControlStatus(session.state.controlStatus);
        try { await controlRun(session.cid, "pause"); }
        catch { if (current.current === session) { session.state.pauseRequested = false; session.state.controlStatus = null; setPauseRequested(false); setControlStatus(null); setStreamError("Could not pause the run. It may have already finished."); } }
    }, []);
    const resume = useCallback(async () => {
        const session = current.current;
        if (!session) return;
        session.state.controlStatus = "Resume requested";
        setControlStatus("Resume requested");
        try { await controlRun(session.cid, "resume"); }
        catch { if (current.current === session) { session.state.controlStatus = null; setControlStatus(null); setStreamError("Could not resume the run. It may have already finished."); } }
    }, []);
    const abandon = useCallback(async () => { unsubscribe(); setDetached(true); setOverlay(null); }, [unsubscribe]);

    return { sendMessage, stop, pause, resume, abandon, streaming, detached, paused, pauseRequested, controlStatus,
        reconnecting, statusLine, streamError, setStreamError, overlay };
}
