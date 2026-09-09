/**
 * ChatGPT-style thread: user bubbles, assistant prose, results — never source.
 */

import React, { useLayoutEffect, useRef, type ReactNode } from "react";
import { Copy } from "lucide-react";
import type { AssistantContent, ChatMessage, Part } from "../types/protocol";
import { isAssistantContent, isUserContent, textFromAssistant } from "../types/protocol";
import { commandToken, parseSlashCommands } from "../services/skills";
import MarkdownRenderer from "./MarkdownRenderer";
import { hasDisplayArtifact, type ToolPart } from "./ToolCallCard";
import "./MessageList.css";

interface Props {
    messages: ChatMessage[];
    conversationId: string | null;
    streaming?: boolean;
    onPickOption?: (text: string) => void;
    onOpenWork?: () => void;
}

function ThinkingLabel({ streaming }: { streaming: boolean }) {
    return (
        <div className="thinking">
            <span className={`thinking__label ${streaming ? "thinking__label--live" : ""}`}>
                {streaming ? "Thinking…" : "Thought"}
            </span>
        </div>
    );
}

function isCommentaryText(parts: Part[], index: number): boolean {
    const tools: ToolPart[] = [];
    for (let i = index + 1; i < parts.length; i++) {
        const part = parts[i];
        if (part.type === "reasoning") continue;
        if (part.type === "text") break;
        if (part.type === "tool_call") tools.push(part);
    }
    if (!tools.length) return false;
    return !tools.some((t) => hasDisplayArtifact(t) || t.status === "running");
}

function AssistantBody({
    parts,
    conversationId,
    streaming,
    onOpenWork,
}: {
    parts: Part[];
    conversationId: string | null;
    streaming: boolean;
    onOpenWork?: () => void;
}) {
    const nodes: ReactNode[] = [];
    let sawReasoning = false;
    const toolCount = parts.filter((p) => p.type === "tool_call").length;

    for (let i = 0; i < parts.length; i++) {
        const part = parts[i];
        if (part.type === "reasoning") {
            if (!sawReasoning) {
                nodes.push(<ThinkingLabel key="thought" streaming={streaming} />);
                sawReasoning = true;
            }
            continue;
        }
        if (part.type === "skills") {
            nodes.push(
                <div key={`skills-${i}`} className="thread__skills">
                    {part.names.map((name) => (
                        <span key={name} className="thread__skill">
                            /{name}
                        </span>
                    ))}
                </div>
            );
            continue;
        }
        if (part.type === "text") {
            if (isCommentaryText(parts, i)) continue;
            nodes.push(
                <MarkdownRenderer key={`text-${i}`} content={part.text} conversationId={conversationId} />
            );
            continue;
        }
        if (part.type === "options") {
            continue;
        }
        if (part.type === "monitor" || part.type === "tool_call" || part.type === "phase" || part.type === "verification") {
            continue;
        }
    }
    if (toolCount > 0) {
        nodes.unshift(
            <button key="work-hint" type="button" className="thread__work-hint" onClick={onOpenWork}>
                {streaming ? "Working" : "Work"} · {toolCount} {toolCount === 1 ? "action" : "actions"}
            </button>
        );
    }

    if (streaming && parts.length === 0) {
        nodes.push(
            <div key="typing" className="thread__typing" aria-label="Working">
                <span className="dot" />
                <span className="dot" />
                <span className="dot" />
            </div>
        );
    }

    return <>{nodes}</>;
}

function AssistantFooter({ content }: { content: AssistantContent }) {
    const copy = () => {
        void navigator.clipboard.writeText(textFromAssistant(content));
    };
    return (
        <div className="assistant-footer">
            <button type="button" className="icon-btn" title="Copy" onClick={copy}>
                <Copy size={16} />
            </button>
        </div>
    );
}

export default function MessageList({ messages, conversationId, streaming, onPickOption, onOpenWork }: Props) {
    const rootRef = useRef<HTMLDivElement>(null);
    const pinnedRef = useRef(true);

    const stick = () => {
        const el = rootRef.current;
        if (!el || !pinnedRef.current) return;
        el.scrollTop = el.scrollHeight;
    };

    useLayoutEffect(() => {
        if (streaming) pinnedRef.current = true;
        stick();
    }, [messages, streaming]);

    useLayoutEffect(() => {
        const el = rootRef.current;
        const inner = el?.firstElementChild;
        if (!inner) return;
        const ro = new ResizeObserver(() => stick());
        ro.observe(inner);
        return () => ro.disconnect();
    }, []);

    return (
        <div
            className="thread"
            ref={rootRef}
            onScroll={() => {
                const el = rootRef.current;
                if (!el) return;
                pinnedRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 72;
            }}
        >
            <div className="thread__inner">
                {messages.map((msg, index) => (
                    <MessageRow
                        key={msg.id}
                        msg={msg}
                        conversationId={conversationId}
                        streaming={Boolean(streaming && index === messages.length - 1)}
                        onPickOption={onPickOption}
                        onOpenWork={onOpenWork}
                    />
                ))}
            </div>
        </div>
    );
}

function MessageRow({
    msg,
    conversationId,
    streaming,
    onPickOption,
    onOpenWork,
}: {
    msg: ChatMessage;
    conversationId: string | null;
    streaming: boolean;
    onPickOption?: (text: string) => void;
    onOpenWork?: () => void;
}) {
    if (msg.role === "user" && isUserContent(msg.content)) {
        const parsed = parseSlashCommands(msg.content.text);
        const names = [...parsed.skills, ...parsed.tools];
        return (
            <div className="thread__row thread__row--user">
                <div className="thread__message">
                    <div className="thread__body thread__body--user">
                        {names.length > 0 && (
                            <div className="thread__skills thread__skills--user">
                                {names.map((name) => (
                                    <span key={name} className="thread__skill">
                                        {commandToken(name)}
                                    </span>
                                ))}
                            </div>
                        )}
                        {parsed.rest ? <p>{parsed.rest}</p> : null}
                        {msg.content.attachments?.length > 0 && (
                            <div className="thread__attachments">
                                {msg.content.attachments.map((a) => (
                                    <a key={a.path} href={a.url || `/api/files/${msg.conversation_id}/${a.path}`}>
                                        {a.name}
                                    </a>
                                ))}
                            </div>
                        )}
                    </div>
                </div>
            </div>
        );
    }

    if (msg.role !== "assistant" || !isAssistantContent(msg.content)) return null;
    const content = msg.content;

    return (
        <div className="thread__row thread__row--assistant">
            <div className="thread__message">
                <div className="thread__body">
                    <AssistantBody
                        parts={content.parts}
                        conversationId={conversationId}
                        streaming={streaming}
                        onOpenWork={onOpenWork}
                    />
                    {!streaming &&
                        content.parts
                            .filter((p): p is Extract<Part, { type: "options" }> => p.type === "options")
                            .map((part, i) => (
                                <div key={`opt-${i}`} className="thread__options">
                                    {part.items.map((item) => (
                                        <button
                                            key={item}
                                            type="button"
                                            className="thread__option"
                                            onClick={() => onPickOption?.(item)}
                                        >
                                            {item}
                                        </button>
                                    ))}
                                </div>
                            ))}
                </div>
                {(content.status === "stopped" || content.status === "error") && (
                    <div className="thread__notice">
                        {content.status === "stopped"
                            ? "Stopped."
                            : "Couldn’t complete that analysis. Try again, or rephrase the question."}
                    </div>
                )}
                {!streaming && <AssistantFooter content={content} />}
            </div>
        </div>
    );
}
