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

import React, { isValidElement, useEffect, useRef, useMemo, memo, useState, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import mermaid from "mermaid";
import PlotRenderer from "./PlotRenderer";
import ChartRenderer from "./ChartRenderer";
import { preprocessLaTeX } from "../utils/formulaHelpers";
import { normalizeAgentMarkdown } from "../utils/markdownNormalize";
import { rewriteWorkspaceLinks } from "../utils/workspaceLinks";
import "./MarkdownRenderer.css";

// Initialise Mermaid once
mermaid.initialize({ startOnLoad: false, theme: "default" });

const KATEX_OPTIONS = {
    output: "htmlAndMathml" as const,
    throwOnError: false,
    strict: false as const,
    macros: {
        "\\R": "\\mathbb{R}",
        "\\N": "\\mathbb{N}",
        "\\Z": "\\mathbb{Z}",
        "\\Q": "\\mathbb{Q}",
        "\\C": "\\mathbb{C}",
        "\\E": "\\mathbb{E}",
    },
};

interface Props {
    content: string;
    conversationId?: string | null;
}

/** Fenced code block with a copy affordance in the top-right corner. */
function CodeBlock({ language, code }: { language: string; code: string }) {
    const [copied, setCopied] = useState(false);

    const copy = () => {
        void navigator.clipboard
            .writeText(code)
            .then(() => {
                setCopied(true);
                window.setTimeout(() => setCopied(false), 1200);
            })
            .catch(() => undefined);
    };

    return (
        <div className="code-block">
            <button
                type="button"
                className="code-block__copy"
                onClick={copy}
                title="Copy code"
                aria-label="Copy code"
            >
                {copied ? "Copied" : "Copy"}
            </button>
            <SyntaxHighlighter language={language} PreTag="div">
                {code}
            </SyntaxHighlighter>
        </div>
    );
}

function MarkdownRenderer({ content, conversationId }: Props) {
    const containerRef = useRef<HTMLDivElement>(null);

    // Preprocess once per content change — normalise LaTeX delimiters,
    // protect math from markdown interpretation, etc.
    const processedContent = useMemo(
        () =>
            preprocessLaTeX(normalizeAgentMarkdown(rewriteWorkspaceLinks(content, conversationId))),
        [content, conversationId]
    );

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
                remarkPlugins={[remarkGfm, [remarkMath, { singleDollarTextMath: true }]]}
                rehypePlugins={[[rehypeKatex, KATEX_OPTIONS]]}
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
                            return <CodeBlock language={lang} code={codeString} />;
                        }

                        // Inline code
                        return (
                            <code className={className} {...rest}>
                                {children}
                            </code>
                        );
                    },
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
                    p({ children }) {
                        if (isFigureGroup(children)) {
                            return <div className="md-figures">{children}</div>;
                        }
                        return <p>{children}</p>;
                    },
                    table({ children }) {
                        return (
                            <div className="markdown-table-wrap">
                                <table>{children}</table>
                            </div>
                        );
                    },
                }}
            >
                {processedContent}
            </ReactMarkdown>
        </div>
    );
}

function isFigureGroup(children: ReactNode): boolean {
    const nodes = React.Children.toArray(children).filter((child) => {
        if (typeof child === "string") return child.trim().length > 0;
        return true;
    });
    if (nodes.length === 0) return false;
    return nodes.every(
        (child) =>
            isValidElement(child) &&
            (child.type === "img" ||
                (typeof child.props === "object" &&
                    child.props !== null &&
                    "className" in child.props &&
                    String((child.props as { className?: string }).className || "").includes("markdown-figure")))
    );
}

export default memo(MarkdownRenderer);
