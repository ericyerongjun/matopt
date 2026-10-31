/** Syntax-highlighted diff via @pierre/diffs; loaded only when a file is expanded. */
import { PatchDiff } from "@pierre/diffs/react";

export default function HighlightedDiff({ patch }: { patch: string }) {
    return <PatchDiff patch={patch} disableWorkerPool options={{ diffStyle: "unified", disableFileHeader: true, themeType: "system", overflow: "wrap" }} />;
}
