/**
 * ChatGPT-style composer: plus, field, model/effort, circular black send.
 */

import React, { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { ArrowUp, Plus, Square, X } from "lucide-react";
import "./MathInput.css";

interface Props {
    onSubmit: (text: string, files: File[]) => void;
    onStop?: () => void;
    disabled?: boolean;
    loading?: boolean;
    placeholder?: string;
    toolbar?: ReactNode;
    showDisclaimer?: boolean;
}

export default function MathInput({
    onSubmit,
    onStop,
    disabled = false,
    loading = false,
    placeholder = "Ask anything",
    toolbar,
    showDisclaimer = true,
}: Props) {
    const [value, setValue] = useState("");
    const [files, setFiles] = useState<File[]>([]);
    const [dragOver, setDragOver] = useState(false);
    const textareaRef = useRef<HTMLTextAreaElement>(null);
    const fileRef = useRef<HTMLInputElement>(null);

    useEffect(() => {
        const ta = textareaRef.current;
        if (!ta) return;
        ta.style.height = "auto";
        ta.style.height = `${Math.min(ta.scrollHeight, 200)}px`;
    }, [value]);

    const addFiles = useCallback((list: FileList | File[]) => {
        const next = Array.from(list);
        if (!next.length) return;
        setFiles((prev) => {
            const names = new Set(prev.map((f) => f.name + f.size));
            return [...prev, ...next.filter((f) => !names.has(f.name + f.size))];
        });
    }, []);

    const handleSubmit = useCallback(() => {
        const trimmed = value.trim();
        if (!trimmed || disabled || loading) return;
        onSubmit(trimmed, files);
        setValue("");
        setFiles([]);
        if (textareaRef.current) textareaRef.current.style.height = "auto";
    }, [value, files, disabled, loading, onSubmit]);

    const canSend = value.trim().length > 0 && !disabled && !loading;

    return (
        <div className="chat-input-wrapper">
            <div
                className={`chat-input ${dragOver ? "chat-input--drag" : ""}`}
                onDragOver={(e) => {
                    e.preventDefault();
                    setDragOver(true);
                }}
                onDragLeave={() => setDragOver(false)}
                onDrop={(e) => {
                    e.preventDefault();
                    setDragOver(false);
                    if (e.dataTransfer.files.length) addFiles(e.dataTransfer.files);
                }}
            >
                <div className="chat-input__main">
                    {files.length > 0 && (
                        <div className="chat-input__chips">
                            {files.map((file) => (
                                <span key={file.name + file.size} className="chat-input__chip">
                                    {file.name}
                                    <button
                                        type="button"
                                        onClick={() => setFiles((prev) => prev.filter((f) => f !== file))}
                                        aria-label={`Remove ${file.name}`}
                                    >
                                        <X size={12} />
                                    </button>
                                </span>
                            ))}
                        </div>
                    )}
                    <textarea
                        ref={textareaRef}
                        className="chat-input__textarea"
                        rows={1}
                        value={value}
                        onChange={(e) => setValue(e.target.value)}
                        onKeyDown={(e) => {
                            if (e.key === "Enter" && !e.shiftKey) {
                                e.preventDefault();
                                handleSubmit();
                            }
                        }}
                        placeholder={placeholder}
                        disabled={disabled || loading}
                    />
                </div>
                <div className="chat-input__bar">
                    <button
                        className="chat-input__attach"
                        onClick={() => fileRef.current?.click()}
                        disabled={disabled || loading}
                        title="Add photos and files"
                        type="button"
                    >
                        <Plus size={20} />
                    </button>
                    <div className="chat-input__bar-right">
                        {toolbar}
                        <button
                            className={`chat-input__send ${canSend || loading ? "chat-input__send--active" : ""}`}
                            onClick={loading ? onStop : handleSubmit}
                            disabled={loading ? !onStop : !canSend}
                            title={loading ? "Stop" : "Send"}
                            type="button"
                        >
                            {loading ? <Square size={12} fill="currentColor" /> : <ArrowUp size={18} strokeWidth={2.5} />}
                        </button>
                    </div>
                </div>
                <input
                    ref={fileRef}
                    type="file"
                    multiple
                    hidden
                    onChange={(e) => {
                        if (e.target.files) addFiles(e.target.files);
                        e.target.value = "";
                    }}
                />
            </div>
            {showDisclaimer && <p className="chat-input__disclaimer">MatOpt can make mistakes. Check important info.</p>}
        </div>
    );
}
