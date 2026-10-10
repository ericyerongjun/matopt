/** Isolated visual fixture. All data is simulated; no backend or model calls. */
import { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Activity, LucideProvider, PanelLeft, X } from 'lucide-react';
import { MatOptShell } from '../../src/features/shell/MatOptShell';
import { ChatSidebar } from '../../src/features/shell/ChatSidebar';
import { RunControls } from '../../src/features/shell/RunControls';
import { ActivityPanel } from '../../src/components/WorkActivity';
import MessageList from '../../src/components/MessageList';
import MathInput from '../../src/components/MathInput';
import { ZCodeIntlProvider } from '../../src/zcode/i18n/IntlProvider';
import { TooltipProvider } from '../../src/zcode/components/ui/tooltip';
import { projectAssistantPresentation } from '../../src/services/presentation';
import { applyTheme } from '../../src/utils/theme';
import { applyUiFontSizePx } from '../../src/utils/uiFontSize';
import type { ActivityItem, AssistantContent, ChatMessage } from '../../src/types/protocol';
import '../../src/zcode/styles.css';
import '../../src/matopt.css';
import '../../src/styles/design-tokens.css';
import 'katex/dist/katex.min.css';

const query = new URL(location.href).searchParams;
applyTheme(query.get('theme') === 'dark' ? 'dark' : 'light');
applyUiFontSizePx(Number(query.get('font')) || 14);
type State = 'running' | 'paused' | 'error';
const initialState: State = query.get('state') === 'paused' ? 'paused' : query.get('state') === 'error' ? 'error' : 'running';
const stamp = Date.now() / 1000;
const item = (id: string, patch: Partial<ActivityItem>): ActivityItem => ({ id, parent_id: null, seq: 1, kind: 'tool', phase: 'work', label: 'Read input', purpose: '', status: 'completed', started_at: stamp - 30, updated_at: stamp, finished_at: stamp - 18, attempt: 1, outputs: [], ...patch });
const result = [
  '### Model comparison',
  '',
  'The linear model has the highest validation accuracy. The margin is small; the final recommendation still needs the source definitions checked.',
  '',
  '| Model | Accuracy | Standard error |',
  '|---|---:|---:|',
  '| Logistic regression | 84.7% | 2.1% |',
  '| Decision tree | 79.3% | 3.4% |',
  '| Naive Bayes | 81.2% | 2.6% |',
  '',
  'With six validation cases, $\\hat p = \\frac{4}{6} \\approx 0.667$.',
  '',
  '```python',
  'correct = (predicted == actual).sum()',
  'accuracy = correct / len(actual)',
  'print(f"Validation accuracy: {accuracy:.3f}")',
  '```',
].join('\n');

function contentFor(state: State): AssistantContent {
  const liveStatus = state === 'error' ? 'failed' : state;
  const items = [
    item('read-phase', { kind: 'phase', label: 'Read the assignment', purpose: 'Extract the questions and relevant data.', summary: 'Six questions and the validation table are available.' }),
    item('read-document', { parent_id: 'read-phase', label: 'Read assignment.pdf', summary: 'Read 4 pages and extracted the validation cases.', outputs: [{ kind: 'file', label: 'Extracted document text', path: 'assignment.txt' }] }),
    item('calculate', { parent_id: 'read-phase', label: 'Calculate validation scores', summary: 'Compared three models across the same validation folds.', outputs: [{ kind: 'file', label: 'Comparison table', path: 'model-comparison.csv' }] }),
    item('check-phase', { kind: 'phase', phase: 'check', label: 'Check the comparison', purpose: 'Check the calculation and the assumptions behind each model.', status: liveStatus, started_at: stamp - 12, finished_at: state === 'error' ? stamp : null }),
    item('source-agent', { parent_id: 'check-phase', kind: 'agent', phase: 'check', label: 'Compare source definitions', purpose: 'Confirm that the assignment and reference use the same definitions.', status: liveStatus, started_at: stamp - 12, finished_at: state === 'error' ? stamp : null }),
    item('source-read', { parent_id: 'source-agent', phase: 'check', label: 'Read the model reference', purpose: 'Find the assumptions for this comparison.', status: liveStatus, started_at: stamp - 8, finished_at: state === 'error' ? stamp : null, attempt: 2, summary: state === 'error' ? 'The reference could not be reached. The available comparison is retained.' : undefined }),
    item('check-numbers', { parent_id: 'check-phase', phase: 'check', label: 'Recompute validation accuracy', purpose: 'Compare the predictions with the six validation labels.', status: state === 'error' ? 'completed' : state, started_at: stamp - 9, finished_at: state === 'error' ? stamp : null, progress: { completed: 4, total: 6, unit: 'cases' } }),
  ];
  return { parts: [{ type: 'result_block', block: { id: 'comparison', revision: 1, status: 'provisional', kind: 'markdown', content: result, evidence_refs: ['calculate'], created_at: stamp - 15 } }],
    provider: 'fixture', model: 'Simulated model', reasoning: 'off', usage: {}, status: state === 'error' ? 'error' : 'streaming', plan: [], stats: { llm_calls: 0, tool_calls: 0 },
    work: { status: state, tool_count: 4, steps: [], verification: { status: state === 'error' ? 'error' : 'not_requested', rounds: state === 'error' ? 1 : 0 }, started_at: stamp - 38, ...(state === 'error' ? { finished_at: stamp, duration_ms: 38000 } : {}), items,
      current_progress: { id: 'p3', seq: 9, created_at: stamp, phase: 'check', text: state === 'paused' ? 'The comparison is saved. Source checks will continue when you resume.' : 'The comparison is ready. I’m checking the definitions and validation cases.' },
      milestones: [{ id: 'p1', seq: 1, created_at: stamp - 30, phase: 'work', text: 'Read all six questions and extracted the validation data.' }, { id: 'p2', seq: 5, created_at: stamp - 15, phase: 'work', text: 'Published the comparison of three models.' }],
    },
  };
}

function Fixture() {
  const [state, setState] = useState<State>(initialState);
  const [sidebar, setSidebar] = useState(!matchMedia('(max-width: 900px)').matches);
  const [inspector, setInspector] = useState(query.get('panel') === 'activity');
  const content = contentFor(state);
  const live = state !== 'error';
  const messages: ChatMessage[] = [
    { id: 'user', conversation_id: 'fixture', seq: 1, role: 'user', content: { text: 'Compare these models and check the validation calculation.', attachments: [] }, created_at: stamp - 39 },
    { id: 'assistant', conversation_id: 'fixture', seq: 2, role: 'assistant', content, created_at: stamp - 38 },
  ];
  const work = projectAssistantPresentation(content, live).work!;
  const chat = { id: 'fixture', title: 'Model comparison', provider: null, model: null, reasoning: null, created_at: stamp, updated_at: stamp, meta: {}, running: live };
  return <MatOptShell sidebarOpen={sidebar} paneOpen={false} inspectorOpen={inspector} onCloseSidebar={() => setSidebar(false)} onClosePane={() => {}} onCloseInspector={() => setInspector(false)}
    sidebar={<ChatSidebar conversations={[chat]} activeId="fixture" pausedConversationId={state === 'paused' ? 'fixture' : null} loading={false} onNewChat={() => {}} onSelect={() => {}} onRename={async () => {}} onDelete={async () => {}} onFiles={() => {}} onClose={() => setSidebar(false)} />}
    header={<><div className="matopt-header-title">{!sidebar && <button className="matopt-chrome-button" aria-label="Open sidebar" onClick={() => setSidebar(true)}><PanelLeft /></button>}<div className="matopt-task-identity"><h1>Model comparison</h1><span>Simulated UI fixture</span></div></div><div className="matopt-header-actions"><button className="matopt-chrome-button" aria-label="Open activity" onClick={() => setInspector(!inspector)}><Activity /></button></div></>}
    conversation={<><div className="matopt-conversation-body"><MessageList messages={messages} conversationId={null} streaming={live} onOpenWork={() => setInspector(true)} /></div>
      <div className="matopt-composer-dock">{live && <RunControls paused={state === 'paused'} pauseRequested={false} reconnecting={false} status={null} onPause={() => setState('paused')} onResume={() => setState('running')} onStop={() => setState('error')} />}
        <MathInput onSubmit={() => false} onStop={() => setState('error')} loading={live} stopControl="external" showDisclaimer={false} />
        <p className="matopt-disclaimer">Simulated UI fixture · no backend or model calls</p></div></>}
    sidePane={null}
    inspector={<><header className="matopt-pane-header"><h2 data-pane-heading tabIndex={-1}>Activity</h2><button className="matopt-chrome-button" aria-label="Close activity" onClick={() => setInspector(false)}><X /></button></header><div className="matopt-activity-content"><ActivityPanel turns={[{ id: 'assistant', work }]} conversationId={null} /></div></>} />;
}

createRoot(document.getElementById('root')!).render(<ZCodeIntlProvider initialLocale="en-US"><TooltipProvider><LucideProvider strokeWidth={1.5}><Fixture /></LucideProvider></TooltipProvider></ZCodeIntlProvider>);
