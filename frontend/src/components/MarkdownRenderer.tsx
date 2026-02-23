// Integrate KaTex, Prism and Mermaid to render mathematical expressions
/**
 * MarkdownRenderer — renders Markdown content with:
 *  - KaTeX for LaTeX math (via remark-math + rehype-katex)
 *  - Prism for syntax-highlighted code blocks
 *  - Mermaid for diagrams (```mermaid code blocks)
 */

import React, { useEffect, useRef } from "react";
import ReactMarkdown from "react-markdown";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import mermaid from "mermaid";

// Initialise Mermaid once
mermaid.initialize({ startOnLoad: false, theme: "default" });

interface Props {
    content: string;
}

export default function MarkdownRenderer({ content }: Props) {
    const containerRef = useRef<HTMLDivElement>(null);

    // Re-render Mermaid diagrams whenever content changes
    useEffect(() => {
        if (containerRef.current) {
            const mermaidDivs =
                containerRef.current.querySelectorAll<HTMLElement>(".mermaid");
            if (mermaidDivs.length > 0) {
                mermaid.run({ nodes: Array.from(mermaidDivs) });
            }
        }
    }, [content]);

    return (
        <div ref={containerRef} className="markdown-body">
            <ReactMarkdown
                remarkPlugins={[remarkMath]}
                rehypePlugins={[rehypeKatex]}
                components={{
                    code({ className, children, ...rest }) {
                        const match = /language-(\w+)/.exec(className || "");
                        const lang = match ? match[1] : "";
                        const codeString = String(children).replace(/\n$/, "");

                        // Mermaid diagrams
                        if (lang === "mermaid") {
                            return <div className="mermaid">{codeString}</div>;
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
                }}
            >
                {content}
            </ReactMarkdown>
        </div>
    );
}