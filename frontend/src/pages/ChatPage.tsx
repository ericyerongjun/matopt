/**
 * ChatPage — ChatGPT-style layout: sidebar + main content area.
 *
 * Sidebar: conversation history, new-chat button, user section.
 * Main: centered message thread, welcome screen when empty,
 *       and a bottom-pinned input bar.
 */

import React, { useState, useCallback, useRef } from "react";
import { useChat } from "../hooks/useChat";
import { useDocument } from "../hooks/useDocument";
import { useExport } from "../hooks/useExport";
import {
    MessageList,
    MathInput,
    FileUploader,
    Sidebar,
    WelcomeScreen,
} from "../components";
import type { ExportFormat } from "../types/export";

export default function ChatPage() {
    const {
        conversations,
        activeId,
        createConversation,
        switchConversation,
        deleteConversation,
        messages,
        loading,
        error,
        sendMessage,
    } = useChat();

    const { upload: uploadDoc, loading: docLoading } = useDocument();
    const { doExport, loading: exportLoading } = useExport();
    const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
    const fileInputRef = useRef<HTMLInputElement>(null);

    const handleNewChat = useCallback(() => {
        createConversation();
    }, [createConversation]);

    const handleSend = useCallback(
        (text: string) => {
            sendMessage(text);
        },
        [sendMessage]
    );

    const handleFileClick = useCallback(() => {
        fileInputRef.current?.click();
    }, []);

    const handleFileChange = useCallback(
        async (e: React.ChangeEvent<HTMLInputElement>) => {
            const file = e.target.files?.[0];
            if (file) {
                await uploadDoc(file);
            }
            // Reset so the same file can be re-selected
            if (fileInputRef.current) fileInputRef.current.value = "";
        },
        [uploadDoc]
    );

    const handleExport = useCallback(
        (format: ExportFormat) => {
            const allContent = messages
                .map((m) =>
                    m.role === "user" ? `**You:** ${m.content}` : m.content
                )
                .join("\n\n---\n\n");
            doExport(allContent, format, "MatOpt Chat Export");
        },
        [messages, doExport]
    );

    const isEmpty = messages.length === 0;

    return (
        <div className="app-layout">
            <Sidebar
                conversations={conversations}
                activeId={activeId}
                onNewChat={handleNewChat}
                onSelect={switchConversation}
                onDelete={deleteConversation}
                onExport={handleExport}
                collapsed={sidebarCollapsed}
                onToggleCollapse={() =>
                    setSidebarCollapsed(!sidebarCollapsed)
                }
            />

            <main className="main">
                {/* Header bar */}
                <header className="main__header">
                    <span className="main__model-label">MatOpt</span>
                </header>

                {/* Message area */}
                <div className="main__thread-area">
                    {isEmpty ? (
                        <WelcomeScreen onSuggestion={handleSend} />
                    ) : (
                        <MessageList messages={messages} loading={loading} />
                    )}
                </div>

                {/* Error banner */}
                {error && (
                    <div className="main__error">
                        <span>{error}</span>
                    </div>
                )}

                {/* Input bar */}
                <div className="main__input-area">
                    <MathInput
                        onSubmit={handleSend}
                        onFileClick={handleFileClick}
                        disabled={loading}
                        loading={loading}
                    />
                </div>

                {/* Hidden file input */}
                <input
                    ref={fileInputRef}
                    type="file"
                    accept=".pdf,.png,.jpg,.jpeg,.webp"
                    style={{ display: "none" }}
                    onChange={handleFileChange}
                />
            </main>
        </div>
    );
}

