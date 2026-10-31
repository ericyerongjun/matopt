/** Accessible unified-diff text: one row per line, marked added / removed / context. */
export function lineKind(line: string): "add" | "del" | "hunk" | "meta" | "context" {
    if (line.startsWith("@@")) return "hunk";
    if (/^(diff --git|index |--- |\+\+\+ |new file|deleted file|similarity|rename |old mode|new mode|Binary files)/.test(line)) return "meta";
    if (line.startsWith("+")) return "add";
    if (line.startsWith("-")) return "del";
    return "context";
}

const SPOKEN = { add: "Added", del: "Removed", hunk: "", meta: "", context: "" } as const;

export function PlainDiff({ patch, path }: { patch: string; path: string }) {
    const lines = patch.split("\n").filter((line) => lineKind(line) !== "meta");
    return <pre className="matopt-diff" aria-label={`Changes to ${path}`} tabIndex={0}><code>{lines.map((line, index) => {
        const kind = lineKind(line);
        return <span key={index} className="matopt-diff-line" data-kind={kind}>
            {SPOKEN[kind] && <span className="sr-only">{SPOKEN[kind]}: </span>}{line || " "}{"\n"}
        </span>;
    })}</code></pre>;
}
