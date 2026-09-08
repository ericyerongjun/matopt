/**
 * Sidebar — server conversations grouped by date, with search, rename, delete.
 */

import React, { useEffect, useMemo, useRef, useState } from "react";
import {
    MessageSquare,
    PanelLeft,
    PanelLeftClose,
    Plus,
    Search,
    Trash2,
    X,
} from "lucide-react";
import type { Conversation } from "../types/protocol";
import "./Sidebar.css";

interface Props {
    conversations: Conversation[];
    activeId: string | null;
    onNewChat: () => void;
    onSelect: (id: string) => void;
    onDelete: (id: string) => void;
    onRename: (id: string, title: string) => void;
    collapsed: boolean;
    onToggleCollapse: () => void;
}

export default function Sidebar({
    conversations,
    activeId,
    onNewChat,
    onSelect,
    onDelete,
    onRename,
    collapsed,
    onToggleCollapse,
}: Props) {
    const [searchOpen, setSearchOpen] = useState(false);
    const [searchQuery, setSearchQuery] = useState("");
    const [editingId, setEditingId] = useState<string | null>(null);
    const [draft, setDraft] = useState("");
    const searchInputRef = useRef<HTMLInputElement>(null);

    useEffect(() => {
        if (searchOpen) searchInputRef.current?.focus();
    }, [searchOpen]);

    const filtered = useMemo(() => {
        if (!searchQuery.trim()) return conversations;
        const q = searchQuery.toLowerCase();
        return conversations.filter((c) => c.title.toLowerCase().includes(q));
    }, [conversations, searchQuery]);

    const today = new Date();
    const todayStart = new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime();
    const weekAgo = todayStart - 7 * 86400000;
    const monthAgo = todayStart - 30 * 86400000;
    const ms = (c: Conversation) => c.updated_at * 1000;

    const groups: { label: string; convs: Conversation[] }[] = [];
    const buckets = [
        { label: "Today", convs: filtered.filter((c) => ms(c) >= todayStart) },
        { label: "Previous 7 Days", convs: filtered.filter((c) => ms(c) >= weekAgo && ms(c) < todayStart) },
        { label: "Previous 30 Days", convs: filtered.filter((c) => ms(c) >= monthAgo && ms(c) < weekAgo) },
        { label: "Older", convs: filtered.filter((c) => ms(c) < monthAgo) },
    ];
    for (const b of buckets) if (b.convs.length) groups.push(b);

    const commitRename = (id: string) => {
        const title = draft.trim();
        setEditingId(null);
        if (title) onRename(id, title);
    };

    return (
        <nav className={`sidebar ${collapsed ? "sidebar--collapsed" : ""}`}>
            <div className="sidebar__top">
                <div className="sidebar__brand-row">
                    {!collapsed && <span className="sidebar__logo">MatOpt</span>}
                    <button className="sidebar__toggle" onClick={onToggleCollapse} title={collapsed ? "Open sidebar" : "Close sidebar"}>
                        {collapsed ? <PanelLeft size={20} /> : <PanelLeftClose size={20} />}
                    </button>
                </div>
                <button className={`sidebar__new-chat ${collapsed ? "sidebar__new-chat--icon" : ""}`} onClick={onNewChat} title="New chat">
                    <Plus size={collapsed ? 20 : 18} />
                    {!collapsed && <span>New chat</span>}
                </button>
            </div>

            {!collapsed && (
                <div className="sidebar__quick">
                    {searchOpen ? (
                        <div className="sidebar__search-bar">
                            <Search size={16} className="sidebar__search-icon" />
                            <input
                                ref={searchInputRef}
                                className="sidebar__search-input"
                                type="text"
                                placeholder="Search chats…"
                                value={searchQuery}
                                onChange={(e) => setSearchQuery(e.target.value)}
                            />
                            <button
                                className="sidebar__search-close"
                                type="button"
                                onClick={() => {
                                    setSearchOpen(false);
                                    setSearchQuery("");
                                }}
                            >
                                <X size={14} />
                            </button>
                        </div>
                    ) : (
                        <button className="sidebar__quick-item" type="button" onClick={() => setSearchOpen(true)}>
                            <Search size={16} />
                            <span>Search chats</span>
                        </button>
                    )}
                </div>
            )}

            {!collapsed && (
                <div className="sidebar__conversations">
                    <div className="sidebar__section-label">Your chats</div>
                    {groups.length === 0 && <div className="sidebar__empty">No conversations yet</div>}
                    {groups.map((group) => (
                        <div key={group.label} className="sidebar__group">
                            <div className="sidebar__group-label">{group.label}</div>
                            {group.convs.map((conv) => (
                                <div
                                    key={conv.id}
                                    role="button"
                                    tabIndex={0}
                                    className={`sidebar__item ${conv.id === activeId ? "sidebar__item--active" : ""}`}
                                    onClick={() => onSelect(conv.id)}
                                    onKeyDown={(e) => {
                                        if (e.key === "Enter" || e.key === " ") {
                                            e.preventDefault();
                                            onSelect(conv.id);
                                        }
                                    }}
                                    onDoubleClick={(e) => {
                                        e.stopPropagation();
                                        setEditingId(conv.id);
                                        setDraft(conv.title);
                                    }}
                                >
                                    <MessageSquare size={16} />
                                    {editingId === conv.id ? (
                                        <input
                                            className="sidebar__rename"
                                            value={draft}
                                            autoFocus
                                            onClick={(e) => e.stopPropagation()}
                                            onChange={(e) => setDraft(e.target.value)}
                                            onBlur={() => commitRename(conv.id)}
                                            onKeyDown={(e) => {
                                                if (e.key === "Enter") commitRename(conv.id);
                                                if (e.key === "Escape") setEditingId(null);
                                            }}
                                        />
                                    ) : (
                                        <span className="sidebar__item-title">
                                            {conv.running && <span className="sidebar__running" />}
                                            {conv.title}
                                        </span>
                                    )}
                                    <button
                                        className="sidebar__item-delete"
                                        onClick={(e) => {
                                            e.stopPropagation();
                                            onDelete(conv.id);
                                        }}
                                        title="Delete"
                                    >
                                        <Trash2 size={14} />
                                    </button>
                                </div>
                            ))}
                        </div>
                    ))}
                </div>
            )}
        </nav>
    );
}
