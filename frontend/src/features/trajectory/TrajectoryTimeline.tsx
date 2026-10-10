import { useRef, type PointerEvent } from "react";
import { Button } from "./controls.tsx";
import { durationLabel } from "./state.ts";
import type { TrajectoryRecord } from "./types.ts";

/** Timing is taken from recorded timestamps; unknown spans are omitted. */
export function TrajectoryTimeline({ records, range, onRangeChange }: {
    records: TrajectoryRecord[];
    range: [number, number] | null;
    onRangeChange: (range: [number, number] | null) => void;
}) {
    const anchor = useRef<number | null>(null);
    const timed = records.filter((record) => record.started_at !== null && record.ended_at !== null);
    if (!timed.length) return <div className="trajectory-timeline telemetry-caption">Timing unavailable for this session.</div>;
    const start = Math.min(...timed.map((record) => record.started_at!));
    const end = Math.max(...timed.map((record) => record.ended_at!));
    const duration = Math.max(0.001, end - start);
    const position = (event: PointerEvent<HTMLDivElement>) => {
        const rect = event.currentTarget.getBoundingClientRect();
        return start + Math.max(0, Math.min(1, (event.clientX - rect.left) / Math.max(1, rect.width))) * duration;
    };
    return <section className="trajectory-timeline" aria-label="Timing overview">
        <div className="telemetry-heading"><span>Timing · {durationLabel((end - start) * 1000)}</span>
            {range && <Button size="sm" onClick={() => onRangeChange(null)}>Clear range</Button>}
        </div>
        <div className="trajectory-timeline__track" title="Drag to filter time; scroll to zoom; right-click to clear"
            onPointerDown={(event) => { if (event.button !== 0) return; anchor.current = position(event); event.currentTarget.setPointerCapture?.(event.pointerId); }}
            onPointerMove={(event) => { if (anchor.current !== null) { const current = position(event); onRangeChange([Math.min(anchor.current, current), Math.max(anchor.current, current)]); } }}
            onPointerUp={(event) => {
                if (anchor.current !== null) { const current = position(event); if (Math.abs(current - anchor.current) < duration / 100) onRangeChange(null); }
                anchor.current = null;
            }}
            onPointerCancel={() => { anchor.current = null; }}
            onContextMenu={(event) => { event.preventDefault(); onRangeChange(null); }}
            onWheel={(event) => {
                const current = range ?? [start, end];
                const midpoint = (current[0] + current[1]) / 2;
                const half = Math.min(duration / 2, Math.max(duration / 100, (current[1] - current[0]) / 2 * (event.deltaY < 0 ? 0.8 : 1.25)));
                onRangeChange([Math.max(start, midpoint - half), Math.min(end, midpoint + half)]);
            }}>
            {timed.slice(-200).map((record, index) => {
                const width = Math.max(0.25, (record.ended_at! - record.started_at!) / duration * 100);
                const ttft = record.first_token_ms == null || !record.duration_ms ? 0 : Math.min(100, record.first_token_ms / record.duration_ms * 100);
                return <span key={record.id} className={`trajectory-timeline__span trajectory-timeline__span--${record.type.startsWith("tool") ? "tool" : "assistant"}`}
                    title={`${record.label}: ${durationLabel(record.duration_ms)}; first token ${durationLabel(record.first_token_ms)}`}
                    style={{ left: `${(record.started_at! - start) / duration * 100}%`, width: `${width}%`, top: `${10 + index % 3 * 12}px` }}>
                    {ttft > 0 && <span className="trajectory-timeline__ttft" style={{ width: `${ttft}%` }} />}
                </span>;
            })}
            {range && <span className="trajectory-timeline__selection" style={{ left: `${(range[0] - start) / duration * 100}%`, width: `${(range[1] - range[0]) / duration * 100}%` }} />}
        </div>
        <div className="trajectory-timeline__legend"><span>First token wait</span><span>Assistant decode</span><span>Tool</span></div>
        <div className="trajectory-timeline__ranges">
            <label>From <input type="range" aria-label="Time range start" min={start} max={end} step={duration / 1000}
                value={range?.[0] ?? start} onChange={(event) => onRangeChange([Math.min(Number(event.target.value), range?.[1] ?? end), range?.[1] ?? end])} /></label>
            <label>To <input type="range" aria-label="Time range end" min={start} max={end} step={duration / 1000}
                value={range?.[1] ?? end} onChange={(event) => onRangeChange([range?.[0] ?? start, Math.max(Number(event.target.value), range?.[0] ?? start)])} /></label>
        </div>
    </section>;
}
