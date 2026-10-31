/** Public activity only. Diagnostic messages and raw tool payloads have no UI path here. */
import { useEffect, useState } from "react";
import { Check, ChevronDown, ChevronRight, Circle, CircleAlert, ExternalLink, FileText, Globe, Loader2, Pause, Users, X } from "lucide-react";
import type { ActivityItem, ActivityOutput, ActivityPhase, GuardianReview, TranscriptEntry } from "../types/protocol.ts";
import { shellSessionBadge } from "./transcriptBadges.ts";
import "../features/runs/runs.css";
import { InlineMathText } from "./InlineMathText.tsx";
import type { PresentedWork } from "../services/presentation.ts";
import { useOptionalUiWorkspace } from "../features/intelligent-ui/UiWorkspace.tsx";
import { Button } from "../zcode/components/ui/button.js";
import { Task, TaskContent, TaskTrigger } from "../zcode/components/ai-elements/task.js";
import "./conversation.css";

const PHASES: Array<[ActivityPhase, string]> = [["work", "Working"], ["check", "Checking results"], ["final", "Final response"]];
const STATUS = { queued: "Queued", running: "In progress", completed: "Done", failed: "Failed", cancelled: "Cancelled", paused: "Paused" };
const CHECKS: Record<string, string> = { skipped: "Check skipped", correct: "Check passed", incorrect: "Check found issues", uncertain: "Check inconclusive", limit_reached: "Check limit reached", error: "Check incomplete" };
type ActivityFilter = "all" | "running" | "failures" | "outputs";

export function formatElapsed(milliseconds: number): string {
    const seconds = Math.max(0, Math.floor(milliseconds / 1000));
    if (seconds < 60) return `${seconds}s`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
    return `${Math.floor(seconds / 3600)}h ${Math.floor(seconds % 3600 / 60)}m`;
}

function useClock(active: boolean) {
    const [now, setNow] = useState(() => Date.now());
    useEffect(() => {
        if (!active) return;
        setNow(Date.now());
        const timer = setInterval(() => setNow(Date.now()), 1000);
        return () => clearInterval(timer);
    }, [active]);
    return now;
}

function WorkIcon({ status }: { status: ActivityItem["status"] }) {
    const Icon = status === "running" ? Loader2 : status === "completed" ? Check : status === "failed" ? CircleAlert : status === "cancelled" ? X : status === "paused" ? Pause : Circle;
    return <Icon className={`matopt-work-icon${status === "running" ? " matopt-work-spinning" : ""}`} aria-hidden="true" />;
}

export function activityOutputUrl(output: ActivityOutput, conversationId?: string | null): string | null {
    if (output.url && (/^https?:\/\//i.test(output.url) || /^\/api\/files\//.test(output.url))) return output.url;
    if (output.path && conversationId && !output.path.startsWith("/") && !output.path.split(/[\\/]/).some((part) => part === "..")) {
        return `/api/files/${encodeURIComponent(conversationId)}/${output.path.split("/").map(encodeURIComponent).join("/")}`;
    }
    return null;
}

function OutputLink({ output, conversationId }: { output: ActivityOutput; conversationId?: string | null }) {
    const workspace = useOptionalUiWorkspace();
    const surface = output.surface_id ? workspace?.records[output.surface_id]?.surface : undefined;
    const url = activityOutputUrl(output, conversationId);
    const Icon = output.kind === "source" ? Globe : FileText;
    if (surface) return <button type="button" className="matopt-work-output" onClick={() => workspace?.openSurface(surface)}><Icon aria-hidden="true" />{output.label || surface.title}</button>;
    if (url) return <a className="matopt-work-output" href={url} target="_blank" rel="noopener noreferrer"><Icon aria-hidden="true" />{output.label || "Open output"}<ExternalLink aria-hidden="true" /></a>;
    return <span className="matopt-work-output"><Icon aria-hidden="true" />{output.label || "Output"}</span>;
}

function ItemDetails({ item, now, conversationId, children, expanded, onExpandedChange, context }: { item: ActivityItem; now: number; conversationId?: string | null; children?: React.ReactNode; expanded: boolean; onExpandedChange: (expanded: boolean) => void; context: boolean }) {
    const end = item.finished_at !== null ? item.finished_at * 1000 : ["running", "queued"].includes(item.status) ? now : item.updated_at * 1000;
    const elapsed = item.started_at !== null ? formatElapsed(end - item.started_at * 1000) : null;
    return <li data-activity-id={item.id} data-parent-id={item.parent_id ?? undefined} data-activity-status={item.status} data-activity-kind={item.kind} data-activity-context={context || undefined}>
        <details className="matopt-activity-item" open={expanded}>
            <summary onClick={(event) => { event.preventDefault(); onExpandedChange(!expanded); }}><WorkIcon status={item.status} /><span className="matopt-work-label" title={item.label}>{item.kind === "agent" && <Users aria-hidden="true" />}<span>{item.label}{item.attempt > 1 && <span className="matopt-work-retry"> Attempt {item.attempt}</span>}</span></span>
                <span className="matopt-work-state"><span>{STATUS[item.status]}</span>{elapsed && <span className="matopt-work-item-elapsed">{elapsed}</span>}</span><ChevronDown className="matopt-work-chevron" aria-hidden="true" /></summary>
            <div className="matopt-activity-detail">
                {item.purpose && <p>{item.purpose}</p>}
                {item.summary && item.summary !== item.purpose && <p>{item.summary}</p>}
                <MeasuredProgress item={item} />
                {item.outputs.length > 0 && <ul className="matopt-work-outputs" aria-label="Outputs">{item.outputs.map((output, index) => <li key={`${output.path || output.url || output.surface_id}-${index}`}><OutputLink output={output} conversationId={conversationId} /></li>)}</ul>}
                {!item.purpose && !item.summary && !item.outputs.length && <p>{STATUS[item.status]}.</p>}
            </div>
            {children}
        </details>
    </li>;
}

function MeasuredProgress({ item }: { item: ActivityItem }) {
    if (!item.progress) return null;
    const { completed, total, unit } = item.progress;
    return <div className="matopt-measured-progress"><span>{completed}{total !== undefined ? ` of ${total}` : ""}{unit ? ` ${unit}` : ""}</span>
        {total !== undefined && <progress value={completed} max={total} aria-label={`${item.label}: ${completed} of ${total}${unit ? ` ${unit}` : ""}`} />}</div>;
}

function matchesFilter(item: ActivityItem, filter: ActivityFilter) {
    return filter === "all" || filter === "running" && ["running", "queued", "paused"].includes(item.status)
        || filter === "failures" && item.status === "failed" || filter === "outputs" && item.outputs.length > 0;
}

function hasMatchingActivity(work: PresentedWork, filter: ActivityFilter) {
    if (filter === "all") return true;
    if (work.items.length) return work.items.some((item) => matchesFilter(item, filter));
    if (filter === "running") return work.status === "paused" || work.steps.some((step) => step.status === "running");
    if (filter === "failures") return work.steps.some((step) => step.status === "error") || work.verification.status === "error";
    return false;
}

function filterEmptyLabel(filter: ActivityFilter) {
    return filter === "running" ? "No active actions." : filter === "failures" ? "No failed actions." : filter === "outputs" ? "No outputs yet." : "No activity yet.";
}

export function SafeWorkTimeline({ work, conversationId, filter = "all" }: { work: PresentedWork; conversationId?: string | null; filter?: ActivityFilter }) {
    const now = useClock(work.status === "running");
    // Keep each filter's manual choices when rows disappear, receive revisions, or settle.
    const [disclosures, setDisclosures] = useState<Record<string, boolean>>({});
    if (work.items.length) {
        const byId = new Map(work.items.map((item) => [item.id, item]));
        const matches = new Set(work.items.filter((item) => matchesFilter(item, filter)).map((item) => item.id));
        if (!matches.size) return <p className="matopt-work-empty">{filterEmptyLabel(filter)}</p>;
        const included = new Set(matches);
        for (const id of matches) {
            let item = byId.get(id)!;
            const ancestors = new Set([id]);
            while (item.parent_id) {
                const parent = byId.get(item.parent_id);
                if (!parent || parent.phase !== item.phase || ancestors.has(parent.id)) break;
                included.add(parent.id); ancestors.add(parent.id); item = parent;
            }
        }
        type ActivityNode = { item: ActivityItem; children: ActivityNode[] };
        const renderItems = (nodes: ActivityNode[]): React.ReactNode => <ol className="matopt-activity-items">{nodes.map(({ item, children }) => {
            const key = `${filter}:${item.id}`;
            // Filters reveal paths to matches; the user can still close any branch.
            const expanded = disclosures[key] ?? (filter !== "all" && children.length > 0);
            return <ItemDetails key={item.id} item={item} now={now} conversationId={conversationId} context={!matches.has(item.id)} expanded={expanded} onExpandedChange={(open) => setDisclosures((current) => ({ ...current, [key]: open }))}>{children.length > 0 && renderItems(children)}</ItemDetails>;
        })}</ol>;
        return <div className="matopt-work-timeline">{PHASES.map(([phase, label]) => {
            const group = work.items.filter((item) => item.phase === phase && included.has(item.id));
            if (!group.length) return null;
            const groupIds = new Set(group.map((item) => item.id));
            const children = new Map<string, ActivityItem[]>();
            for (const item of group) if (item.parent_id && item.parent_id !== item.id && groupIds.has(item.parent_id)) {
                children.set(item.parent_id, [...(children.get(item.parent_id) ?? []), item]);
            }
            const visited = new Set<string>();
            const build = (item: ActivityItem): ActivityNode[] => {
                if (visited.has(item.id)) return [];
                visited.add(item.id);
                return [{ item, children: (children.get(item.id) ?? []).flatMap(build) }];
            };
            const roots = group.filter((item) => !item.parent_id || !groupIds.has(item.parent_id) || item.parent_id === item.id);
            const forest = roots.flatMap(build);
            // Malformed cycles must not hide actions or repeat them indefinitely.
            forest.push(...group.flatMap(build));
            return <section key={phase} className="matopt-activity-phase" aria-label={label}>{!group.some((item) => item.kind === "phase") && <h4>{label}</h4>}{renderItems(forest)}</section>;
        })}</div>;
    }
    const verification = filter === "all" || filter === "failures" && work.verification.status === "error" ? CHECKS[work.verification.status] : undefined;
    const steps = work.steps.filter((step) => filter === "all" || filter === "running" && step.status === "running" || filter === "failures" && step.status === "error");
    return <div className="space-y-3 py-2 text-ui-sm text-foreground-subtle">
        {steps.length > 0 && <ol className="space-y-2" aria-label="Work steps">{steps.map((step, index) => <li key={index} className="flex items-start justify-between gap-4"><span>{step.label}</span><span>{step.status === "running" ? "In progress" : step.status === "error" ? "Incomplete" : "Done"}</span></li>)}</ol>}
        {verification && <p>{verification}{work.verification.rounds > 0 ? ` · ${work.verification.rounds} ${work.verification.rounds === 1 ? "round" : "rounds"}` : ""}</p>}
        {!steps.length && !verification && <p>{work.status === "running" ? "Preparing a response…" : work.status === "paused" ? "Work paused." : work.status === "stopped" ? "Work stopped." : work.status === "error" ? "The work could not be completed." : "Work complete."}</p>}
    </div>;
}

export function WorkDisclosure({ work, conversationId, onOpenWork }: { work: PresentedWork; conversationId?: string | null; onOpenWork?: () => void }) {
    // Never reset this choice on a status transition or a canonical history refresh.
    const [expanded, setExpanded] = useState(false);
    const [showAllActive, setShowAllActive] = useState(false);
    const running = work.status === "running";
    const live = running || work.status === "paused";
    const now = useClock(running);
    const duration = work.durationMs ?? (work.startedAt !== undefined ? (work.finishedAt !== undefined ? work.finishedAt * 1000 : now) - work.startedAt * 1000 : undefined);
    const count = work.items.length ? work.items.filter((item) => item.kind === "tool" || item.kind === "agent").length : work.steps.length || work.toolCount;
    const elapsed = duration !== undefined ? formatElapsed(duration) : null;
    const outcome = work.status === "stopped" ? `Stopped${elapsed ? ` after ${elapsed}` : ""}`
        : work.status === "error" ? `Couldn’t finish${elapsed ? ` · ${elapsed}` : ""}`
        : `Worked${elapsed ? ` for ${elapsed}` : ""}`;
    const title = live ? work.status === "paused" ? "Paused" : "Working…" : `${outcome} · ${count} ${work.items.length ? count === 1 ? "action" : "actions" : count === 1 ? "step" : "steps"}`;
    const active = work.items.filter((item) => item.kind !== "phase" && ["running", "queued", "paused"].includes(item.status));
    // Milestones the agent wrote with update_plan; shown inline so long runs read as a checklist.
    const plan = work.items.filter((item) => item.kind === "phase" && item.purpose === "Task milestone" && item.status !== "cancelled");
    const visibleActive = showAllActive ? active : active.slice(0, 3);
    const statusIcon = work.status === "complete" ? "completed" : work.status === "error" ? "failed" : work.status === "stopped" ? "cancelled" : work.status === "paused" ? "paused" : "running";
    const transcript = work.transcript ?? [];
    return <div className="matopt-live-work" data-work-status={work.status}>
        <Task open={expanded} onOpenChange={setExpanded} data-testid="work-disclosure">
            <TaskTrigger title={title}><Button type="button" variant="ghost" size="sm" aria-label={title} className="matopt-work-trigger h-auto max-w-full justify-start px-0 text-foreground-subtle hover:bg-transparent">
                <WorkIcon status={statusIcon} /><span role={!work.progress?.text || !live ? "status" : undefined}>{title}</span>{live && duration !== undefined && <span className="matopt-work-elapsed" aria-hidden="true">{formatElapsed(duration)}</span>}<ChevronDown className="matopt-work-chevron" aria-hidden="true" />
            </Button></TaskTrigger>
            {live && transcript.length > 0 && <WorkTranscript entries={transcript} reviews={work.reviews} live />}
            {live && <div className="matopt-work-now">
                {plan.length > 0 && <ol className="matopt-work-plan" aria-label="Task plan">{plan.map((item) => <li key={item.id} data-activity-status={item.status}>
                    <WorkIcon status={item.status} /><span>{item.label}</span>
                </li>)}</ol>}
                {!transcript.length && work.progress?.text && <p className="matopt-work-commentary" role="status" aria-live="polite">{work.progress.text}</p>}
                {!transcript.length && active.length > 0 && <ul className="matopt-active-actions" aria-label="Active actions">{visibleActive.map((item) => <li key={item.id} data-activity-id={item.id} data-activity-status={item.status}>
                    <WorkIcon status={item.status} /><div><span>{item.label}</span>{item.attempt > 1 && <span className="matopt-work-retry">Attempt {item.attempt}</span>}<MeasuredProgress item={item} /></div><span className="matopt-work-state">{STATUS[item.status]}</span>
                </li>)}</ul>}
                {active.length > 3 && <button type="button" className="matopt-more-actions" aria-expanded={showAllActive} onClick={() => setShowAllActive((value) => !value)}>{showAllActive ? "Show fewer actions" : `${active.length - 3} more active ${active.length - 3 === 1 ? "action" : "actions"}`}</button>}
            </div>}
            <TaskContent className="matopt-work-history">{transcript.length > 0 && !live ? <WorkTranscript entries={transcript} reviews={work.reviews} /> : <><WorkMilestones work={work} /><SafeWorkTimeline work={work} conversationId={conversationId} /></>}{onOpenWork && <Button type="button" variant="ghost" size="sm" className="matopt-open-activity" onClick={onOpenWork}><ExternalLink className="size-3.5" aria-hidden="true" />Open activity</Button>}</TaskContent>
        </Task>
    </div>;
}

/** MCP tools arrive untitled as `server__tool`; show them as a sourced call. */
function TranscriptTitle({ entry }: { entry: TranscriptEntry }) {
    const separator = entry.title || !entry.tool ? -1 : entry.tool.indexOf("__");
    if (separator <= 0) return <span className="matopt-tx-title">{entry.title ?? entry.tool}</span>;
    return <span className="matopt-tx-title matopt-tx-mcp"><span className="matopt-tx-mcp-badge">MCP</span><strong>{entry.tool!.slice(0, separator)}</strong><span aria-hidden="true">›</span><span className="matopt-tx-subject">{entry.tool!.slice(separator + 2)}</span></span>;
}

/** Claude Code-style narration and expandable action rows. */
const GUARDIAN = { allow: "Review allowed", deny: "Review denied", error: "Review failed" } as const;

export function WorkTranscript({ entries, reviews = [], live = false }: { entries: TranscriptEntry[]; reviews?: GuardianReview[]; live?: boolean }) {
    const byCall = new Map(reviews.map((review) => [`action:${review.call_id}`, review]));
    return <ol className="matopt-tx" aria-label="Work transcript">{entries.map((entry) => {
        if (entry.kind === "note") return <li key={entry.id} className="matopt-tx-note"><InlineMathText text={entry.text ?? ""} /></li>;
        const shell = shellSessionBadge(entry, live);
        const review = byCall.get(entry.id);
        return <li key={entry.id} className="matopt-tx-action" data-status={entry.status}>
            <details>
                <summary>
                    <TranscriptTitle entry={entry} />
                    {entry.subject && <span className="matopt-tx-subject">{entry.subject}</span>}
                    {shell && <span className="matopt-tx-badge" data-tone={shell.tone}>{shell.label}</span>}
                    {review && <span className="matopt-tx-badge" data-tone={review.outcome === "allow" ? "success" : "danger"} title={review.rationale || undefined}>{GUARDIAN[review.outcome]}</span>}
                    {entry.status === "running" && <Loader2 className="matopt-work-icon matopt-work-spinning" aria-label="Running" />}
                    {entry.status === "error" && <CircleAlert className="matopt-work-icon matopt-tx-failed" aria-label="Failed" />}
                    {(entry.input || entry.output || review) && <ChevronRight className="matopt-tx-chevron" aria-hidden="true" />}
                </summary>
                {entry.input && <pre className="matopt-tx-block" data-label={entry.language === "json" ? "Arguments" : "Input"}><code>{entry.input}</code></pre>}
                {review && <p className="matopt-tx-guardian"><strong>{GUARDIAN[review.outcome]}</strong>{review.risk_level ? ` · ${review.risk_level} risk` : ""}{review.rationale ? `: ${review.rationale}` : ""}</p>}
                {entry.output && <pre className="matopt-tx-block matopt-tx-output" data-label={entry.status === "error" ? "Error" : "Output"}><code>{entry.output}</code></pre>}
            </details>
        </li>;
    })}</ol>;
}

function WorkMilestones({ work, collapsible = false }: { work: PresentedWork; collapsible?: boolean }) {
    const milestones = (work.milestones ?? []).filter((update, index, updates) => update.text && (index === 0 || update.text !== updates[index - 1].text));
    if (!milestones.length) return null;
    const history = <ol className="matopt-milestones" aria-label="Task updates">{milestones.slice(-6).map((update) => <li key={update.id}>{update.text}</li>)}</ol>;
    return collapsible ? <details className="matopt-update-history"><summary><span>Task updates</span><span>{Math.min(milestones.length, 6)}</span><ChevronDown aria-hidden="true" /></summary>{history}</details> : history;
}

export function ActivityPanel({ turns, conversationId }: { turns: Array<{ id: string; work: PresentedWork }>; conversationId: string | null }) {
    const [filter, setFilter] = useState<ActivityFilter>("all");
    const ordered = [...turns].reverse();
    const visibleTurns = new Set(ordered.filter(({ work }) => hasMatchingActivity(work, filter)).map(({ id }) => id));
    return <div className="matopt-public-activity">
        <div className="matopt-activity-filters" role="group" aria-label="Filter activity">{([["all", "All"], ["running", "Active"], ["failures", "Failures"], ["outputs", "Outputs"]] as const).map(([value, label]) => <button type="button" key={value} aria-pressed={filter === value} onClick={() => setFilter(value)}>{label}</button>)}</div>
        {ordered.length ? <>
            {!visibleTurns.size && <p className="matopt-work-empty" role="status">{filterEmptyLabel(filter)}</p>}
            {/* Keep hidden turns mounted so filtering does not discard disclosure choices. */}
            <ol className="matopt-activity-turns" aria-label="Activity timeline" hidden={!visibleTurns.size}>{ordered.map(({ id, work }, index) => <li key={id} data-work-status={work.status} hidden={!visibleTurns.has(id)}><h3>{index === 0 ? "Current task" : `Earlier turn ${turns.length - index}`}<span>{work.status === "running" ? "In progress" : work.status === "paused" ? "Paused" : work.durationMs !== undefined ? formatElapsed(work.durationMs) : ""}</span></h3>{filter === "all" && <>{(work.status === "running" || work.status === "paused") && work.progress?.text && <p className="matopt-inspector-current">{work.progress.text}</p>}<WorkMilestones work={work} collapsible /></>}<SafeWorkTimeline work={work} filter={filter} conversationId={conversationId} /></li>)}</ol>
        </> : <p className="matopt-loading">Activity appears here as you work.</p>}
    </div>;
}
