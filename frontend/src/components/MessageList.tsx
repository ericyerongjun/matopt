/**
 * MessageList — ChatGPT-style message thread.
 *
 * Full-width alternating rows with avatar icons.
 * No chat bubbles — clean, centered layout.
 */

import React, { useRef, useEffect } from "react";
import { User, Bot, Wrench } from "lucide-react";
import type { Message } from "../types/message";
import MarkdownRenderer from "./MarkdownRenderer";

interface Props {
    messages: Message[];
    loading?: boolean;
}

export default function MessageList({ messages, loading }: Props) {
    const bottomRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }, [messages.length, loading]);

    if (messages.length === 0 && !loading) {
        return null; // Empty state handled by parent
    }

    return (
        <div className="thread">
            {messages.map((msg) => (
                <div
                    key={msg.id}
                    className={`thread__row ${msg.role === "user"
                            ? "thread__row--user"
                            : "thread__row--assistant"
                        }`}
                >
                    <div className="thread__message">
                        <div className="thread__avatar">
                            {msg.role === "user" ? (
                                <div className="avatar avatar--user">
                                    <User size={18} />
                                </div>
                            ) : (
                                <div className="avatar avatar--assistant">
                                    <Bot size={18} />
                                </div>
                            )}
                        </div>
                        <div className="thread__content">
                            <div className="thread__role">
                                {msg.role === "user" ? "You" : "MatOpt"}
                            </div>
                            <div className="thread__body">
                                {msg.role === "assistant" ? (
                                    <MarkdownRenderer content={msg.content} />
                                ) : (
                                    <p>{msg.content}</p>
                                )}
                            </div>
                            {msg.toolCalls && msg.toolCalls.length > 0 && (
                                <details className="thread__tools">
                                    <summary>
                                        <Wrench size={14} />
                                        <span>
                                            {msg.toolCalls.length} tool
                                            {msg.toolCalls.length > 1
                                                ? "s"
                                                : ""}{" "}
                                            used
                                        </span>
                                    </summary>
                                    <div className="thread__tools-list">
                                        {msg.toolCalls.map((tc, i) => (
                                            <div
                                                key={i}
                                                className="thread__tool-item"
                                            >
                                                <code>{tc.name}</code>
                                                {tc.result && (
                                                    <pre className="thread__tool-result">
                                                        {tc.result}
                                                    </pre>
                                                )}
                                            </div>
                                        ))}
                                    </div>
                                </details>
                            )}
                        </div>
                    </div>
                </div>
            ))}

            {loading && (
                <div className="thread__row thread__row--assistant">
                    <div className="thread__message">
                        <div className="thread__avatar">
                            <div className="avatar avatar--assistant">
                                <Bot size={18} />
                            </div>
                        </div>
                        <div className="thread__content">
                            <div className="thread__role">MatOpt</div>
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
