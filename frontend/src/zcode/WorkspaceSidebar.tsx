/* Maintained render-subtree migration from ZCode 29628c9acdb81b703bbd4080c207a0e7ce5e276e:
 * packages/ui/src/WorkspaceSidebar.tsx and WorkspaceSidebar/WorkspacePurposeSection.tsx.
 * Apache-2.0. MatOpt seam: one HTTP project/task list; native sidebar/section JSX and classes retained.
 * Remote connections, plugins, automation, account and workspace ordering are absent capabilities. */
import { useRef, useState } from "react";
import { ChevronDown, ChevronRight, FolderOpen, Search } from "lucide-react";
import { TID_SIDEBAR } from "@zcode/shared";
import { NewTaskButtonGroup } from "@/NewTaskButtonGroup.js";
import { MemoTaskItem } from "@/TaskListItem.js";
import { Button } from "@/components/ui/button.js";
import { Input } from "@/components/ui/input.js";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible.js";
import type { Conversation } from "../types/protocol";

export function WorkspaceSidebar({ conversations, activeId, loading, onCreateTask, onSelectTask, onRenameTask, onDeleteTask, onOpenFiles }: {
  conversations: Conversation[]; activeId: string | null; loading?: boolean;
  onCreateTask: () => void; onSelectTask: (id: string) => void;
  onRenameTask: (id: string, title: string) => Promise<void>; onDeleteTask: (id: string) => Promise<void>;
  onOpenFiles: () => void;
}) {
  const [open, setOpen] = useState(true);
  const [searchOpen, setSearchOpen] = useState(false);
  const [query, setQuery] = useState("");
  const searchRef = useRef<HTMLInputElement>(null);
  const visible = conversations.filter((item) => item.title.toLocaleLowerCase().includes(query.toLocaleLowerCase()));
  return <aside data-testid={TID_SIDEBAR} className="flex h-full flex-col overflow-hidden">
    <div className="h-12 [app-region:drag]" />
    <div className="relative flex-1 min-h-0 overflow-hidden">
      <div className="absolute inset-0 flex min-h-0 flex-col transition-transform duration-200 ease-out">
        <div className="flex flex-col gap-1 px-2 py-3">
          <NewTaskButtonGroup onCreateTask={onCreateTask} />
          <Button variant="ghost" onClick={() => { setSearchOpen((value) => !value); setQuery(""); }} data-icon="inline-start" size="lg"
            className="w-full justify-start gap-2 text-foreground hover:bg-surface-hover hover:text-foreground" aria-expanded={searchOpen}>
            <Search className="size-4" /><span className="min-w-0 flex-1 truncate text-left">Search tasks</span>
          </Button>
          {searchOpen && <Input autoFocus ref={searchRef} placeholder="Search task history" value={query} onChange={(event) => setQuery(event.target.value)} aria-label="Search task history" />}
          <Button variant="ghost" onClick={onOpenFiles} data-icon="inline-start" size="lg" className="w-full justify-start gap-2 text-foreground hover:bg-surface-hover hover:text-foreground">
            <FolderOpen className="size-4" /><span className="min-w-0 flex-1 truncate text-left">Workspace files</span>
          </Button>
        </div>
        <div className="relative flex min-h-0 flex-1 flex-col">
          <div className="flex flex-1 min-h-0 flex-col gap-3 overflow-y-auto" style={{ overflowAnchor: "none" }}>
            <div className="flex min-h-0 flex-col gap-3 px-2">
              <section data-testid="matopt-project-section" aria-label="MatOpt" className="group/purpose-section relative">
                <Collapsible open={open} onOpenChange={setOpen}>
                  <div className="flex h-7 min-w-0 items-center">
                    <CollapsibleTrigger asChild><button type="button" className="flex h-7 min-w-0 flex-1 items-center gap-1 px-2.5 text-left text-ui-base font-medium text-foreground-subtlest outline-none transition-colors hover:text-foreground focus-visible:text-foreground focus-visible:ring-2 focus-visible:ring-ring/30">
                      <span className="min-w-0 truncate">MatOpt</span>
                      {open ? <ChevronDown aria-hidden="true" className="size-3.5 shrink-0 opacity-0 transition-opacity group-hover/purpose-section:opacity-100 group-focus-within/purpose-section:opacity-100 [@media(hover:none)]:opacity-100" /> : <ChevronRight aria-hidden="true" className="size-3.5 shrink-0 opacity-0 transition-opacity group-hover/purpose-section:opacity-100 group-focus-within/purpose-section:opacity-100 [@media(hover:none)]:opacity-100" />}
                    </button></CollapsibleTrigger>
                  </div>
                  <CollapsibleContent>
                    <ul className="flex flex-col gap-0.5" aria-label="Task history">{visible.map((task) => <MemoTaskItem key={task.id} task={task} isActive={task.id === activeId} onSelect={() => onSelectTask(task.id)} onRename={(title) => onRenameTask(task.id, title)} onDelete={() => onDeleteTask(task.id)} />)}</ul>
                    {!visible.length && <p className="px-2.5 py-3 text-ui-sm text-foreground-subtlest">{loading ? "Loading tasks…" : query ? "No matching tasks" : "No tasks yet"}</p>}
                  </CollapsibleContent>
                </Collapsible>
              </section>
            </div>
          </div>
        </div>
      </div>
    </div>
  </aside>;
}
