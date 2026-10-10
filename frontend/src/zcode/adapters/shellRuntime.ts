/** MatOpt browser action seam for native ZCode controls; no RPC or telemetry emulation. */
export function useShortcutCommandLabel(command: string): string {
  const mac = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform);
  const modifier = mac ? "⌘" : "Ctrl+";
  return command === "newTask" ? `${modifier}N` : "";
}

export function runUserAction<T>({ operation }: { operation: () => T; input?: unknown; completed?: unknown; failureStage?: string }): T {
  return operation();
}
