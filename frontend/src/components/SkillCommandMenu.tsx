/**
 * Slash-command palette for skills — Cursor / Claude / ChatGPT style.
 * Opened when the composer is `/` plus an optional filter token.
 */

import React, { useEffect, useMemo, useRef } from "react";
import { sourceLabel, skillIntro, type SkillInfo } from "../services/skills";
import "./SkillCommandMenu.css";

interface Props {
    skills: SkillInfo[];
    query: string;
    activeIndex: number;
    onActiveIndexChange: (index: number) => void;
    onSelect: (skill: SkillInfo) => void;
    onClose: () => void;
}

export function filterSkills(skills: SkillInfo[], query: string): SkillInfo[] {
    const q = query.trim().toLowerCase();
    if (!q) return skills;
    const scored = skills.map((skill) => {
        const name = skill.name.toLowerCase();
        const hay = `${skill.name} ${skill.description}`.toLowerCase();
        let score = 0;
        if (name === q) score = 3;
        else if (name.startsWith(q)) score = 2;
        else if (name.includes(q) || hay.includes(q)) score = 1;
        return { skill, score };
    });
    return scored
        .filter((row) => row.score > 0)
        .sort((a, b) => b.score - a.score || a.skill.name.localeCompare(b.skill.name))
        .map((row) => row.skill);
}

function groupSkills(skills: SkillInfo[]): { source: string; items: SkillInfo[] }[] {
    const order = ["matopt", "anthropic"];
    const buckets = new Map<string, SkillInfo[]>();
    for (const skill of skills) {
        const key = skill.source || "local";
        const list = buckets.get(key) ?? [];
        list.push(skill);
        buckets.set(key, list);
    }
    const keys = [
        ...order.filter((k) => buckets.has(k)),
        ...[...buckets.keys()].filter((k) => !order.includes(k)).sort(),
    ];
    return keys.map((source) => ({ source, items: buckets.get(source) ?? [] }));
}

export default function SkillCommandMenu({
    skills,
    query,
    activeIndex,
    onActiveIndexChange,
    onSelect,
    onClose,
}: Props) {
    const listRef = useRef<HTMLDivElement>(null);
    const matches = useMemo(() => filterSkills(skills, query), [skills, query]);
    const groups = useMemo(() => groupSkills(matches), [matches]);
    const flat = matches;

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
        <div className="skill-menu" ref={listRef} role="listbox" aria-label="Skills">
            <div className="skill-menu__header">
                <span>Skills</span>
                <span className="skill-menu__hint">Type to filter · Enter to use</span>
            </div>
            {flat.length === 0 ? (
                <div className="skill-menu__empty">No skills match “{query}”.</div>
            ) : (
                groups.map((group) => (
                    <div key={group.source} className="skill-menu__group">
                        <div className="skill-menu__group-label">{sourceLabel(group.source)}</div>
                        {group.items.map((skill) => {
                            const index = cursor++;
                            const active = index === activeIndex;
                            return (
                                <button
                                    key={skill.name}
                                    type="button"
                                    role="option"
                                    aria-selected={active}
                                    data-skill-index={index}
                                    className={`skill-menu__item ${active ? "skill-menu__item--active" : ""}`}
                                    onMouseEnter={() => onActiveIndexChange(index)}
                                    onClick={() => onSelect(skill)}
                                >
                                    <span className="skill-menu__name">/{skill.name}</span>
                                    <span className="skill-menu__intro">{skillIntro(skill.description)}</span>
                                </button>
                            );
                        })}
                    </div>
                ))
            )}
        </div>
    );
}
