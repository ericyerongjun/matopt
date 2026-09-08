/**
 * ChatGPT-style thread: user bubbles, assistant prose, results — never source.
 */

import React, { useEffect, useRef, type ReactNode } from "react";
import { Copy } from "lucide-react";
import type { AssistantContent, ChatMessage, Part } from "../types/protocol";
import { isAssistantContent, isUserContent, textFromAssistant } from "../types/protocol";
import MarkdownRenderer from "./MarkdownRenderer";
import ToolCallCard, {
    AnalysisTrace,
    CODE_TOOLS,
    hasDisplayArtifact,
    SILENT_TOOLS,
    type ToolPart,
} from "./ToolCallCard";
import "./MessageList.css";

interface Props {
    messages: ChatMessage[];
    conversationId: string | null;
    streaming?: boolean;
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
}: {
    parts: Part[];
    conversationId: string | null;
    streaming: boolean;
}) {
    const nodes: ReactNode[] = [];
    let sawReasoning = false;
    let sawCodeTrace = false;
    const codeTools = parts.filter((p): p is ToolPart => p.type === "tool_call" && CODE_TOOLS.has(p.name));

    for (let i = 0; i < parts.length; i++) {
        const part = parts[i];
        if (part.type === "reasoning") {
            if (!sawReasoning) {
                nodes.push(<ThinkingLabel key="thought" streaming={streaming} />);
                sawReasoning = true;
            }
            continue;
        }
        if (part.type === "text") {
            if (isCommentaryText(parts, i)) continue;
            nodes.push(
                <MarkdownRenderer key={`text-${i}`} content={part.text} conversationId={conversationId} />
            );
            continue;
        }
        if (part.type === "tool_call") {
            if (SILENT_TOOLS.has(part.name)) continue;
            if (CODE_TOOLS.has(part.name)) {
                if (!sawCodeTrace) {
                    nodes.push(
                        <AnalysisTrace key="analyzed" parts={codeTools} conversationId={conversationId} />
                    );
                    sawCodeTrace = true;
                }
                continue;
            }
            nodes.push(<ToolCallCard key={part.id} part={part} conversationId={conversationId} />);
        }
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

export default function MessageList({ messages, conversationId, streaming }: Props) {
    const bottomRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }, [messages]);

    return (
        <div className="thread">
            {messages.map((msg, index) => (
                <MessageRow
                    key={msg.id}
                    msg={msg}
                    conversationId={conversationId}
                    streaming={Boolean(streaming && index === messages.length - 1)}
                />
            ))}
            <div ref={bottomRef} />
        </div>
    );
}

function MessageRow({
    msg,
    conversationId,
    streaming,
}: {
    msg: ChatMessage;
    conversationId: string | null;
    streaming: boolean;
}) {
    if (msg.role === "user" && isUserContent(msg.content)) {
        return (
            <div className="thread__row thread__row--user">
                <div className="thread__message">
                    <div className="thread__body thread__body--user">
                        <p>{msg.content.text}</p>
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
                    <AssistantBody parts={content.parts} conversationId={conversationId} streaming={streaming} />
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
