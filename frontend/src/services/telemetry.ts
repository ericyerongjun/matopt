import apiClient from "./api.ts";
import type { EventPage, FrozenRequest, TrajectoryProjection, UsageProjection } from "../features/trajectory/types.ts";
import { EVENT_PAGE_SIZE } from "../features/trajectory/state.ts";

interface ReadClient {
    get<T>(path: string, config?: { params?: Record<string, number>; signal?: AbortSignal }): Promise<{ data: T }>;
}

/** A small injectable service keeps pagination and cancellation testable. */
export function createTelemetryService(client: ReadClient = apiClient) {
    const base = (id: string) => `/conversations/${encodeURIComponent(id)}`;
    return {
        async trajectory(id: string, signal?: AbortSignal): Promise<TrajectoryProjection> {
            return (await client.get<TrajectoryProjection>(`${base(id)}/trajectory`, { signal })).data;
        },
        async usage(id: string, signal?: AbortSignal): Promise<UsageProjection> {
            return (await client.get<UsageProjection>(`${base(id)}/usage`, { signal })).data;
        },
        async request(id: string, seq: number, signal?: AbortSignal): Promise<FrozenRequest> {
            return (await client.get<FrozenRequest>(`${base(id)}/requests/${seq}`, { signal })).data;
        },
        async events(id: string, fromSeq = -1, limit = EVENT_PAGE_SIZE, signal?: AbortSignal): Promise<EventPage> {
            return (await client.get<EventPage>(`${base(id)}/events`, {
                params: { from_seq: fromSeq, limit }, signal,
            })).data;
        },
    };
}

export const telemetryService = createTelemetryService();
