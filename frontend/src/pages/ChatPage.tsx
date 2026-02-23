/**
 * ChatPage — main page combining chat messages, input, file upload, and export.
 */

import React, { useCallback } from "react";
import { useChat } from "../hooks/useChat";
import { useDocument } from "../hooks/useDocument";
import { useExport } from "../hooks/useExport";
import {
    MessageList,
    MathInput,
    FileUploader,
    ExportButton,
} from "../components";
import type { ExportFormat } from "../types/export";

export default function ChatPage() {
    const { messages, loading, error, sendMessage, clearChat } = useChat();
    const { upload: uploadDoc, loading: docLoading } = useDocument();
    const { doExport, loading: exportLoading } = useExport();

    const handleFile = useCallback(
        async (file: File) => {
            await uploadDoc(file);
            // TODO: display parsed document or inject into chat context
        },
        [uploadDoc]
    );

    const handleExport = useCallback(
        (format: ExportFormat) => {
            const allContent = messages
                .map((m) => (m.role === "user" ? `**You:** ${m.content}` : m.content))
                .join("\n\n---\n\n");
            doExport(allContent, format, "MatOpt Chat Export");
        },
        [messages, doExport]
    );

    return (
        <div className="chat-page">
            <header className="chat-page__header">
                <h1>MatOpt</h1>
                <div className="chat-page__actions">
                    <ExportButton
                        onExport={handleExport}
                        disabled={exportLoading || messages.length === 0}
                    />
                    <button onClick={clearChat} disabled={messages.length === 0}>
                        Clear
                    </button>
                </div>
            </header>

            <main className="chat-page__messages">
                <MessageList messages={messages} loading={loading} />
            </main>

            {error && <div className="chat-page__error">{error}</div>}

            <footer className="chat-page__footer">
                <FileUploader onFile={handleFile} disabled={docLoading} />
                <MathInput onSubmit={sendMessage} disabled={loading} />
            </footer>
        </div>
    );
}
