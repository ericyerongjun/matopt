/**
 * MessageList — displays conversation messages with role-based styling.
 * User messages right-aligned, assistant messages left-aligned.
 */

import React, { useRef, useEffect } from "react";
import type { Message } from "../types/message";
import MarkdownRenderer from "./MarkdownRenderer";

interface Props {
    messages: Message[];
    loading?: boolean;
}

export default function MessageList({ messages, loading }: Props) {
    const bottomRef = useRef<HTMLDivElement>(null);

    // Auto-scroll to bottom on new messages
    useEffect(() => {
        bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }, [messages.length]);

    return (
        <div className="message-list">
            {messages.map((msg) => (
                <div key={msg.id} className={`message message--${msg.role}`}>
                    <div className="message__role">
                        {msg.role === "user" ? "You" : "MatOpt"}
                    </div>
                    <div className="message__content">
                        {msg.role === "assistant" ? (
                            <MarkdownRenderer content={msg.content} />
                        ) : (
                            <p>{msg.content}</p>
                        )}
                    </div>
                    {msg.toolCalls && msg.toolCalls.length > 0 && (
                        <details className="message__tools">
                            <summary>
                                {msg.toolCalls.length} tool call
                                {msg.toolCalls.length > 1 ? "s" : ""} used
                            </summary>
                            <ul>
                                {msg.toolCalls.map((tc, i) => (
                                    <li key={i}>
                                        <strong>{tc.name}</strong>
                                        {tc.result && (
                                            <pre className="tool-result">{tc.result}</pre>
                                        )}
                                    </li>
                                ))}
                            </ul>
                        </details>
                    )}
                </div>
            ))}
            {loading && (
                <div className="message message--assistant message--loading">
                    <span className="dot-loader" />
                </div>
            )}
            <div ref={bottomRef} />
        </div>
    );
}
