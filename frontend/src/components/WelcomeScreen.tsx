/**
 * WelcomeScreen — shown when no conversation is active or the chat is empty.
 * Displays logo, greeting, and suggestion chips (ChatGPT-style).
 */

import React, { useEffect, useMemo, useState } from "react";
import { Calculator, BookOpen, BrainCircuit, ChartLine } from "lucide-react";
import { fetchSuggestions } from "../services/suggestions";
import InlineMathText from "./InlineMathText";

interface Props {
    onSuggestion: (text: string) => void;
}

export default function WelcomeScreen({ onSuggestion }: Props) {
    const [iconOk, setIconOk] = useState(true);
    const [loading, setLoading] = useState(true);
    const [suggestions, setSuggestions] = useState<string[]>([]);
    const [error, setError] = useState<string | null>(null);

    const iconPool = useMemo(
        () => [Calculator, BookOpen, BrainCircuit, ChartLine],
        []
    );

    useEffect(() => {
        let active = true;
        setLoading(true);
        setError(null);
        fetchSuggestions(4)
            .then((resp) => {
                if (!active) return;
                const items = resp.suggestions
                    .map((s) => s.trim())
                    .filter(Boolean);
                setSuggestions(items);
            })
            .catch(() => {
                if (!active) return;
                setSuggestions([]);
                setError("Unable to load suggestions. Please try again.");
            })
            .finally(() => {
                if (!active) return;
                setLoading(false);
            });

        return () => {
            active = false;
        };
    }, []);

    const chips = suggestions.map((text, i) => {
        const Icon = iconPool[i % iconPool.length];
        return { icon: <Icon size={20} />, text };
    });

    return (
        <div className="welcome">
            <div className="welcome__logo" aria-hidden="true">
                {iconOk ? (
                    <img
                        className="welcome__logo-img"
                        src="/assets/interaction-icon.png"
                        alt=""
                        onError={() => setIconOk(false)}
                    />
                ) : (
                    <span className="welcome__logo-icon">∑</span>
                )}
            </div>
            <h1 className="welcome__title">What can I help with?</h1>
            <div className="welcome__suggestions">
                {chips.map((s, i) => (
                    <button
                        key={i}
                        className="welcome__chip"
                        onClick={() => onSuggestion(s.text)}
                    >
                        {s.icon}
                        <InlineMathText text={s.text} />
                    </button>
                ))}
            </div>
            {loading && (
                <div className="welcome__loading">Loading suggestions...</div>
            )}
            {!loading && error && (
                <div className="welcome__loading">{error}</div>
            )}
        </div>
    );
}
