/** GET /api/skills and /api/tools — slash catalog (Cursor / Claude / Codex). */

import apiClient from "./api.ts";
import { commandToken, parseSlashCommands, parseSlashSkillTokens } from "./slash.ts";

export { commandToken, parseSlashCommands, parseSlashSkillTokens };

export interface SkillInfo {
    name: string;
    description: string;
    source: string;
    license?: string | null;
    files?: string[];
    available?: boolean;
}

export interface ToolInfo {
    name: string;
    description: string;
    kind?: string;
    source: string;
    available?: boolean;
}

export type CommandKind = "skill" | "tool";

export interface CommandInfo {
    kind: CommandKind;
    name: string;
    description: string;
    source: string;
    available?: boolean;
}

const FALLBACK_TOOLS: ToolInfo[] = [
    { name: "python", description: "Run code in the conversation kernel.", source: "matopt", kind: "code" },
    { name: "bash", description: "Run a shell command in the workspace.", source: "matopt", kind: "terminal" },
    { name: "read_file", description: "Read a workspace file.", source: "matopt", kind: "file" },
    { name: "write_file", description: "Create or overwrite a workspace file.", source: "matopt", kind: "file" },
    { name: "web_search", description: "Search the live web.", source: "matopt", kind: "search" },
];

export async function listSkills(): Promise<SkillInfo[]> {
    const { data } = await apiClient.get<{ skills: SkillInfo[] }>("/skills");
    return data.skills ?? [];
}

export async function listTools(): Promise<ToolInfo[]> {
    const { data } = await apiClient.get<{ tools: ToolInfo[] }>("/tools");
    return data.tools ?? [];
}

export function fallbackTools(): ToolInfo[] {
    return FALLBACK_TOOLS.map((tool) => ({ ...tool, available: false }));
}

export function toCommands(skills: SkillInfo[], tools: ToolInfo[]): CommandInfo[] {
    return [
        ...tools.map((tool) => ({
            kind: "tool" as const,
            name: tool.name,
            description: tool.description,
            source: tool.source || "matopt",
            available: tool.available ?? true,
        })),
        ...skills.map((skill) => ({
            kind: "skill" as const,
            name: skill.name,
            description: skill.description,
            source: skill.source,
            available: skill.available ?? true,
        })),
    ];
}

export function skillIntro(description: string, max = 160): string {
    const flat = (description || "").replace(/\s+/g, " ").trim();
    const sentence = flat.split(/(?<=\.)\s/)[0] || flat;
    if (sentence.length <= max) return sentence;
    return sentence.slice(0, max - 1).trimEnd() + "…";
}

export function sourceLabel(source: string, kind?: CommandKind): string {
    if (kind === "tool") return "Tools";
    if (source === "matopt") return "MatOpt";
    if (source === "data") return "Data";
    if (source === "anthropic") return "Anthropic";
    return source || "Custom";
}
