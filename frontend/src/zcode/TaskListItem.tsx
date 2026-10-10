/* Maintained render-subtree migration from ZCode 29628c9acdb81b703bbd4080c207a0e7ce5e276e:
 * packages/ui/src/TaskListItem.tsx, native default task row and hover actions.
 * Apache-2.0. MatOpt seam: HTTP conversation metadata replaces ZCode task stores;
 * unavailable pin/workflow/git/archive capabilities are omitted. Delete remains explicitly Delete. */
import { useRef, useState } from "react";
import { Ellipsis, LoaderIcon, Pencil, Trash2 } from "lucide-react";
import { TID_TASK_ITEM, testId } from "@zcode/shared";
import { cn } from "@/components/lib/utils.js";
import { TaskTitleOverflowText } from "@/components/TaskTitleOverflowText.js";
import { Button } from "@/components/ui/button.js";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu.js";
import { TaskRenameDialog } from "@/TaskRenameDialog.js";
import { useZCodeIntl } from "@/i18n/IntlProvider.js";
import type { Conversation } from "../types/protocol";

export function MemoTaskItem({ task, isActive, onSelect, onRename, onDelete }: {
  task: Conversation; isActive: boolean; onSelect: () => void;
  onRename: (title: string) => Promise<void>; onDelete: () => Promise<void>;
}) {
  const { intl } = useZCodeIntl();
  const [menuOpen, setMenuOpen] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [renameDraft, setRenameDraft] = useState(task.title);
  const [deleteConfirm, setDeleteConfirm] = useState(false);
  const [error, setError] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const minutes = Math.max(0, Math.floor((Date.now() / 1000 - task.updated_at) / 60));
  const taskTimeLabel = minutes < 1 ? "now" : minutes < 60 ? `${minutes}m` : minutes < 1440 ? `${Math.floor(minutes / 60)}h` : `${Math.floor(minutes / 1440)}d`;
  const submitRename = async () => {
    if (!renameDraft.trim()) return;
    try { await onRename(renameDraft.trim()); setRenaming(false); setError(""); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Rename failed"); }
  };
  return <>
    <TaskRenameDialog open={renaming} value={renameDraft} inputRef={inputRef} intl={intl}
      onOpenChange={setRenaming} onChange={setRenameDraft} onCancel={() => setRenaming(false)} onConfirm={() => void submitRename()} />
    <li data-testid={testId(TID_TASK_ITEM, task.id)} data-task-item-key={task.id} onClick={onSelect} tabIndex={0}
      onKeyDown={(event) => { if (event.target === event.currentTarget && (event.key === "Enter" || event.key === " ")) { event.preventDefault(); onSelect(); } }}
      className={cn("group/task-item flex cursor-pointer gap-2 rounded-lg pl-2.5 pr-1 py-1 transition-[background-color,border-color,box-shadow]", "items-center", isActive ? "bg-selected" : "hover:bg-surface-hover")}>
      <div className="relative flex size-4 shrink-0 items-center justify-center">
        <span aria-hidden="true" className="flex size-4 items-center justify-center transition-opacity">
          {task.running ? <LoaderIcon className="size-4 animate-spin text-foreground-subtle" /> : null}
        </span>
      </div>
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex min-w-0 items-center gap-2">
          <div className="relative min-w-0 flex h-6 flex-1 flex-wrap items-center gap-1.5">
            <TaskTitleOverflowText className="text-ui-base text-foreground" title={task.title}>{task.title}</TaskTitleOverflowText>
          </div>
          <span data-task-row-metadata="true" className="mr-0.5 flex shrink-0 items-center gap-1 text-ui-sm text-foreground-subtle">{taskTimeLabel}</span>
          <span data-task-row-actions="true" className={cn("flex shrink-0 items-center gap-0.5", !menuOpen && "opacity-0 group-hover/task-item:opacity-100 group-focus-within/task-item:opacity-100 [@media(hover:none)]:opacity-100")} onClick={(event) => event.stopPropagation()}>
            <DropdownMenu open={menuOpen} onOpenChange={setMenuOpen}>
              <DropdownMenuTrigger asChild><Button type="button" variant="ghost" size="icon-sm" aria-label="Task actions" onMouseDown={(event) => event.stopPropagation()}><Ellipsis className="size-3.5" /></Button></DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem onSelect={() => { setRenameDraft(task.title); setRenaming(true); }}><Pencil className="size-4" />Rename</DropdownMenuItem>
                <DropdownMenuItem onSelect={() => setDeleteConfirm(true)}><Trash2 className="size-4" />Delete</DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </span>
        </div>
      </div>
    </li>
    {deleteConfirm && <div className="mx-2 flex items-center justify-between gap-2 rounded-lg bg-destructive/10 p-2 text-ui-sm" role="alert">
      <span>Delete this task?</span><button type="button" className="text-destructive" onClick={() => void onDelete().catch((reason) => { setError(String(reason)); setDeleteConfirm(false); })}>Delete</button>
      <button type="button" onClick={() => setDeleteConfirm(false)}>Cancel</button>
    </div>}
    {error && <p role="alert" className="px-3 text-ui-sm text-destructive">{error}</p>}
  </>;
}
