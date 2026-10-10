import { useEffect, useMemo, useRef, useState } from "react";
import type { JSONValue } from "../../types/ui.ts";
import { sandboxOrigin, validateFrameMessage } from "./bridge.ts";

/** Only a dedicated static runtime may execute generated HTML. It has no host API proxy or same-origin privileges. */
export function MiniAppFrame({ surfaceId, title, html, state = {}, onStateChange, fallback = "This interactive preview is unavailable." }: {
    surfaceId: string; title: string; html: string;
    state?: Record<string, JSONValue>;
    onStateChange?: (state: Record<string, JSONValue>) => void;
    fallback?: string;
}) {
    const frame = useRef<HTMLIFrameElement>(null);
    const [height, setHeight] = useState(320);
    const [ready, setReady] = useState(false);
    const [unavailable, setUnavailable] = useState(false);
    const identity = useMemo(() => ({ channel: crypto.randomUUID(), surfaceId }), [surfaceId, html]);
    const origin = typeof window === "undefined" ? null : sandboxOrigin(window.location, import.meta.env?.VITE_UI_SANDBOX_ORIGIN);
    const source = origin ? `${origin}/#${new URLSearchParams({ channel: identity.channel, surface: surfaceId })}` : undefined;
    const latest = useRef({ state, onStateChange, html }); latest.current = { state, onStateChange, html };
    const theme = () => document.documentElement.classList.contains("dark") ? "dark" : "light";

    useEffect(() => {
        setReady(false); setUnavailable(false); setHeight(320);
        if (!source) return;
        const timeout = setTimeout(() => setUnavailable(true), 10_000);
        const receive = (event: MessageEvent) => {
            // The sandbox has an opaque origin, so source identity and the unguessable channel bind the frame.
            if (!frame.current || event.source !== frame.current.contentWindow) return;
            const message = validateFrameMessage(event.data, identity, latest.current.state);
            if (!message) return;
            if (message.type === "matopt:ready") {
                frame.current.contentWindow?.postMessage({ type: "matopt:init", version: 1, ...identity, html: latest.current.html, state: latest.current.state, theme: theme() }, "*");
                clearTimeout(timeout); setReady(true); setUnavailable(false);
            } else if (message.type === "matopt:resize") setHeight(message.height);
            else if (message.type === "matopt:state") latest.current.onStateChange?.(message.state);
        };
        const observer = new MutationObserver(() => frame.current?.contentWindow?.postMessage({ type: "matopt:theme", version: 1, ...identity, theme: theme() }, "*"));
        observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class", "data-theme"] });
        window.addEventListener("message", receive);
        return () => { clearTimeout(timeout); observer.disconnect(); window.removeEventListener("message", receive); };
    }, [source, identity]);
    useEffect(() => {
        if (ready) frame.current?.contentWindow?.postMessage({ type: "matopt:state", version: 1, ...identity, state }, "*");
    }, [state, ready, identity]);

    if (!origin) return <p className="ui-surface-note" role="status">{fallback} The preview runtime must use a separate origin.</p>;
    return <div className="ui-mini-app">
        {!ready && !unavailable && <p className="ui-surface-note" role="status">Loading interactive preview…</p>}
        {unavailable && <p className="ui-surface-note" role="status">{fallback} Start the isolated preview runtime to enable it.</p>}
        <iframe ref={frame} key={source} src={source} title={title} sandbox="allow-scripts" referrerPolicy="no-referrer"
            className="ui-mini-app-frame" style={{ height, visibility: ready ? "visible" : "hidden", ...(unavailable ? { height: 0 } : {}) }} />
    </div>;
}
