/** MatOpt actions shown with the copied native ZCode Task component. */
import { useMemo } from "react";
import { Check, LoaderCircle, ShieldAlert, Wrench } from "lucide-react";
import { Task, TaskContent, TaskTrigger } from "../zcode/components/ai-elements/task.js";
import { Button } from "../zcode/components/ui/button.js";
import { Badge } from "../zcode/components/ui/badge.js";
import type { AssistantContent, ChatMessage, Part, PlanStep } from "../types/protocol";
import { isAssistantContent } from "../types/protocol";
import { selectOutputArtifacts } from "../services/outputArtifacts";
import { ArtifactView } from "./ArtifactView";
type ToolPart = Extract<Part, { type: "tool_call" }>;
interface Props {
    messages: ChatMessage[];
    conversationId: string | null;
    open: boolean;
    onToggle: () => void;
}

interface ToolGroup {
    name: string;
    items: ToolPart[];
}

function collect(messages: ChatMessage[]) {
    const tools: ToolPart[] = [];
    const monitors: Extract<Part, { type: "monitor" }>[] = [];
    let plan: PlanStep[] = [];
    for (const msg of messages) {
        if (msg.role !== "assistant" || !isAssistantContent(msg.content)) continue;
        const content = msg.content as AssistantContent;
        if (content.plan?.length) plan = content.plan;
        for (const part of content.parts) {
            if (part.type === "tool_call") tools.push(part);
            if (part.type === "monitor") monitors.push(part);
        }
    }
    return { tools, monitors, plan };
}

function groupTools(tools: ToolPart[]): ToolGroup[] {
    const groups: ToolGroup[] = [];
    for (const tool of tools) {
        const last = groups[groups.length - 1];
        if (last && last.name === tool.name) last.items.push(tool);
        else groups.push({ name: tool.name, items: [tool] });
    }
    return groups;
}

function verbFor(name: string, running: boolean) {
    if (running) return "Running";
    if (name === "web_search" || name === "web_fetch") return "Searched";
    if (name === "python" || name === "bash") return "Ran";
    if (name.includes("write") || name.includes("edit")) return "Edited";
    if (name.startsWith("read") || name === "list_files" || name === "grep") return "Read";
    if (name.includes("skill")) return "Loaded";
    if (name === "update_plan") return "Updated";
    return "Called";
}


export default function AnalysisPanel({ messages, conversationId, open, onToggle }: Props) {
 const { tools, monitors, plan } = collect(messages);
 const groups = useMemo(() => groupTools(tools), [tools]);
 const artifacts = useMemo(() => selectOutputArtifacts(messages), [messages]);
 if (!open) return <Button variant="ghost" onClick={onToggle}>Actions ({tools.length})</Button>;
 return <aside className="flex h-full min-h-0 flex-col text-ui-base" aria-label="Work">
  <header className="flex h-10 shrink-0 items-center justify-between border-b border-border px-4"><span className="flex items-center gap-2">Actions<Badge variant="secondary">{tools.length}</Badge></span><Button variant="ghost" size="sm" onClick={onToggle}>Close</Button></header>
  <div className="min-h-0 flex-1 space-y-5 overflow-auto p-4">
   {monitors.filter(m => m.level !== "ok").map((m,i) => <p key={i} className="flex items-start gap-2 rounded-lg bg-warning/10 p-3 text-ui-sm text-warning"><ShieldAlert className="size-4 shrink-0" />{m.summary}</p>)}
   {plan.length > 0 && <section><h3 className="mb-2 font-medium">Plan</h3><ol className="space-y-2">{plan.map((step,i) => <li key={i} className="flex items-start gap-2 text-ui-sm"><Check className={`mt-0.5 size-3.5 shrink-0 ${step.status === "completed" ? "text-success" : "text-foreground-subtlest"}`} />{step.content}</li>)}</ol></section>}
   <section className="space-y-3"><h3 className="font-medium">Steps</h3>{!groups.length && <p className="text-foreground-subtle">Tool calls appear here.</p>}
    {groups.map((group,i) => {
      const running = group.items.some(t=>t.status==="running"); const failed=group.items.some(t=>t.status==="error");
      return <Task key={`${group.name}-${i}`} defaultOpen={false}><TaskTrigger title={`${verbFor(group.name,running)} ${group.name}${group.items.length>1 ? ` ×${group.items.length}` : ""}`} className={failed ? "text-destructive" : undefined} /><TaskContent><ol className="space-y-2 text-ui-sm text-foreground-subtle">{group.items.map((tool,j)=><li key={tool.id||j}>{tool.status}{tool.result?.error && <p className="whitespace-pre-wrap text-destructive">{tool.result.error}</p>}</li>)}</ol></TaskContent></Task>;
    })}
   </section>
   {artifacts.length>0 && <section className="space-y-3"><h3 className="font-medium">Output</h3>{artifacts.map((art,i)=><ArtifactView key={art.digest||art.path||`${art.name}-${i}`} artifact={art} conversationId={conversationId} />)}</section>}
  </div>
 </aside>;
}
