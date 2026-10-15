import '../../test/jsdom-env.mjs';
import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import type { ActivityItem, AssistantContent, ChatMessage, Part, ResultBlock } from '../types/protocol.ts';
import { projectAssistantPresentation } from '../services/presentation.ts';
import { ActivityPanel, activityOutputUrl } from './WorkActivity.tsx';
import { ZCodeIntlProvider } from '../zcode/i18n/IntlProvider.js';
import { TooltipProvider } from '../zcode/components/ui/tooltip.js';

// jsdom has no canvas implementation; native Mermaid correctly uses its null fallback.
Object.defineProperty(window.HTMLCanvasElement.prototype, 'getContext', { configurable: true, value: () => null });
const { default: MessageList } = await import('./MessageList.tsx');

let root: Root | null = null;
let container: HTMLDivElement | null = null;
afterEach(() => { act(() => root?.unmount()); root = null; container?.remove(); container = null; });
function assistant(parts: Part[], status: AssistantContent['status'] = 'complete'): ChatMessage {
    return { id: 'a1', conversation_id: 'task', seq: 1, role: 'assistant', created_at: 1,
        content: { parts, model: 'deepseek-flash', provider: 'deepseek', reasoning: 'high', usage: {}, plan: [], stats: { llm_calls: 1, tool_calls: 0 }, status } };
}
function mount(messages: ChatMessage[], extra: { streaming?: boolean; onPickOption?: (text: string) => void; onOpenWork?: () => void; onQuote?: (quote: import('../services/quote.ts').QuoteRef) => void } = {}) {
    container = document.createElement('div'); document.body.append(container); root = createRoot(container);
    act(() => root?.render(<ZCodeIntlProvider initialLocale="en-US"><TooltipProvider>
        <MessageList messages={messages} conversationId="task" {...extra} />
    </TooltipProvider></ZCodeIntlProvider>));
    return container;
}
const tick = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
const partial = (extra: Partial<ResultBlock> = {}): ResultBlock => ({ id: 'a1:result:table', revision: 1, kind: 'markdown',
    status: 'provisional', content: 'A **useful result**.', evidence_refs: [], created_at: 1, ...extra });

test('native messages preserve user content, commands and real attachment links', () => {
    const message: ChatMessage = { id: 'u1', conversation_id: 'task', seq: 0, role: 'user', created_at: 1,
        content: { text: '/python Explain this\non two lines', attachments: [{ name: 'data.csv', path: 'data.csv', url: '/api/files/task/data.csv' }] } };
    const dom = mount([message]);
    assert.ok(dom.querySelector('.is-user'));
    assert.ok(dom.textContent?.includes('Python analysis'));
    assert.equal(dom.querySelector('.matopt-command-badge')?.getAttribute('title'), '/python');
    assert.ok(dom.textContent?.includes('Explain this\non two lines'));
    assert.equal(dom.querySelector('a')?.getAttribute('href'), '/api/files/task/data.csv');
    assert.ok(dom.textContent?.includes('data.csv'));
});

test('all internal work shares one collapsed disclosure that never reveals raw detail', () => {
    const secrets = ['PRIVATE_REASONING', 'PRIVATE_NARRATION', 'PRIVATE_CODE', 'PRIVATE_STDOUT', 'PRIVATE_VERIFY_TEXT', 'PRIVATE_REPORT', 'PRIVATE_AGENT_MESSAGE'];
    const dom = mount([assistant([
        { type: 'reasoning', text: secrets[0] },
        { type: 'text', text: secrets[1] },
        { type: 'tool_call', id: 'call-1', name: 'python', arguments: { code: secrets[2] }, status: 'completed', result: { ok: true, output: secrets[3], error: null, artifacts: [], meta: {} } },
        { type: 'text', phase: 'verify', text: secrets[4] },
        { type: 'verification', verdict: 'correct', report: secrets[5], round: 1 },
        { type: 'monitor', level: 'warning', summary: secrets[6] },
        { type: 'text', phase: 'revise', text: 'The final answer is 2.' },
    ])]);
    assert.equal(dom.querySelectorAll('[data-testid="work-disclosure"]').length, 1);
    assert.equal(dom.querySelectorAll('[data-testid="chat-reasoning-trigger"]').length, 0);
    const trigger = dom.querySelector<HTMLButtonElement>('[data-testid="work-disclosure"] button')!;
    assert.equal(trigger.textContent, 'Worked · 2 steps');
    assert.equal(trigger.getAttribute('aria-label'), 'Worked · 2 steps');
    assert.equal(trigger.getAttribute('aria-expanded'), 'false');
    assert.ok(dom.textContent?.includes('The final answer is 2.'));
    assert.ok(!dom.textContent?.includes('Computing'));
    act(() => trigger.click());
    assert.equal(trigger.getAttribute('aria-expanded'), 'true');
    assert.ok(document.getElementById(trigger.getAttribute('aria-controls')!));
    assert.ok(dom.textContent?.includes('Computing'));
    assert.ok(dom.textContent?.includes('Checking answer'));
    assert.ok(dom.textContent?.includes('Check passed · 1 round'));
    for (const secret of secrets) assert.ok(!dom.innerHTML.includes(secret), `${secret} must never enter the rendered tree`);
});

test('public final answer streams in the same markdown node and work stays collapsed', () => {
    const message = assistant([], 'streaming');
    const content = message.content as AssistantContent;
    content.presentation = 'final';
    content.work = { status: 'running', tool_count: 1, verification: { status: 'correct', rounds: 1 }, steps: [{ label: 'Computing', status: 'complete' }] };
    const dom = mount([message], { streaming: true });
    assert.equal(dom.querySelector('[data-testid="assistant-answer"]')?.textContent, '');
    assert.equal(dom.querySelector('[role="status"]')?.textContent, 'Working…');
    const update = (answer: string) => act(() => root?.render(<ZCodeIntlProvider initialLocale="en-US"><TooltipProvider>
        <MessageList messages={[{ ...message, content: { ...content, parts: [{ type: 'text', text: answer }] } }]} conversationId="task" streaming />
    </TooltipProvider></ZCodeIntlProvider>));
    update('The result is');
    const markdown = dom.querySelector('[data-testid="assistant-answer"]')?.firstElementChild;
    assert.ok(markdown);
    update('The result is **2**.');
    assert.equal(dom.querySelector('[data-testid="assistant-answer"]')?.firstElementChild, markdown);
    assert.equal(dom.querySelector('[data-testid="assistant-answer"] strong')?.textContent, '2');
    assert.equal(dom.querySelector('[data-testid="work-disclosure"] button')?.getAttribute('aria-expanded'), 'false');
    assert.equal(dom.querySelectorAll('[data-testid="assistant-answer"]').length, 1);
});

test('legacy in-progress narration never flashes into the answer body', () => {
    const dom = mount([assistant([
        { type: 'reasoning', text: 'PRIVATE_THOUGHT' }, { type: 'text', text: 'PRIVATE_INTERMEDIATE_RESPONSE' },
    ], 'streaming')], { streaming: true });
    assert.equal(dom.querySelector('[data-testid="assistant-answer"]')?.textContent, '');
    const trigger = dom.querySelector<HTMLButtonElement>('[data-testid="work-disclosure"] button')!;
    act(() => trigger.click());
    assert.ok(!dom.innerHTML.includes('PRIVATE_'));
    assert.equal(dom.querySelectorAll('[data-testid="work-disclosure"]').length, 1);
});

test('options and Activity stay usable while copy includes only the final answer', async () => {
    const picked: string[] = []; const opened: number[] = []; const copied: string[] = [];
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: async (text: string) => { copied.push(text); } } });
    const message = assistant([
        { type: 'text', text: 'Q4 corrected answer' }, { type: 'options', items: ['Show the derivation'] },
    ]);
    Object.assign(message.content, {
        presentation: 'final', earlier_drafts: ['The earlier full Q1–Q4 draft'],
        notice: 'This saved revision may update only part of the earlier answer.',
        work: { status: 'complete', tool_count: 1, verification: { status: 'correct', rounds: 1 }, steps: [{ label: 'Computing', status: 'complete' }] },
    });
    const dom = mount([message], { onPickOption: (text) => picked.push(text), onOpenWork: () => opened.push(1) });
    const buttons = () => Array.from(dom.querySelectorAll<HTMLButtonElement>('button'));
    assert.ok(!dom.textContent?.includes('The earlier full Q1–Q4 draft'));
    assert.ok(dom.textContent?.includes('This saved revision may update only part of the earlier answer.'));
    act(() => buttons().find((button) => button.textContent === 'Show the derivation')!.click());
    act(() => buttons().find((button) => button.textContent === 'Worked · 1 step')!.click());
    act(() => buttons().find((button) => button.textContent === 'Open activity')!.click());
    act(() => buttons().find((button) => button.textContent === 'Earlier draft')!.click());
    assert.ok(dom.textContent?.includes('unverified draft'));
    assert.ok(dom.textContent?.includes('The earlier full Q1–Q4 draft'));
    act(() => buttons().find((button) => button.textContent?.includes('Copy answer'))!.click());
    await tick();
    assert.deepEqual(picked, ['Show the derivation']); assert.deepEqual(opened, [1]); assert.deepEqual(copied, ['Q4 corrected answer']);
});

test('empty streaming turns expose a single safe status and stopped turns expose their notice', () => {
    const dom = mount([assistant([], 'streaming')], { streaming: true });
    assert.equal(dom.querySelector('[role="status"]')?.textContent, 'Working…');
    assert.equal(dom.querySelectorAll('[data-testid="work-disclosure"]').length, 1);
    act(() => root?.render(<ZCodeIntlProvider initialLocale="en-US"><TooltipProvider><MessageList messages={[assistant([], 'stopped')]} conversationId="task" /></TooltipProvider></ZCodeIntlProvider>));
    assert.ok(dom.textContent?.includes('Stopped.'));
    assert.ok(Array.from(dom.querySelectorAll<HTMLButtonElement>('button')).find((button) => button.textContent?.includes('Copy answer'))?.disabled);
});

test('interrupted work is labelled honestly and its expanded state is never presented as complete', () => {
    const message = assistant([], 'error');
    Object.assign(message.content, { presentation: 'final', work: { status: 'running', tool_count: 0, verification: { status: 'not_requested', rounds: 0 }, steps: [] } });
    const dom = mount([message]);
    const trigger = dom.querySelector<HTMLButtonElement>('[data-testid="work-disclosure"] button')!;
    assert.equal(trigger.getAttribute('aria-label'), 'Couldn’t finish · 0 steps');
    act(() => trigger.click());
    assert.ok(dom.textContent?.includes('The work could not be completed.'));
    assert.ok(!dom.textContent?.includes('Work complete.'));
    assert.ok(!dom.textContent?.includes('Working…'));
});

test('final provider text renders immediately without simulated post-completion streaming', () => {
    const message = assistant([], 'streaming');
    Object.assign(message.content, { presentation: 'final' });
    const dom = mount([message], { streaming: true });
    const final = 'The result is ready. ' + 'This paragraph belongs to the final response. '.repeat(90) + 'END_OF_FINAL';
    act(() => root?.render(<ZCodeIntlProvider initialLocale="en-US"><TooltipProvider>
        <MessageList messages={[{ ...message, content: { ...(message.content as AssistantContent), status: 'complete', parts: [{ type: 'text', text: final }, { type: 'options', items: ['Continue'] }] } }]} conversationId="task" />
    </TooltipProvider></ZCodeIntlProvider>));
    const response = dom.querySelector('[data-response-streaming]')!;
    assert.equal(response.getAttribute('aria-busy'), 'false');
    assert.ok(response.textContent?.includes('END_OF_FINAL'));
    assert.ok(dom.textContent?.includes('Continue'));
    assert.ok(dom.textContent?.includes('Copy answer'));
});

function activity(extra: Partial<ActivityItem> = {}): ActivityItem {
    return { id: 'python-1', parent_id: null, seq: 1, kind: 'tool', phase: 'work', label: 'Computing', purpose: 'Check the calculation.', status: 'running', started_at: 10, updated_at: 10, finished_at: null, attempt: 1, outputs: [], ...extra };
}

function liveWorkMessage(items: ActivityItem[]): ChatMessage {
    const message = assistant([], 'streaming');
    Object.assign(message.content, { work: { status: 'running', tool_count: items.length, verification: { status: 'not_requested', rounds: 0 }, steps: [], items, started_at: 10,
        current_progress: { id: 'progress-1', seq: 5, text: 'The PDF is ready. I’m checking the calculations.', created_at: 12, phase: 'work' } } });
    return message;
}

test('live commentary and parallel tool identities stay visible above a collapsed work record', () => {
    const first = activity({ progress: { completed: 2, total: 5, unit: 'pages' } });
    const second = activity({ id: 'python-2', seq: 2, started_at: 11 });
    const message = liveWorkMessage([first, second]);
    const dom = mount([message], { streaming: true });
    assert.ok(dom.textContent?.includes('The PDF is ready. I’m checking the calculations.'));
    assert.equal(dom.querySelectorAll('[aria-label="Active actions"] li').length, 2);
    assert.equal(dom.querySelector('progress')?.getAttribute('value'), '2');
    assert.equal(dom.querySelector('progress')?.getAttribute('max'), '5');
    const trigger = dom.querySelector<HTMLButtonElement>('[data-testid="work-disclosure"] button')!;
    assert.equal(trigger.getAttribute('aria-expanded'), 'false');
    const changed = liveWorkMessage([{ ...first, status: 'completed', finished_at: 12 }, second]);
    act(() => root?.render(<ZCodeIntlProvider initialLocale="en-US"><TooltipProvider><MessageList messages={[changed]} conversationId="task" streaming /></TooltipProvider></ZCodeIntlProvider>));
    assert.equal(dom.querySelectorAll('[aria-label="Active actions"] li').length, 1);
    assert.equal(dom.querySelector('[aria-label="Active actions"] li')?.getAttribute('data-activity-id'), 'python-2');
    assert.equal(dom.querySelectorAll('[aria-label="Active actions"] progress').length, 0);
});

test('manual activity expansion survives completion and duration comes from recorded timestamps', () => {
    const message = liveWorkMessage([activity()]);
    const dom = mount([message], { streaming: true });
    const trigger = dom.querySelector<HTMLButtonElement>('[data-testid="work-disclosure"] button')!;
    act(() => trigger.click());
    const finalContent: AssistantContent = { ...(message.content as AssistantContent), status: 'complete', presentation: 'final', parts: [{ type: 'text', text: 'The answer is 2.' }], work: { ...(message.content as AssistantContent).work!, status: 'complete', finished_at: 82, duration_ms: 72000, items: [activity({ status: 'completed', finished_at: 82 })] } };
    act(() => root?.render(<ZCodeIntlProvider initialLocale="en-US"><TooltipProvider><MessageList messages={[{ ...message, content: finalContent }]} conversationId="task" /></TooltipProvider></ZCodeIntlProvider>));
    assert.equal(trigger.getAttribute('aria-expanded'), 'true');
    assert.equal(trigger.getAttribute('aria-label'), 'Worked for 1m 12s · 1 action');
    assert.equal(dom.querySelector('[aria-label="Active actions"]'), null);
    assert.ok(dom.textContent?.includes('The answer is 2.'));
    assert.ok(!dom.textContent?.includes('The PDF is ready.'));
});

test('Activity groups agents and tools, preserves retry attempts, and filters failures and linked outputs', () => {
    const message = liveWorkMessage([
        activity({ id: 'agent', kind: 'agent', label: 'Checking calculations', purpose: 'Check Q4.', status: 'completed', finished_at: 12 }),
        activity({ id: 'failed', parent_id: 'agent', status: 'failed', summary: 'The calculation failed.', finished_at: 11 }),
        activity({ id: 'retry', parent_id: 'agent', attempt: 2, status: 'completed', summary: 'The table is ready.', finished_at: 12, outputs: [{ kind: 'file', label: 'Results table', path: 'results.csv' }] }),
        activity({ id: 'final', phase: 'final', kind: 'phase', label: 'Preparing final answer', purpose: 'Write the checked answer.' }),
    ]);
    const work = projectAssistantPresentation(message.content as AssistantContent, true).work!;
    const dom = mount([]);
    act(() => root?.render(<ActivityPanel turns={[{ id: message.id, work }]} conversationId="task" />));
    assert.equal(dom.querySelectorAll('.matopt-activity-phase').length, 2);
    assert.equal(dom.querySelector('[data-activity-id="agent"] [data-activity-id="retry"]')?.getAttribute('data-parent-id'), 'agent');
    assert.ok(dom.textContent?.includes('Attempt 2'));
    const clickFilter = (text: string) => act(() => Array.from(dom.querySelectorAll<HTMLButtonElement>('[aria-label="Filter activity"] button')).find((button) => button.textContent === text)!.click());
    clickFilter('Failures');
    assert.equal(dom.querySelectorAll('[data-activity-id]').length, 2);
    assert.equal(dom.querySelector('[data-activity-context]')?.getAttribute('data-activity-id'), 'agent');
    assert.equal(dom.querySelector('[data-activity-id="agent"] > details')?.hasAttribute('open'), true);
    assert.ok(dom.querySelector('[data-activity-id="agent"] [data-activity-id="failed"]'));
    clickFilter('Outputs');
    assert.equal(dom.querySelectorAll('[data-activity-id]').length, 2);
    assert.equal(dom.querySelector('a')?.getAttribute('href'), '/api/files/task/results.csv');
    assert.ok(dom.textContent?.includes('The table is ready.'));
    clickFilter('Active');
    assert.equal(dom.querySelector('[data-activity-id]')?.getAttribute('data-activity-id'), 'final');
});

test('activity output links reject executable URLs and escaping workspace paths', () => {
    assert.equal(activityOutputUrl({ kind: 'source', label: 'unsafe', url: 'javascript:alert(1)' }, 'task'), null);
    assert.equal(activityOutputUrl({ kind: 'file', label: 'unsafe', path: '../secret' }, 'task'), null);
    assert.equal(activityOutputUrl({ kind: 'file', label: 'safe', path: 'result table.csv' }, 'task'), '/api/files/task/result%20table.csv');
});

test('incoming deltas do not pull the reader away from earlier messages', () => {
    const message = assistant([{ type: 'text', text: 'First chunk' }], 'streaming');
    Object.assign(message.content, { presentation: 'final' });
    const dom = mount([message], { streaming: true });
    const scroller = dom.querySelector<HTMLElement>('[data-testid="chat-view"]')!;
    Object.defineProperties(scroller, { scrollHeight: { configurable: true, value: 1800 }, clientHeight: { configurable: true, value: 600 } });
    scroller.scrollTop = 100;
    act(() => scroller.dispatchEvent(new Event('scroll', { bubbles: true })));
    act(() => root?.render(<ZCodeIntlProvider initialLocale="en-US"><TooltipProvider><MessageList messages={[{ ...message, content: { ...(message.content as AssistantContent), parts: [{ type: 'text', text: 'First chunk, followed by more of the answer.' }] } }]} conversationId="task" streaming /></TooltipProvider></ZCodeIntlProvider>));
    assert.equal(scroller.scrollTop, 100);
    scroller.scrollTop = 1190;
    act(() => scroller.dispatchEvent(new Event('scroll', { bubbles: true })));
    act(() => root?.render(<ZCodeIntlProvider initialLocale="en-US"><TooltipProvider><MessageList messages={[{ ...message, content: { ...(message.content as AssistantContent), parts: [{ type: 'text', text: 'The completed answer.' }] } }]} conversationId="task" streaming /></TooltipProvider></ZCodeIntlProvider>));
    assert.equal(scroller.scrollTop, 1800);
});

test('published Markdown renders tables and math while explicitly requested source remains literal', async () => {
    const source = '```markdown\n**literal**\n| A | B |\n| --- | --- |\n| 1 | 2 |\n$x^2$\n<script>alert(1)</script>\n```';
    const message = liveWorkMessage([activity({ id: 'a1:tool:1', label: 'Computed table', status: 'completed' })]);
    (message.content as AssistantContent).parts = [
        { type: 'text', text: 'PRIVATE_WORKER' }, { type: 'reasoning', text: 'PRIVATE_REASONING' },
        { type: 'result_block', block: { ...partial({ content: '| Value | Result |\n| --- | --- |\n| $x$ | **2** |',
            evidence_refs: ['https://example.com/reference', 'results/table one.csv', 'a1:tool:1', 'javascript:alert(1)', '../secret', '//example.com/unsafe'] }),
            reasoning: 'PRIVATE_BLOCK_METADATA' } as ResultBlock },
        { type: 'result_block', block: partial({ id: 'a1:result:source', kind: 'source', content: source }) },
        { type: 'result_block', block: partial({ id: 'a1:result:withdrawn', status: 'withdrawn', content: 'WITHDRAWN_RESULT' }) },
    ];
    const opened: boolean[] = [];
    const dom = mount([message], { streaming: true, onOpenWork: () => opened.push(true) });
    await tick();
    const markdown = dom.querySelector('[data-result-block="a1:result:table"]')!;
    assert.equal(markdown.querySelectorAll('table').length, 1);
    assert.equal(markdown.querySelector('strong')?.textContent, '2');
    assert.ok(markdown.querySelector('.katex'));
    assert.equal(markdown.querySelector('[aria-label="Supporting evidence"] a')?.getAttribute('href'), 'https://example.com/reference');
    assert.ok(markdown.querySelector('a[href="/api/files/task/results/table%20one.csv"]'));
    assert.equal(markdown.querySelectorAll('[aria-label="Supporting evidence"] li').length, 3);
    act(() => markdown.querySelector<HTMLButtonElement>('[aria-label="Supporting evidence"] button')!.click());
    assert.deepEqual(opened, [true]);
    const literal = dom.querySelector('[data-result-block="a1:result:source"]')!;
    assert.equal(literal.querySelector('pre code')?.textContent, source);
    assert.equal(literal.querySelectorAll('table, strong, .katex, script, iframe').length, 0);
    assert.ok(dom.textContent?.includes('Partial result · not final'));
    assert.equal(dom.innerHTML.includes('PRIVATE_'), false);
    assert.equal(dom.textContent?.includes('WITHDRAWN_RESULT'), false);
});

test('steering after the active assistant preserves streaming and canonical final collapses superseded results', () => {
    const message = liveWorkMessage([activity()]);
    const content = message.content as AssistantContent;
    content.presentation = 'final';
    content.parts = [{ type: 'result_block', block: partial() }, { type: 'text', text: 'The final answer is' }];
    const steer: ChatMessage = { id: 'steer', conversation_id: 'task', seq: 3, role: 'user', created_at: 3,
        content: { text: 'Include the units.', attachments: [] } };
    const dom = mount([message, steer], { streaming: true });
    const answerRow = dom.querySelector('[data-message-id="a1"]')!;
    const response = answerRow.querySelector('[data-response-streaming="true"]');
    assert.ok(response, 'the assistant remains active even though a steering message follows it');
    assert.equal(response.getAttribute('aria-busy'), 'true');
    assert.equal(answerRow.textContent?.includes('Copy answer'), false);
    assert.ok(answerRow.querySelector('[aria-label="Active actions"]'));
    const final: ChatMessage = { ...message, content: { ...content, status: 'complete', parts: [
        { type: 'text', text: 'The final answer is **2 metres**.' },
        { type: 'result_block', block: partial({ revision: 2, status: 'superseded' }) },
    ] } };
    act(() => root?.render(<ZCodeIntlProvider initialLocale="en-US"><TooltipProvider><MessageList messages={[final, steer]} conversationId="task" /></TooltipProvider></ZCodeIntlProvider>));
    assert.equal(dom.querySelector('[data-message-id="a1"]'), answerRow);
    assert.equal(answerRow.querySelector('[data-response-streaming="false"]'), response);
    assert.equal(answerRow.querySelector('strong')?.textContent, '2 metres');
    assert.equal(answerRow.querySelector('[data-result-block]'), null);
    const earlier = [...answerRow.querySelectorAll<HTMLButtonElement>('button')].find((button) => button.textContent === 'Earlier results')!;
    assert.equal(earlier.getAttribute('aria-expanded'), 'false');
    act(() => earlier.click());
    assert.equal(answerRow.querySelector('[data-result-block]')?.getAttribute('data-result-status'), 'superseded');
    assert.ok(answerRow.textContent?.includes('Earlier result · superseded'));
    assert.ok(answerRow.textContent?.includes('Copy answer'));
});

test('interrupted partial output keeps its provisional label and can be copied without internal content', async () => {
    const copied: string[] = [];
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: async (text: string) => { copied.push(text); } } });
    const message = assistant([{ type: 'result_block', block: partial({ kind: 'source', content: '**literal source**' }) },
        { type: 'reasoning', text: 'PRIVATE_REASONING' }], 'stopped');
    const dom = mount([message]);
    assert.ok(dom.textContent?.includes('Partial result · not final'));
    assert.ok(dom.textContent?.includes('Stopped.'));
    assert.equal(dom.querySelector('strong'), null);
    act(() => [...dom.querySelectorAll<HTMLButtonElement>('button')].find((button) => button.textContent?.includes('Copy answer'))!.click());
    await tick();
    assert.deepEqual(copied, ['**literal source**']);
    assert.equal(dom.innerHTML.includes('PRIVATE_'), false);
});

test('scroll-to-bottom resumes following without forcing readers down on streaming updates', () => {
    const dom = mount([assistant([{ type: 'text', text: 'First answer' }])]);
    const viewport = dom.querySelector<HTMLElement>('[data-testid="chat-view"]')!;
    Object.defineProperties(viewport, { scrollHeight: { configurable: true, value: 1600 }, clientHeight: { configurable: true, value: 400 } });
    act(() => { viewport.scrollTop = 200; viewport.dispatchEvent(new Event('scroll', { bubbles: true })); });
    const update = (cid: string) => act(() => root?.render(<ZCodeIntlProvider initialLocale="en-US"><TooltipProvider>
        <MessageList messages={[assistant([{ type: 'text', text: 'Streaming update' }], 'streaming')]} conversationId={cid} streaming />
    </TooltipProvider></ZCodeIntlProvider>));
    update('task');
    assert.equal(viewport.scrollTop, 200);
    const button = dom.querySelector<HTMLButtonElement>('button[aria-label="Scroll to bottom"]')!;
    assert.ok(button);
    act(() => button.click());
    assert.equal(viewport.scrollTop, 1600);
    assert.equal(dom.querySelector('button[aria-label="Scroll to bottom"]'), null);
    act(() => { viewport.scrollTop = 200; viewport.dispatchEvent(new Event('scroll', { bubbles: true })); });
    update('another-task');
    assert.equal(viewport.scrollTop, 1600);
    assert.equal(dom.querySelector('button[aria-label="Scroll to bottom"]'), null);
});

test('quoted user messages display all literal quoted text and the question on reload', async () => {
    const { composeQuotedMessage } = await import('../services/quote.ts');
    const text = composeQuotedMessage([{ source: 'assistant', text: '<question>literal text</question>' }], 'Why is it true?');
    const dom = mount([{ id: 'u1', conversation_id: 'task', seq: 1, role: 'user', created_at: 1, content: { text, attachments: [] } }]);
    assert.equal(dom.querySelector('blockquote')?.textContent, '<question>literal text</question>');
    assert.equal(dom.querySelector('.matopt-user-content p')?.textContent, 'Why is it true?');
    assert.ok(!dom.textContent?.includes('"version"'));
});

test('a selected answer opens the quote menu while a plain right click keeps the browser menu', async () => {
    const quoted: unknown[] = [];
    const dom = mount([assistant([{ type: 'text', text: 'A passage to discuss.' }])], { onQuote: (quote) => quoted.push(quote) });
    const viewport = dom.querySelector<HTMLElement>('[data-testid="chat-view"]')!;
    const plain = new MouseEvent('contextmenu', { bubbles: true, cancelable: true });
    act(() => viewport.dispatchEvent(plain));
    assert.equal(plain.defaultPrevented, false);
    const passage = dom.querySelector('[data-testid="assistant-answer"] p')!;
    const range = document.createRange(); range.selectNodeContents(passage);
    const selection = window.getSelection()!; selection.removeAllRanges(); selection.addRange(range);
    const event = new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: 30, clientY: 40 });
    await act(async () => { passage.dispatchEvent(event); });
    assert.equal(event.defaultPrevented, true);
    const item = Array.from(document.querySelectorAll<HTMLElement>('[role="menuitem"]')).find((node) => node.textContent?.includes('Quote in reply'))!;
    assert.ok(item);
    await act(async () => { item.click(); });
    assert.deepEqual(quoted, [{ text: 'A passage to discuss.', source: 'assistant' }]);
    assert.equal(document.querySelector('[role="menu"]'), null);
    assert.equal(selection.toString(), '');
});

test('a selection crossing message boundaries never receives an incorrect quote attribution', () => {
    const dom = mount([assistant([{ type: 'text', text: 'First' }]), { ...assistant([{ type: 'text', text: 'Second' }]), id: 'a2' }], { onQuote: () => assert.fail('must not quote') });
    const paragraphs = dom.querySelectorAll('[data-testid="assistant-answer"] p');
    const range = document.createRange(); range.setStart(paragraphs[0].firstChild!, 0); range.setEnd(paragraphs[1].firstChild!, 3);
    const selection = window.getSelection()!; selection.removeAllRanges(); selection.addRange(range);
    const event = new MouseEvent('contextmenu', { bubbles: true, cancelable: true });
    act(() => paragraphs[0].dispatchEvent(event));
    assert.equal(event.defaultPrevented, false);
    assert.equal(document.querySelector('[role="menu"]'), null);
    selection.removeAllRanges();
});
