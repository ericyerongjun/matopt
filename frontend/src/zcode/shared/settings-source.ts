/* Vendored whole file from ZCode 29628c9acdb81b703bbd4080c207a0e7ce5e276e: packages/shared/src/settings-source.ts
 * Apache-2.0; original notices retained. */
export type SettingsDirectorySource = "zcode" | "agents" | "claude";

export type SettingsDirectoryScope = "user" | "project";

export interface SettingsDirectoryLocation {
  source: SettingsDirectorySource;
  scope: SettingsDirectoryScope;
  directoryPath: string;
  projectPath?: string;
}
