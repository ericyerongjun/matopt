// Integrate KaTex, Prism and Mermaid to render mathematical expressions
/**
 * MarkdownRenderer — renders Markdown content with:
 *  - KaTeX for LaTeX math (via remark-math + rehype-katex)
 *  - Prism for syntax-highlighted code blocks
 *  - Mermaid for diagrams (```mermaid code blocks)
 *
 * A preprocessing step normalises all LaTeX delimiters (\[…\], \(…\))
 * to the $…$ / $$…$$ form that remark-math expects.
 */

import React, { useEffect, useRef, useMemo, memo } from "react";
import ReactMarkdown from "react-markdown";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import mermaid from "mermaid";
import PlotRenderer from "./PlotRenderer";
import ChartRenderer from "./ChartRenderer";
import { preprocessLaTeX } from "../utils/formulaHelpers";

// Initialise Mermaid once
mermaid.initialize({ startOnLoad: false, theme: "default" });

interface Props {
    content: string;
}

function MarkdownRenderer({ content }: Props) {
    const containerRef = useRef<HTMLDivElement>(null);

    // Preprocess once per content change — normalise LaTeX delimiters,
    // protect math from markdown interpretation, etc.
    const processedContent = useMemo(() => preprocessLaTeX(content), [content]);

    // Re-render Mermaid diagrams whenever content changes
    useEffect(() => {
        if (containerRef.current) {
            const mermaidDivs =
                containerRef.current.querySelectorAll<HTMLElement>(".mermaid");
            if (mermaidDivs.length > 0) {
                mermaid.run({ nodes: Array.from(mermaidDivs) });
            }
        }
    }, [processedContent]);

    return (
        <div ref={containerRef} className="markdown-body">
            <ReactMarkdown
                remarkPlugins={[remarkMath]}
                rehypePlugins={[[rehypeKatex, { output: "htmlAndMathml", throwOnError: false, strict: false }]]}
                components={{
                    code({ className, children, ...rest }) {
                        const match = /language-(\w+)/.exec(className || "");
                        const lang = match ? match[1] : "";
                        const codeString = String(children).replace(/\n$/, "");

                        // Mermaid diagrams
                        if (lang === "mermaid") {
                            return <div className="mermaid">{codeString}</div>;
                        }

                        // Plotly interactive charts
                        if (lang === "plotly") {
                            return <PlotRenderer json={codeString} />;
                        }

                        // Chart.js charts
                        if (lang === "chartjs" || lang === "chart") {
                            return <ChartRenderer json={codeString} />;
                        }

                        // Syntax-highlighted code blocks
                        if (lang) {
                            return (
                                <SyntaxHighlighter language={lang} PreTag="div">
                                    {codeString}
                                </SyntaxHighlighter>
                            );
                        }

                        // Inline code
                        return (
                            <code className={className} {...rest}>
                                {children}
                            </code>
                        );
                    },
                    // Render images — supports base64 data URLs from matplotlib
                    img({ src, alt, ...rest }) {
                        return (
                            <img
                                src={src}
                                alt={alt || "Generated figure"}
                                className="markdown-figure"
                                loading="lazy"
                                {...rest}
                            />
                        );
                    },
                }}
            >
                {processedContent}
            </ReactMarkdown>
        </div>
    );
}

export default memo(MarkdownRenderer);
