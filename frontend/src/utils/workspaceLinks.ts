/**
 * Rewrite relative workspace links in assistant Markdown so the browser
 * can fetch them through the files API.
 *
 * `![plot](figures/fig_001.png)` → `![plot](/api/files/{id}/figures/fig_001.png)`
 */
export function rewriteWorkspaceLinks(markdown: string, conversationId?: string | null): string {
    if (!conversationId) return markdown;
    return markdown.replace(
        /(!?\[[^\]]*]\()(?!https?:|data:|\/|#|mailto:)([^)\s]+)(\))/g,
        (_full, prefix: string, path: string, suffix: string) => {
            const cleaned = path.replace(/^\.\//, "").replace(/^\/+/, "");
            return `${prefix}/api/files/${conversationId}/${cleaned}${suffix}`;
        }
    );
}

/** Best-effort extraction of a string field from incomplete JSON. */
export function peekJsonString(raw: string, key: string): string | undefined {
    try {
        const parsed = JSON.parse(raw) as Record<string, unknown>;
        if (typeof parsed[key] === "string") return parsed[key] as string;
    } catch {
        /* still streaming */
    }
    const re = new RegExp(`"${key}"\\s*:\\s*"`);
    const match = re.exec(raw);
    if (!match) return undefined;
    let i = match.index + match[0].length;
    let out = "";
    while (i < raw.length) {
        const ch = raw[i];
        if (ch === "\\") {
            const next = raw[i + 1];
            if (next === "n") out += "\n";
            else if (next === "t") out += "\t";
            else if (next === '"') out += '"';
            else if (next === "\\") out += "\\";
            else if (next) out += next;
            i += 2;
            continue;
        }
        if (ch === '"') break;
        out += ch;
        i += 1;
    }
    return out;
}
