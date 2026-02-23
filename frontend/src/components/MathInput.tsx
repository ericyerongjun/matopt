/**
 * MathInput — dual-mode input: LaTeX text area or handwriting canvas.
 *
 * On submit, the typed LaTeX is sent directly.
 * TODO: integrate react-canvas-draw for handwriting → OCR path.
 */

import React, { useState, useCallback } from "react";

interface Props {
    onSubmit: (latex: string) => void;
    disabled?: boolean;
    placeholder?: string;
}

export default function MathInput({
    onSubmit,
    disabled = false,
    placeholder = "Type a question or LaTeX…",
}: Props) {
    const [value, setValue] = useState("");

    const handleSubmit = useCallback(
        (e: React.FormEvent) => {
            e.preventDefault();
            const trimmed = value.trim();
            if (!trimmed) return;
            onSubmit(trimmed);
            setValue("");
        },
        [value, onSubmit]
    );

    const handleKeyDown = useCallback(
        (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
            // Submit on Enter (without Shift)
            if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                handleSubmit(e as unknown as React.FormEvent);
            }
        },
        [handleSubmit]
    );

    return (
        <form className="math-input" onSubmit={handleSubmit}>
            <textarea
                className="math-input__textarea"
                rows={3}
                value={value}
                onChange={(e) => setValue(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder={placeholder}
                disabled={disabled}
            />
            <button
                className="math-input__submit"
                type="submit"
                disabled={disabled || !value.trim()}
            >
                Send
            </button>
        </form>
    );
}
