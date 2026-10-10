/** Browser-host type boundary for optional native ZCode service contexts.
 * MatOpt supplies HTTP URLs and does not install desktop RPC services. */
import type { FileMediaPreview, FileStat, Locale, LocalePreference } from "../shared/index.js";
export interface BroadcastMessage { channel: string; payload: unknown; sourceWindowId?: number }
export interface IBroadcastService {
    send(message: BroadcastMessage): Promise<void>;
    onMessage(listener: (message: BroadcastMessage) => void): { dispose(): void };
}
export interface ISettingService {
    get(): Promise<{ locale?: Locale; localePreference?: LocalePreference }>;
    update(patch: { locale?: Locale; localePreference?: LocalePreference }): Promise<void>;
}
export interface IServiceAccessor {
    fileService: {
        readMediaPreview(params: { path: string; maxBytes?: number }): Promise<FileMediaPreview>;
        stat(params: { path: string }): Promise<FileStat>;
    };
}
