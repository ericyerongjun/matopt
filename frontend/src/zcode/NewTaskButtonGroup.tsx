/* Vendored from ZCode 29628c9acdb81b703bbd4080c207a0e7ce5e276e: packages/ui/src/NewTaskButtonGroup.tsx
 * Apache-2.0. MatOpt host: shortcut labels and action dispatch use a browser adapter; render source unchanged. */
import { MessageCirclePlus } from "lucide-react";
import { TID_TASK_NEW_BUTTON } from "@zcode/shared";
import { cn } from "@/components/lib/utils.js";
import { useZCodeIntl } from "@/i18n/IntlProvider.js";
import { useShortcutCommandLabel } from "@/adapters/shellRuntime.js";
import { runUserAction } from "@/adapters/shellRuntime.js";

export function NewTaskButtonGroup({
  onCreateTask,
  disabled = false,
}: {
  onCreateTask: () => void;
  disabled?: boolean;
}) {
  const { intl } = useZCodeIntl();
  const newTaskShortcutLabel = useShortcutCommandLabel("newTask");
  return (
    <div
      role="button"
      tabIndex={disabled ? -1 : 0}
      onKeyDown={(event) => {
        if (!disabled && (event.key === "Enter" || event.key === " ")) {
          event.preventDefault();
          onCreateTask();
        }
      }}
      aria-disabled={disabled}
      data-testid={TID_TASK_NEW_BUTTON}
      onClick={() => {
        if (!disabled) {
          runUserAction({
            input: { featureId: "task.lifecycle", action: "create", trigger: "button" },
            operation: onCreateTask,
            completed: { resultSource: "optimistic_projection" },
            failureStage: "task_create",
          });
        }
      }}
      className={cn(
        "group w-full h-8 rounded-lg inline-flex shrink-0 items-center justify-stretch gap-2 overflow-hidden pl-2.5 pr-2.5 hover:bg-surface-hover hover:text-foreground active:translate-y-0 cursor-pointer",
        disabled &&
          "cursor-not-allowed text-foreground-subtlest hover:bg-transparent hover:text-foreground-subtlest",
      )}
    >
      <div className="flex min-w-0 flex-1 items-center gap-2 text-ui-base">
        <MessageCirclePlus className="h-4 w-4 shrink-0" />
        <span className="truncate">{intl.formatMessage({ id: "taskList.newThread" })}</span>
        <span className="ml-auto shrink-0 text-ui-xs font-normal text-foreground-subtlest">
          {newTaskShortcutLabel}
        </span>
      </div>
    </div>
  );
}
