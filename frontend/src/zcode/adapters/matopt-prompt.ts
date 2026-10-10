/** MatOpt HTTP/command binding; native PromptInput components stay unchanged. */
import type { FileUIPart } from "ai";
import { commandToken, parseSlashCommands } from "../../services/slash.ts";
import type { CommandInfo } from "../../services/skills";

export function filterPromptCommands(commands: CommandInfo[], query: string): CommandInfo[] {
  const q = query.trim().toLowerCase().replace(/_/g, "-");
  if (!q) return commands;
  return commands.map((command) => {
    const name = command.name.toLowerCase().replace(/_/g, "-");
    const hay = `${command.name} ${command.description}`.toLowerCase();
    const score = name === q ? 3 : name.startsWith(q) ? 2 : name.includes(q) || hay.includes(q) ? 1 : 0;
    return { command, score };
  }).filter((row) => row.score > 0)
    .sort((a, b) => b.score - a.score || a.command.name.localeCompare(b.command.name))
    .map((row) => row.command);
}

export function resolvePromptSubmission(raw: string, pinned: CommandInfo[], catalog: CommandInfo[]) {
  const parsed = parseSlashCommands(raw, {
    skills: catalog.filter((command) => command.kind === "skill").map((command) => command.name),
    tools: catalog.filter((command) => command.kind === "tool").map((command) => command.name),
  });
  const skills = [...new Set([...pinned.filter((command) => command.kind === "skill").map((command) => command.name), ...parsed.skills])];
  const tools = [...new Set([...pinned.filter((command) => command.kind === "tool").map((command) => command.name), ...parsed.tools])];
  const prefix = [...skills, ...tools].map(commandToken).join(" ");
  const text = [prefix, parsed.rest.trim()].filter(Boolean).join(" ");
  return text ? { text, skills, tools } : null;
}

export async function promptFilesForUpload(parts: FileUIPart[]): Promise<File[]> {
  // PromptInput owns preview URLs and supplies data URLs on submission. MatOpt
  // uploads binary multipart files; finish reading before native URL cleanup.
  return Promise.all(parts.map(async (part) => {
    if (!part.url.startsWith("data:") && !part.url.startsWith("blob:")) {
      throw new Error("Prompt attachments must be local files.");
    }
    const response = await fetch(part.url);
    if (!response.ok) throw new Error("Unable to read the attached file.");
    const blob = await response.blob();
    return new File([blob], part.filename || "attachment", { type: part.mediaType || blob.type });
  }));
}
