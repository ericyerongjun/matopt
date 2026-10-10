/* Maintained render-subtree migration from ZCode 29628c9acdb81b703bbd4080c207a0e7ce5e276e:
 * packages/ui/src/WorkspaceHeader.tsx, WorkspaceHeaderSections.tsx and
 * WorkspaceHeaderSections/WorkspaceHeaderActionSection.tsx. Apache-2.0.
 * MatOpt seam: title and action slots replace task RPC orchestration. Native render classes retained. */
import type { ReactNode } from "react";
import { Folder } from "lucide-react";
import { TID_WORKSPACE_HEADER, TID_WORKSPACE_TITLE, TID_WORKSPACE_PATH } from "@zcode/shared";
import { cn } from "@/components/lib/utils.js";
import { Button } from "@/components/ui/button.js";
import { ControlHintTooltip } from "@/ControlHintTooltip.js";
import { WorkspaceSidePaneToggleButton } from "@/WorkspaceSidePaneToggleButton.js";

export function WorkspaceHeader({ variant = "task", activeTaskTitle, isSidebarVisible, isSidePaneOpen, onToggleSidePane, onOpenFiles, actions }: {
  variant?: "task" | "draft"; activeTaskTitle: string; isSidebarVisible: boolean;
  isSidePaneOpen: boolean; onToggleSidePane: () => void; onOpenFiles: () => void; actions?: ReactNode;
}) {
  return <header data-testid={TID_WORKSPACE_HEADER} data-workspace-header-variant={variant}
    className={cn("@container/workspace-header relative flex w-full shrink-0 h-12 border-b", variant === "draft" ? "border-transparent" : "border-border/50")}>
    <div className={cn("flex h-12 flex-1 min-w-0 items-center justify-between gap-2 overflow-hidden p-2 [app-region:drag] transition-[padding] duration-300", !isSidebarVisible && "pl-38")}>
      {variant === "task" ? <div className="flex min-w-0 items-center gap-2 overflow-hidden [app-region:no-drag]">
        <ControlHintTooltip title="MatOpt workspace files" side="bottom" align="start">
          <Button type="button" variant="ghost" size="icon-md" data-testid={TID_WORKSPACE_PATH} aria-label="MatOpt workspace files" onClick={onOpenFiles}><Folder className="size-4 text-foreground-subtle" /></Button>
        </ControlHintTooltip>
        <h1 data-testid={TID_WORKSPACE_TITLE} className="flex min-w-12 max-w-100 shrink items-center gap-2 truncate font-semibold text-foreground @max-[560px]/workspace-header:max-w-[30vw] @max-[420px]/workspace-header:max-w-[22vw] text-ui-base" title={activeTaskTitle}>
          <span className="min-w-0 truncate">{activeTaskTitle}</span>
        </h1>
      </div> : <div className="min-w-0 flex-1" aria-hidden="true" />}
      <div className="flex shrink-0 items-center [app-region:no-drag] gap-0.5">
        {actions}
        <WorkspaceSidePaneToggleButton isSidePaneOpen={isSidePaneOpen} onToggleSidePane={onToggleSidePane} shortcutLabel="" />
      </div>
    </div>
  </header>;
}
