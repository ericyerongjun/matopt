/**
 * MessageList — ChatGPT-style message thread.
 *
 * Full-width alternating rows with avatar icons.
 * No chat bubbles — clean, centered layout.
 */

import React, { useRef, useEffect, useState, memo, useCallback } from "react";
import { Brain, Code2, ChevronRight } from "lucide-react";
import type { Message } from "../types/message";
import MarkdownRenderer from "./MarkdownRenderer";
import InlineMathText from "./InlineMathText";
import { fetchFollowUps } from "../services/followups";

interface Props {
    messages: Message[];
    loading?: boolean;
}

function areToolCallsEqual(
    a: Message["toolCalls"] | undefined,
    b: Message["toolCalls"] | undefined
) {
    if (a === b) return true;
    if (!a || !b) return false;
    if (a.length !== b.length) return false;
    for (let i = 0; i < a.length; i += 1) {
        if (a[i].name !== b[i].name) return false;
        if (a[i].result !== b[i].result) return false;
    }
    return true;
}

function areStringArraysEqual(a?: string[], b?: string[]) {
    if (a === b) return true;
    if (!a || !b) return false;
    if (a.length !== b.length) return false;
    for (let i = 0; i < a.length; i += 1) {
        if (a[i] !== b[i]) return false;
    }
    return true;
}

const MessageRow = memo(
    function MessageRow({
        msg,
        followups,
        followupsLoading,
        onFollowupsRegenerate,
        onFollowupClick,
    }: {
        msg: Message;
        followups?: string[];
        followupsLoading?: boolean;
        onFollowupClick?: (text: string) => void;
        onFollowupsRegenerate?: () => void;
    }) {
        return (
            <div
                className={`thread__row ${msg.role === "user"
                    ? "thread__row--user"
                    : "thread__row--assistant"
                    }`}
            >
                <div className="thread__message">
                    <div className="thread__content">
                        <div className="thread__body">
                            {msg.role === "assistant" ? (
                                <MarkdownRenderer content={msg.content} />
                            ) : (
                                <p>{msg.content}</p>
                            )}
                        </div>
                        {msg.role === "assistant" && onFollowupClick && (
                            <div className="thread__followups">
                                <div className="thread__followups-header">
                                    <span>Follow-ups</span>
                                    {onFollowupsRegenerate && (
                                        <button
                                            className="thread__followups-refresh"
                                            onClick={onFollowupsRegenerate}
                                            disabled={followupsLoading}
                                            type="button"
                                        >
                                            Regenerate
                                        </button>
                                    )}
                                </div>
                                {followupsLoading && (
                                    <div className="thread__followups-loading">
                                        Generating follow-ups...
                                    </div>
                                )}
                                {!followupsLoading &&
                                    followups &&
                                    followups.length > 0 && (
                                        <div className="thread__followups-list">
                                            {followups.map((f, i) => (
                                                <button
                                                    key={`${msg.id}-f-${i}`}
                                                    className="thread__followup-chip"
                                                    onClick={() =>
                                                        onFollowupClick(f)
                                                    }
                                                >
                                                    <InlineMathText text={f} />
                                                </button>
                                            ))}
                                        </div>
                                    )}
                            </div>
                        )}
                        {msg.toolCalls && msg.toolCalls.length > 0 && (
                            <div className="thread__reasoning">
                                {/* Thinking / reasoning summary */}
                                <details className="thread__thinking" open>
                                    <summary className="thread__thinking-summary">
                                        <Brain size={14} />
                                        <span>Reasoning</span>
                                        <ChevronRight size={14} className="thread__thinking-chevron" />
                                    </summary>
                                    <div className="thread__thinking-steps">
                                        {msg.toolCalls.map((tc, i) => {
                                            const isExecPython = tc.name === "exec_python";
                                            const code = isExecPython
                                                ? (tc.arguments?.code as string) || ""
                                                : "";
                                            const toolLabel = tc.name
                                                .replace(/_/g, " ")
                                                .replace(/\b\w/g, (c) => c.toUpperCase());
                                            return (
                                                <div key={i} className="thread__step">
                                                    <div className="thread__step-header">
                                                        {isExecPython ? (
                                                            <Code2 size={13} />
                                                        ) : (
                                                            <Brain size={13} />
                                                        )}
                                                        <span className="thread__step-label">
                                                            {isExecPython
                                                                ? "Ran Python code"
                                                                : toolLabel}
                                                        </span>
                                                    </div>
                                                    {isExecPython && code && (
                                                        <pre className="thread__code-block">
                                                            <code>{code}</code>
                                                        </pre>
                                                    )}
                                                    {tc.result && (
                                                        <div className="thread__step-result">
                                                            <MarkdownRenderer content={tc.result} />
                                                        </div>
                                                    )}
                                                </div>
                                            );
                                        })}
                                    </div>
                                </details>
                            </div>
                        )}
                    </div>
                </div>
            </div>
        );
    },
    (prev, next) =>
        prev.msg.id === next.msg.id &&
        prev.msg.role === next.msg.role &&
        prev.msg.content === next.msg.content &&
        areToolCallsEqual(prev.msg.toolCalls, next.msg.toolCalls) &&
        prev.followupsLoading === next.followupsLoading &&
        areStringArraysEqual(prev.followups, next.followups)
);

export default function MessageList({ messages, loading }: Props) {
    const bottomRef = useRef<HTMLDivElement>(null);
    const [followups, setFollowups] = useState<string[]>([]);
    const [followupsLoading, setFollowupsLoading] = useState(false);

    const lastAssistant = [...messages]
        .reverse()
        .find((m) => m.role === "assistant");

    useEffect(() => {
        bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }, [messages.length, loading]);

    const loadFollowups = useCallback(async () => {
        if (!lastAssistant?.content) return;
        setFollowupsLoading(true);
        try {
            const resp = await fetchFollowUps({
                content: lastAssistant.content,
                count: 3,
            });
            setFollowups(
                resp.followups.map((f) => f.trim()).filter(Boolean)
            );
        } catch {
            setFollowups([]);
        } finally {
            setFollowupsLoading(false);
        }
    }, [lastAssistant?.content]);

    useEffect(() => {
        if (!lastAssistant?.content) {
            setFollowups([]);
            setFollowupsLoading(false);
            return;
        }
        void loadFollowups();
    }, [lastAssistant?.id, lastAssistant?.content, loadFollowups]);

    if (messages.length === 0 && !loading) {
        return null; // Empty state handled by parent
    }

    return (
        <div className="thread">
            {messages.map((msg) => (
                <MessageRow
                    key={msg.id}
                    msg={msg}
                    followups={
                        msg.id === lastAssistant?.id ? followups : undefined
                    }
                    followupsLoading={
                        msg.id === lastAssistant?.id ? followupsLoading : false
                    }
                    onFollowupsRegenerate={
                        msg.id === lastAssistant?.id ? loadFollowups : undefined
                    }
                    onFollowupClick={(text) => {
                        const customEvent = new CustomEvent(
                            "matopt:followup",
                            { detail: text }
                        );
                        window.dispatchEvent(customEvent);
                    }}
                />
            ))}

            {loading && (
                <div className="thread__row thread__row--assistant">
                    <div className="thread__message">
                        <div className="thread__content">
                            <div className="thread__body">
                                <div className="thread__typing">
                                    <span className="dot"></span>
                                    <span className="dot"></span>
                                    <span className="dot"></span>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            )}

            <div ref={bottomRef} />
        </div>
    );
}
