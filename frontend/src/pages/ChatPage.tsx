/**
 * ChatPage — OpenAI/ChatGPT light canvas: home greeting, thread, composer.
 */

import React, { useCallback, useEffect, useRef, useState } from "react";
import { FolderOpen, MoreHorizontal, RotateCcw, ShieldCheck } from "lucide-react";
import { useChat } from "../hooks/useChat";
import { useConversations } from "../hooks/useConversations";
import { useLlmSelection } from "../hooks/useLlmSelection";
import { useSkills } from "../hooks/useSkills";
import { useWorkspaceFiles } from "../hooks/useWorkspaceFiles";
import { exportConversation, restartKernel } from "../services/conversations";
import { uploadFiles } from "../services/files";
import {
    AnalysisPanel,
    FilesPanel,
    MathInput,
    MessageList,
    ModelPicker,
    Sidebar,
    WelcomeScreen,
} from "../components";
import "./ChatPage.css";

export default function ChatPage() {
    const llm = useLlmSelection();
    const convos = useConversations();
    const files = useWorkspaceFiles(convos.activeId);
    const skillCatalog = useSkills();
    const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
    const [filesOpen, setFilesOpen] = useState(false);
    const [workOpen, setWorkOpen] = useState(false);
    const [menuOpen, setMenuOpen] = useState(false);
    const [webSearch, setWebSearch] = useState(() => {
        try {
            return localStorage.getItem("matopt.webSearch") === "1";
        } catch {
            return false;
        }
    });
    const menuRef = useRef<HTMLDivElement>(null);

    const chat = useChat({
        activeId: convos.activeId,
        messages: convos.messages,
        setMessages: convos.setMessages,
        setActiveId: convos.setActiveId,
        createConversation: convos.create,
        refreshList: convos.refreshList,
        provider: llm.provider,
        model: llm.model,
        reasoning: llm.reasoning,
        verify: llm.verify,
        webSearch,
        onArtifacts: () => {
            void files.refresh();
        },
    });

    const handleSend = useCallback(
        (text: string, attached: File[] = [], skillNames: string[] = []) => {
            void chat.sendMessage(text, attached, skillNames);
        },
        [chat.sendMessage]
    );

    useEffect(() => {
        if (!menuOpen) return;
        const onClick = (event: MouseEvent) => {
            if (!menuRef.current?.contains(event.target as Node)) setMenuOpen(false);
        };
        document.addEventListener("mousedown", onClick);
        return () => document.removeEventListener("mousedown", onClick);
    }, [menuOpen]);

    const messages =
        chat.streaming && chat.overlay && convos.activeId === chat.overlay.cid
            ? chat.overlay.messages
            : convos.messages;
    const showWelcome = !convos.activeId && messages.length === 0 && !chat.streaming;
    const error = convos.error || chat.streamError || llm.error;

    const composer = (
        <MathInput
            onSubmit={handleSend}
            onStop={() => void chat.stop()}
            disabled={false}
            loading={chat.streaming}
            showDisclaimer={!showWelcome}
            skills={skillCatalog.skills}
            webSearch={webSearch}
            onWebSearchChange={(on) => {
                setWebSearch(on);
                try {
                    localStorage.setItem("matopt.webSearch", on ? "1" : "0");
                } catch {
                    /* private mode */
                }
            }}
            toolbar={
                <ModelPicker
                    catalog={llm.catalog}
                    provider={llm.provider}
                    model={llm.model}
                    label={llm.selectedLabel}
                    onSelect={llm.select}
                    disabled={chat.streaming}
                    menuUp={!showWelcome}
                    levels={llm.allowedLevels}
                    reasoning={llm.reasoning}
                    onReasoningChange={llm.selectReasoning}
                />
            }
        />
    );

    return (
        <div className="app-layout">
            <Sidebar
                conversations={convos.conversations}
                activeId={convos.activeId}
                onNewChat={convos.newChat}
                onSelect={convos.select}
                onDelete={(id) => void convos.remove(id)}
                onRename={(id, title) => void convos.rename(id, title)}
                collapsed={sidebarCollapsed}
                onToggleCollapse={() => setSidebarCollapsed((v) => !v)}
            />

            <main className={`main ${showWelcome ? "main--home" : ""}`}>
                <header className="main__header">
                    <div className="main__header-left" />
                    <div className="main__header-right">
                        <div className="header-menu" ref={menuRef}>
                            <button
                                type="button"
                                className="header-menu__trigger"
                                title="More"
                                onClick={() => setMenuOpen((v) => !v)}
                            >
                                <MoreHorizontal size={18} />
                            </button>
                            {menuOpen && (
                                <div className="header-menu__list">
                                    {convos.activeId && (
                                        <>
                                            <button
                                                type="button"
                                                onClick={() => {
                                                    setFilesOpen((v) => !v);
                                                    void files.refresh();
                                                    setMenuOpen(false);
                                                }}
                                            >
                                                <FolderOpen size={16} />
                                                {filesOpen ? "Hide files" : "Files"}
                                            </button>
                                            <button
                                                type="button"
                                                disabled={messages.length === 0}
                                                onClick={() => {
                                                    if (convos.activeId) void exportConversation(convos.activeId, "markdown");
                                                    setMenuOpen(false);
                                                }}
                                            >
                                                Export markdown
                                            </button>
                                            <button
                                                type="button"
                                                disabled={messages.length === 0}
                                                onClick={() => {
                                                    if (convos.activeId) void exportConversation(convos.activeId, "ipynb");
                                                    setMenuOpen(false);
                                                }}
                                            >
                                                Export notebook
                                            </button>
                                            <button
                                                type="button"
                                                disabled={chat.streaming}
                                                onClick={() => {
                                                    if (convos.activeId) void restartKernel(convos.activeId);
                                                    setMenuOpen(false);
                                                }}
                                            >
                                                <RotateCcw size={16} />
                                                Restart analysis
                                            </button>
                                        </>
                                    )}
                                    <label className="header-menu__check">
                                        <ShieldCheck size={16} />
                                        <span>Check answers</span>
                                        <input
                                            type="checkbox"
                                            checked={llm.verify}
                                            onChange={(e) => llm.selectVerify(e.target.checked)}
                                            disabled={chat.streaming}
                                        />
                                    </label>
                                </div>
                            )}
                        </div>
                    </div>
                </header>

                <div className="main__stage">
                    {showWelcome ? (
                        <>
                            <WelcomeScreen />
                            {error && (
                                <div className="main__error">
                                    <span>{error}</span>
                                </div>
                            )}
                            <div className="main__input-area">{composer}</div>
                        </>
                    ) : (
                        <>
                            {chat.statusLine && <div className="main__status">{chat.statusLine}</div>}
                            <div className="main__body">
                                <div className="main__thread-col">
                                    <div className="main__thread-area">
                                        <MessageList
                                            messages={messages}
                                            conversationId={convos.activeId}
                                            streaming={chat.streaming}
                                            onPickOption={(text) => void chat.sendMessage(text)}
                                            onOpenWork={() => setWorkOpen(true)}
                                        />
                                    </div>
                                    {error && (
                                        <div className="main__error">
                                            <span>{error}</span>
                                        </div>
                                    )}
                                    <div className="main__input-area">{composer}</div>
                                </div>
                                <AnalysisPanel
                                    messages={messages}
                                    conversationId={convos.activeId}
                                    open={workOpen}
                                    onToggle={() => setWorkOpen((v) => !v)}
                                />
                                {filesOpen && convos.activeId && (
                                    <FilesPanel
                                        conversationId={convos.activeId}
                                        files={files.files}
                                        onDelete={(path) => void files.remove(path)}
                                        onClose={() => setFilesOpen(false)}
                                        onUpload={(list) => {
                                            if (!convos.activeId) return;
                                            void uploadFiles(convos.activeId, Array.from(list)).then(() => files.refresh());
                                        }}
                                    />
                                )}
                            </div>
                        </>
                    )}
                </div>
            </main>
        </div>
    );
}
