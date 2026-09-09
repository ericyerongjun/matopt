/**
 * Slash-command palette for skills and tools — Cursor / Claude / Codex style.
 * Opened when the composer is `/` plus an optional filter token.
 */

import React, { useEffect, useMemo, useRef } from "react";
import { commandToken, skillIntro, sourceLabel, type CommandInfo } from "../services/skills";
import "./SkillCommandMenu.css";

interface Props {
    commands: CommandInfo[];
    query: string;
    activeIndex: number;
    onActiveIndexChange: (index: number) => void;
    onSelect: (command: CommandInfo) => void;
    onClose: () => void;
}

export function filterCommands(commands: CommandInfo[], query: string): CommandInfo[] {
    const q = query.trim().toLowerCase().replace(/_/g, "-");
    if (!q) return commands;
    const scored = commands.map((command) => {
        const name = command.name.toLowerCase();
        const hyphen = name.replace(/_/g, "-");
        const hay = `${command.name} ${command.description}`.toLowerCase();
        let score = 0;
        if (name === q || hyphen === q) score = 3;
        else if (name.startsWith(q) || hyphen.startsWith(q)) score = 2;
        else if (name.includes(q) || hyphen.includes(q) || hay.includes(q)) score = 1;
        return { command, score };
    });
    return scored
        .filter((row) => row.score > 0)
        .sort((a, b) => b.score - a.score || a.command.name.localeCompare(b.command.name))
        .map((row) => row.command);
}

/** @deprecated use filterCommands */
export function filterSkills(commands: CommandInfo[], query: string): CommandInfo[] {
    return filterCommands(commands, query);
}

function groupCommands(commands: CommandInfo[]): { key: string; label: string; items: CommandInfo[] }[] {
    const tools = commands.filter((c) => c.kind === "tool");
    const skills = commands.filter((c) => c.kind === "skill");
    const order = ["matopt", "lambda", "anthropic"];
    const buckets = new Map<string, CommandInfo[]>();
    for (const skill of skills) {
        const key = skill.source || "local";
        const list = buckets.get(key) ?? [];
        list.push(skill);
        buckets.set(key, list);
    }
    const groups: { key: string; label: string; items: CommandInfo[] }[] = [];
    if (tools.length) groups.push({ key: "tools", label: "Tools", items: tools });
    const keys = [
        ...order.filter((k) => buckets.has(k)),
        ...[...buckets.keys()].filter((k) => !order.includes(k)).sort(),
    ];
    for (const source of keys) {
        groups.push({
            key: source,
            label: sourceLabel(source, "skill"),
            items: buckets.get(source) ?? [],
        });
    }
    return groups;
}

export default function SkillCommandMenu({
    commands,
    query,
    activeIndex,
    onActiveIndexChange,
    onSelect,
    onClose,
}: Props) {
    const listRef = useRef<HTMLDivElement>(null);
    const matches = useMemo(() => filterCommands(commands, query), [commands, query]);
    const groups = useMemo(() => groupCommands(matches), [matches]);

    useEffect(() => {
        const node = listRef.current?.querySelector<HTMLElement>(`[data-skill-index="${activeIndex}"]`);
        node?.scrollIntoView({ block: "nearest" });
    }, [activeIndex]);

    useEffect(() => {
        const onDoc = (event: MouseEvent) => {
            const target = event.target as HTMLElement | null;
            if (listRef.current?.contains(target)) return;
            if (target?.closest(".chat-input")) return;
            onClose();
        };
        document.addEventListener("mousedown", onDoc);
        return () => document.removeEventListener("mousedown", onDoc);
    }, [onClose]);

    let cursor = 0;

    return (
        <div className="skill-menu" ref={listRef} role="listbox" aria-label="Skills and tools">
            <div className="skill-menu__header">
                <span>Skills & tools</span>
                <span className="skill-menu__hint">Type to filter · Enter to run</span>
            </div>
            {matches.length === 0 ? (
                <div className="skill-menu__empty">No commands match “{query}”.</div>
            ) : (
                groups.map((group) => (
                    <div key={group.key} className="skill-menu__group">
                        <div className="skill-menu__group-label">{group.label}</div>
                        {group.items.map((command) => {
                            const index = cursor++;
                            const active = index === activeIndex;
                            return (
                                <button
                                    key={`${command.kind}:${command.name}`}
                                    type="button"
                                    role="option"
                                    aria-selected={active}
                                    data-skill-index={index}
                                    className={`skill-menu__item ${active ? "skill-menu__item--active" : ""}`}
                                    onMouseEnter={() => onActiveIndexChange(index)}
                                    onClick={() => onSelect(command)}
                                >
                                    <span className="skill-menu__name">
                                        <span className={`skill-menu__kind skill-menu__kind--${command.kind}`}>
                                            {command.kind === "tool" ? "tool" : "skill"}
                                        </span>
                                        {commandToken(command.name)}
                                    </span>
                                    <span className="skill-menu__intro">{skillIntro(command.description)}</span>
                                </button>
                            );
                        })}
                    </div>
                ))
            )}
        </div>
    );
}
