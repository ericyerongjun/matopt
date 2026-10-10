import type { JSONValue, UIAction, UINode, UISurface } from "../../types/ui.ts";

const NODE_TYPES = new Set(["markdown", "equation", "comparison", "table", "plot", "mermaid", "tabs", "disclosure", "slider", "number", "select", "toggle", "button", "stack", "grid"]);
const FORBIDDEN_KEYS = new Set(["__proto__", "constructor", "prototype"]);

export function isJSONValue(value: unknown, depth = 0): value is JSONValue {
    if (depth > 24) return false;
    if (value === null || typeof value === "string" || typeof value === "boolean") return true;
    if (typeof value === "number") return Number.isFinite(value);
    if (Array.isArray(value)) return value.length <= 100_000 && value.every((item) => isJSONValue(item, depth + 1));
    return typeof value === "object" && Object.entries(value).every(([key, item]) => !FORBIDDEN_KEYS.has(key) && isJSONValue(item, depth + 1));
}

export function isState(value: unknown): value is Record<string, JSONValue> {
    return Boolean(value && typeof value === "object" && !Array.isArray(value) && isJSONValue(value));
}

/** The server validates the full contract; reject corrupt or unsupported history at the renderer boundary too. */
export function isSurface(value: unknown): value is UISurface {
    if (!value || typeof value !== "object") return false;
    const surface = value as UISurface;
    if (surface.version !== 1 || typeof surface.id !== "string" || !surface.id || FORBIDDEN_KEYS.has(surface.id) || !Number.isInteger(surface.revision) || surface.revision < 0
        || !["native", "mini_app"].includes(surface.kind) || !["preview", "committed", "error"].includes(surface.status)
        || typeof surface.title !== "string" || typeof surface.fallback !== "string" || !isState(surface.state)
        || !Array.isArray(surface.nodes) || !Array.isArray(surface.actions)) return false;
    if (surface.computed_state !== undefined && surface.computed_state !== null && !isState(surface.computed_state)) return false;
    const seen = new Set<string>();
    let count = 0;
    const validNode = (node: UINode, depth = 0): boolean => {
        if (!node || typeof node !== "object" || depth > 12 || ++count > 500 || typeof node.id !== "string" || !node.id || FORBIDDEN_KEYS.has(node.id) || seen.has(node.id)
            || !NODE_TYPES.has(node.type) || !isState(node.props)) return false;
        if (typeof node.props.stateKey === "string" && FORBIDDEN_KEYS.has(node.props.stateKey)) return false;
        seen.add(node.id);
        return node.children === undefined || (Array.isArray(node.children) && node.children.every((child) => validNode(child, depth + 1)));
    };
    return surface.nodes.every((node) => validNode(node))
        && surface.actions.every((action) => action && typeof action === "object" && typeof action.id === "string" && !FORBIDDEN_KEYS.has(action.id) && typeof action.label === "string" && isState(action.input_schema) && Number.isInteger(action.handler_version))
        && (surface.kind !== "mini_app" || surface.status === "preview" || typeof surface.app?.html === "string");
}

/** A deliberately small data binding language: no expressions, evaluation, URLs, or executable code. */
export function hasBindings(value: JSONValue): boolean {
    if (Array.isArray(value)) return value.some(hasBindings);
    if (value && typeof value === "object") return (Object.keys(value).length === 1 && typeof value.$state === "string") || Object.values(value).some(hasBindings);
    return false;
}

export function resolveBindings(value: JSONValue, state: Record<string, JSONValue>): JSONValue {
    if (Array.isArray(value)) return value.map((item) => resolveBindings(item, state));
    if (value && typeof value === "object") {
        if (Object.keys(value).length === 1 && typeof value.$state === "string") return Object.prototype.hasOwnProperty.call(state, value.$state) ? state[value.$state] : null;
        return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, resolveBindings(item, state)]));
    }
    return value;
}

export function initialSurfaceState(surface: UISurface): Record<string, JSONValue> {
    const state = { ...surface.state };
    const walk = (nodes: UINode[]) => nodes.forEach((node) => {
        const key = node.props.stateKey;
        if (typeof key === "string" && !FORBIDDEN_KEYS.has(key) && !Object.prototype.hasOwnProperty.call(state, key)) {
            state[key] = node.props.value ?? (node.type === "toggle" ? false : node.type === "number" || node.type === "slider" ? node.props.min ?? 0 : "");
        }
        walk(node.children ?? []);
    });
    walk(surface.nodes);
    return state;
}

export function computedSurfaceState(surface: UISurface): Record<string, JSONValue> {
    const computed = (surface as UISurface & { computed_state?: Record<string, JSONValue> | null }).computed_state;
    return isState(computed) ? { ...computed } : initialSurfaceState(surface);
}

export function resetSurfaceState(surface: UISurface, previous?: Record<string, JSONValue>): Record<string, JSONValue> {
    const state = initialSurfaceState(surface);
    if (previous) for (const key of Object.keys(state)) {
        if (Object.prototype.hasOwnProperty.call(previous, key)) state[key] = previous[key];
    }
    const walk = (nodes: UINode[]) => nodes.forEach((node) => {
        if (typeof node.props.stateKey === "string" && !FORBIDDEN_KEYS.has(node.props.stateKey) && node.props.value !== undefined) state[node.props.stateKey] = node.props.value;
        walk(node.children ?? []);
    });
    walk(surface.nodes);
    return state;
}

export function actionValues(action: UIAction, state: Record<string, JSONValue>): Record<string, JSONValue> {
    if (action.input_schema.additionalProperties === true) return { ...state };
    const properties = action.input_schema.properties;
    if (!properties || typeof properties !== "object" || Array.isArray(properties)) return {};
    return Object.fromEntries(Object.keys(properties).filter((key) => Object.prototype.hasOwnProperty.call(state, key)).map((key) => [key, state[key]]));
}

/** Mini-apps may update declared state, never introduce hidden command or capability fields. */
export function declaredStateUpdate(value: unknown, current: Record<string, JSONValue>): Record<string, JSONValue> | null {
    if (!isState(value) || JSON.stringify(value).length > 64_000) return null;
    if (Object.keys(value).some((key) => !Object.prototype.hasOwnProperty.call(current, key))) return null;
    return { ...current, ...value };
}

export function hasChangedActionInputs(surface: UISurface, state: Record<string, JSONValue>, resultState: Record<string, JSONValue>): boolean {
    return surface.actions.some((action) => JSON.stringify(actionValues(action, state)) !== JSON.stringify(actionValues(action, resultState)));
}

export function surfaceExplanation(surface: UISurface, state: Record<string, JSONValue>, resultState = state): string {
    const pending = hasChangedActionInputs(surface, state, resultState);
    return `Explain the current result in “${surface.title}” (surface ${surface.id}, revision ${surface.revision}).\nCurrent controls: ${JSON.stringify(state).slice(0, 6000)}\n${pending ? `Displayed result was computed with: ${JSON.stringify(resultState).slice(0, 6000)}. The changed controls have not been recomputed; explain the existing result with that distinction.\n` : ""}Result summary: ${surface.fallback.slice(0, 6000)}`;
}

export function surfaceExport(surface: UISurface, state: Record<string, JSONValue>, computed = computedSurfaceState(surface)): string {
    return JSON.stringify({ ...surface, state, computed_state: computed }, null, 2);
}
