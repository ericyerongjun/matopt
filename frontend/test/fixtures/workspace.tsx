// Isolated, simulated visual fixture. No backend mutations or model calls.
import { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Activity, Layers, PanelLeft, X } from 'lucide-react';
import { MatOptShell } from '../../src/features/shell/MatOptShell';
import { WorkspacePanel, ResultWorkspace, type WorkspaceView } from '../../src/features/shell/WorkspacePanel';
import { RunControls } from '../../src/features/shell/RunControls';
import { WorkDisclosure, ActivityPanel } from '../../src/components/WorkActivity';
import MathInput from '../../src/components/MathInput';
import MarkdownRenderer from '../../src/components/MarkdownRenderer';
import { UiWorkspaceProvider } from '../../src/features/intelligent-ui/UiWorkspace';
import { UISurfaceView } from '../../src/features/intelligent-ui/UISurfaceView';
import { ZCodeIntlProvider } from '../../src/zcode/i18n/IntlProvider';
import { TooltipProvider } from '../../src/zcode/components/ui/tooltip';
import { applyTheme } from '../../src/utils/theme';
import { bootUiFontSize } from '../../src/utils/uiFontSize';
import type { PresentedWork } from '../../src/services/presentation';
import type { UISurface } from '../../src/types/ui';
import '../../src/zcode/styles.css'; import '../../src/matopt.css'; import '../../src/styles/design-tokens.css'; import 'katex/dist/katex.min.css';
applyTheme(new URL(location.href).searchParams.get('theme') === 'dark' ? 'dark' : 'light');
bootUiFontSize();
const structuredTable = new URL(location.href).searchParams.get('table') === 'native';
const surface: UISurface = { version: 1, id: 'estimates', revision: 1, kind: 'native', title: 'Model comparison', fallback: 'Compare the cross-validation estimates.', status: 'committed', state: { folds: 5 }, actions: [], nodes: [
  { id: 'intro', type: 'markdown', props: { text: '### Cross-validation results\nThe linear model is the strongest baseline.' + (structuredTable ? '' : '\n\n| Model | Accuracy | Standard error |\n|---|---:|---:|\n| Logistic regression | 0.847 | 0.021 |\n| Decision tree | 0.793 | 0.034 |\n| Naive Bayes | 0.812 | 0.026 |') } },
  ...(structuredTable ? [{ id: 'comparison-table', type: 'table' as const, props: { label: 'Cross-validation results', columns: [{ key: 'model', label: 'Model' }, { key: 'accuracy', label: 'Accuracy' }, { key: 'error', label: 'Standard error' }], rows: [['Logistic regression', 0.847, 0.021], ['Decision tree', 0.793, 0.034], ['Naive Bayes', 0.812, 0.026]] } }] : []),
  { id: 'folds', type: 'number', props: { label: 'Validation folds', stateKey: 'folds', min: 2, max: 10 } },
] };
const stamp = Date.now() / 1000;
const base: PresentedWork = { status: 'running', toolCount: 6, verification: { status: 'not_requested', rounds: 0 }, steps: [], startedAt: stamp - 28,
  progress: { id: 'p3', seq: 9, created_at: stamp, phase: 'check', text: 'The comparison is ready. I’m checking the assumptions and finishing the explanation.' },
  milestones: [{ id: 'p1', seq: 1, created_at: stamp - 25, phase: 'work', text: 'Read the assignment and extracted all six questions.' }, { id: 'p2', seq: 5, created_at: stamp - 10, phase: 'work', text: 'Completed the model comparison with five validation folds.' }],
  items: ['Checking calculations', 'Comparing source definitions', 'Checking the result table', 'Reviewing boundary cases', 'Preparing references', 'Writing the explanation'].map((label, i) => ({ id: `tool-${i}`, parent_id: null, seq: i, kind: 'tool', phase: 'check', label, purpose: 'Check the reported result against the task evidence.', status: 'running', started_at: stamp - 10, updated_at: stamp, finished_at: null, attempt: 1, outputs: [] })) };
function Fixture() {
  const [sidebar, setSidebar] = useState(!matchMedia('(max-width: 900px)').matches); const [pane, setPane] = useState(false); const [inspector, setInspector] = useState(false); const [view, setView] = useState<WorkspaceView>('results'); const [paused, setPaused] = useState(false); const [done, setDone] = useState(false);
  const work: PresentedWork = { ...base, status: done ? 'complete' : paused ? 'paused' : 'running', ...(done ? { durationMs: 31000 } : {}), items: base.items.map((item) => ({ ...item, status: done ? 'completed' : paused ? 'paused' : 'running', finished_at: done ? stamp + 3 : null })) };
  return <UiWorkspaceProvider conversationId={null} capabilities={{ version: 1, native: true, mini_apps: false, node_types: ['markdown', 'number', 'table'] }} onCanvasOpen={() => { setPane(true); setView('results'); setInspector(false); }}>
  <MatOptShell sidebarOpen={sidebar} paneOpen={pane} inspectorOpen={inspector} onCloseSidebar={() => setSidebar(false)} onClosePane={() => setPane(false)} onCloseInspector={() => setInspector(false)}
    sidebar={<><div className="matopt-sidebar-top"><b className="matopt-wordmark">MatOpt</b><button className="matopt-chrome-button" aria-label="Collapse sidebar" onClick={() => setSidebar(false)}><PanelLeft /></button></div><div className="matopt-history"><h2>Visual test fixture</h2><p style={{padding:14}}>Assignment analysis</p><p style={{padding:14,color:'var(--color-foreground-subtle)'}}>Simulated activity</p></div></>}
    header={<><div className="matopt-header-title">{!sidebar && <button className="matopt-chrome-button" aria-label="Open sidebar" onClick={() => setSidebar(true)}><PanelLeft /></button>}<div className="matopt-task-identity"><h1>Assignment analysis</h1><span><i className={!done ? 'is-active' : ''} />{done ? 'Workspace' : paused ? 'Paused' : 'In progress'}</span></div></div><div className="matopt-header-actions"><button className="matopt-chrome-button" aria-label="Open workspace" onClick={() => setPane(!pane)}><Layers /></button><button className="matopt-chrome-button" aria-label="Open activity" onClick={() => setInspector(!inspector)}><Activity /></button></div></>}
    conversation={<><div className="matopt-conversation-body"><div data-testid="chat-view" style={{overflow:'auto',width:'100%'}}><div className="matopt-message-column" style={{margin:'0 auto'}}><p style={{background:'var(--color-surface)',padding:'12px 18px',borderRadius:18,marginBottom:28}}>Check my model comparison and explain which assumptions matter.</p><WorkDisclosure work={work} onOpenWork={() => setInspector(true)} /><div className="partial-result"><p className="partial-result-status">{done ? 'Result' : 'Available so far'}</p><MarkdownRenderer content="The linear model gives the strongest baseline. The difference is modest, so the final recommendation also considers variance across folds." /></div><UISurfaceView surface={surface} /></div></div></div><div className="matopt-composer-dock">{!done && <RunControls paused={paused} pauseRequested={false} reconnecting={false} status={null} onPause={() => setPaused(true)} onResume={() => setPaused(false)} onStop={() => setDone(true)} />}<MathInput stopControl="external" showDisclaimer={false} onSubmit={() => false} onStop={() => setDone(true)} loading={!done} /><p className="matopt-disclaimer">Simulated UI fixture · no model or backend actions</p></div></>}
    sidePane={<WorkspacePanel view={view} onViewChange={setView} onClose={() => setPane(false)} results={<ResultWorkspace surfaces={[surface]} artifacts={[]} />} files={<div className="file-preview__document"><h2>assignment.pdf</h2><p>Fixture file preview. The result remains mounted in the Results tab.</p></div>} resultCount={1} fileCount={1} />}
    inspector={<><header className="matopt-pane-header"><h2 data-pane-heading tabIndex={-1}>Activity</h2><button className="matopt-chrome-button" aria-label="Close activity" onClick={() => setInspector(false)}><X /></button></header><div style={{overflow:'auto'}}><ActivityPanel turns={[{id:'one',work}]} conversationId={null} /></div></>} />
  </UiWorkspaceProvider>;
}
createRoot(document.getElementById('root')!).render(<ZCodeIntlProvider initialLocale="en-US"><TooltipProvider><Fixture /></TooltipProvider></ZCodeIntlProvider>);
