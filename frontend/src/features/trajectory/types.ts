/** Read-only projections of the durable session log; times are epoch seconds. */
export interface SessionEvent {
    seq: number;
    time: number;
    type: string;
    data: Record<string, unknown>;
    surfaceOp?: unknown;
    sourceEventSeqs?: number[];
}

export interface EventPage {
    events: SessionEvent[];
    next_seq: number;
    has_more: boolean;
}

/** Fetched only for the selected assistant record's Input inspector. */
export interface FrozenRequest {
    messages: Record<string, unknown>[];
    tools: unknown[] | null;
    config: Record<string, unknown>;
}

export interface ExactTokenUsage {
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
    uncached_input_tokens?: number;
    cached_tokens?: number;
    cache_read_tokens?: number;
    cache_write_tokens?: number;
    reasoning_tokens?: number;
}

export interface TrajectoryRecord {
    id: string;
    seq: number;
    type: string;
    label: string;
    status: string;
    started_at: number | null;
    ended_at: number | null;
    duration_ms: number | null;
    first_token_ms: number | null;
    input: unknown;
    output: unknown;
    usage: ExactTokenUsage | null;
    source_event_seqs: number[];
}

export interface TrajectoryStep {
    step: number;
    status: string;
    started_at: number | null;
    ended_at: number | null;
    records: TrajectoryRecord[];
}

export interface TrajectoryTurn {
    turn: number;
    status: string;
    started_at: number | null;
    ended_at: number | null;
    steps: TrajectoryStep[];
}

export interface TrajectoryProjection {
    turns: TrajectoryTurn[];
    stats: {
        turns: number;
        steps: number;
        llm_ms: number;
        tool_ms: number;
        ttft_ms: number;
        ttft_steps: number;
        decode_ms: number;
        decode_tokens: number;
    };
    legacy?: boolean;
}

export interface UsageProjection {
    exact: ExactTokenUsage | null;
    turns: Array<{ turn: number; exact: ExactTokenUsage | null }>;
    estimated_context: { total_tokens: number; method: string } | null;
    cost: { amount: number | string; currency: string } | null;
    context: { window: number | null; pressure_tokens: number | null; projected_tokens: number | null } | null;
    coverage?: { attempts: number; reported_attempts: number; incomplete_turns: number };
}
