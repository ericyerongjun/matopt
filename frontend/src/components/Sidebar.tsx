/**
 * Sidebar — ChatGPT-style dark sidebar with conversation history.
 */

import React, { useState } from "react";
import {
    Plus,
    MessageSquare,
    Trash2,
    PanelLeftClose,
    PanelLeft,
    Settings,
    FileText,
    Download,
} from "lucide-react";
import type { Conversation } from "../types/conversation";
import type { ExportFormat } from "../types/export";

interface Props {
    conversations: Conversation[];
    activeId: string | null;
    onNewChat: () => void;
    onSelect: (id: string) => void;
    onDelete: (id: string) => void;
    onExport?: (format: ExportFormat) => void;
    collapsed: boolean;
    onToggleCollapse: () => void;
}

export default function Sidebar({
    conversations,
    activeId,
    onNewChat,
    onSelect,
    onDelete,
    collapsed,
    onToggleCollapse,
}: Props) {
    // Group conversations by date
    const today = new Date();
    const todayStart = new Date(
        today.getFullYear(),
        today.getMonth(),
        today.getDate()
    ).getTime();
    const weekAgo = todayStart - 7 * 86400000;
    const monthAgo = todayStart - 30 * 86400000;

    const groups: { label: string; convs: Conversation[] }[] = [];
    const todayConvs = conversations.filter(
        (c) => c.updatedAt >= todayStart
    );
    const weekConvs = conversations.filter(
        (c) => c.updatedAt >= weekAgo && c.updatedAt < todayStart
    );
    const monthConvs = conversations.filter(
        (c) => c.updatedAt >= monthAgo && c.updatedAt < weekAgo
    );
    const olderConvs = conversations.filter(
        (c) => c.updatedAt < monthAgo
    );

    if (todayConvs.length) groups.push({ label: "Today", convs: todayConvs });
    if (weekConvs.length)
        groups.push({ label: "Previous 7 Days", convs: weekConvs });
    if (monthConvs.length)
        groups.push({ label: "Previous 30 Days", convs: monthConvs });
    if (olderConvs.length) groups.push({ label: "Older", convs: olderConvs });

    return (
        <nav className={`sidebar ${collapsed ? "sidebar--collapsed" : ""}`}>
            {/* Top section */}
            <div className="sidebar__top">
                <button
                    className="sidebar__toggle"
                    onClick={onToggleCollapse}
                    title={collapsed ? "Open sidebar" : "Close sidebar"}
                >
                    {collapsed ? (
                        <PanelLeft size={20} />
                    ) : (
                        <PanelLeftClose size={20} />
                    )}
                </button>

                {!collapsed && (
                    <button
                        className="sidebar__new-chat"
                        onClick={onNewChat}
                        title="New chat"
                    >
                        <Plus size={18} />
                        <span>New chat</span>
                    </button>
                )}
                {collapsed && (
                    <button
                        className="sidebar__new-chat sidebar__new-chat--icon"
                        onClick={onNewChat}
                        title="New chat"
                    >
                        <Plus size={20} />
                    </button>
                )}
            </div>

            {/* Conversation list */}
            {!collapsed && (
                <div className="sidebar__conversations">
                    {groups.length === 0 && (
                        <div className="sidebar__empty">
                            No conversations yet
                        </div>
                    )}
                    {groups.map((group) => (
                        <div key={group.label} className="sidebar__group">
                            <div className="sidebar__group-label">
                                {group.label}
                            </div>
                            {group.convs.map((conv) => (
                                <div
                                    key={conv.id}
                                    className={`sidebar__item ${conv.id === activeId
                                            ? "sidebar__item--active"
                                            : ""
                                        }`}
                                    onClick={() => onSelect(conv.id)}
                                >
                                    <MessageSquare size={16} />
                                    <span className="sidebar__item-title">
                                        {conv.title}
                                    </span>
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

            {/* Bottom section */}
            {!collapsed && (
                <div className="sidebar__bottom">
                    <div className="sidebar__user">
                        <div className="sidebar__avatar">M</div>
                        <span>MatOpt User</span>
                    </div>
                </div>
            )}
        </nav>
    );
}
