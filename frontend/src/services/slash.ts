/** Leading `/skill-name` and `/tool_name` tokens (Cursor / Claude / Codex). */

const SLASH_LEAD = /^\/([a-z0-9]+(?:[-_][a-z0-9]+)*)(?:\s+|$)/i;

function catalogMap(names: Iterable<string>): Map<string, string> {
    const map = new Map<string, string>();
    for (const name of names) {
        const key = name.toLowerCase();
        map.set(key, name);
        map.set(key.replace(/_/g, "-"), name);
        map.set(key.replace(/-/g, "_"), name);
    }
    return map;
}

export function parseSlashCommands(
    text: string,
    known?: { skills?: Iterable<string>; tools?: Iterable<string> }
): { rest: string; skills: string[]; tools: string[] } {
    const skillCat = known?.skills ? catalogMap(known.skills) : null;
    const toolCat = known?.tools ? catalogMap(known.tools) : null;
    const skills: string[] = [];
    const tools: string[] = [];
    let rest = (text || "").trimStart();
    while (true) {
        const match = rest.match(SLASH_LEAD);
        if (!match) break;
        const raw = match[1].toLowerCase();
        if (skillCat || toolCat) {
            const skill = skillCat?.get(raw);
            const tool = toolCat?.get(raw);
            if (skill) {
                if (!skills.includes(skill)) skills.push(skill);
            } else if (tool) {
                if (!tools.includes(tool)) tools.push(tool);
            } else {
                break;
            }
        } else if (!skills.includes(raw)) {
            skills.push(raw);
        }
        rest = rest.slice(match[0].length).trimStart();
    }
    return { rest, skills, tools };
}

/** Strip leading `/skill-name` tokens, the same way Cursor/Claude slash commands work. */
export function parseSlashSkillTokens(
    text: string,
    knownNames?: Iterable<string>
): { rest: string; names: string[] } {
    if (knownNames) {
        const parsed = parseSlashCommands(text, { skills: knownNames, tools: [] });
        return { rest: parsed.rest, names: parsed.skills };
    }
    const parsed = parseSlashCommands(text);
    return { rest: parsed.rest, names: [...parsed.skills, ...parsed.tools] };
}

export function commandToken(name: string): string {
    return `/${name.replace(/_/g, "-")}`;
}

export interface ActiveSlashToken {
    start: number;
    end: number;
    query: string;
}

/** Locate only the command being edited, without interpreting prose as a command.
 * URLs/paths, inline and fenced code, and math retain their ordinary slash syntax.
 */
export function activeSlashToken(text: string, caret: number): ActiveSlashToken | null {
    const position = Math.max(0, Math.min(text.length, caret));
    const before = text.slice(0, position);
    const match = before.match(/(?:^|\s)(\/([a-z0-9_-]*))$/i);
    if (!match) return null;
    const start = position - match[1].length;
    const prefix = text.slice(0, start);
    const suffix = text.slice(position).match(/^[a-z0-9_-]*/i)?.[0] ?? "";
    const end = position + suffix.length;
    if (/[/.\\:]/.test(text[end] ?? "")) return null;
    // A slash inside a code or math delimiter is content, even after whitespace.
    const fenceCount = (prefix.match(/```/g) ?? []).length;
    const withoutFences = prefix.replace(/```/g, "");
    if (fenceCount % 2 || (withoutFences.match(/`/g) ?? []).length % 2) return null;
    if ((prefix.match(/(?<!\\)\$/g) ?? []).length % 2) return null;
    if (prefix.lastIndexOf("\\(") > prefix.lastIndexOf("\\)") || prefix.lastIndexOf("\\[") > prefix.lastIndexOf("\\]")) return null;
    const preceding = prefix.trimEnd().split(/\s+/).at(-1) ?? "";
    if (/^(?:[a-z]|[-+]?\d+(?:\.\d+)?|[=+*^÷×])$/i.test(preceding)) return null;
    return { start, end, query: match[2] + suffix };
}
