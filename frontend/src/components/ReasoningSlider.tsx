/**
 * Compact thinking-effort control, in the ChatGPT header style.
 */

import React from "react";
import type { ReasoningLevel, ReasoningStop } from "../types/protocol";
import "./ReasoningSlider.css";

interface Props {
    levels: ReasoningStop[];
    value: ReasoningLevel;
    onChange: (level: ReasoningLevel) => void;
    disabled?: boolean;
}

export default function ReasoningSlider({ levels, value, onChange, disabled = false }: Props) {
    if (!levels.length) return null;
    const current = levels.find((l) => l.id === value) ?? levels[0];

    return (
        <label className="thinking-chip">
            <span>Thinking</span>
            <select
                value={current.id}
                disabled={disabled}
                onChange={(e) => onChange(e.target.value as ReasoningLevel)}
                aria-label="Thinking effort"
            >
                {levels.map((level) => (
                    <option key={level.id} value={level.id}>
                        {level.label}
                    </option>
                ))}
            </select>
        </label>
    );
}
