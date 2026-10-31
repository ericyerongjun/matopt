/** "N files changed" under an answer; saved answers fetch their turn's summary once. */
import { useEffect, useState } from "react";
import { FileDiff } from "lucide-react";
import type { AssistantContent, ChangedFile } from "../../types/protocol.ts";
import { turnChangeSummary } from "../../services/changes.ts";
import { ChangeCounts, changeTotals } from "./ChangesView.tsx";
import "./changes.css";

export function TurnChangesChip({ content, conversationId, streaming, onOpen }: {
    content: AssistantContent; conversationId: string | null; streaming: boolean; onOpen: (turn: number) => void;
}) {
    const turn = typeof content.turn === "number" ? content.turn : content.changes?.turn;
    const known = content.changes?.files;
    const [fetched, setFetched] = useState<ChangedFile[] | null>(null);
    const needsFetch = !known && turn !== undefined && !streaming && content.status !== "streaming" && Boolean(conversationId);
    useEffect(() => {
        setFetched(null);
        if (!needsFetch || !conversationId || turn === undefined) return;
        let current = true;
        void turnChangeSummary(conversationId, turn).then((files) => { if (current) setFetched(files); });
        return () => { current = false; };
    }, [needsFetch, conversationId, turn]);
    const files = known ?? fetched ?? [];
    if (turn === undefined || !files.length) return null;
    const totals = changeTotals(files);
    const label = `${files.length} ${files.length === 1 ? "file" : "files"} changed`;
    return <button type="button" className="matopt-changes-chip" onClick={() => onOpen(turn)} aria-label={`${label}. Review changes`}>
        <FileDiff aria-hidden="true" /><span>{label}</span><ChangeCounts file={files.some((file) => file.added !== undefined || file.removed !== undefined) ? totals : {}} />
    </button>;
}
