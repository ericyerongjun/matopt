/** MatOpt protocol binding for complete native ZCode messages and task elements. */
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { ArrowDown, Check, ChevronDown, Copy } from "lucide-react";
import type { ActivityItem, ChatMessage, ResultBlock } from "../types/protocol.ts";
import { isAssistantContent, isUserContent } from "../types/protocol.ts";
import { parseSlashCommands } from "../services/slash.ts";
import { splitQuotedMessage, type QuoteRef } from "../services/quote.ts";
import QuoteContextMenu from "./QuoteContextMenu.tsx";
import { commandPresentation } from "./commandPresentation.ts";
import { projectAssistantPresentation } from "../services/presentation.ts";
import MarkdownRenderer from "./MarkdownRenderer.tsx";
import { InlineMathText } from "./InlineMathText.tsx";
import { WorkDisclosure } from "./WorkActivity.tsx";
export { WorkDisclosure, SafeWorkTimeline } from "./WorkActivity.tsx";
import { ArtifactView } from "./ArtifactView.tsx";
import { UISurfaceView } from "../features/intelligent-ui/UISurfaceView.tsx";
import { Button } from "../zcode/components/ui/button.js";
import { Message, MessageContent, MessageActions, MessageAction } from "../zcode/components/ai-elements/message.js";
import { Task, TaskContent, TaskTrigger } from "../zcode/components/ai-elements/task.js";
import { Attachment, AttachmentPreview, Attachments } from "../zcode/components/ai-elements/attachments.js";
import "./conversation.css";

interface Props {
    messages: ChatMessage[];
    conversationId: string | null;
    streaming?: boolean;
    onPickOption?: (text: string) => void;
    onOpenWork?: () => void;
    onQuote?: (quote: QuoteRef) => void;
}

function TaskHeader({ title, children, live = false }: { title: string; children?: ReactNode; live?: boolean }) {
    return <TaskTrigger title={title}>
        <Button type="button" variant="ghost" size="sm" aria-label={title} className="h-auto max-w-full justify-start px-0 text-foreground-subtle hover:bg-transparent">
            {children}<span className="min-w-0 truncate" role={live ? "status" : undefined}>{title}</span>
            <ChevronDown className="size-3.5 shrink-0 transition-transform group-data-[state=open]:rotate-180" aria-hidden="true" />
        </Button>
    </TaskTrigger>;
}

function AssistantFooter({ answer }: { answer: string }) {
    const [copied, setCopied] = useState(false);
    const copy = async () => {
        try {
            await navigator.clipboard.writeText(answer);
            setCopied(true);
        } catch { setCopied(false); }
    };
    return <MessageActions className="matopt-answer-actions">
        <MessageAction tooltip={copied ? "Copied" : "Copy answer"} label={copied ? "Copied" : "Copy answer"} disabled={!answer} onClick={() => void copy()}>
            {copied ? <Check className="size-4" /> : <Copy className="size-4" />}
        </MessageAction>
    </MessageActions>;
}

export default function MessageList({ messages, conversationId, streaming = false, onPickOption, onOpenWork, onQuote }: Props) {
    const rootRef = useRef<HTMLDivElement>(null);
    const pinnedRef = useRef(true);
    const [atBottom, setAtBottom] = useState(true);
    const [quoteMenu, setQuoteMenu] = useState<(QuoteRef & { x: number; y: number }) | null>(null);
    const [copyError, setCopyError] = useState(false);
    const stick = () => {
        const element = rootRef.current;
        if (element && pinnedRef.current) { element.scrollTop = element.scrollHeight; setAtBottom(true); }
    };
    useLayoutEffect(() => { pinnedRef.current = true; stick(); setQuoteMenu(null); setCopyError(false); }, [conversationId]);
    useLayoutEffect(stick, [messages, streaming]);
    useLayoutEffect(() => {
        const inner = rootRef.current?.firstElementChild;
        if (!inner || typeof ResizeObserver === "undefined") return;
        const observer = new ResizeObserver(stick);
        observer.observe(inner);
        return () => observer.disconnect();
    }, []);
    useEffect(() => {
        if (!quoteMenu) return;
        const close = () => setQuoteMenu(null);
        window.addEventListener("resize", close);
        return () => window.removeEventListener("resize", close);
    }, [quoteMenu]);
    const openQuoteMenu = (x: number, y: number) => {
        if (!onQuote) return false;
        const selection = window.getSelection();
        if (!selection || selection.isCollapsed || !selection.rangeCount || !selection.toString().trim()) return false;
        const range = selection.getRangeAt(0);
        const rowFor = (node: Node) => (node.nodeType === Node.ELEMENT_NODE ? node as Element : node.parentElement)?.closest<HTMLElement>("[data-quote-source]");
        const row = rowFor(range.startContainer);
        if (!row || row !== rowFor(range.endContainer) || !rootRef.current?.contains(row)) return false;
        const source = row.dataset.quoteSource;
        if (source !== "user" && source !== "assistant") return false;
        setQuoteMenu({ x, y, source, text: selection.toString().trim() });
        setCopyError(false);
        return true;
    };
    return <div className="relative flex min-h-0 min-w-0 flex-1 flex-col">
      <div className="min-h-0 min-w-0 flex-1 overflow-y-auto overscroll-contain" ref={rootRef} data-testid="chat-view"
        onContextMenu={(event) => { if (openQuoteMenu(event.clientX, event.clientY)) event.preventDefault(); }}
        onKeyDown={(event) => {
            if (event.key !== "ContextMenu" && !(event.shiftKey && event.key === "F10")) return;
            const bounds = rootRef.current?.getBoundingClientRect();
            if (openQuoteMenu((bounds?.left ?? 0) + 16, (bounds?.top ?? 0) + 16)) event.preventDefault();
        }}
        onScroll={() => {
            const element = rootRef.current;
            if (element) {
                pinnedRef.current = element.scrollHeight - element.scrollTop - element.clientHeight < 72;
                setAtBottom(pinnedRef.current);
            }
            setQuoteMenu(null);
        }}>
        <div className="matopt-message-column" data-markdown-table-layout-root="true">
            {messages.map((message) => <MessageRow key={message.id} message={message} conversationId={conversationId}
                streaming={streaming && isAssistantContent(message.content) && message.content.status === "streaming"} onPickOption={onPickOption} onOpenWork={onOpenWork} />)}
        </div>
      </div>
      {!atBottom && <Button type="button" variant="outline" size="icon-md" className="absolute bottom-3 left-1/2 -translate-x-1/2 rounded-full bg-background shadow-sm"
          aria-label="Scroll to bottom" onClick={() => { pinnedRef.current = true; stick(); }}><ArrowDown className="size-4" aria-hidden="true" /></Button>}
      {copyError && <p className="absolute bottom-14 left-1/2 -translate-x-1/2 bg-background p-2 text-ui-sm" role="status">Could not copy the selection. Use your browser’s copy command.</p>}
      {quoteMenu && onQuote && <QuoteContextMenu x={quoteMenu.x} y={quoteMenu.y} onClose={() => setQuoteMenu(null)}
          onQuote={() => { onQuote({ text: quoteMenu.text, source: quoteMenu.source }); setQuoteMenu(null); window.getSelection()?.removeAllRanges(); }}
          onCopy={() => {
              const text = quoteMenu.text;
              setQuoteMenu(null);
              void (async () => { try { await navigator.clipboard.writeText(text); } catch { setCopyError(true); } })();
          }} />}
    </div>;
}

function ResultEvidence({ references, conversationId, items, onOpenWork }: {
    references: string[]; conversationId: string | null; items: ActivityItem[]; onOpenWork?: () => void;
}) {
    const evidence = [...new Set(references)].flatMap((reference) => {
        const activity = items.find((item) => item.id === reference);
        if (activity) return [<li key={reference}>{onOpenWork
            ? <button type="button" onClick={onOpenWork}>{activity.label}</button>
            : <span>{activity.label}</span>}</li>];
        let href: string | null = null;
        if (/^https?:\/\//i.test(reference)) {
            try { const url = new URL(reference); if (!url.username && !url.password) href = url.href; } catch { /* Invalid references have no interactive target. */ }
        } else if (conversationId && reference && !/^[\/\\]|[:\\\u0000-\u001f\u007f]/.test(reference)
            && !reference.split("/").some((part) => part === ".." || part === "." || !part)) {
            href = `/api/files/${encodeURIComponent(conversationId)}/${reference.split("/").map(encodeURIComponent).join("/")}`;
        }
        return href ? [<li key={reference}><a href={href} target="_blank" rel="noopener noreferrer">{reference}</a></li>] : [];
    });
    return evidence.length > 0 ? <ul className="partial-result-evidence" aria-label="Supporting evidence">{evidence}</ul> : null;
}

function PublicResult({ block, conversationId, items, onOpenWork }: {
    block: ResultBlock; conversationId: string | null; items: ActivityItem[]; onOpenWork?: () => void;
}) {
    return <section className="partial-result my-4 min-w-0" data-result-block={block.id} data-result-revision={block.revision} data-result-status={block.status}
        aria-label={block.status === "provisional" ? "Partial result" : block.status === "superseded" ? "Earlier result" : "Result"}>
        <p className="partial-result-status"><span aria-hidden="true" className="partial-result-dot" />{block.status === "provisional" ? "Partial result · not final" : block.status === "superseded" ? "Earlier result · superseded" : "Result"}</p>
        {block.kind === "source" ? <pre className="partial-result-source max-w-full overflow-x-auto whitespace-pre rounded-lg bg-muted p-4 text-ui-sm"><code>{block.content}</code></pre>
            : <MarkdownRenderer content={block.content} conversationId={conversationId} />}
        <ResultEvidence references={block.evidence_refs} conversationId={conversationId} items={items} onOpenWork={onOpenWork} />
    </section>;
}

function MessageRow({ message, conversationId, streaming, onPickOption, onOpenWork }: {
    message: ChatMessage; conversationId: string | null; streaming: boolean;
    onPickOption?: (text: string) => void; onOpenWork?: () => void;
}) {
    if (message.role === "user" && isUserContent(message.content)) {
        const parsed = parseSlashCommands(message.content.text);
        const quoted = splitQuotedMessage(parsed.rest);
        const commands = [...parsed.skills.map((name) => ({ name, kind: "skill" as const })), ...parsed.tools.map((name) => ({ name, kind: "tool" as const }))];
        return <Message from="user" className="matopt-user-message" data-message-id={message.id} data-quote-source="user"><MessageContent className="matopt-user-content">
            {commands.length > 0 && <div className="matopt-message-commands">{commands.map((command) => {
                const { label, Icon } = commandPresentation({ name: command.name });
                return <span className="matopt-command-badge" key={`${command.kind}:${command.name}`} title={`/${command.name}`}><Icon size={16} aria-hidden="true" />{label}</span>;
            })}</div>}
            {quoted?.quotes.map((quote, index) => <blockquote key={index} className="matopt-quoted-passage whitespace-pre-wrap break-words">{quote.text}</blockquote>)}
            {(quoted?.question || parsed.rest) && <p className="whitespace-pre-wrap break-words">{quoted ? quoted.question : parsed.rest}</p>}
            {message.content.attachments?.length > 0 && <Attachments variant="inline" className="matopt-message-files">
                {message.content.attachments.map((attachment) => {
                    const href = attachment.url || `/api/files/${message.conversation_id}/${attachment.path}`;
                    const isImage = attachment.kind?.startsWith("image");
                    const extension = attachment.name.match(/\.([a-z0-9]{1,10})$/i)?.[1].toUpperCase();
                    const fileType = isImage ? "Image" : extension === "PDF" ? "PDF document" : extension ? `${extension} file` : "File";
                    return <a key={attachment.path} className="matopt-message-file" href={href} target="_blank" rel="noopener noreferrer"
                        title={attachment.name} aria-label={`Open ${attachment.name} (opens in a new tab)`}>
                        <Attachment variant="inline" className="matopt-message-file-card" data={{ type: "file", id: attachment.path, filename: attachment.name, mediaType: isImage ? "image/*" : "application/octet-stream", url: href }}>
                            <AttachmentPreview className="matopt-message-file-icon" aria-hidden="true" />
                            <div className="matopt-message-file-info">
                                <span className="matopt-message-file-name">{attachment.name}</span>
                                <span className="matopt-message-file-type">{fileType}</span>
                            </div>
                        </Attachment>
                    </a>;
                })}
            </Attachments>}
        </MessageContent></Message>;
    }
    if (message.role !== "assistant" || !isAssistantContent(message.content)) return null;
    const content = message.content;
    const presentation = projectAssistantPresentation(content, streaming);
    const answerForCopy = presentation.answer || [...presentation.resultBlocks.map((block) => block.content), ...presentation.surfaces.map((surface) => surface.fallback)].join("\n\n");
    return <Message from="assistant" className="matopt-assistant-message" data-message-id={message.id}><MessageContent className="matopt-assistant-content w-full">
        <div className="matopt-answer-author"><span className="matopt-answer-mark" aria-hidden="true">Σ</span><span>MatOpt</span></div>
        {presentation.work && <WorkDisclosure work={presentation.work} conversationId={conversationId} onOpenWork={onOpenWork} />}
        <div data-testid="assistant-answer" data-quote-source="assistant" className="matopt-answer-document min-w-0">
            {presentation.resultBlocks.map((block) => <PublicResult key={block.id} block={block} conversationId={conversationId} items={presentation.work?.items ?? []} onOpenWork={onOpenWork} />)}
            {presentation.answer && <MarkdownRenderer content={presentation.answer} conversationId={conversationId} streaming={streaming} />}
            {presentation.surfaces.map((surface) => <UISurfaceView key={surface.id} surface={surface} />)}
            {presentation.artifacts.map((artifact, index) => <ArtifactView key={artifact.digest || artifact.path || `${artifact.name}-${index}`} artifact={artifact} conversationId={conversationId} />)}
        </div>
        {presentation.notice && <p className="text-ui-sm text-foreground-subtle" role="status">{presentation.notice}</p>}
        {presentation.earlierResults.length > 0 && <Task defaultOpen={false} className="earlier-results">
            <TaskHeader title="Earlier results" />
            <TaskContent>{presentation.earlierResults.map((block) => <PublicResult key={block.id} block={block} conversationId={conversationId} items={presentation.work?.items ?? []} onOpenWork={onOpenWork} />)}</TaskContent>
        </Task>}
        {presentation.earlierDrafts.length > 0 && <Task defaultOpen={false} className="earlier-results">
            <TaskHeader title={presentation.earlierDrafts.length === 1 ? "Earlier draft" : "Earlier drafts"} />
            <TaskContent><p className="text-ui-sm text-foreground-subtle">Earlier, unverified draft. The final revision above may update only part of it.</p>
                {presentation.earlierDrafts.map((draft, index) => <MarkdownRenderer key={index} content={draft} conversationId={conversationId} />)}
            </TaskContent>
        </Task>}
        {presentation.options.length > 0 && <div className="flex flex-wrap gap-2">{presentation.options.map((option) => <Button key={option} type="button" variant="outline" size="sm" disabled={!onPickOption} onClick={() => onPickOption?.(option)}><InlineMathText text={option} /></Button>)}</div>}
        {(content.status === "stopped" || content.status === "error") && <p className="text-foreground-subtle text-ui-sm" role="status">
            {content.status === "stopped" ? "Stopped." : "Couldn’t complete that analysis. Try again, or rephrase the question."}
        </p>}
    </MessageContent>
        {!streaming && <AssistantFooter answer={answerForCopy} />}
    </Message>;
}
