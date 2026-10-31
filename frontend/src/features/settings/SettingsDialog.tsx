/** Server settings: features, read-only security, integrations, memories and instructions. */
import { useCallback, useEffect, useId, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { BookText, Brain, FlaskConical, Plug, RefreshCw, Shield, Trash2 } from "lucide-react";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog.js";
import { errorMessage, isUnavailable } from "../../services/api.ts";
import { getSettings, setFeature, type FeatureFlag, type SettingsSnapshot } from "../../services/settings.ts";
import { memoriesService, type MemoryNote, type MemorySnapshot } from "../../services/memories.ts";
import { pluginsService, type PluginInfo } from "../../services/plugins.ts";
import { migrationService, type MigrationItem, type MigrationResult } from "../../services/migration.ts";
import "./settings-dialog.css";

export type SettingsSection = "features" | "security" | "integrations" | "memories" | "instructions";
const SECTIONS: Array<[SettingsSection, string, typeof Shield]> = [
    ["features", "Features", FlaskConical], ["security", "Security", Shield], ["integrations", "Integrations", Plug],
    ["memories", "Memories", Brain], ["instructions", "Instructions", BookText],
];

type Load<T> = { data: T | null; loading: boolean; error: string | null; unavailable: boolean };
const idle = <T,>(): Load<T> => ({ data: null, loading: false, error: null, unavailable: false });

/** Fetch on open; a 404 means the server does not offer this part yet. */
function useLoad<T>(open: boolean, fetcher: (signal: AbortSignal) => Promise<T>, fallback: string) {
    const [state, setState] = useState<Load<T>>(idle);
    const [revision, setRevision] = useState(0);
    const fetcherRef = useRef(fetcher); fetcherRef.current = fetcher;
    useEffect(() => {
        if (!open) return;
        const controller = new AbortController();
        setState((previous) => ({ ...previous, loading: true, error: null }));
        fetcherRef.current(controller.signal).then(
            (data) => { if (!controller.signal.aborted) setState({ data, loading: false, error: null, unavailable: false }); },
            (error) => {
                if (controller.signal.aborted) return;
                const unavailable = isUnavailable(error);
                setState({ data: null, loading: false, unavailable, error: unavailable ? null : errorMessage(error, fallback) });
            });
        return () => controller.abort();
    }, [open, revision, fallback]);
    const reload = useCallback(() => setRevision((value) => value + 1), []);
    return [state, setState, reload] as const;
}

function Switch({ checked, onChange, label, disabled, describedBy }: {
    checked: boolean; onChange: (next: boolean) => void; label: string; disabled?: boolean; describedBy?: string;
}) {
    return <button type="button" role="switch" className="matopt-switch" aria-checked={checked} aria-label={label} aria-describedby={describedBy}
        disabled={disabled} onClick={() => onChange(!checked)}><span aria-hidden="true" /></button>;
}

function Status({ state, what }: { state: Load<unknown>; what: string }) {
    if (state.unavailable) return <p className="matopt-settings-note" role="status">{what} {what.endsWith("s") ? "are" : "is"} not available on this server.</p>;
    if (state.error) return <p className="matopt-settings-note is-error" role="alert">{state.error}</p>;
    if (state.loading && !state.data) return <p className="matopt-settings-note" role="status">Loading {what.toLowerCase()}…</p>;
    return null;
}

const STAGES: Record<string, string> = { stable: "Stable", beta: "Beta", experimental: "Experimental", deprecated: "Deprecated", under_development: "In development", removed: "Removed" };

function FeaturesSection({ settings, onFeature }: { settings: Load<SettingsSnapshot>; onFeature: (next: FeatureFlag) => void }) {
    const [busy, setBusy] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [restart, setRestart] = useState<Set<string>>(new Set());
    const features = settings.data?.features ?? [];
    const toggle = async (feature: FeatureFlag, enabled: boolean) => {
        setBusy(feature.key); setError(null);
        try {
            const updated = await setFeature(feature.key, enabled);
            onFeature(updated ?? { ...feature, enabled });
            if (!(updated ?? feature).live) setRestart((keys) => new Set(keys).add(feature.key));
        } catch (failure) { setError(errorMessage(failure, `Could not change “${feature.name}”.`)); }
        finally { setBusy(null); }
    };
    return <>
        <p className="matopt-settings-lead">Turn optional capabilities on or off. Changes are saved to your user configuration.</p>
        <Status state={settings} what="Features" />
        {error && <p className="matopt-settings-note is-error" role="alert">{error}</p>}
        {settings.data && !features.length && <p className="matopt-settings-note">No optional features are available.</p>}
        {features.length > 0 && <ul className="matopt-settings-list" aria-label="Features">{features.map((feature) => {
            const descriptionId = `feature-${feature.key}-description`;
            return <li key={feature.key} className="matopt-setting-row">
                <div className="matopt-setting-text">
                    <div className="matopt-setting-title"><strong>{feature.name}</strong><span className="matopt-stage" data-stage={feature.stage}>{STAGES[feature.stage] ?? feature.stage}</span></div>
                    <p id={descriptionId}>{feature.description}{!feature.live && <span className="matopt-setting-hint"> Applies after restart.</span>}
                        {feature.origin && feature.origin !== "default" && <span className="matopt-setting-hint"> Set by {feature.origin}.</span>}</p>
                    {restart.has(feature.key) && <p className="matopt-setting-hint" role="status">Saved. Restart the MatOpt server to apply it.</p>}
                </div>
                <Switch checked={feature.enabled} label={feature.name} describedBy={descriptionId} disabled={busy === feature.key} onChange={(next) => void toggle(feature, next)} />
            </li>;
        })}</ul>}
    </>;
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
    return <div className="matopt-fact"><dt>{label}</dt><dd>{children}</dd></div>;
}

function SecuritySection({ settings }: { settings: Load<SettingsSnapshot> }) {
    const security = settings.data?.security;
    const config = settings.data?.config;
    const reviewer = { off: "Not required", guardian: "Automatic review (guardian)", user: "Ask you" } as const;
    return <>
        <p className="matopt-settings-lead">These protections are set by the operator in the server configuration or environment and cannot be changed here.</p>
        <Status state={settings} what="Security settings" />
        {settings.data && !security && <p className="matopt-settings-note">This server does not report its security settings.</p>}
        {security && <dl className="matopt-facts">
            <Fact label="Sandbox">{security.sandbox.mode}{security.sandbox.available ? ` · ${security.sandbox.backend}` : " · not available on this host"}</Fact>
            <Fact label="Network in sandbox">{security.sandbox.network}</Fact>
            <Fact label="Approvals">{reviewer[security.approvals_reviewer]}</Fact>
            <Fact label="Hooks">{security.hooks.enabled ? `On${security.hooks.events.length ? ` · ${security.hooks.events.join(", ")}` : ""}` : "Off"}
                {security.hooks.path && <code className="matopt-path">{security.hooks.path}</code>}</Fact>
            <Fact label="Command rules">{security.exec_rules.rules} {security.exec_rules.rules === 1 ? "rule" : "rules"}{security.exec_rules.path && <code className="matopt-path">{security.exec_rules.path}</code>}</Fact>
            <Fact label="Secret redaction">{security.redact_tool_output ? "Tool output is redacted" : "Off"}</Fact>
        </dl>}
        {config && <p className="matopt-settings-foot">Configuration {config.exists ? "file" : "file (not created yet)"}: <code className="matopt-path">{config.path}</code>{config.profile && <> · profile <strong>{config.profile}</strong></>}</p>}
    </>;
}

const MCP_STATE = { connected: "Connected", failed: "Failed", disabled: "Disabled" } as const;
const SOURCES: Record<string, string> = { claude: "Claude Code", claude_code: "Claude Code", "claude-code": "Claude Code", cursor: "Cursor", codex: "Codex" };

function PluginsBlock({ open }: { open: boolean }) {
    const [plugins, setPlugins, reload] = useLoad(open, (signal) => pluginsService.list(signal), "Could not load plugins.");
    const [path, setPath] = useState("");
    const [busy, setBusy] = useState<string | null>(null);
    const [message, setMessage] = useState<{ text: string; error?: boolean } | null>(null);
    const [confirming, setConfirming] = useState<string | null>(null);
    const inputId = useId();
    const act = async (key: string, run: () => Promise<{ restart_required: boolean }>, done: string) => {
        setBusy(key); setMessage(null);
        try {
            const result = await run();
            setMessage({ text: result.restart_required ? `${done} Restart the MatOpt server to apply it.` : done });
            reload();
        } catch (failure) { setMessage({ text: errorMessage(failure, "The plugin change failed."), error: true }); }
        finally { setBusy(null); setConfirming(null); }
    };
    const update = (id: string, change: Partial<PluginInfo>) => setPlugins((state) => ({ ...state, data: state.data?.map((plugin) => plugin.id === id ? { ...plugin, ...change } : plugin) ?? null }));
    return <section className="matopt-settings-block" aria-labelledby={`${inputId}-title`}>
        <h3 id={`${inputId}-title`}>Plugins</h3>
        <Status state={plugins} what="Plugins" />
        {plugins.data && !plugins.data.length && <p className="matopt-settings-note">No plugins installed.</p>}
        {plugins.data && plugins.data.length > 0 && <ul className="matopt-settings-list" aria-label="Installed plugins">{plugins.data.map((plugin) => {
            const parts = [[plugin.skills.length, "skill"], [plugin.mcp_servers.length, "MCP server"], [plugin.hooks.length, "hook"]] as const;
            return <li key={plugin.id} className="matopt-setting-row">
                <div className="matopt-setting-text">
                    <div className="matopt-setting-title"><strong>{plugin.name}</strong>{plugin.version && <span className="matopt-setting-hint">v{plugin.version}</span>}</div>
                    {plugin.description && <p>{plugin.description}</p>}
                    <p className="matopt-setting-hint">{parts.filter(([count]) => count > 0).map(([count, noun]) => `${count} ${noun}${count === 1 ? "" : "s"}`).join(" · ") || "No contributions"}</p>
                    {plugin.errors.map((error, index) => <p key={index} className="matopt-setting-hint is-error">{error}</p>)}
                    {confirming === plugin.id && <div className="matopt-confirm" role="group" aria-label={`Remove ${plugin.name}?`}>
                        <span>Remove {plugin.name}? Its files are deleted.</span>
                        <button type="button" className="matopt-settings-button is-danger" disabled={busy !== null} onClick={() => void act(plugin.id, () => pluginsService.remove(plugin.id), `Removed ${plugin.name}.`)}>Remove</button>
                        <button type="button" className="matopt-settings-button" onClick={() => setConfirming(null)}>Cancel</button>
                    </div>}
                </div>
                <div className="matopt-setting-controls">
                    <Switch checked={plugin.enabled} label={`Enable ${plugin.name}`} disabled={busy !== null} onChange={(next) => void act(plugin.id, async () => {
                        const result = await pluginsService.setEnabled(plugin.id, next); update(plugin.id, { enabled: next }); return result;
                    }, `${next ? "Enabled" : "Disabled"} ${plugin.name}.`)} />
                    <button type="button" className="matopt-icon-button" aria-label={`Remove ${plugin.name}`} title="Remove" disabled={busy !== null} onClick={() => setConfirming(plugin.id)}><Trash2 aria-hidden="true" /></button>
                </div>
            </li>;
        })}</ul>}
        <form className="matopt-inline-form" onSubmit={(event) => {
            event.preventDefault(); const value = path.trim(); if (!value) return;
            void act("install", async () => { const result = await pluginsService.install(value); setPath(""); return result; }, "Plugin installed.");
        }}>
            <label htmlFor={inputId}>Install from a folder on the server</label>
            <div><input id={inputId} value={path} onChange={(event) => setPath(event.target.value)} placeholder="/path/to/plugin" spellCheck={false} autoComplete="off" />
                <button type="submit" className="matopt-settings-button" disabled={!path.trim() || busy !== null}>{busy === "install" ? "Installing…" : "Install"}</button></div>
        </form>
        {message && <p className={`matopt-settings-note${message.error ? " is-error" : ""}`} role={message.error ? "alert" : "status"}>{message.text}</p>}
    </section>;
}

function MigrationBlock() {
    const [items, setItems] = useState<Load<MigrationItem[]>>(idle);
    const [selected, setSelected] = useState<Set<string>>(new Set());
    const [results, setResults] = useState<Map<string, MigrationResult>>(new Map());
    const [importing, setImporting] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const titleId = useId();
    const detect = async () => {
        setItems((state) => ({ ...state, loading: true, error: null })); setResults(new Map()); setError(null);
        try {
            const found = await migrationService.detect();
            setItems({ data: found, loading: false, error: null, unavailable: false });
            setSelected(new Set(found.filter((item) => !item.conflicts.length).map((item) => item.id)));
        } catch (failure) {
            setItems({ data: null, loading: false, unavailable: isUnavailable(failure), error: isUnavailable(failure) ? null : errorMessage(failure, "Could not look for settings to import.") });
        }
    };
    const run = async () => {
        setImporting(true); setError(null);
        try { setResults(new Map((await migrationService.import([...selected])).map((result) => [result.id, result]))); }
        catch (failure) { setError(errorMessage(failure, "The import failed.")); }
        finally { setImporting(false); }
    };
    return <section className="matopt-settings-block" aria-labelledby={titleId}>
        <h3 id={titleId}>Import from Claude Code, Cursor or Codex</h3>
        <p className="matopt-settings-lead">Copy MCP servers, skills and instruction files configured for other agents on this server.</p>
        <div><button type="button" className="matopt-settings-button" disabled={items.loading} onClick={() => void detect()}><RefreshCw aria-hidden="true" />{items.loading ? "Looking…" : items.data ? "Look again" : "Find importable settings"}</button></div>
        <Status state={items} what="Import" />
        {items.data && !items.data.length && <p className="matopt-settings-note">Nothing to import was found.</p>}
        {items.data && items.data.length > 0 && <>
            <ul className="matopt-settings-list" aria-label="Importable settings">{items.data.map((item) => {
                const result = results.get(item.id);
                const id = `migrate-${item.id}`;
                return <li key={item.id} className="matopt-setting-row is-check">
                    <input type="checkbox" id={id} checked={selected.has(item.id)} disabled={importing} onChange={(event) => setSelected((current) => {
                        const next = new Set(current); if (event.target.checked) next.add(item.id); else next.delete(item.id); return next;
                    })} />
                    <label htmlFor={id} className="matopt-setting-text">
                        <span className="matopt-setting-title"><strong>{item.summary || item.item_type}</strong><span className="matopt-stage">{SOURCES[item.source] ?? item.source}</span>
                            {item.count > 0 && <span className="matopt-setting-hint">{item.count} {item.count === 1 ? "item" : "items"}</span>}</span>
                        <span className="matopt-setting-hint">{[item.item_type, item.scope].filter(Boolean).join(" · ")}{item.path && <code className="matopt-path">{item.path}</code>}</span>
                        {item.conflicts.length > 0 && <span className="matopt-setting-hint is-warning">Conflicts with existing: {item.conflicts.join(", ")}</span>}
                    </label>
                    {result && <span className="matopt-import-result" data-status={result.status} role="status">{result.status === "imported" ? "Imported" : result.status === "skipped" ? "Skipped" : result.status === "failed" || result.status === "error" ? "Failed" : result.status}{result.detail ? `: ${result.detail}` : ""}</span>}
                </li>;
            })}</ul>
            <div className="matopt-settings-actions"><button type="button" className="matopt-settings-button is-primary" disabled={!selected.size || importing} onClick={() => void run()}>{importing ? "Importing…" : selected.size ? `Import ${selected.size} selected` : "Import selected"}</button></div>
        </>}
        {error && <p className="matopt-settings-note is-error" role="alert">{error}</p>}
    </section>;
}

function IntegrationsSection({ open, settings }: { open: boolean; settings: Load<SettingsSnapshot> }) {
    const servers = settings.data?.mcp.servers ?? [];
    return <>
        <section className="matopt-settings-block" aria-labelledby="matopt-mcp-title">
            <h3 id="matopt-mcp-title">MCP servers</h3>
            <Status state={settings} what="MCP status" />
            {settings.data && !servers.length && <p className="matopt-settings-note">No MCP servers are configured.</p>}
            {servers.length > 0 && <ul className="matopt-settings-list" aria-label="MCP servers">{servers.map((server) => <li key={server.name} className="matopt-setting-row">
                <div className="matopt-setting-text">
                    <div className="matopt-setting-title"><strong>{server.name}</strong></div>
                    <p className="matopt-setting-hint">{server.tools} {server.tools === 1 ? "tool" : "tools"}{server.resources ? ` · ${server.resources} ${server.resources === 1 ? "resource" : "resources"}` : ""}</p>
                    {server.error && <p className="matopt-setting-hint is-error">{server.error}</p>}
                </div>
                <span className="matopt-state" data-state={server.state}>{MCP_STATE[server.state]}</span>
            </li>)}</ul>}
        </section>
        <PluginsBlock open={open} />
        <MigrationBlock />
    </>;
}

function NoteRow({ note, onSave, onDelete }: { note: MemoryNote; onSave: (text: string) => Promise<void>; onDelete: () => Promise<void> }) {
    const [editing, setEditing] = useState(false);
    const [draft, setDraft] = useState(note.text);
    const [busy, setBusy] = useState(false);
    const run = async (task: () => Promise<void>) => {
        setBusy(true);
        try { await task(); setEditing(false); } catch { /* The section reports the failure. */ } finally { setBusy(false); }
    };
    return <li className="matopt-note">
        {editing ? <form onSubmit={(event) => { event.preventDefault(); if (draft.trim()) void run(() => onSave(draft.trim())); }}>
            <textarea aria-label="Edit note" value={draft} rows={3} onChange={(event) => setDraft(event.target.value)} autoFocus />
            <div className="matopt-settings-actions">
                <button type="button" className="matopt-settings-button" onClick={() => { setDraft(note.text); setEditing(false); }}>Cancel</button>
                <button type="submit" className="matopt-settings-button is-primary" disabled={busy || !draft.trim()}>Save</button>
            </div>
        </form> : <>
            <div className="matopt-note-text">{note.title && <strong>{note.title}</strong>}<p>{note.text}</p></div>
            <div className="matopt-setting-controls">
                <button type="button" className="matopt-settings-button" disabled={busy} onClick={() => setEditing(true)} aria-label={`Edit note${note.title ? ` ${note.title}` : ""}`}>Edit</button>
                <button type="button" className="matopt-icon-button" disabled={busy} aria-label={`Delete note${note.title ? ` ${note.title}` : ""}`} title="Delete" onClick={() => void run(onDelete)}><Trash2 aria-hidden="true" /></button>
            </div>
        </>}
    </li>;
}

function MemoriesSection({ open, conversationId, conversationMemory }: { open: boolean; conversationId?: string | null; conversationMemory?: "enabled" | "disabled" }) {
    const [memories, setMemories, reload] = useLoad<MemorySnapshot>(open, (signal) => memoriesService.get(signal), "Could not load memories.");
    const [draft, setDraft] = useState("");
    const [confirmClear, setConfirmClear] = useState(false);
    const [message, setMessage] = useState<{ text: string; error?: boolean } | null>(null);
    const [busy, setBusy] = useState(false);
    const [chatMode, setChatMode] = useState(conversationMemory ?? "enabled");
    useEffect(() => setChatMode(conversationMemory ?? "enabled"), [conversationMemory, conversationId]);
    const noteId = useId();
    const run = async (task: () => Promise<void>, done?: string, failure = "The change failed.") => {
        setBusy(true); setMessage(null);
        try { await task(); if (done) setMessage({ text: done }); }
        catch (error) { setMessage({ text: errorMessage(error, failure), error: true }); throw error; }
        finally { setBusy(false); }
    };
    const setNotes = (notes: (current: MemoryNote[]) => MemoryNote[]) => setMemories((state) => state.data ? { ...state, data: { ...state.data, notes: notes(state.data.notes) } } : state);
    const data = memories.data;
    return <>
        <p className="matopt-settings-lead">MatOpt can remember useful facts from earlier chats. You can review, edit or remove what it keeps.</p>
        <Status state={memories} what="Memories" />
        {data && !data.enabled && <p className="matopt-settings-note">Memories are turned off. Enable the memories feature to use them.</p>}
        {conversationId && data && <div className="matopt-setting-row">
            <div className="matopt-setting-text"><div className="matopt-setting-title"><strong>Use memories in this chat</strong></div><p>When off, this chat neither reads nor adds memories.</p></div>
            <Switch checked={chatMode === "enabled"} label="Use memories in this chat" disabled={busy} onChange={(next) => {
                const mode = next ? "enabled" : "disabled";
                void run(async () => { await memoriesService.setConversationMode(conversationId, mode); setChatMode(mode); }, undefined, "Could not change this chat’s memory setting.").catch(() => {});
            }} />
        </div>}
        {data && <section className="matopt-settings-block" aria-labelledby={`${noteId}-summary`}>
            <div className="matopt-block-head"><h3 id={`${noteId}-summary`}>Summary</h3>
                <button type="button" className="matopt-settings-button" disabled={busy} onClick={() => void run(async () => { await memoriesService.refresh(); reload(); }, "Memories are being refreshed.", "Could not refresh memories.").catch(() => {})}><RefreshCw aria-hidden="true" />Refresh</button></div>
            {data.summary || data.memory_md ? <div className="matopt-memory-summary" tabIndex={0} aria-label="Memory summary">{data.summary || data.memory_md}</div>
                : <p className="matopt-settings-note">Nothing has been learned yet.</p>}
            {data.conversations_learned > 0 && <p className="matopt-setting-hint">Learned from {data.conversations_learned} {data.conversations_learned === 1 ? "chat" : "chats"}.</p>}
        </section>}
        {data && <section className="matopt-settings-block" aria-labelledby={`${noteId}-notes`}>
            <h3 id={`${noteId}-notes`}>Notes</h3>
            {data.notes.length ? <ul className="matopt-notes" aria-label="Memory notes">{data.notes.map((note) => <NoteRow key={note.id} note={note}
                onSave={(text) => run(async () => { const saved = await memoriesService.updateNote(note.id, text); setNotes((notes) => notes.map((entry) => entry.id === note.id ? saved ?? { ...entry, text } : entry)); }, undefined, "Could not save the note.")}
                onDelete={() => run(async () => { await memoriesService.deleteNote(note.id); setNotes((notes) => notes.filter((entry) => entry.id !== note.id)); }, "Note deleted.", "Could not delete the note.")} />)}</ul>
                : <p className="matopt-settings-note">No notes yet.</p>}
            <form className="matopt-inline-form" onSubmit={(event) => {
                event.preventDefault(); const text = draft.trim(); if (!text) return;
                void run(async () => { const note = await memoriesService.addNote(text); setDraft(""); if (note) setNotes((notes) => [...notes, note]); else reload(); }, "Note added.", "Could not add the note.").catch(() => {});
            }}>
                <label htmlFor={noteId}>Add a note for MatOpt to remember</label>
                <div><textarea id={noteId} rows={2} value={draft} onChange={(event) => setDraft(event.target.value)} placeholder="For example: I prefer SI units and NumPy over pandas." />
                    <button type="submit" className="matopt-settings-button is-primary" disabled={busy || !draft.trim()}>Add</button></div>
            </form>
        </section>}
        {data && <div className="matopt-danger-zone">{confirmClear
            ? <div className="matopt-confirm" role="group" aria-label="Clear all memories?">
                <span>Clear the summary and every note? This cannot be undone.</span>
                <button type="button" className="matopt-settings-button is-danger" disabled={busy} onClick={() => void run(async () => { await memoriesService.clear(); setConfirmClear(false); reload(); }, "All memories cleared.", "Could not clear memories.").catch(() => {})}>Clear all</button>
                <button type="button" className="matopt-settings-button" onClick={() => setConfirmClear(false)}>Cancel</button>
            </div>
            : <button type="button" className="matopt-settings-button is-danger" disabled={busy} onClick={() => setConfirmClear(true)}><Trash2 aria-hidden="true" />Clear all memories…</button>}</div>}
        {message && <p className={`matopt-settings-note${message.error ? " is-error" : ""}`} role={message.error ? "alert" : "status"}>{message.text}</p>}
    </>;
}

function InstructionsSection({ settings }: { settings: Load<SettingsSnapshot> }) {
    const operator = settings.data?.instructions?.operator;
    return <>
        <p className="matopt-settings-lead">Project instructions (AGENTS.md files) the operator configured. MatOpt follows them in every chat.</p>
        <Status state={settings} what="Instructions" />
        {settings.data && (!operator || !operator.sources.length) && <p className="matopt-settings-note">No instruction files are loaded.</p>}
        {operator && operator.sources.length > 0 && <>
            <ul className="matopt-settings-list" aria-label="Instruction files">{operator.sources.map((source) => <li key={source} className="matopt-setting-row"><code className="matopt-path">{source}</code></li>)}</ul>
            <p className="matopt-setting-hint">{operator.chars.toLocaleString()} characters in total.</p>
        </>}
    </>;
}

export function SettingsDialog({ open, onOpenChange, initialSection = "features", conversationId, conversationMemory }: {
    open: boolean; onOpenChange: (open: boolean) => void; initialSection?: SettingsSection;
    conversationId?: string | null; conversationMemory?: "enabled" | "disabled";
}) {
    const [section, setSection] = useState<SettingsSection>(initialSection);
    const [settings, setSettings] = useLoad(open, getSettings, "Could not load settings.");
    const id = useId();
    useEffect(() => { if (open) setSection(initialSection); }, [open, initialSection]);
    const onKey = (event: KeyboardEvent<HTMLButtonElement>, value: SettingsSection) => {
        const keys = ["ArrowDown", "ArrowRight", "ArrowUp", "ArrowLeft", "Home", "End"];
        if (!keys.includes(event.key)) return;
        event.preventDefault();
        const order = SECTIONS.map(([key]) => key);
        const index = order.indexOf(value);
        const next = event.key === "Home" ? order[0] : event.key === "End" ? order[order.length - 1]
            : order[(index + (["ArrowDown", "ArrowRight"].includes(event.key) ? 1 : order.length - 1)) % order.length];
        setSection(next);
        document.getElementById(`${id}-${next}-tab`)?.focus();
    };
    const onFeature = (next: FeatureFlag) => setSettings((state) => state.data
        ? { ...state, data: { ...state.data, features: state.data.features.map((feature) => feature.key === next.key ? next : feature) } } : state);
    return <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="matopt-settings-dialog">
            <DialogHeader>
                <DialogTitle>Settings</DialogTitle>
                <DialogDescription>Server features, security, integrations and what MatOpt remembers.</DialogDescription>
            </DialogHeader>
            <div className="matopt-settings-layout">
                <div className="matopt-settings-nav" role="tablist" aria-label="Settings sections" aria-orientation="vertical">
                    {SECTIONS.map(([value, label, Icon]) => <button key={value} type="button" role="tab" id={`${id}-${value}-tab`} aria-controls={`${id}-${value}`}
                        aria-selected={section === value} tabIndex={section === value ? 0 : -1} onClick={() => setSection(value)} onKeyDown={(event) => onKey(event, value)}>
                        <Icon aria-hidden="true" /><span>{label}</span></button>)}
                </div>
                {SECTIONS.map(([value]) => <div key={value} id={`${id}-${value}`} role="tabpanel" aria-labelledby={`${id}-${value}-tab`} className="matopt-settings-panel" hidden={section !== value} tabIndex={0}>
                    {value === "features" && <FeaturesSection settings={settings} onFeature={onFeature} />}
                    {value === "security" && <SecuritySection settings={settings} />}
                    {value === "integrations" && <IntegrationsSection open={open} settings={settings} />}
                    {value === "memories" && <MemoriesSection open={open} conversationId={conversationId} conversationMemory={conversationMemory} />}
                    {value === "instructions" && <InstructionsSection settings={settings} />}
                </div>)}
            </div>
        </DialogContent>
    </Dialog>;
}
