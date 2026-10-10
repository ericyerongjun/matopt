import { useLayoutEffect, useMemo, useRef, useState } from "react";
import { Button } from "./controls.tsx";
import { Input } from "./controls.tsx";
import { Pill } from "./controls.tsx";
import { durationLabel, flattenRecords, matchesRecord, olderAnchor, visibleRecords } from "./state.ts";
import type { SessionEvent, TrajectoryProjection } from "./types.ts";
import { TrajectoryInspector } from "./TrajectoryInspector.tsx";
import { TrajectoryTimeline } from "./TrajectoryTimeline.tsx";
import "./trajectory.css";

export function TrajectoryView({ conversationId, projection, events, loading, error, running, onLoadOlder, onRefresh }: {
    conversationId: string;
    projection: TrajectoryProjection | null;
    events: SessionEvent[];
    loading: boolean;
    error: string | null;
    running: boolean;
    onLoadOlder: (beforeSeq: number) => void;
    onRefresh: () => void;
}) {
    const [query, setQuery] = useState("");
    const [range, setRange] = useState<[number, number] | null>(null);
    const [selectedId, setSelectedId] = useState<string | null>(null);
    const [anchorSeq, setAnchorSeq] = useState<number | null>(null);
    const [following, setFollowing] = useState(true);
    const ledgerRef = useRef<HTMLDivElement>(null);
    const prependRef = useRef<{ height: number; top: number } | null>(null);
    const records = useMemo(() => flattenRecords(projection), [projection]);
    const visible = visibleRecords(records, anchorSeq);
    const selected = records.find((record) => record.id === selectedId);
    const visibleIds = new Set((query || range ? records : visible).filter((record) => matchesRecord(record, query, range)).map((record) => record.id));

    useLayoutEffect(() => {
        if (anchorSeq === null && visible[0]) setAnchorSeq(visible[0].seq);
        const ledger = ledgerRef.current;
        if (!ledger) return;
        if (prependRef.current) {
            ledger.scrollTop = prependRef.current.top + ledger.scrollHeight - prependRef.current.height;
            prependRef.current = null;
        } else if (following) ledger.scrollTop = ledger.scrollHeight;
    }, [records, anchorSeq, following]);

    const loadOlder = () => {
        const next = visible[0] ? olderAnchor(records, visible[0].seq) : null;
        if (next === null) return;
        const ledger = ledgerRef.current;
        if (ledger) prependRef.current = { height: ledger.scrollHeight, top: ledger.scrollTop };
        setFollowing(false);
        setAnchorSeq(next);
        onLoadOlder(events[0]?.seq ?? visible[0].seq);
    };

    return <div className="trajectory-view">
        <div className="trajectory-view__main">
            <div className="trajectory-toolbar">
                <Input aria-label="Search trajectory" placeholder="Search records" value={query} onChange={(event) => setQuery(event.target.value)} />
                <Pill>{running || projection?.turns.some((turn) => turn.status === "running") ? "Live" : "Recorded"}</Pill>
                <Button size="sm" onClick={onRefresh} disabled={loading}>Refresh</Button>
                <Button size="sm" onClick={() => { setFollowing(true); const ledger = ledgerRef.current; if (ledger) ledger.scrollTop = ledger.scrollHeight; }} aria-pressed={following}>Follow tail</Button>
            </div>
            {error && <p className="trajectory-error" role="status">{error}</p>}
            {projection?.legacy && <p className="telemetry-caption trajectory-notice">This conversation predates the durable log. Its messages remain available in the Agent pane; historical timing and attempt usage are unknown.</p>}
            <TrajectoryTimeline records={records} range={range} onRangeChange={setRange} />
            <div ref={ledgerRef} className="trajectory-ledger" onScroll={() => {
                const ledger = ledgerRef.current;
                if (ledger) setFollowing(ledger.scrollHeight - ledger.scrollTop - ledger.clientHeight < 48);
            }}>
                {!query && !range && visible.length < records.length && <Button size="sm" onClick={loadOlder}>Load older records ({records.length - visible.length})</Button>}
                {!records.length && <p className="telemetry-caption">{loading ? "Loading trajectory…" : "No recorded events yet."}</p>}
                {records.length > 0 && visibleIds.size === 0 && <p className="telemetry-caption">No records match this filter.</p>}
                {projection?.turns.map((turn) => {
                    if (!turn.steps.some((step) => step.records.some((record) => visibleIds.has(record.id)))) return null;
                    return <section className="trajectory-turn" key={turn.turn}>
                        <h3>Turn {turn.turn} <span>{turn.status}</span> <span>{turn.started_at == null || turn.ended_at == null ? "Timing unknown" : durationLabel((turn.ended_at - turn.started_at) * 1000)}</span></h3>
                        {turn.steps.map((step) => {
                            const stepRecords = step.records.filter((record) => visibleIds.has(record.id));
                            if (!stepRecords.length) return null;
                            return <div className="trajectory-step" key={step.step}>
                                <h4>{step.step === 0 ? "Turn input" : `Step ${step.step}`} <span>{step.status}</span></h4>
                                {stepRecords.map((record) => <button key={record.id} type="button" className="trajectory-record" aria-pressed={selectedId === record.id} onClick={() => setSelectedId(record.id)}>
                                    <span className="trajectory-record__seq">{record.seq}</span>
                                    <span className="trajectory-record__label">{record.label}<small>{record.type}</small></span>
                                    <span className="trajectory-record__status" data-status={record.status}>{record.status}</span>
                                    <span className="trajectory-record__duration">{durationLabel(record.duration_ms)}</span>
                                </button>)}
                            </div>;
                        })}
                    </section>;
                })}
            </div>
        </div>
        {selected && <TrajectoryInspector key={selected.id} conversationId={conversationId} record={selected} events={events} onClose={() => setSelectedId(null)} onLoadSource={(seq) => onLoadOlder(seq + 200)} />}
    </div>;
}
