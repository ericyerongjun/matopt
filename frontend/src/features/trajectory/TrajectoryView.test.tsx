import "../../../test/diffs-dom-env.mjs";
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { TrajectoryView } from "./TrajectoryView.tsx";
import { TooltipProvider } from "../../zcode/components/ui/tooltip.js";
import { ZCodeIntlProvider } from "../../zcode/i18n/IntlProvider.js";
import { UsageDetails } from "./UsageDetails.tsx";
import { TrajectoryInspector } from "./TrajectoryInspector.tsx";
import type { FrozenRequest, TrajectoryProjection, TrajectoryRecord, UsageProjection } from "./types.ts";

let root: Root | null = null;
let container: HTMLDivElement | null = null;
afterEach(() => { act(() => root?.unmount()); root = null; container?.remove(); container = null; });

function mount(element: React.ReactNode) {
    container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container);
    act(() => root?.render(<ZCodeIntlProvider initialLocale="en-US"><TooltipProvider>{element}</TooltipProvider></ZCodeIntlProvider>));
}
function click(text: string) {
    const button = [...document.querySelectorAll<HTMLButtonElement>("button")].find((row) => row.textContent === text);
    assert.ok(button, `button exists: ${text}`);
    act(() => { button.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, button: 0, ctrlKey: false })); button.click(); });
}
const records = (count: number): TrajectoryRecord[] => Array.from({ length: count }, (_, seq) => ({
    id: String(seq), seq, label: `Attempt ${seq}`, type: "assistant/attempt", status: "interrupted",
    started_at: 100 + seq, ended_at: 102 + seq, duration_ms: 2000, first_token_ms: 300,
    input: { prompt: "hello" }, output: "Partial output", usage: null, source_event_seqs: [seq],
}));
const projection = (count: number): TrajectoryProjection => ({
    turns: [{ turn: 1, status: "interrupted", started_at: 100, ended_at: 100 + count,
        steps: [{ step: 1, status: "interrupted", started_at: 100, ended_at: 100 + count, records: records(count) }] }],
    stats: { turns: 1, steps: 1, llm_ms: 2000, tool_ms: 0, ttft_ms: 300, ttft_steps: 1, decode_ms: 1700, decode_tokens: 0 },
});

test("trajectory reveals older rows on demand and keeps interrupted attempts inspectable", () => {
    const loaded: number[] = [];
    mount(<TrajectoryView conversationId="one" projection={projection(75)} events={[]} loading={false} error={null} running={false}
        onLoadOlder={(seq) => loaded.push(seq)} onRefresh={() => {}} />);
    assert.equal(document.querySelectorAll(".trajectory-record").length, 50);
    click("Load older records (25)");
    assert.equal(document.querySelectorAll(".trajectory-record").length, 75);
    assert.deepEqual(loaded, [25]);
    const row = document.querySelector<HTMLButtonElement>(".trajectory-record");
    act(() => row?.click());
    assert.match(document.querySelector(".trajectory-inspector")?.textContent ?? "", /interrupted/);
    assert.match(document.querySelector(".trajectory-inspector")?.textContent ?? "", /Partial output/);
    click("Timing");
    assert.match(document.querySelector(".trajectory-inspector")?.textContent ?? "", /300 ms/);
    assert.match(document.querySelector(".trajectory-inspector")?.textContent ?? "", /2.00 s/);
});

test("legacy trajectory explains unavailable evidence without fabricating records", () => {
    mount(<TrajectoryView conversationId="one" projection={{ ...projection(0), legacy: true }} events={[]} loading={false} error={null} running={false}
        onLoadOlder={() => {}} onRefresh={() => {}} />);
    assert.match(document.body.textContent ?? "", /predates the durable log/);
    assert.equal(document.querySelectorAll(".trajectory-record").length, 0);
});

test("turn prelude is labelled as input rather than a model step", () => {
    const data = projection(1);
    data.turns[0].steps[0].step = 0;
    data.turns[0].steps[0].records[0].type = "user/message";
    mount(<TrajectoryView conversationId="one" projection={data} events={[]} loading={false} error={null} running={false}
        onLoadOlder={() => {}} onRefresh={() => {}} />);
    assert.match(document.body.textContent ?? "", /Turn input/);
    assert.doesNotMatch(document.body.textContent ?? "", /Step 0/);
});

test("request Input fetches lazily, aborts on session/tab changes, and ignores stale envelopes", async () => {
    const pending: Array<{ id: string; seq: number; signal?: AbortSignal; resolve: (request: FrozenRequest) => void }> = [];
    const requestLoader = (id: string, seq: number, signal?: AbortSignal) => new Promise<FrozenRequest>((resolve) => pending.push({ id, seq, signal, resolve }));
    const record = { ...records(1)[0], input: { request_seq: 7, tools: [], config: { model: "test" } } };
    const render = (conversationId: string) => <TrajectoryInspector conversationId={conversationId} record={record}
        events={[]} onClose={() => {}} onLoadSource={() => {}} requestLoader={requestLoader} />;
    mount(render("one"));
    assert.equal(pending.length, 0, "opening Output does not fetch any growing request history");
    click("Input");
    assert.equal(pending.length, 1);
    assert.equal(pending[0].id, "one");
    assert.equal(pending[0].seq, 7);
    act(() => root?.render(<ZCodeIntlProvider initialLocale="en-US"><TooltipProvider>{render("two")}</TooltipProvider></ZCodeIntlProvider>));
    assert.equal(pending[0].signal?.aborted, true);
    assert.equal(pending.length, 2);
    pending[0].resolve({ messages: [{ content: "OLD SESSION INPUT" }], tools: [], config: {} });
    await Promise.resolve();
    assert.doesNotMatch(document.body.textContent ?? "", /OLD SESSION INPUT/);
    click("Output");
    assert.equal(pending[1].signal?.aborted, true, "leaving Input cancels its only in-flight envelope");
    click("Input");
    assert.equal(pending.length, 3, "reopening fetches rather than retaining a history cache");
});

test("usage distinguishes unknown provider reports, explicit estimates, and configured zero cost", () => {
    const usage: UsageProjection = { exact: null, turns: [{ turn: 1, exact: null }], cost: { amount: "0", currency: "USD" },
        context: null, estimated_context: { total_tokens: 42, method: "4 characters per token" },
        coverage: { attempts: 2, reported_attempts: 1, incomplete_turns: 1 } };
    mount(<UsageDetails usage={usage} onClose={() => {}} />);
    assert.match(document.body.textContent ?? "", /Unknown/);
    assert.match(document.body.textContent ?? "", /Estimated context: 42/);
    assert.match(document.body.textContent ?? "", /0 USD/);
    assert.match(document.body.textContent ?? "", /Reported attempts: 1 \/ 2/);
});
