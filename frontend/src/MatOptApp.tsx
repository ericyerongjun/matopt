/** MatOpt conversation workspace, using the licensed ZCode component primitives. */
import { useCallback, useEffect, useRef, useState } from "react";
import { Activity, Calculator, ChartNoAxesCombined, Download, FileCode2, Layers, Monitor, Moon, PanelLeft, RotateCcw, Settings, ShieldCheck, SlidersHorizontal, Smile, SquareFunction, SquarePen, Sun, Type, X, CaseSensitive } from "lucide-react";
import { Button } from "@/components/ui/button.js";
import { DropdownMenu, DropdownMenuCheckboxItem, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuRadioGroup, DropdownMenuRadioItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu.js";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs.js";
import { useChat } from "./hooks/useChat";
import { useConversations } from "./hooks/useConversations";
import { useLlmSelection } from "./hooks/useLlmSelection";
import { useSkills } from "./hooks/useSkills";
import { useWorkspaceFiles } from "./hooks/useWorkspaceFiles";
import { exportConversation, restartKernel } from "./services/conversations";
import { uploadFiles } from "./services/files";
import { loadTheme, setTheme as persistTheme, watchSystemTheme, type Theme } from "./utils/theme";
import { DEFAULT_UI_FONT_SIZE_PX, loadUiFontSizePx, MAX_UI_FONT_SIZE_PX, MIN_UI_FONT_SIZE_PX, setUiFontSizePx } from "./utils/uiFontSize";
import { loadFontPreferences, setFontPreferences } from "./utils/fontPreferences";
import { loadChatMode, setChatMode as persistChatMode } from "./utils/chatMode";
import { ModeSwitch } from "./components/ModeSwitch";
import { FontSettingsDialog } from "./features/settings/FontSettingsDialog";
import { SettingsDialog } from "./features/settings/SettingsDialog";
import { ChangesView } from "./features/changes/ChangesView";
import { AgentsPanel } from "./features/runs/AgentsPanel";
import { JobsPanel } from "./features/runs/JobsPanel";
import { ApprovalCard } from "./features/runs/ApprovalCard";
import { runsService } from "./services/agents";
import { telemetryService } from "./services/telemetry.ts";
import { projectAssistantPresentation } from "./services/presentation.ts";
import type { UsageProjection } from "./features/trajectory/types.ts";
import { UsageDetails } from "./features/trajectory/UsageDetails";
import { WorkspaceWorkbench } from "./features/workbench/WorkspaceWorkbench";
import { UiWorkspaceProvider } from "./features/intelligent-ui/index.ts";
import { MatOptShell } from "./features/shell/MatOptShell";
import { ChatSidebar } from "./features/shell/ChatSidebar";
import { WorkspacePanel, ResultWorkspace, type WorkspaceView } from "./features/shell/WorkspacePanel";
import { RunControls } from "./features/shell/RunControls";
import { isAssistantContent, textFromAssistant } from "./types/protocol";
import { AvatarPanel } from "./features/avatar/AvatarPanel";
import { LanguagePicker } from "./features/voice/LanguagePicker";
import { speak, stopSpeaking } from "./features/voice/playback";
import { useVoiceStore } from "./features/voice/voiceStore";
import MathInput from "./components/MathInput";
import MessageList from "./components/MessageList";
import { ActivityPanel } from "./components/WorkActivity";
import ModelPicker from "./components/ModelPicker";
import { TASK_STARTERS, taskStarterDraft } from "./services/taskStarters";
import type { QuoteRef, QuoteRequest } from "./services/quote";
import { MatOptMark } from "./components/MatOptMark.tsx";


/** Usage is loaded only while its tab is open; normal UI never downloads raw diagnostics. */
function SessionUsage({ conversationId, running, revision, onClose }: {
  conversationId: string | null; running: boolean; revision: number; onClose: () => void;
}) {
  const [usage, setUsage] = useState<UsageProjection | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    setError(false);
    if (!conversationId) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = async () => {
      try {
        const result = await telemetryService.usage(conversationId, controller.signal);
        if (!controller.signal.aborted) { setUsage(result); setError(false); }
      } catch {
        if (!controller.signal.aborted) setError(true);
      } finally {
        if (running && !controller.signal.aborted) timer = setTimeout(load, 2000);
      }
    };
    void load();
    return () => { controller.abort(); if (timer !== undefined) clearTimeout(timer); };
  }, [conversationId, running, revision]);
  return <>{error && <p className="px-4 py-2 text-ui-sm text-foreground-subtle" role="status">Usage is temporarily unavailable.</p>}<UsageDetails usage={usage} onClose={onClose} /></>;
}

export default function MatOptApp() {
  const llm = useLlmSelection();
  const convos = useConversations();
  const files = useWorkspaceFiles(convos.activeId);
  const skillCatalog = useSkills();
  const voice = useVoiceStore();
  const [sidebarVisible, setSidebarVisible] = useState(() => !window.matchMedia("(max-width: 900px)").matches);
  const [paneVisible, setPaneVisible] = useState(false);
  const [view, setView] = useState<WorkspaceView>("results");
  const [activityVisible, setActivityVisible] = useState(false);
  const [activityView, setActivityView] = useState("timeline");
  const [uiFontSize, setUiFontSize] = useState(loadUiFontSizePx);
  const [fonts, setFonts] = useState(loadFontPreferences);
  const [fontsOpen, setFontsOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [chatMode, setChatMode] = useState(loadChatMode);
  const [changesTurn, setChangesTurn] = useState<number | null>(null);
  const [theme, setTheme] = useState<Theme>(loadTheme);
  const [draftPreset, setDraftPreset] = useState<{ id: number; text: string; skills?: string[] } | undefined>();
  const [quoteRequest, setQuoteRequest] = useState<(QuoteRequest & { conversationId: string | null }) | undefined>();
  const quotePassage = (quote: QuoteRef) => setQuoteRequest({ ...quote, id: crypto.randomUUID(), conversationId: convos.activeId });
  const chat = useChat({ activeId: convos.activeId, messages: convos.messages, setMessages: convos.setMessages,
    setActiveId: convos.setActiveId, createConversation: convos.create, refreshList: convos.refreshList,
    provider: llm.provider, model: llm.model, reasoning: llm.reasoning, verify: llm.verify, language: voice.language, mode: chatMode,
    onArtifacts: () => { void files.refresh(); } });
  const attachedRun = Boolean(chat.streaming && chat.overlay?.cid === convos.activeId);
  const messages = attachedRun && chat.overlay ? chat.overlay.messages : convos.messages;
  const error = convos.error || chat.streamError || llm.error;
  const inputDisabled = llm.loading || convos.loadingThread || (chat.streaming && !attachedRun);
  const closePane = useCallback(() => setPaneVisible(false), []);
  const dismissSidebar = useCallback(() => setSidebarVisible(false), []);
  const openPane = useCallback((next: WorkspaceView) => {
    setView(next); setPaneVisible(true); setActivityVisible(false);
    if (window.matchMedia("(max-width: 1023px)").matches) setSidebarVisible(false);
  }, []);
  const openCanvas = useCallback(() => openPane("results"), [openPane]);
  const openActivity = useCallback(() => { setActivityView("timeline"); setActivityVisible(true); }, []);
  const closeActivity = useCallback(() => setActivityVisible(false), []);
  const openFiles = () => { openPane("files"); void files.refresh(); };
  const selectChat = (id: string) => {
    if (window.matchMedia("(max-width: 900px)").matches) setSidebarVisible(false);
    if (id !== convos.activeId) { setDraftPreset(undefined); convos.select(id); }
  };
  const newChat = useCallback(() => {
    setDraftPreset(undefined); void chat.abandon(); convos.newChat(); setPaneVisible(false); setActivityVisible(false);
    if (window.matchMedia("(max-width: 900px)").matches) setSidebarVisible(false);
  }, [chat.abandon, convos.newChat]);
  const handleSend = useCallback((text: string, attached: File[] = [], skillNames: string[] = [], toolNames: string[] = []) => {
    if (inputDisabled) return false;
    return chat.sendMessage(text, attached, skillNames, toolNames);
  }, [chat.sendMessage, inputDisabled]);
  const handleExplain = useCallback((text: string) => { void handleSend(text); }, [handleSend]);
  // Hands-free conversation sends what was said; otherwise the transcript is inserted for review.
  const handleVoiceTranscript = useCallback((text: string) => {
    if (!useVoiceStore.getState().conversation) return false;
    void handleSend(text);
    return true;
  }, [handleSend]);
  // The avatar thinks while this chat's run works, smiles when it ends, and reads the answer if asked.
  const wasRunning = useRef(false);
  useEffect(() => {
    const store = useVoiceStore.getState();
    if (attachedRun) { stopSpeaking(); store.setMood("thinking"); wasRunning.current = true; return; }
    if (!wasRunning.current) return;
    wasRunning.current = false;
    const last = [...messages].reverse().find((message) => message.role === "assistant");
    const content = last && isAssistantContent(last.content) ? last.content : null;
    store.setMood(content?.status === "complete" ? "happy" : "idle");
    const settle = setTimeout(() => { if (useVoiceStore.getState().mood === "happy") useVoiceStore.getState().setMood("idle"); }, 2500);
    if (content?.status === "complete" && store.speakReplies) {
      const text = textFromAssistant(content);
      if (text.trim()) void speak(text).catch(() => undefined);
    }
    return () => clearTimeout(settle);
  }, [attachedRun, messages]);
  const handleUpload = async (list: File[]) => { if (!convos.activeId) return; await uploadFiles(convos.activeId, list); await files.refresh(); };
  useEffect(() => watchSystemTheme(loadTheme), []);
  useEffect(() => { setPaneVisible(false); setActivityVisible(false); setActivityView("timeline"); setDraftPreset(undefined); setQuoteRequest(undefined); setChangesTurn(null); }, [convos.activeId]);
  const openChanges = useCallback((turn: number) => { setChangesTurn(turn); openPane("changes"); }, [openPane]);
  const decideApproval = useCallback(async (approvalId: string, decision: "allow" | "deny") => {
    if (!chat.runId) throw new Error("This run is no longer active.");
    await runsService.decide(chat.runId, approvalId, decision);
  }, [chat.runId]);
  useEffect(() => {
    const query = window.matchMedia("(max-width: 1023px)");
    const changed = () => { if (query.matches && paneVisible) setSidebarVisible(false); };
    query.addEventListener("change", changed);
    return () => query.removeEventListener("change", changed);
  }, [paneVisible]);
  useEffect(() => {
    const handleKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "n") { event.preventDefault(); newChat(); }
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [newChat]);
  useEffect(() => { try { localStorage.removeItem("matopt.webSearch"); } catch { /* Storage is optional. */ } }, []);

  const settings = <DropdownMenu><DropdownMenuTrigger asChild><Button type="button" variant="ghost" size="icon-md" aria-label="Workspace settings" title="Settings"><Settings className="size-5" /></Button></DropdownMenuTrigger>
    <DropdownMenuContent align="end" className="matopt-settings-menu w-64">
      <DropdownMenuCheckboxItem checked={llm.verify} disabled={chat.streaming} onCheckedChange={llm.selectVerify}><ShieldCheck className="size-4" />Check results</DropdownMenuCheckboxItem>
      {convos.activeId && <><DropdownMenuSeparator />
        <DropdownMenuItem onSelect={() => void exportConversation(convos.activeId!, "markdown")} disabled={!messages.length}><Download className="size-5" />Export markdown</DropdownMenuItem>
        <DropdownMenuItem onSelect={() => void exportConversation(convos.activeId!, "ipynb")} disabled={!messages.length}><FileCode2 className="size-5" />Export notebook</DropdownMenuItem>
        <DropdownMenuItem onSelect={() => void restartKernel(convos.activeId!)} disabled={chat.streaming}><RotateCcw className="size-5" />Restart analysis</DropdownMenuItem></>}
      <DropdownMenuSeparator /><DropdownMenuLabel>Text size · {Math.round(uiFontSize / DEFAULT_UI_FONT_SIZE_PX * 100)}%</DropdownMenuLabel>
      <DropdownMenuItem disabled={uiFontSize <= MIN_UI_FONT_SIZE_PX} onSelect={() => setUiFontSize(setUiFontSizePx(uiFontSize - 1))}><Type className="size-4" />Smaller text</DropdownMenuItem>
      <DropdownMenuItem disabled={uiFontSize >= MAX_UI_FONT_SIZE_PX} onSelect={() => setUiFontSize(setUiFontSizePx(uiFontSize + 1))}><Type className="size-5" />Larger text</DropdownMenuItem>
      <DropdownMenuItem onSelect={() => setUiFontSize(setUiFontSizePx(DEFAULT_UI_FONT_SIZE_PX))}><RotateCcw className="size-5" />Default text size</DropdownMenuItem>
      <DropdownMenuItem onSelect={() => setFontsOpen(true)}><CaseSensitive className="size-5" />Fonts…</DropdownMenuItem>
      <DropdownMenuSeparator /><DropdownMenuItem onSelect={() => setSettingsOpen(true)}><SlidersHorizontal className="size-5" />Settings…</DropdownMenuItem>
      <DropdownMenuSeparator /><DropdownMenuLabel>Appearance</DropdownMenuLabel>
      <DropdownMenuRadioGroup value={theme} onValueChange={(value) => { persistTheme(value as Theme); setTheme(value as Theme); }}>
        <DropdownMenuRadioItem value="light"><Sun className="size-4" />Light</DropdownMenuRadioItem><DropdownMenuRadioItem value="dark"><Moon className="size-4" />Dark</DropdownMenuRadioItem><DropdownMenuRadioItem value="system"><Monitor className="size-4" />Match system</DropdownMenuRadioItem>
      </DropdownMenuRadioGroup>
    </DropdownMenuContent>
  </DropdownMenu>;
  const toolbar = <>{!attachedRun && <ModeSwitch value={chatMode} disabled={chat.streaming} onChange={(next) => setChatMode(persistChatMode(next))} />}<ModelPicker catalog={llm.catalog} provider={llm.provider} model={llm.model} label={llm.selectedLabel} onSelect={llm.select} disabled={chat.streaming} levels={llm.allowedLevels} reasoning={llm.reasoning} onReasoningChange={llm.selectReasoning} menuUp /><LanguagePicker disabled={chat.streaming} /></>;
  const composer = <MathInput key={convos.activeId ?? "draft"} onSubmit={handleSend} onStop={() => void chat.stop()} loading={attachedRun}
    quoteRequest={quoteRequest?.conversationId === convos.activeId ? quoteRequest : undefined}
    stopControl={attachedRun && messages.length > 0 ? "external" : "inline"} voiceLanguage={voice.language} onVoiceTranscript={handleVoiceTranscript}
    disabled={inputDisabled} showDisclaimer={false} commands={skillCatalog.commands} toolbar={toolbar} draftPreset={messages.length ? undefined : draftPreset} />;
  const conversation = <>
    <div className="matopt-conversation-body">
      {convos.loadingThread ? <p className="matopt-loading" role="status">Loading chat…</p> : messages.length ? <MessageList messages={messages} conversationId={convos.activeId} streaming={attachedRun} onPickOption={handleExplain} onOpenWork={openActivity} onQuote={inputDisabled ? undefined : quotePassage} onOpenChanges={openChanges} />
        : <div className="matopt-welcome"><div className="matopt-welcome-inner">
          <MatOptMark size={48} className="matopt-welcome-symbol" />
          <h1>What would you like to work on?</h1>
          <p className="matopt-welcome-description">Explore data, solve a problem, or build something useful.</p>
          {composer}
          <div className="matopt-task-starters" aria-label="Start with a draft">
            {TASK_STARTERS.map((starter) => {
              const Icon = starter.skill === "numerical-optimization" ? Calculator : starter.label === "Fit a regression" ? SquareFunction : ChartNoAxesCombined;
              return <button key={starter.label} type="button" disabled={inputDisabled} title="Add this example to your draft" onClick={() => {
                const draft = taskStarterDraft(starter);
                setDraftPreset((previous) => ({ id: (previous?.id ?? 0) + 1, text: draft.text, skills: draft.skills }));
              }}><Icon aria-hidden="true" />{starter.label}</button>;
            })}
          </div>
        </div></div>}
    </div>
    {chat.streaming && !attachedRun && <div className="matopt-status">Another chat is running. <Button variant="ghost" size="sm" onClick={() => void chat.stop()}>Stop task</Button></div>}
    {error && <p className="matopt-status matopt-error" role="alert">{error}</p>}
    {skillCatalog.error && <p className="matopt-catalog-status" role="status">{skillCatalog.error}. You can still send a message.</p>}
    {messages.length > 0 && <div className="matopt-composer-dock">{attachedRun && chat.approvals.length > 0 && <div className="matopt-approvals" aria-label="Waiting for your approval">
      {chat.approvals.map((approval) => <ApprovalCard key={approval.id} approval={approval} onDecide={(decision) => decideApproval(approval.id, decision)} />)}</div>}{attachedRun && <RunControls paused={chat.paused} pauseRequested={chat.pauseRequested} reconnecting={chat.reconnecting} status={chat.controlStatus}
      onPause={() => void chat.pause()} onResume={() => void chat.resume()} onStop={() => void chat.stop()} />}{composer}<p className="matopt-disclaimer">MatOpt can make mistakes. Check important results.</p></div>}
    {!messages.length && <p className="matopt-disclaimer matopt-welcome-disclaimer">MatOpt can make mistakes. Check important results.</p>}
    {voice.avatarOpen && <AvatarPanel />}
  </>;
  const presentations = messages.flatMap((message) => message.role === "assistant" && isAssistantContent(message.content)
    ? [{ id: message.id, presentation: projectAssistantPresentation(message.content, attachedRun && message.content.status === "streaming") }] : []);
  const workTimeline = presentations.flatMap(({ id, presentation }) => presentation.work ? [{ id, work: presentation.work }] : []);
  const surfaces = [...new Map(presentations.flatMap(({ presentation }) => presentation.surfaces).map((surface) => [surface.id, surface])).values()];
  const artifacts = [...new Map(presentations.flatMap(({ presentation }) => presentation.artifacts).map((artifact) => [artifact.path || artifact.url || `${artifact.kind}:${artifact.name}`, artifact])).values()];
  const surfaceIds = surfaces.map((surface) => surface.id);
  const activeConversation = convos.conversations.find((conversation) => conversation.id === convos.activeId);
  const taskTitle = activeConversation?.title || "New task";
  const memoryMode = activeConversation?.meta?.memory_mode ?? activeConversation?.meta?.memory;
  const changeTurns = [...new Set(messages.flatMap((message) => message.role === "assistant" && isAssistantContent(message.content) && typeof message.content.turn === "number" ? [message.content.turn] : []))];
  const selectedChanges = messages.flatMap((message) => message.role === "assistant" && isAssistantContent(message.content) && message.content.turn === changesTurn ? [message.content.changes?.files.length ?? 0] : [])[0] ?? 0;
  const taskContext = attachedRun ? chat.reconnecting ? "Reconnecting" : chat.paused ? "Paused" : "In progress" : null;
  const sidePane = <WorkspacePanel view={view} onViewChange={(next) => { setView(next); if (next === "files") void files.refresh(); }} onClose={closePane} resultCount={surfaces.length + artifacts.length} fileCount={files.files.length}
    results={<ResultWorkspace surfaces={surfaces} artifacts={artifacts} />}
    changes={<ChangesView conversationId={convos.activeId} turn={changesTurn} turns={changeTurns} onTurnChange={setChangesTurn} revision={messages.length} />} changeCount={selectedChanges}
    files={<WorkspaceWorkbench key={convos.activeId ?? "draft"} conversationId={convos.activeId} files={files.files} loading={files.loading} onRefresh={files.refresh} onUpload={convos.activeId ? handleUpload : undefined} onDelete={convos.activeId ? files.remove : undefined} />} />;
  const inspector = <>
    <header className="matopt-pane-header"><div className="matopt-inspector-heading"><Activity aria-hidden="true" /><h2 tabIndex={-1} data-pane-heading>Activity</h2></div>
      <button type="button" className="matopt-chrome-button" aria-label="Close activity" onClick={closeActivity}><X aria-hidden="true" /></button></header>
    <Tabs value={activityView} onValueChange={setActivityView} className="matopt-activity-tabs"><TabsList aria-label="Activity views"><TabsTrigger value="timeline">Timeline</TabsTrigger><TabsTrigger value="usage">Usage</TabsTrigger><TabsTrigger value="agents">Agents</TabsTrigger><TabsTrigger value="jobs">Jobs</TabsTrigger></TabsList>
      <TabsContent value="timeline" className="matopt-activity-content"><ActivityPanel key={convos.activeId ?? "draft"} turns={workTimeline} conversationId={convos.activeId} /></TabsContent>
      <TabsContent value="usage" className="matopt-activity-content">{activityVisible && activityView === "usage" && <SessionUsage key={convos.activeId ?? "draft"} conversationId={convos.activeId} running={attachedRun} revision={messages.length} onClose={closeActivity} />}</TabsContent>
      <TabsContent value="agents" className="matopt-activity-content">{activityVisible && activityView === "agents" && <AgentsPanel key={convos.activeId ?? "draft"} conversationId={convos.activeId} running={attachedRun} live={attachedRun ? chat.subagents : []} revision={messages.length} />}</TabsContent>
      <TabsContent value="jobs" className="matopt-activity-content">{activityVisible && activityView === "jobs" && <JobsPanel key={convos.activeId ?? "draft"} conversationId={convos.activeId} running={attachedRun} revision={messages.length} />}</TabsContent>
    </Tabs>
  </>;
  return <UiWorkspaceProvider conversationId={convos.activeId} conversationBusy={attachedRun} surfaceIds={surfaceIds} onExplain={inputDisabled ? undefined : handleExplain} onCanvasOpen={openCanvas}>
    <MatOptShell sidebarOpen={sidebarVisible} paneOpen={paneVisible} onCloseSidebar={dismissSidebar} onClosePane={closePane} conversation={conversation} sidePane={sidePane} inspector={inspector} inspectorOpen={activityVisible} onCloseInspector={closeActivity}
      sidebar={<ChatSidebar conversations={convos.conversations} activeId={convos.activeId} pausedConversationId={attachedRun && chat.paused ? convos.activeId : undefined} loading={convos.loadingList} onNewChat={newChat} onSelect={selectChat} onRename={convos.rename} onDelete={convos.remove} onFiles={openFiles} onClose={dismissSidebar} />}
      header={<><div className="matopt-header-title">{!sidebarVisible && <><Button variant="ghost" size="icon-md" aria-label="Open sidebar" title="Open sidebar" onClick={() => setSidebarVisible(true)}><PanelLeft className="size-5" /></Button><Button variant="ghost" size="icon-md" aria-label="New chat" title="New chat" onClick={newChat}><SquarePen className="size-5" /></Button></>}<div className="matopt-task-identity"><h1 title={taskTitle}>{taskTitle}</h1>{taskContext && <span><i className="is-active" aria-hidden="true" />{taskContext}</span>}</div></div>
        <div className="matopt-header-actions"><Button variant="ghost" size="sm" aria-label="Open workspace" title="Workspace" aria-expanded={paneVisible && !activityVisible} onClick={() => paneVisible && !activityVisible ? closePane() : openPane(view)}><Layers className="size-4" /><span>Workspace</span></Button><Button variant="ghost" size="sm" aria-label="Open activity" title="Activity" aria-expanded={activityVisible} onClick={() => activityVisible ? closeActivity() : openActivity()}><Activity className="size-4" /><span>Activity</span></Button><Button variant="ghost" size="sm" aria-label="Voice tutor" title="Voice tutor" aria-pressed={voice.avatarOpen} onClick={() => voice.setPreference("avatarOpen", !voice.avatarOpen)}><Smile className="size-4" /><span>Tutor</span></Button>{settings}<FontSettingsDialog open={fontsOpen} onOpenChange={setFontsOpen} value={fonts} onChange={(next) => setFonts(setFontPreferences(next))} />
          <SettingsDialog open={settingsOpen} onOpenChange={setSettingsOpen} conversationId={convos.activeId} conversationMemory={memoryMode === "disabled" ? "disabled" : memoryMode === "enabled" ? "enabled" : undefined} /></div></>} />
  </UiWorkspaceProvider>;
}
