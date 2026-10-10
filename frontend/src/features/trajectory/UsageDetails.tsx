import { tokenLabel } from "./state.ts";
import type { ExactTokenUsage, UsageProjection } from "./types.ts";
import { Button } from "./controls.tsx";
import "./trajectory.css";

export function UsageNumbers({ usage }: { usage: ExactTokenUsage | null }) {
    const buckets: Array<[string, number | undefined]> = [
        ["Input", usage?.prompt_tokens], ["Output", usage?.completion_tokens],
        ["Total", usage?.total_tokens],
        ["Uncached input", usage?.uncached_input_tokens],
        ["Cache read", usage?.cache_read_tokens ?? usage?.cached_tokens],
        ["Cache write", usage?.cache_write_tokens], ["Reasoning (within output)", usage?.reasoning_tokens],
    ];
    return <dl className="telemetry-numbers">{buckets.map(([label, value]) => (
        <div key={label}><dt>{label}</dt><dd>{tokenLabel(value)}</dd></div>
    ))}</dl>;
}

export function UsageDetails({ usage, onClose }: { usage: UsageProjection | null; onClose: () => void }) {
    return <section className="usage-details" aria-label="Session usage">
        <div className="telemetry-heading"><strong>Session usage</strong><Button size="sm" onClick={onClose}>Close</Button></div>
        <p className="telemetry-caption">Exact provider reports. Missing usage stays unknown; reasoning is included in output.</p>
        <UsageNumbers usage={usage?.exact ?? null} />
        {usage?.coverage && <p className="telemetry-caption">
            Reported attempts: {usage.coverage.reported_attempts} / {usage.coverage.attempts}
            {usage.coverage.incomplete_turns > 0 && ` · ${usage.coverage.incomplete_turns} turns with incomplete usage`}
        </p>}
        <div className="usage-details__secondary">
            <div><strong>Cost</strong><p>{usage?.cost ? `${usage.cost.amount} ${usage.cost.currency}` : "Unavailable"}</p></div>
            <div><strong>Context</strong><p>Window: {tokenLabel(usage?.context?.window)} · Reported input: {tokenLabel(usage?.context?.pressure_tokens)}</p>
                <p>Estimated next request: {tokenLabel(usage?.context?.projected_tokens)}</p>
                {usage?.estimated_context && <p>Estimated context: {tokenLabel(usage.estimated_context.total_tokens)} ({usage.estimated_context.method})</p>}
            </div>
        </div>
        {(usage?.turns.length ?? 0) > 0 && <details>
            <summary>Usage by turn</summary>
            <table className="telemetry-table"><thead><tr><th>Turn</th><th>Input</th><th>Output</th><th>Total</th></tr></thead>
                <tbody>{usage!.turns.map((turn) => <tr key={turn.turn}><th>{turn.turn}</th>
                    <td>{tokenLabel(turn.exact?.prompt_tokens)}</td><td>{tokenLabel(turn.exact?.completion_tokens)}</td><td>{tokenLabel(turn.exact?.total_tokens)}</td>
                </tr>)}</tbody></table>
        </details>}
    </section>;
}
