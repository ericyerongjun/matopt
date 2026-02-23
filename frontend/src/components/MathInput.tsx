/**
 * ChatInput — ChatGPT-style centered input bar.
 *
 * Pill-shaped container with attachment button, auto-resizing textarea,
 * and a send arrow button.
 */

import React, { useState, useCallback, useRef, useEffect } from "react";
import { ArrowUp, Paperclip, Square } from "lucide-react";

interface Props {
    onSubmit: (text: string) => void;
    onFileClick?: () => void;
    disabled?: boolean;
    loading?: boolean;
    placeholder?: string;
}

export default function MathInput({
    onSubmit,
    onFileClick,
    disabled = false,
    loading = false,
    placeholder = "Message MatOpt…",
}: Props) {
    const [value, setValue] = useState("");
    const textareaRef = useRef<HTMLTextAreaElement>(null);

    // Auto-resize textarea
    useEffect(() => {
        const ta = textareaRef.current;
        if (!ta) return;
        ta.style.height = "auto";
        ta.style.height = `${Math.min(ta.scrollHeight, 200)}px`;
    }, [value]);

    const handleSubmit = useCallback(() => {
        const trimmed = value.trim();
        if (!trimmed || disabled) return;
        onSubmit(trimmed);
        setValue("");
        // Reset height
        if (textareaRef.current) {
            textareaRef.current.style.height = "auto";
        }
    }, [value, disabled, onSubmit]);

    const handleKeyDown = useCallback(
        (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
            if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                handleSubmit();
            }
        },
        [handleSubmit]
    );

    const canSend = value.trim().length > 0 && !disabled;

    return (
        <div className="chat-input-wrapper">
            <div className="chat-input">
                {onFileClick && (
                    <button
                        className="chat-input__attach"
                        onClick={onFileClick}
                        disabled={disabled}
                        title="Attach file"
                        type="button"
                    >
                        <Paperclip size={20} />
                    </button>
                )}
                <textarea
                    ref={textareaRef}
                    className="chat-input__textarea"
                    rows={1}
                    value={value}
                    onChange={(e) => setValue(e.target.value)}
                    onKeyDown={handleKeyDown}
                    placeholder={placeholder}
                    disabled={disabled}
                />
                <button
                    className={`chat-input__send ${canSend ? "chat-input__send--active" : ""}`}
                    onClick={loading ? undefined : handleSubmit}
                    disabled={!canSend && !loading}
                    title={loading ? "Stop" : "Send"}
                    type="button"
                >
                    {loading ? <Square size={16} /> : <ArrowUp size={18} />}
                </button>
            </div>
            <p className="chat-input__disclaimer">
                MatOpt can make mistakes. Verify important math.
            </p>
        </div>
    );
}

