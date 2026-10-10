import assert from "node:assert/strict";
import { test } from "node:test";
import { durationLabel, eventWindowReducer, matchesRecord, olderAnchor, requestSequence, tokenLabel, visibleRecords } from "./state.ts";
import type { SessionEvent, TrajectoryRecord } from "./types.ts";

const event = (seq: number): SessionEvent => ({ seq, time: 100 + seq, type: "assistant/message", data: { turn: 1 } });
const record = (seq: number): TrajectoryRecord => ({
    id: String(seq), seq, type: "tool/call", label: "Python", status: "interrupted",
    started_at: 10, ended_at: 12, duration_ms: 2000, first_token_ms: null,
    input: { code: "print(42)" }, output: "42", usage: null, source_event_seqs: [seq],
});

test("event pages deduplicate overlap, preserve order, and never move the live cursor back", () => {
    let state = eventWindowReducer({ conversationId: null, events: [], nextSeq: -1 }, { type: "reset", conversationId: "one" });
    state = eventWindowReducer(state, { type: "page", conversationId: "one", page: { events: [event(3), event(4)], next_seq: 4, has_more: false } });
    state = eventWindowReducer(state, { type: "page", conversationId: "one", page: { events: [event(1), event(2), event(3)], next_seq: 3, has_more: false } });
    assert.deepEqual(state.events.map((row) => row.seq), [1, 2, 3, 4]);
    assert.equal(state.nextSeq, 4);
});

test("a late event response from the previous session cannot contaminate the active session", () => {
    const state = { conversationId: "new", events: [], nextSeq: -1 };
    assert.equal(eventWindowReducer(state, { type: "page", conversationId: "old", page: { events: [event(1)], next_seq: 1, has_more: false } }), state);
});

test("the tail starts at 50 records and retains its prefix while new records arrive", () => {
    const records = Array.from({ length: 120 }, (_, index) => record(index));
    const initial = visibleRecords(records, null);
    assert.equal(initial.length, 50);
    assert.equal(initial[0].seq, 70);
    assert.equal(olderAnchor(records, initial[0].seq), 20);
    assert.equal(visibleRecords([...records, record(120)], 70).length, 51);
    assert.equal(visibleRecords([...records, record(120)], 70)[0].seq, 70);
});

test("record filters search recorded input/output and include spans crossing a time range", () => {
    assert.equal(matchesRecord(record(1), "PRINT", [11, 13]), true);
    assert.equal(matchesRecord(record(1), "42", null), true);
    assert.equal(matchesRecord(record(1), "", [13, 14]), false);
    assert.equal(matchesRecord({ ...record(1), started_at: null }, "", [10, 12]), false);
});

test("unknown usage/timing remain unknown and exact zero remains zero", () => {
    assert.equal(tokenLabel(undefined), "Unknown");
    assert.equal(tokenLabel(0), "0");
    assert.equal(durationLabel(null), "Unknown");
    assert.equal(durationLabel(250), "250 ms");
    assert.equal(durationLabel(1500), "1.50 s");
});

test("only assistant request references trigger a frozen-request lookup", () => {
    assert.equal(requestSequence({ ...record(1), type: "assistant/message", input: { request_seq: 0 } }), 0);
    assert.equal(requestSequence({ ...record(1), type: "assistant/attempt", input: { request_seq: 7 } }), 7);
    assert.equal(requestSequence({ ...record(1), input: { request_seq: 7 } }), null, "a tool argument is never interpreted as a request reference");
    assert.equal(requestSequence({ ...record(1), type: "assistant/message", input: { request_seq: -1 } }), null);
    assert.equal(requestSequence({ ...record(1), type: "assistant/message", input: { request_seq: "7" } }), null);
});
