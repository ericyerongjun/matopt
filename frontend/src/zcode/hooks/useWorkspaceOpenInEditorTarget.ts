/** MatOpt browser capability adapter. Remote desktop editor targets are unavailable. */
import type { OpenInEditorRemoteTarget } from "../shared/index.js";
export function useWorkspaceOpenInEditorTarget(_scope: { workspacePath?: string; workspaceIdentity?: string; workspaceRemoteSessionId?: string }): {
    isRemoteWorkspace: boolean; remoteTarget: OpenInEditorRemoteTarget | undefined;
} {
    return { isRemoteWorkspace: false, remoteTarget: undefined };
}
