import { rewriteWorkspaceLinks } from "../../utils/workspaceLinks.ts";

/** Native ZCode treats local paths as desktop files; MatOpt exposes HTTP files. */
export function prepareWorkspaceMarkdown(markdown: string, conversationId?: string | null, origin?: string): string {
    let fence: { marker: string; length: number } | null = null;
    return markdown.split(/(?<=\n)/).map((line) => {
        const match = /^ {0,3}(`{3,}|~{3,})([^\r\n]*)/.exec(line);
        if (match) {
            if (fence && match[1][0] === fence.marker && match[1].length >= fence.length && !match[2].trim()) fence = null;
            else if (!fence) fence = { marker: match[1][0], length: match[1].length };
            return line;
        }
        if (fence) return line;
        // Protect literal link examples in inline code as well as fenced code.
        return line.split(/(`+[^`]*`+)/g).map((segment) => {
            if (segment.startsWith("`")) return segment;
            // DeepSeek may emit workspace://reports/file.pdf rather than a relative path.
            const normalized = conversationId ? segment.replace(
                /(!?\[[^\]]*]\()workspace:\/\/([^\s)]+)(\))/gi,
                (_full, prefix: string, path: string, suffix: string) => `${prefix}${path.replace(/^\/+/, "")}${suffix}`,
            ) : segment;
            const rewritten = rewriteWorkspaceLinks(normalized, conversationId);
            if (!origin) return rewritten;
            return rewritten.replace(/(!?\[[^\]]*]\()(\/api\/files\/[^)\s]+)(\))/g,
                (_full, prefix: string, path: string, suffix: string) => `${prefix}${new URL(path, origin).href}${suffix}`);
        }).join("");
    }).join("");
}
