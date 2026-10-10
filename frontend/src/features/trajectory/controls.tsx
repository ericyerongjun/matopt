import { forwardRef, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode } from "react";
import { Button as NativeButton } from "../../zcode/components/ui/button.js";
import { Input as NativeInput } from "../../zcode/components/ui/input.js";
import { Badge } from "../../zcode/components/ui/badge.js";
import { Tabs, TabsList, TabsTrigger } from "../../zcode/components/ui/tabs.js";
import { CodeBlock, CodeBlockHeader, CodeBlockCopyButton } from "../../zcode/components/ai-elements/code-block.js";
export const Button = forwardRef<HTMLButtonElement, ButtonHTMLAttributes<HTMLButtonElement> & { size?: "sm"; children?: ReactNode }>((props, ref) => <NativeButton variant="ghost" {...props} ref={ref} />);
export const Input = NativeInput;
export function Pill({ children }: { children?: ReactNode }) { return <Badge variant="secondary">{children}</Badge>; }
export function SegmentedTabs<T extends string>({ value, onChange, label, items }: { value: T; onChange: (value: T) => void; label: string; items: Array<{ value: T; label: ReactNode; id: string; panelId: string }> }) {
 return <Tabs value={value} onValueChange={value => onChange(value as T)}><TabsList aria-label={label}>{items.map(item => <TabsTrigger key={item.value} value={item.value} id={item.id} aria-controls={item.panelId}>{item.label}</TabsTrigger>)}</TabsList></Tabs>;
}
export type JsonTreeLabels = Record<string, string | ((action: string) => string)>;
export function JsonTree({ data, label }: { data: unknown; label: string; labels?: JsonTreeLabels }) { const code=JSON.stringify(data, null, 2); return <CodeBlock code={code} language="json" aria-label={label}><CodeBlockHeader><span>{label}</span><CodeBlockCopyButton /></CodeBlockHeader></CodeBlock>; }
