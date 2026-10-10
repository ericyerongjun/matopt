/** Thin MatOpt command/upload binding around the copied ZCode PromptInput. */
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { ArrowUp, Check, Plus, Square, X } from "lucide-react";
import { activeSlashToken, commandToken } from "../services/slash.ts";
import type { CommandInfo, SkillInfo } from "../services/skills";
import { skillIntro } from "../services/skills.ts";
import {
  PromptInput, PromptInputActionAddAttachments, PromptInputActionMenu,
  PromptInputActionMenuContent, PromptInputActionMenuTrigger, PromptInputBody,
  PromptInputCommand, PromptInputCommandEmpty,
  PromptInputCommandGroup, PromptInputCommandItem, PromptInputCommandList,
  PromptInputHeader, PromptInputProvider, PromptInputSubmit,
  PromptInputTextarea, PromptInputTools, usePromptInputAttachments,
  usePromptInputController, type PromptInputMessage,
} from "../zcode/components/ai-elements/prompt-input.js";
import {
  Attachment, AttachmentInfo, AttachmentPreview, AttachmentRemove, Attachments,
} from "../zcode/components/ai-elements/attachments.js";
import { filterPromptCommands, promptFilesForUpload, resolvePromptSubmission } from "../zcode/adapters/matopt-prompt.ts";
import { shouldIgnoreEscapeForStopGeneration } from "../zcode/v4/composer/escapeStop.ts";
import { commandPresentation } from "./commandPresentation.ts";
import "./MathInput.css";

interface Props {
  onSubmit: (text: string, files: File[], skills?: string[], tools?: string[]) => void | boolean | Promise<void | boolean>;
  onStop?: () => void;
  /** A surrounding run-control group may provide the single visible Stop action. */
  stopControl?: "inline" | "external";
  disabled?: boolean;
  loading?: boolean;
  placeholder?: string;
  toolbar?: ReactNode;
  /** Fill an editable draft once per action ID, without submitting or clearing selections. */
  draftPreset?: { id: number; text: string };
  showDisclaimer?: boolean;
  commands?: CommandInfo[];
  /** @deprecated pass commands */
  skills?: SkillInfo[];
  externalSkill?: SkillInfo | null;
  externalKey?: number;
  onExternalSkillConsumed?: () => void;
}

const keyFor = (command: CommandInfo) => `${command.kind}:${command.name}`;
type PinnedCommand = CommandInfo & { selectionId: string };
type MenuPosition = { left: number; top: number; width: number; maxHeight: number };

function PromptAttachments({ pinned, removePinned, disabled }: {
  pinned: CommandInfo[]; removePinned: (command: CommandInfo) => void; disabled: boolean;
}) {
  const attachments = usePromptInputAttachments();
  if (!pinned.length && !attachments.files.length) return null;
  return <PromptInputHeader className="matopt-composer-selections" aria-label="Selected tools, skills, and attachments">
    <Attachments variant="inline" className="matopt-composer-selection-list">
      {pinned.map((command) => {
        const { label, Icon } = commandPresentation(command);
        return <div key={keyFor(command)} className="matopt-command-chip" data-selected-command={keyFor(command)} role="group" aria-label={`${label} selected`}>
          <Icon size={20} strokeWidth={1.8} aria-hidden="true" />
          <span className="matopt-command-chip-label" title={commandToken(command.name)}>{label}</span>
          <button type="button" disabled={disabled} className="matopt-command-chip-remove" aria-label={`Remove ${label}`}
            onMouseDown={(event) => event.preventDefault()} onClick={() => removePinned(command)}><X size={14} strokeWidth={1.8} aria-hidden="true" /></button>
        </div>;
      })}
      {attachments.files.map((file) => <Attachment key={file.id} className="matopt-composer-file" data={file} variant="inline" onRemove={() => attachments.remove(file.id)}>
        <AttachmentPreview />
        <AttachmentInfo />
        <AttachmentRemove alwaysVisible disabled={disabled} label={`Remove ${file.filename || "attachment"}`} />
      </Attachment>)}
    </Attachments>
  </PromptInputHeader>;
}

function MatOptPrompt({ onSubmit, onStop, stopControl = "inline", disabled = false, loading = false,
  placeholder = "Ask anything, or type /", toolbar, draftPreset, showDisclaimer = true,
  commands, skills = [], externalSkill = null, externalKey = 0, onExternalSkillConsumed,
}: Props) {
  const controller = usePromptInputController();
  const value = controller.textInput.value;
  const setValue = controller.textInput.setInput;
  const [pinned, setPinned] = useState<PinnedCommand[]>([]);
  const [activeIndex, setActiveIndex] = useState(0);
  const [caret, setCaret] = useState(0);
  const [composing, setComposing] = useState(false);
  const [dismissedToken, setDismissedToken] = useState<string | null>(null);
  const [menuIds, setMenuIds] = useState<{ list?: string; option?: string }>({});
  const [menuPosition, setMenuPosition] = useState<MenuPosition | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const live = useRef({ onSubmit, disabled, mounted: true });
  const appliedPreset = useRef<number | null>(null);
  live.current.onSubmit = onSubmit;
  live.current.disabled = disabled;
  const catalog = useMemo<CommandInfo[]>(() => commands?.length ? commands : skills.map((skill) => ({
    kind: "skill" as const, name: skill.name, description: skill.description, source: skill.source, available: skill.available,
  })), [commands, skills]);
  const slashToken = activeSlashToken(value, caret);
  const slashQuery = slashToken?.query ?? "";
  const tokenIdentity = `${value}:${caret}`;
  const slashOpen = Boolean(slashToken) && catalog.length > 0 && !disabled && !composing && dismissedToken !== tokenIdentity;
  const matches = useMemo(() => {
    const direct = filterPromptCommands(catalog, slashQuery);
    const query = slashQuery.toLowerCase().replace(/[-_]/g, " ");
    return [...direct, ...catalog.filter((command) => {
      if (direct.includes(command)) return false;
      const presentation = commandPresentation(command);
      return `${presentation.label} ${presentation.description ?? ""}`.toLowerCase().includes(query);
    })];
  }, [catalog, slashQuery]);
  const canSend = Boolean(value.trim() || pinned.length) && !disabled;
  const showStop = loading && !canSend && stopControl === "inline";

  useEffect(() => {
    live.current.mounted = true;
    return () => { live.current.mounted = false; };
  }, []);
  useEffect(() => {
    if (!draftPreset || disabled || composing || appliedPreset.current === draftPreset.id) return;
    appliedPreset.current = draftPreset.id;
    setValue(draftPreset.text);
    setCaret(draftPreset.text.length);
    setDismissedToken(`${draftPreset.text}:${draftPreset.text.length}`);
    requestAnimationFrame(() => {
      if (!live.current.mounted) return;
      textareaRef.current?.focus();
      textareaRef.current?.setSelectionRange(draftPreset.text.length, draftPreset.text.length);
    });
  }, [draftPreset, disabled, composing, setValue]);
  useEffect(() => setActiveIndex(0), [slashQuery, slashOpen]);
  useLayoutEffect(() => {
    if (!slashOpen) { setMenuPosition(null); return; }
    const reposition = () => {
      const anchor = wrapperRef.current?.getBoundingClientRect();
      if (!anchor) return;
      const viewport = window.visualViewport;
      const viewportTop = viewport?.offsetTop ?? 0;
      const viewportLeft = viewport?.offsetLeft ?? 0;
      const viewportHeight = viewport?.height ?? window.innerHeight;
      const viewportWidth = viewport?.width ?? window.innerWidth;
      const margin = 8, gap = 8;
      const above = Math.max(0, anchor.top - viewportTop - margin - gap);
      const below = Math.max(0, viewportTop + viewportHeight - anchor.bottom - margin - gap);
      const placeAbove = above >= 280 || above >= below;
      const maxHeight = Math.max(0, Math.min(360, placeAbove ? above : below));
      const renderedHeight = menuRef.current?.getBoundingClientRect().height || maxHeight;
      const height = Math.min(maxHeight, renderedHeight);
      const width = Math.min(440, Math.max(280, anchor.width), Math.max(0, viewportWidth - margin * 2));
      const next = {
        left: Math.max(viewportLeft + margin, Math.min(anchor.left, viewportLeft + viewportWidth - margin - width)),
        top: placeAbove ? Math.max(viewportTop + margin, anchor.top - gap - height) : anchor.bottom + gap,
        width, maxHeight,
      };
      setMenuPosition((previous) => previous && Object.keys(next).every((key) => previous[key as keyof MenuPosition] === next[key as keyof MenuPosition]) ? previous : next);
    };
    reposition();
    const observer = new ResizeObserver(reposition);
    if (wrapperRef.current) observer.observe(wrapperRef.current);
    if (menuRef.current) observer.observe(menuRef.current);
    window.addEventListener("resize", reposition);
    window.addEventListener("scroll", reposition, true);
    window.visualViewport?.addEventListener("resize", reposition);
    window.visualViewport?.addEventListener("scroll", reposition);
    return () => {
      observer.disconnect(); window.removeEventListener("resize", reposition); window.removeEventListener("scroll", reposition, true);
      window.visualViewport?.removeEventListener("resize", reposition); window.visualViewport?.removeEventListener("scroll", reposition);
    };
  }, [slashOpen, matches]);
  useLayoutEffect(() => {
    // cmdk owns these generated IDs; passing an id prop does not override them.
    const list = slashOpen ? menuRef.current?.querySelector<HTMLElement>("[cmdk-list]") : null;
    const command = matches[activeIndex];
    const option = command ? Array.from(list?.querySelectorAll<HTMLElement>("[cmdk-item]") ?? [])
      .find((item) => item.getAttribute("data-value") === keyFor(command)) : undefined;
    setMenuIds((previous) => previous.list === list?.id && previous.option === option?.id
      ? previous : { list: list?.id, option: option?.id });
    option?.scrollIntoView({ block: "nearest" });
  }, [slashOpen, activeIndex, matches]);
  useEffect(() => {
    if (!loading || !onStop) return;
    const stop = (event: globalThis.KeyboardEvent) => {
      if (event.key !== "Escape" || shouldIgnoreEscapeForStopGeneration(event)) return;
      event.preventDefault();
      onStop();
    };
    window.addEventListener("keydown", stop);
    return () => window.removeEventListener("keydown", stop);
  }, [loading, onStop]);
  const pinCommand = useCallback((command: CommandInfo, removeActiveToken = true) => {
    if (command.available === false) return;
    setPinned((current) => current.some((item) => keyFor(item) === keyFor(command)) ? current : [...current, { ...command, selectionId: crypto.randomUUID() }]);
    const active = removeActiveToken ? activeSlashToken(value, caret) : null;
    const nextValue = active ? value.slice(0, active.start) + value.slice(active.end) : value;
    const nextCaret = active?.start ?? caret;
    setValue(nextValue);
    setCaret(nextCaret);
    setDismissedToken(`${nextValue}:${nextCaret}`);
    requestAnimationFrame(() => {
      textareaRef.current?.focus();
      textareaRef.current?.setSelectionRange(nextCaret, nextCaret);
    });
  }, [setValue, value, caret]);
  const consumeRef = useRef(onExternalSkillConsumed);
  consumeRef.current = onExternalSkillConsumed;
  const pinRef = useRef(pinCommand);
  pinRef.current = pinCommand;
  useEffect(() => {
    if (!externalSkill) return;
    pinRef.current({ kind: "skill", name: externalSkill.name, description: externalSkill.description, source: externalSkill.source }, false);
    consumeRef.current?.();
  }, [externalSkill, externalKey]);
  useEffect(() => {
    if (!slashOpen) return;
    const close = (event: MouseEvent | TouchEvent) => {
      if (!wrapperRef.current?.contains(event.target as Node) && !menuRef.current?.contains(event.target as Node)) setDismissedToken(tokenIdentity);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("touchstart", close, { passive: true });
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("touchstart", close); };
  }, [slashOpen, tokenIdentity]);

  const submit = async (message: PromptInputMessage) => {
    const payload = resolvePromptSubmission(message.text, pinned, catalog);
    if (disabled || !payload) throw new Error("Enter task instructions before sending.");
    const submittedSelectionIds = new Set(pinned.map((command) => command.selectionId));
    const callback = onSubmit;
    const files = await promptFilesForUpload(message.files);
    if (!live.current.mounted || live.current.disabled || live.current.onSubmit !== callback) {
      throw new Error("The active task changed before the attachments were ready.");
    }
    const accepted = await callback(payload.text, files, payload.skills, payload.tools);
    if (accepted === false) throw new Error("The message could not be sent. Your draft has been kept.");
    setPinned((current) => current.filter((command) => !submittedSelectionIds.has(command.selectionId)));
    if (textareaRef.current?.value === message.text) setCaret(0);
  };
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.nativeEvent.isComposing || composing || event.keyCode === 229) return;
    if (slashOpen) {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        setActiveIndex((index) => Math.max(0, Math.min(matches.length - 1, index + (event.key === "ArrowDown" ? 1 : -1))));
        return;
      }
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); setDismissedToken(tokenIdentity); return; }
      if (event.key === "Tab" || (event.key === "Enter" && !event.shiftKey)) {
        const command = matches[activeIndex];
        if (command) { event.preventDefault(); pinCommand(command); }
        else setDismissedToken(tokenIdentity);
        return;
      }
    }
    if (event.key === "Backspace" && value === "" && pinned.length) {
      event.preventDefault();
      setPinned((current) => current.slice(0, -1));
    }
  };

  return <div className="matopt-composer matopt-composer-modern relative w-full" ref={wrapperRef} data-matopt-native-composer="true" data-empty={!value && !pinned.length && !controller.attachments.files.length}>
    {slashOpen && createPortal(<div ref={menuRef} data-matopt-command-menu="true" className="matopt-command-menu matopt-command-menu-modern"
      style={{ position: "fixed", zIndex: 70, ...(menuPosition ?? { visibility: "hidden" }), maxHeight: menuPosition?.maxHeight ?? 360 }}
      onMouseDown={(event) => event.preventDefault()}>
      <PromptInputCommand label="Skills and tools" shouldFilter={false} value={matches[activeIndex] ? keyFor(matches[activeIndex]) : ""}
        onValueChange={(key) => { const index = matches.findIndex((command) => keyFor(command) === key); if (index >= 0) setActiveIndex(index); }}>
        <PromptInputCommandList label="Skills and tools" style={{ maxHeight: Math.max(0, (menuPosition?.maxHeight ?? 360) - 10) }}>
          <PromptInputCommandEmpty>No matching skills or tools.</PromptInputCommandEmpty>
          <PromptInputCommandGroup heading="Skills and tools">
            {matches.map((command) => {
              const { label, Icon, description } = commandPresentation(command);
              const selected = pinned.some((item) => keyFor(item) === keyFor(command));
              return <PromptInputCommandItem key={keyFor(command)} value={keyFor(command)} title={commandToken(command.name)} disabled={command.available === false}
                aria-label={`${label}, ${command.kind}${command.available === false ? ", unavailable" : selected ? ", selected" : ""}`} onSelect={() => pinCommand(command)}>
                <span className="matopt-command-icon"><Icon size={20} strokeWidth={1.8} aria-hidden="true" /></span>
                <div className="matopt-command-copy"><span className="matopt-command-label">{label}</span><span className="matopt-command-description">{description ?? skillIntro(command.description, 120)}</span></div>
                <span className="matopt-command-kind">{command.available === false ? "Unavailable" : selected ? <Check size={16} strokeWidth={1.8} aria-hidden="true" /> : command.kind === "skill" ? "Skill" : "Tool"}</span>
              </PromptInputCommandItem>;
            })}
          </PromptInputCommandGroup>
        </PromptInputCommandList>
      </PromptInputCommand>
    </div>, document.body)}
    <PromptInput multiple onSubmit={submit}
      onDropCapture={(event) => { if (disabled || loading) { event.preventDefault(); event.stopPropagation(); } }}>
      <PromptAttachments pinned={pinned} disabled={disabled} removePinned={(command) => {
        setPinned((current) => current.filter((item) => keyFor(item) !== keyFor(command)));
        requestAnimationFrame(() => textareaRef.current?.focus());
      }} />
      <PromptInputBody className="matopt-composer-body">
        <PromptInputTextarea ref={textareaRef} rows={1} aria-label="Task instructions" disabled={disabled}
          placeholder={loading ? "Steer this turn…" : placeholder} onKeyDown={onKeyDown}
          role="combobox" aria-autocomplete="list" aria-expanded={slashOpen} aria-controls={slashOpen ? menuIds.list : undefined}
          aria-activedescendant={slashOpen ? menuIds.option : undefined}
          onChange={(event) => { setCaret(event.currentTarget.selectionStart); setDismissedToken(null); }}
          onSelect={(event) => setCaret(event.currentTarget.selectionStart)}
          onCompositionStartCapture={() => setComposing(true)} onCompositionEndCapture={() => setComposing(false)}
          onPasteCapture={(event) => { if ((disabled || loading) && event.clipboardData.files.length) { event.preventDefault(); event.stopPropagation(); } }} />
      </PromptInputBody>
      <div className="matopt-composer-footer">
        <PromptInputTools className="matopt-composer-add">
          <PromptInputActionMenu>
            <PromptInputActionMenuTrigger className="matopt-composer-add-button" aria-label="Add photos and files" disabled={disabled || loading}><Plus size={20} strokeWidth={1.8} aria-hidden="true" /></PromptInputActionMenuTrigger>
            <PromptInputActionMenuContent className="matopt-composer-attachment-menu"><PromptInputActionAddAttachments disabled={disabled || loading} /></PromptInputActionMenuContent>
          </PromptInputActionMenu>
          {toolbar && <div className="matopt-composer-toolbar">{toolbar}</div>}
        </PromptInputTools>
        <PromptInputTools className="matopt-composer-actions">
          <span className="matopt-composer-command-hint" aria-hidden="true"><kbd>/</kbd> for tools</span>
          <PromptInputSubmit className="matopt-composer-send" status={showStop ? "streaming" : "ready"} onStop={onStop}
            disabled={showStop ? !onStop : !canSend} aria-label={showStop ? "Stop" : loading ? "Steer" : "Send"}>
            {showStop ? <Square size={16} strokeWidth={1.8} fill="currentColor" aria-hidden="true" /> : <ArrowUp size={20} strokeWidth={1.8} aria-hidden="true" />}
          </PromptInputSubmit>
        </PromptInputTools>
      </div>
    </PromptInput>
    {showDisclaimer && <p className="matopt-composer-disclaimer">MatOpt can make mistakes. Check important info.</p>}
  </div>;
}

export default function MathInput(props: Props) {
  return <PromptInputProvider><MatOptPrompt {...props} /></PromptInputProvider>;
}
