/**
 * InlineMathText — renders a short text string that may contain
 * inline LaTeX (`$...$` or `\(...\)`) using KaTeX.
 *
 * Unlike MarkdownRenderer (which processes full Markdown), this is
 * a lightweight component intended for suggestion chips, follow-up
 * buttons, and similar UI elements where only inline math is needed.
 */

import React, { memo, useMemo } from "react";
import katex from "katex";

interface Props {
    text: string;
    /** Extra CSS class name(s) on the wrapper <span> */
    className?: string;
}

/**
 * Split `text` on inline-math delimiters and return an array of segments
 * that alternate between plain text and rendered KaTeX HTML.
 */
function renderSegments(text: string): React.ReactNode[] {
    // Normalise \(...\) → $...$
    let normalised = text.replace(/\\\(([\s\S]*?)\\\)/g, (_m, inner: string) => `$${inner}$`);

    // Split on $...$ (non-greedy, single-line)
    const parts = normalised.split(/(\$[^$]+?\$)/g);

    return parts.map((part, i) => {
        if (part.startsWith("$") && part.endsWith("$") && part.length > 2) {
            const latex = part.slice(1, -1);
            try {
                const html = katex.renderToString(latex, {
                    throwOnError: false,
                    displayMode: false,
                    strict: false,
                    output: "htmlAndMathml",
                });
                return (
                    <span
                        key={i}
                        dangerouslySetInnerHTML={{ __html: html }}
                    />
                );
            } catch {
                // If KaTeX fails, just show the raw LaTeX
                return <span key={i}>{part}</span>;
            }
        }
        return <span key={i}>{part}</span>;
    });
}

function InlineMathText({ text, className }: Props) {
    const segments = useMemo(() => renderSegments(text), [text]);
    return <span className={className}>{segments}</span>;
}

export default memo(InlineMathText);
