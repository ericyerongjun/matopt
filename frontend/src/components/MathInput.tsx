/**
 * ChatGPT-style composer: plus, slash skills/tools, field, model/effort, circular send.
 */

import React, { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ArrowUp, Globe, Plus, Slash, Square, X } from "lucide-react";
import SkillCommandMenu, { filterCommands } from "./SkillCommandMenu";
import {
    commandToken,
    parseSlashCommands,
    type CommandInfo,
    type SkillInfo,
} from "../services/skills";
import "./MathInput.css";
import "./SkillCommandMenu.css";

interface Props {
    onSubmit: (text: string, files: File[], skills?: string[], tools?: string[]) => void;
    onStop?: () => void;
    disabled?: boolean;
    loading?: boolean;
    placeholder?: string;
    toolbar?: ReactNode;
    showDisclaimer?: boolean;
    commands?: CommandInfo[];
    /** @deprecated pass `commands` */
    skills?: SkillInfo[];
    externalSkill?: SkillInfo | null;
    externalKey?: number;
    onExternalSkillConsumed?: () => void;
    webSearch?: boolean;
    onWebSearchChange?: (on: boolean) => void;
}

const SLASH_TOKEN = /^\/([a-z0-9][-_a-z0-9]*)?$/i;

function asCommands(commands: CommandInfo[] | undefined, skills: SkillInfo[] | undefined): CommandInfo[] {
    if (commands && commands.length) return commands;
    return (skills || []).map((skill) => ({
        kind: "skill" as const,
        name: skill.name,
        description: skill.description,
        source: skill.source,
    }));
}

export default function MathInput({
    onSubmit,
    onStop,
    disabled = false,
    loading = false,
    placeholder = loading ? "Steer this turn…" : "Ask anything or type / for a skill or tool",
    toolbar,
    showDisclaimer = true,
    commands,
    skills = [],
    externalSkill = null,
    externalKey = 0,
    onExternalSkillConsumed,
    webSearch = false,
    onWebSearchChange,
}: Props) {
    const catalog = useMemo(() => asCommands(commands, skills), [commands, skills]);
    const [value, setValue] = useState("");
    const [files, setFiles] = useState<File[]>([]);
    const [pinned, setPinned] = useState<CommandInfo[]>([]);
    const [dragOver, setDragOver] = useState(false);
    const [activeIndex, setActiveIndex] = useState(0);
    const textareaRef = useRef<HTMLTextAreaElement>(null);
    const fileRef = useRef<HTMLInputElement>(null);

    const slashMatch = value.match(SLASH_TOKEN);
    const slashOpen = Boolean(slashMatch) && catalog.length > 0 && !disabled;
    const slashQuery = slashMatch ? slashMatch[1] || "" : "";
    const matches = useMemo(() => filterCommands(catalog, slashQuery), [catalog, slashQuery]);

    useEffect(() => {
        setActiveIndex(0);
    }, [slashQuery, slashOpen]);

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

    const pinCommand = useCallback((command: CommandInfo) => {
        setPinned((prev) => (prev.some((c) => c.kind === command.kind && c.name === command.name) ? prev : [...prev, command]));
        setValue("");
        requestAnimationFrame(() => textareaRef.current?.focus());
    }, []);

    const consumeRef = useRef(onExternalSkillConsumed);
    consumeRef.current = onExternalSkillConsumed;

    useEffect(() => {
        if (!externalSkill) return;
        pinCommand({
            kind: "skill",
            name: externalSkill.name,
            description: externalSkill.description,
            source: externalSkill.source,
        });
        consumeRef.current?.();
    }, [externalSkill, externalKey, pinCommand]);

    const submitWith = useCallback(
        (raw: string, extra: CommandInfo[] = []) => {
            if (disabled) return;
            const known = {
                skills: catalog.filter((c) => c.kind === "skill").map((c) => c.name),
                tools: catalog.filter((c) => c.kind === "tool").map((c) => c.name),
            };
            const parsed = parseSlashCommands(raw, known);
            const skillNames = [...pinned.filter((c) => c.kind === "skill").map((c) => c.name)];
            const toolNames = [...pinned.filter((c) => c.kind === "tool").map((c) => c.name)];
            for (const command of extra) {
                if (command.kind === "skill" && !skillNames.includes(command.name)) skillNames.push(command.name);
                if (command.kind === "tool" && !toolNames.includes(command.name)) toolNames.push(command.name);
            }
            for (const n of parsed.skills) if (!skillNames.includes(n)) skillNames.push(n);
            for (const n of parsed.tools) if (!toolNames.includes(n)) toolNames.push(n);
            const rest = parsed.rest.trim();
            const prefix = [
                ...skillNames.map((n) => commandToken(n)),
                ...toolNames.map((n) => commandToken(n)),
            ].join(" ");
            const message = [prefix, rest].filter(Boolean).join(" ");
            if (!message) return;
            onSubmit(message, files, skillNames, toolNames);
            setValue("");
            setFiles([]);
            setPinned([]);
            if (textareaRef.current) textareaRef.current.style.height = "auto";
        },
        [catalog, disabled, files, onSubmit, pinned]
    );

    const handleSubmit = useCallback(() => submitWith(value), [submitWith, value]);

    const canSend = (value.trim().length > 0 || pinned.length > 0) && !disabled;
    const showStop = loading && !canSend;

    const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
        if (slashOpen) {
            if (e.key === "ArrowDown") {
                e.preventDefault();
                setActiveIndex((i) => Math.min(i + 1, Math.max(matches.length - 1, 0)));
                return;
            }
            if (e.key === "ArrowUp") {
                e.preventDefault();
                setActiveIndex((i) => Math.max(i - 1, 0));
                return;
            }
            if (e.key === "Escape") {
                e.preventDefault();
                setValue("");
                return;
            }
            if (e.key === "Tab") {
                e.preventDefault();
                const command = matches[activeIndex];
                if (command) pinCommand(command);
                return;
            }
            if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                const command = matches[activeIndex];
                if (!command) {
                    handleSubmit();
                    return;
                }
                const typed = slashQuery.toLowerCase().replace(/_/g, "-");
                const name = command.name.toLowerCase().replace(/_/g, "-");
                if (typed && typed === name) {
                    submitWith(value, [command]);
                    return;
                }
                pinCommand(command);
                return;
            }
        }
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            handleSubmit();
        }
        if (e.key === "Backspace" && value === "" && pinned.length) {
            setPinned((prev) => prev.slice(0, -1));
        }
    };

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
                {slashOpen && (
                    <SkillCommandMenu
                        commands={catalog}
                        query={slashQuery}
                        activeIndex={activeIndex}
                        onActiveIndexChange={setActiveIndex}
                        onSelect={(command) => {
                            const typed = slashQuery.toLowerCase().replace(/_/g, "-");
                            const name = command.name.toLowerCase().replace(/_/g, "-");
                            if (typed && typed === name && pinned.length === 0) {
                                submitWith(`/${command.name}`, [command]);
                                return;
                            }
                            pinCommand(command);
                        }}
                        onClose={() => setValue((v) => (SLASH_TOKEN.test(v) ? "" : v))}
                    />
                )}
                <div className="chat-input__main">
                    {(files.length > 0 || pinned.length > 0) && (
                        <div className="chat-input__chips">
                            {pinned.map((command) => (
                                <span
                                    key={`${command.kind}:${command.name}`}
                                    className={`skill-chip ${command.kind === "tool" ? "skill-chip--tool" : ""}`}
                                >
                                    {commandToken(command.name)}
                                    <button
                                        type="button"
                                        onClick={() =>
                                            setPinned((prev) =>
                                                prev.filter((c) => !(c.kind === command.kind && c.name === command.name))
                                            )
                                        }
                                        aria-label={`Remove ${command.name}`}
                                    >
                                        <X size={12} />
                                    </button>
                                </span>
                            ))}
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
                        onKeyDown={onKeyDown}
                        placeholder={loading ? "Steer this turn…" : placeholder}
                        disabled={disabled}
                    />
                </div>
                <div className="chat-input__bar">
                    <div className="chat-input__bar-left">
                        <button
                            className="chat-input__attach"
                            onClick={() => fileRef.current?.click()}
                            disabled={disabled || loading}
                            title="Add photos and files"
                            type="button"
                        >
                            <Plus size={20} />
                        </button>
                        <button
                            className="chat-input__attach"
                            onClick={() => {
                                setValue("/");
                                textareaRef.current?.focus();
                            }}
                            disabled={disabled}
                            title="Skills and tools"
                            type="button"
                            aria-label="Browse skills and tools"
                            aria-expanded={slashOpen}
                        >
                            <Slash size={18} />
                        </button>
                        <button
                            className={`chat-input__attach chat-input__search ${webSearch ? "chat-input__attach--on" : ""}`}
                            onClick={() => onWebSearchChange?.(!webSearch)}
                            disabled={disabled}
                            title={webSearch ? "Web Search on" : "Web Search"}
                            type="button"
                            aria-pressed={webSearch}
                        >
                            <Globe size={16} />
                            <span>Search</span>
                        </button>
                    </div>
                    <div className="chat-input__bar-right">
                        {toolbar}
                        <button
                            className={`chat-input__send ${canSend || loading ? "chat-input__send--active" : ""}`}
                            onClick={showStop ? onStop : handleSubmit}
                            disabled={showStop ? !onStop : !canSend}
                            title={showStop ? "Stop" : loading ? "Steer" : "Send"}
                            type="button"
                        >
                            {showStop ? <Square size={12} fill="currentColor" /> : <ArrowUp size={18} strokeWidth={2.5} />}
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
