/** Syntax-highlighted diff via @pierre/diffs; loaded only when a file is expanded. */
import { PatchDiff } from "@pierre/diffs/react";
import { useSyncExternalStore } from "react";

function subscribeToTheme(onChange: () => void) {
    const observer = new MutationObserver(onChange);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
}

export default function HighlightedDiff({ patch }: { patch: string }) {
    // The app's own theme, which may differ from the operating system's.
    const dark = useSyncExternalStore(subscribeToTheme, () => document.documentElement.classList.contains("dark"));
    return <PatchDiff patch={patch} disableWorkerPool options={{ diffStyle: "unified", disableFileHeader: true, themeType: dark ? "dark" : "light", overflow: "wrap" }} />;
}
