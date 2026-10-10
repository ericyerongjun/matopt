import assert from "node:assert/strict";
import { test } from "node:test";
import { createTelemetryService } from "./telemetry.ts";

test("telemetry uses encoded conversation paths, bounded event pages, and forwards cancellation", async () => {
    const requests: Array<{ path: string; config: unknown }> = [];
    const client = { async get<T>(path: string, config?: unknown): Promise<{ data: T }> {
        requests.push({ path, config });
        return { data: { events: [], next_seq: 12, has_more: false } as T };
    } };
    const service = createTelemetryService(client);
    const signal = new AbortController().signal;
    const result = await service.events("chat/one", 12, 50, signal);
    assert.equal(result.next_seq, 12);
    assert.deepEqual(requests[0], { path: "/conversations/chat%2Fone/events", config: { params: { from_seq: 12, limit: 50 }, signal } });
    await service.events("one");
    assert.deepEqual(requests[1], { path: "/conversations/one/events", config: { params: { from_seq: -1, limit: 200 }, signal: undefined } });
    await service.trajectory("one", signal);
    await service.usage("one", signal);
    assert.equal(requests[2].path, "/conversations/one/trajectory");
    assert.equal(requests[3].path, "/conversations/one/usage");
    await service.request("chat/one", 7, signal);
    assert.deepEqual(requests[4], { path: "/conversations/chat%2Fone/requests/7", config: { signal } });
});
