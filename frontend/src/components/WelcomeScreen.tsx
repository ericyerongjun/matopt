/**
 * WelcomeScreen — shown when no conversation is active or the chat is empty.
 * Displays logo, greeting, and suggestion chips (ChatGPT-style).
 */

import React from "react";
import { Calculator, BookOpen, BrainCircuit, ChartLine } from "lucide-react";

interface Props {
    onSuggestion: (text: string) => void;
}

const SUGGESTIONS = [
    {
        icon: <Calculator size={20} />,
        text: "Solve \\( x^2 - 5x + 6 = 0 \\)",
    },
    {
        icon: <BookOpen size={20} />,
        text: "Explain the chain rule in calculus",
    },
    {
        icon: <BrainCircuit size={20} />,
        text: "Find eigenvalues of [[2,1],[1,3]]",
    },
    {
        icon: <ChartLine size={20} />,
        text: "Plot \\( \\sin(x) \\) and \\( \\cos(x) \\) from -2π to 2π",
    },
];

export default function WelcomeScreen({ onSuggestion }: Props) {
    return (
        <div className="welcome">
            <div className="welcome__logo">
                <span className="welcome__logo-icon">∑</span>
            </div>
            <h1 className="welcome__title">What can I help with?</h1>
            <div className="welcome__suggestions">
                {SUGGESTIONS.map((s, i) => (
                    <button
                        key={i}
                        className="welcome__chip"
                        onClick={() => onSuggestion(s.text)}
                    >
                        {s.icon}
                        <span>{s.text}</span>
                    </button>
                ))}
            </div>
        </div>
    );
}
