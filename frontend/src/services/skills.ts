/** GET /api/skills catalog client. */

import apiClient from "./api";

export interface SkillInfo {
    name: string;
    description: string;
    source: string;
    license?: string | null;
    files?: string[];
}

const SLASH_LEAD = /^\/([a-z0-9]+(?:-[a-z0-9]+)*)(?:\s+|$)/i;

export async function listSkills(): Promise<SkillInfo[]> {
    const { data } = await apiClient.get<{ skills: SkillInfo[] }>("/skills");
    return data.skills ?? [];
}

/** Strip leading `/skill-name` tokens, the same way Cursor/Claude slash commands work. */
export function parseSlashSkillTokens(
    text: string,
    knownNames?: Iterable<string>
): { rest: string; names: string[] } {
    const known = knownNames
        ? new Map([...knownNames].map((name) => [name.toLowerCase(), name]))
        : null;
    const names: string[] = [];
    let rest = (text || "").trimStart();
    while (true) {
        const match = rest.match(SLASH_LEAD);
        if (!match) break;
        const raw = match[1];
        if (known) {
            const hit = known.get(raw.toLowerCase());
            if (!hit) break;
            if (!names.includes(hit)) names.push(hit);
        } else if (!names.includes(raw.toLowerCase())) {
            names.push(raw.toLowerCase());
        }
        rest = rest.slice(match[0].length).trimStart();
    }
    return { rest, names };
}

export function skillIntro(description: string, max = 160): string {
    const flat = (description || "").replace(/\s+/g, " ").trim();
    const sentence = flat.split(/(?<=\.)\s/)[0] || flat;
    if (sentence.length <= max) return sentence;
    return sentence.slice(0, max - 1).trimEnd() + "…";
}

export function sourceLabel(source: string): string {
    if (source === "matopt") return "MatOpt";
    if (source === "anthropic") return "Anthropic";
    return source || "Custom";
}
