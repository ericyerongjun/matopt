/**
 * Streamdown's Mermaid plugin with the library loaded on first render.
 *
 * `@streamdown/mermaid` imports `mermaid` statically, which put the whole
 * diagram library in the entry chunk for every chat, although few answers
 * contain a diagram. This keeps its interface and defaults but defers the
 * import until a diagram is actually rendered.
 */
import type { MermaidConfig } from "mermaid";

type MermaidInstance = {
    initialize: (config: MermaidConfig) => void;
    render: (id: string, source: string) => Promise<{ svg: string }>;
};

export type LazyDiagramPlugin = {
    name: "mermaid";
    type: "diagram";
    language: string;
    getMermaid: (config?: MermaidConfig) => MermaidInstance;
};

const DEFAULTS: MermaidConfig = {
    startOnLoad: false, theme: "default", securityLevel: "strict", fontFamily: "monospace", suppressErrorRendering: true,
};

let library: Promise<typeof import("mermaid").default> | null = null;
const loadMermaid = () => (library ??= import("mermaid").then((module) => module.default));

export function createLazyMermaidPlugin(options: { config?: MermaidConfig } = {}): LazyDiagramPlugin {
    let config: MermaidConfig = { ...DEFAULTS, ...options.config };
    let applied: MermaidConfig | null = null;
    const instance: MermaidInstance = {
        initialize(next) { config = { ...DEFAULTS, ...options.config, ...next }; },
        async render(id, source) {
            const mermaid = await loadMermaid();
            // Mermaid's configuration is global; re-apply when this plugin's differs.
            if (applied !== config) { mermaid.initialize(config); applied = config; }
            return mermaid.render(id, source);
        },
    };
    return {
        name: "mermaid", type: "diagram", language: "mermaid",
        getMermaid(next) { if (next) instance.initialize(next); return instance; },
    };
}

export const lazyMermaid = createLazyMermaidPlugin();
