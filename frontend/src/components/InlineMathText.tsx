import katex from "katex";
import { Fragment, useMemo } from "react";

// Pandoc/remark-math rule: no space just inside the dollars and no digit after
// the closing one, so prices such as "$5 and $10" stay text.
const INLINE_MATH = /\$(?!\s)([^$\n]*?[^\s$])\$(?!\d)|\\\((.+?)\\\)/g;

export type InlineSegment = { kind: "text" | "math"; value: string };

/** Split short UI text such as follow-up chips into plain and inline-math runs. */
export function splitInlineMath(text: string): InlineSegment[] {
    const segments: InlineSegment[] = [];
    let last = 0;
    for (const match of text.matchAll(INLINE_MATH)) {
        const index = match.index ?? 0;
        if (index > last) segments.push({ kind: "text", value: text.slice(last, index) });
        segments.push({ kind: "math", value: (match[1] ?? match[2]).trim() });
        last = index + match[0].length;
    }
    if (last < text.length) segments.push({ kind: "text", value: text.slice(last) });
    return segments;
}

/** Plain text with `$…$` / `\(…\)` rendered by KaTeX; never interprets other markup. */
export function InlineMathText({ text }: { text: string }) {
    const segments = useMemo(() => splitInlineMath(text), [text]);
    return <>{segments.map((segment, index) => segment.kind === "text"
        ? <Fragment key={index}>{segment.value}</Fragment>
        : <span key={index} dangerouslySetInnerHTML={{ __html: katex.renderToString(segment.value, {
            displayMode: false, throwOnError: false, trust: false, strict: "ignore", output: "htmlAndMathml" }) }} />)}</>;
}
