/** MatOpt's narrow barrel for whole pinned ZCode shared modules. */
export { rewriteMarkdownArtifactImageSources, decodeMarkdownArtifactImageSource } from "./markdown-artifact-images.js";
export { getMediaPreviewFormat, MEDIA_PREVIEW_FORMATS } from "./media-preview.js";
export type { MediaPreviewKind } from "./media-preview.js";
export { formatLogPrefix } from "./log-format.js";
export { extractConversationPreviewFileReferences } from "./conversation-preview-artifacts.js";
export { getZCodeToolFamilyForName, isZCodeFileContentWriteToolName, normalizeZCodeToolName } from "./tool-identity.js";
export type { ZCodeKnownToolName, ZCodeToolFamily } from "./tool-identity.js";
export { isTodoPlanToolName } from "./tool-plan-adapter.js";
export { DEFAULT_LOCALE } from "./protocol.js";
export type { FileEntry, FileStat, FileMediaPreview, Locale, LocalePreference } from "./protocol.js";
export type { EditorInfo, IPlatformService, OpenInEditorOptions, OpenInEditorRemoteTarget } from "./platform.js";
export type { RemoteTarget } from "./remoteTarget.js";
export type { GitFileChange, GitRepositorySummary } from "./git.js";
export type { ZCodeAssistantMessageFeedback, ZCodePromptAttachment, ZCodeTimelineMeta,
    ZCodeTaskSnapshotBodyRef, ZCodeTaskSnapshotToolFieldRef, ZCodeTaskSnapshotToolSlice } from "./zcode-task-types-core.js";

export { TID_TASK_NEW_BUTTON, TID_SIDE_PANE_TOGGLE, TID_WORKSPACE_HEADER, TID_WORKSPACE_TITLE, TID_WORKSPACE_PATH, TID_WORKSPACE_MORE_BUTTON, TID_SIDEBAR, TID_TASK_ITEM, testId } from "./test-ids.js";

export type { ZCodeAssistantMessagePart } from "./assistant-message-parts.js";

export { TID_CHAT_REASONING_CONTENT, TID_CHAT_REASONING_TRIGGER } from "./test-ids.js";
