/**
 * Claude-style model + effort menu, used in the composer toolbar.
 */

import React, { useEffect, useRef, useState } from "react";
import { Check, ChevronRight } from "lucide-react";
import type { ProviderCatalog, ProviderId, ReasoningLevel, ReasoningStop } from "../types/protocol";
import "./ModelPicker.css";

interface Props {
    catalog: ProviderCatalog | null;
    provider: ProviderId | null;
    model: string | null;
    label: string;
    disabled?: boolean;
    menuUp?: boolean;
    levels: ReasoningStop[];
    reasoning: ReasoningLevel;
    onSelect: (provider: ProviderId, model: string) => void;
    onReasoningChange: (level: ReasoningLevel) => void;
}

export default function ModelPicker({
    catalog,
    provider,
    model,
    label,
    disabled = false,
    menuUp = false,
    levels,
    reasoning,
    onSelect,
    onReasoningChange,
}: Props) {
    const [open, setOpen] = useState(false);
    const [panel, setPanel] = useState<"models" | "effort">("models");
    const rootRef = useRef<HTMLDivElement>(null);
    const currentEffort = levels.find((l) => l.id === reasoning) ?? levels[0];

    useEffect(() => {
        if (!open) return;
        const onClick = (event: MouseEvent) => {
            if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
        };
        document.addEventListener("mousedown", onClick);
        return () => document.removeEventListener("mousedown", onClick);
    }, [open]);

    useEffect(() => {
        if (!open) setPanel("models");
    }, [open]);

    return (
        <div className={`model-picker ${menuUp ? "model-picker--up" : ""}`} ref={rootRef}>
            <button
                className="composer-pill"
                type="button"
                disabled={disabled || !catalog}
                onClick={() => setOpen((value) => !value)}
                aria-haspopup="listbox"
                aria-expanded={open}
            >
                <span>{label}</span>
            </button>
            {open && catalog && (
                <div className="model-picker__menus">
                    {panel === "effort" && levels.length > 0 && (
                        <div className="model-picker__menu model-picker__menu--effort" role="listbox">
                            <p className="model-picker__effort-copy">
                                Higher effort means more thorough responses, but takes longer.
                            </p>
                            {levels.map((level) => {
                                const selected = level.id === reasoning;
                                return (
                                    <button
                                        key={level.id}
                                        type="button"
                                        role="option"
                                        aria-selected={selected}
                                        className={`model-picker__option${selected ? " model-picker__option--active" : ""}`}
                                        onClick={() => {
                                            onReasoningChange(level.id);
                                            setPanel("models");
                                        }}
                                    >
                                        <span className="model-picker__option-text">
                                            <span>{level.label}</span>
                                        </span>
                                        {selected && <Check size={16} className="model-picker__check" />}
                                    </button>
                                );
                            })}
                        </div>
                    )}
                    <div className="model-picker__menu" role="listbox">
                        {catalog.providers.map((group) => (
                            <div key={group.id} className="model-picker__group">
                                <div className="model-picker__group-label">
                                    {group.label}
                                    {!group.configured && <span className="model-picker__hint">Add {group.env_key}</span>}
                                </div>
                                {group.models.map((option) => {
                                    const selected = group.id === provider && option.id === model;
                                    return (
                                        <button
                                            key={option.id}
                                            type="button"
                                            role="option"
                                            aria-selected={selected}
                                            className={`model-picker__option${selected ? " model-picker__option--active" : ""}`}
                                            disabled={!group.configured}
                                            onClick={() => {
                                                onSelect(group.id, option.id);
                                                setOpen(false);
                                            }}
                                        >
                                            <span className="model-picker__option-text">
                                                <span>{option.label}</span>
                                                {option.description && (
                                                    <span className="model-picker__desc">{option.description}</span>
                                                )}
                                            </span>
                                            {selected && <Check size={16} className="model-picker__check" />}
                                        </button>
                                    );
                                })}
                            </div>
                        ))}
                        {levels.length > 0 && (
                            <button
                                type="button"
                                className="model-picker__effort-row"
                                onClick={() => setPanel((p) => (p === "effort" ? "models" : "effort"))}
                            >
                                <span>Effort</span>
                                <span className="model-picker__effort-value">
                                    {currentEffort?.label ?? "Medium"}
                                    <ChevronRight size={16} />
                                </span>
                            </button>
                        )}
                    </div>
                </div>
            )}
        </div>
    );
}
