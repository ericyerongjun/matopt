import { useEffect, useState } from "react";
import { Button } from "./controls.tsx";
import { JsonTree, type JsonTreeLabels } from "./controls.tsx";
import { SegmentedTabs } from "./controls.tsx";
import { durationLabel, requestSequence } from "./state.ts";
import type { FrozenRequest, SessionEvent, TrajectoryRecord } from "./types.ts";
import { telemetryService } from "../../services/telemetry.ts";
import { UsageNumbers } from "./UsageDetails.tsx";

const JSON_LABELS: JsonTreeLabels = {
    copyValue: "Copy value", copyJson: "Copy JSON", copyPath: "Copy path",
    copyPrettyJson: "Copy formatted JSON", copyCompactJson: "Copy compact JSON",
    copied: "Copied", copyFailed: "Copy failed", collapseNode: "Collapse", expandNode: "Expand",
    copyButtonTitle: (action) => action,
};

function InspectValue({ value, label }: { value: unknown; label: string }) {
    if (value == null) return <p className="telemetry-caption">No {label.toLowerCase()} recorded.</p>;
    if (typeof value === "string") return <pre className="trajectory-inspector__text">{value}</pre>;
    return <JsonTree data={typeof value === "object" ? value : { value }} label={label} labels={JSON_LABELS} />;
}

export function TrajectoryInspector({ conversationId, record, events, onClose, onLoadSource, requestLoader = telemetryService.request }: {
    conversationId: string;
    record: TrajectoryRecord;
    events: SessionEvent[];
    onClose: () => void;
    onLoadSource: (seq: number) => void;
    requestLoader?: (id: string, seq: number, signal?: AbortSignal) => Promise<FrozenRequest>;
}) {
    const [tab, setTab] = useState<"input" | "output" | "timing" | "usage">("output");
    const [loadedRequest, setLoadedRequest] = useState<{ key: string; value: FrozenRequest } | null>(null);
    const [requestError, setRequestError] = useState<string | null>(null);
    const [retryRevision, setRetryRevision] = useState(0);
    const requestSeq = requestSequence(record);
    const requestKey = `${conversationId}/${record.id}/${requestSeq}`;
    useEffect(() => {
        // Keep at most the selected request. Leaving Input releases its full
        // message history rather than growing a cache across inspected steps.
        setLoadedRequest(null);
        setRequestError(null);
        if (tab !== "input" || requestSeq === null) return;
        const controller = new AbortController();
        void requestLoader(conversationId, requestSeq, controller.signal).then((value) => {
            if (!controller.signal.aborted) setLoadedRequest({ key: requestKey, value });
        }, (failure) => {
            if (!controller.signal.aborted) setRequestError(failure instanceof Error ? failure.message : "Unable to load the recorded request");
        });
        return () => controller.abort();
    }, [conversationId, record.id, requestSeq, requestKey, tab, requestLoader, retryRevision]);
    const sourceEvents = events.filter((event) => record.source_event_seqs.includes(event.seq));
    const missingSourceSeqs = record.source_event_seqs.filter((seq) => !sourceEvents.some((event) => event.seq === seq));
    return <aside className="trajectory-inspector" aria-label="Record inspector">
        <div className="telemetry-heading"><strong>{record.label}</strong><Button size="sm" onClick={onClose}>Close inspector</Button></div>
        <p className="telemetry-caption">{record.type} · {record.status} · Event {record.seq}</p>
        <SegmentedTabs label="Record details" value={tab} onChange={setTab} items={[
            { value: "input", label: "Input", id: "inspector-input-tab", panelId: "inspector-input" },
            { value: "output", label: "Output", id: "inspector-output-tab", panelId: "inspector-output" },
            { value: "timing", label: "Timing", id: "inspector-timing-tab", panelId: "inspector-timing" },
            { value: "usage", label: "Usage", id: "inspector-usage-tab", panelId: "inspector-usage" },
        ]} />
        <div role="tabpanel" id={`inspector-${tab}`} aria-labelledby={`inspector-${tab}-tab`} className="trajectory-inspector__body">
            {tab === "input" && (requestSeq === null ? <InspectValue value={record.input} label="Input" />
                : loadedRequest?.key === requestKey ? <InspectValue value={loadedRequest.value} label="Recorded request input" />
                : <>
                    <p className="telemetry-caption" role="status">{requestError ? `Recorded request unavailable: ${requestError}` : "Loading recorded request…"}</p>
                    {requestError && <Button size="sm" onClick={() => setRetryRevision((value) => value + 1)}>Retry request</Button>}
                    <InspectValue value={record.input} label="Request metadata" />
                </>)}
            {tab === "output" && <InspectValue value={record.output} label="Output" />}
            {tab === "usage" && <UsageNumbers usage={record.usage} />}
            {tab === "timing" && <>
                <dl className="telemetry-numbers">
                    <div><dt>Start</dt><dd>{record.started_at == null ? "Unknown" : new Date(record.started_at * 1000).toLocaleString()}</dd></div>
                    <div><dt>End</dt><dd>{record.ended_at == null ? "Unknown" : new Date(record.ended_at * 1000).toLocaleString()}</dd></div>
                    <div><dt>Duration</dt><dd>{durationLabel(record.duration_ms)}</dd></div>
                    <div><dt>First token</dt><dd>{durationLabel(record.first_token_ms)}</dd></div>
                </dl>
                <details><summary>Recorded source events ({sourceEvents.length}/{record.source_event_seqs.length})</summary>
                    {missingSourceSeqs.length > 0 && <Button size="sm" onClick={() => onLoadSource(Math.min(...missingSourceSeqs))}>Load source events</Button>}
                    <InspectValue value={sourceEvents} label="Source events" />
                </details>
            </>}
        </div>
    </aside>;
}
