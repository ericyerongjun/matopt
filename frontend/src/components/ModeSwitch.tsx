/** Auto / Mat / Opt segmented control for the composer toolbar. */
import { useId, useRef, type KeyboardEvent } from "react";
import type { AnswerMode, ChatMode } from "../types/protocol.ts";
import "./mode.css";

export const MODES: Array<{ value: ChatMode; label: string; description: string }> = [
    { value: "auto", label: "Auto", description: "Auto: decide per message" },
    { value: "mat", label: "Mat", description: "Mat: chat and explain — computes and plots, never changes your files" },
    { value: "opt", label: "Opt", description: "Opt: work on your files, folders and code" },
];

export function ModeSwitch({ value, onChange, disabled = false }: { value: ChatMode; onChange: (mode: ChatMode) => void; disabled?: boolean }) {
    const id = useId();
    const refs = useRef<Array<HTMLButtonElement | null>>([]);
    const move = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
        const step = ["ArrowRight", "ArrowDown"].includes(event.key) ? 1 : ["ArrowLeft", "ArrowUp"].includes(event.key) ? -1 : 0;
        const target = event.key === "Home" ? 0 : event.key === "End" ? MODES.length - 1 : step ? (index + step + MODES.length) % MODES.length : -1;
        if (target < 0) return;
        event.preventDefault();
        onChange(MODES[target].value);
        refs.current[target]?.focus();
    };
    return <div className="matopt-mode-switch" role="radiogroup" aria-label="Mode" aria-disabled={disabled || undefined}>
        {MODES.map((mode, index) => {
            const checked = mode.value === value;
            return <button key={mode.value} ref={(node) => { refs.current[index] = node; }} type="button" role="radio" aria-checked={checked}
                aria-describedby={`${id}-${mode.value}`} title={mode.description} disabled={disabled} tabIndex={checked ? 0 : -1}
                onClick={() => onChange(mode.value)} onKeyDown={(event) => move(event, index)}>
                {mode.label}<span id={`${id}-${mode.value}`} hidden>{mode.description}</span>
            </button>;
        })}
    </div>;
}

const NAMES = { mat: "Mat", opt: "Opt" } as const;

/** The mode an answer ran in; the reason is in the tooltip and accessible description. */
export function AnswerModeBadge({ mode }: { mode: AnswerMode }) {
    const id = useId();
    const switched = Boolean(mode.switched && mode.from && mode.from !== mode.mode);
    const label = switched ? `${NAMES[mode.from!]} → ${NAMES[mode.mode]}` : NAMES[mode.mode];
    const detail = [mode.source === "auto" ? "Chosen automatically" : "Chosen by you", switched ? "switched during this answer" : "", mode.reason].filter(Boolean).join(" · ");
    return <span className="matopt-mode-badge" data-mode={mode.mode} data-switched={switched || undefined} title={detail} aria-describedby={`${id}-reason`}>
        {mode.source === "auto" && <span className="matopt-mode-auto">Auto</span>}<span>{label}</span>
        <span id={`${id}-reason`} hidden>{detail}</span>
    </span>;
}
