/* Vendored whole file from ZCode 29628c9acdb81b703bbd4080c207a0e7ce5e276e: packages/shared/src/remoteAssetInstallMode.ts
 * Apache-2.0; original notices retained. */
export const REMOTE_ASSET_INSTALL_MODES = ["local-download-upload", "remote-download"] as const;

export type RemoteAssetInstallMode = (typeof REMOTE_ASSET_INSTALL_MODES)[number];

export const DEFAULT_REMOTE_ASSET_INSTALL_MODE: RemoteAssetInstallMode = "local-download-upload";

export function normalizeRemoteAssetInstallMode(
  mode?: RemoteAssetInstallMode | null,
): RemoteAssetInstallMode {
  return mode === "remote-download" ? mode : DEFAULT_REMOTE_ASSET_INSTALL_MODE;
}
