import { useMemo, useRef, useState } from "react";
import { ChevronRight, FolderOpen, PanelLeft, Pause, Search, SquarePen, X } from "lucide-react";
import { MemoTaskItem } from "@/TaskListItem.js";
import { Button } from "@/components/ui/button.js";
import { Input } from "@/components/ui/input.js";
import type { Conversation } from "../../types/protocol";
import "./sidebar.css";

type ChatGroup = { key: string; label: string; chats: Conversation[] };

function groupChats(conversations: Conversation[], searching: boolean, pausedConversationId?: string | null): ChatGroup[] {
  const sorted = [...conversations].sort((a, b) => b.updated_at - a.updated_at);
  const isPaused = (chat: Conversation) => chat.running && chat.id === pausedConversationId;
  if (searching) return [
    { key: "paused", label: "Paused", chats: sorted.filter(isPaused) },
    { key: "search", label: "Search results", chats: sorted.filter((chat) => !isPaused(chat)) },
  ].filter((group) => group.chats.length);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const yesterday = new Date(today); yesterday.setDate(yesterday.getDate() - 1);
  const week = new Date(today); week.setDate(week.getDate() - 7);
  const groups: ChatGroup[] = [
    { key: "running", label: "Running", chats: [] },
    { key: "paused", label: "Paused", chats: [] },
    { key: "today", label: "Today", chats: [] },
    { key: "yesterday", label: "Yesterday", chats: [] },
    { key: "week", label: "Previous 7 days", chats: [] },
    { key: "older", label: "Earlier", chats: [] },
  ];
  for (const chat of sorted) {
    const updated = chat.updated_at * 1000;
    const group = isPaused(chat) ? 1 : chat.running ? 0 : updated >= +today ? 2 : updated >= +yesterday ? 3 : updated >= +week ? 4 : 5;
    groups[group].chats.push(chat);
  }
  return groups.filter((group) => group.chats.length);
}

export function ChatSidebar({ conversations, activeId, pausedConversationId, loading, onNewChat, onSelect, onRename, onDelete, onFiles, onClose }: {
  conversations: Conversation[]; activeId: string | null; pausedConversationId?: string | null; loading: boolean; onNewChat: () => void;
  onSelect: (id: string) => void; onRename: (id: string, title: string) => Promise<void>;
  onDelete: (id: string) => Promise<void>; onFiles: () => void; onClose: () => void;
}) {
  const [searching, setSearching] = useState(false);
  const [query, setQuery] = useState("");
  const searchButtonRef = useRef<HTMLButtonElement>(null);
  const visible = useMemo(() => conversations.filter((chat) => chat.title.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())), [conversations, query]);
  const groups = groupChats(visible, Boolean(query.trim()), pausedConversationId);
  const closeSearch = () => { setSearching(false); setQuery(""); searchButtonRef.current?.focus(); };
  return <div className="mo-navigation">
    <div className="mo-navigation-header">
      <button type="button" className="mo-navigation-brand" onClick={onNewChat} aria-label="MatOpt home">
        <span className="mo-navigation-mark" aria-hidden="true"><svg viewBox="0 0 32 32" fill="none"><path d="M24 8H9l8 8-8 8h15" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" /></svg></span>
        <span>MatOpt</span>
      </button>
      <Button type="button" variant="ghost" size="icon-md" className="mo-navigation-collapse" aria-label="Collapse sidebar" title="Collapse sidebar" onClick={onClose}><PanelLeft size={18} aria-hidden="true" /></Button>
    </div>
    <nav className="mo-navigation-actions" aria-label="Workspace">
      <Button variant="ghost" className="mo-navigation-new" onClick={onNewChat}><SquarePen size={18} aria-hidden="true" /><span>New chat</span><kbd className="mo-navigation-shortcut" aria-hidden="true">⌘ N</kbd></Button>
      <Button ref={searchButtonRef} variant="ghost" className="mo-navigation-search-toggle" onClick={() => { setSearching((open) => !open); setQuery(""); }} aria-expanded={searching} aria-controls={searching ? "mo-chat-search" : undefined}><Search size={18} aria-hidden="true" /><span>Search chats</span></Button>
      {searching && <div className="mo-navigation-search" id="mo-chat-search">
        <Input autoFocus placeholder="Find a conversation…" aria-label="Search chat history" value={query} onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => { if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); closeSearch(); } }} />
        <button type="button" aria-label="Close chat search" onClick={closeSearch}><X size={14} aria-hidden="true" /></button>
      </div>}
    </nav>
    <div className="mo-navigation-history" aria-label="Chat history" aria-busy={loading}>
      <ul className="mo-navigation-chat-list">{groups.flatMap((group) => [
        <li key={`heading:${group.key}`} className="mo-navigation-group-heading" data-chat-group={group.key} role="presentation">
          <h2>{group.key === "running" && <span className="mo-navigation-live-dot" aria-hidden="true" />}{group.key === "paused" && <Pause className="mo-navigation-paused-icon" size={12} aria-hidden="true" />}<span>{group.label}</span>{group.key === "running" && <span className="mo-navigation-group-count">{group.chats.length}</span>}</h2>
        </li>,
        // Keep every task under one keyed parent so grouping changes preserve
        // its open action menu or rename draft while a run settles.
        ...group.chats.map((chat) => <MemoTaskItem key={chat.id} task={chat.id === pausedConversationId ? { ...chat, running: false } : chat} isActive={activeId === chat.id} onSelect={() => onSelect(chat.id)} onRename={(title) => onRename(chat.id, title)} onDelete={() => onDelete(chat.id)} />),
      ])}</ul>
      {!visible.length && <div className="mo-navigation-empty"><p>{loading ? "Loading chats…" : query.trim() ? "No matching chats" : "A fresh place to work"}</p><span>{loading ? "" : query.trim() ? "Try another title." : "Your conversations will appear here."}</span></div>}
    </div>
    <div className="mo-navigation-footer">
      <button type="button" onClick={onFiles} className="mo-navigation-files" aria-label="Workspace files">
        <span className="mo-navigation-files-icon"><FolderOpen size={18} aria-hidden="true" /></span>
        <span><strong>Workspace</strong><small>Files and results</small></span><ChevronRight size={16} aria-hidden="true" />
      </button>
    </div>
  </div>;
}
