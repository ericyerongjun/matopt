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
