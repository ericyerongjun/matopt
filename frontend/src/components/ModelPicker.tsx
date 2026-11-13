/** Fixed Flash configuration, bound to the copied native ZCode Radix menu. */
import { useEffect, useState } from "react";
import { ChevronDown } from "lucide-react";
import type { ProviderCatalog, ProviderId, ReasoningLevel, ReasoningStop } from "../types/protocol.ts";
import { Button } from "../zcode/components/ui/button.js";
import {
    DropdownMenu, DropdownMenuCheckboxItem, DropdownMenuContent, DropdownMenuLabel, DropdownMenuRadioGroup,
    DropdownMenuRadioItem, DropdownMenuSeparator, DropdownMenuTrigger,
} from "../zcode/components/ui/dropdown-menu.js";

interface Props {
    catalog: ProviderCatalog | null; provider: ProviderId | null; model: string | null; label: string;
    disabled?: boolean; menuUp?: boolean; levels: ReasoningStop[]; reasoning: ReasoningLevel;
    onSelect: (provider: ProviderId, model: string) => void; onReasoningChange: (level: ReasoningLevel) => void;
    ultrafast?: boolean; onUltrafastChange?: (value: boolean) => void;
}

export default function ModelPicker({ label, disabled = false, menuUp = false, levels, reasoning, onReasoningChange, ultrafast = false, onUltrafastChange }: Props) {
    const [open, setOpen] = useState(false);
    const available = levels.filter((level) => level.id !== "medium");
    const enabled = !disabled && available.length > 0;
    useEffect(() => { if (!enabled) setOpen(false); }, [enabled]);
    const selected = available.find((level) => level.id === reasoning);
    const selectedLabel = ultrafast ? "Ultrafast" : reasoning === "off" ? "Off" : selected?.label ?? reasoning.charAt(0).toUpperCase() + reasoning.slice(1);
    return (
        <DropdownMenu open={open && enabled} onOpenChange={setOpen}>
            <DropdownMenuTrigger asChild disabled={!enabled}>
                <Button type="button" variant="ghost" size="sm" className="matopt-model-trigger" data-ultrafast={ultrafast} aria-label={`${label || "DeepSeek V4.1 Flash"} thinking effort`} title={`${label} · ${selectedLabel} thinking effort`}>
                    <span className="matopt-model-name">{label || "DeepSeek V4.1 Flash"}</span><span className="matopt-model-effort">{selectedLabel}</span><ChevronDown className="size-3.5" aria-hidden="true" />
                </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent side={menuUp ? "top" : "bottom"} align="start" collisionPadding={8} className="matopt-model-menu">
                <div className="matopt-model-description"><strong>{label || "DeepSeek V4.1 Flash"}</strong><span>{ultrafast ? "Quick, concise responses for live demos. Choose a thinking level for deeper reasoning." : "Choose how much time to spend thinking."}</span></div>
                {onUltrafastChange && <><DropdownMenuCheckboxItem checked={ultrafast} onCheckedChange={(checked) => {
                    if (enabled) onUltrafastChange(checked === true);
                }}>Ultrafast demo</DropdownMenuCheckboxItem><DropdownMenuSeparator /></>}
                <DropdownMenuLabel>Thinking effort</DropdownMenuLabel>
                <DropdownMenuRadioGroup value={ultrafast ? "" : reasoning} onValueChange={(value) => {
                    if (enabled && available.some((level) => level.id === value)) onReasoningChange(value as ReasoningLevel);
                }}>
                    {available.map((level) => <DropdownMenuRadioItem key={level.id} value={level.id}>
                        {level.id === "off" ? "Off" : level.label}
                    </DropdownMenuRadioItem>)}
                </DropdownMenuRadioGroup>
            </DropdownMenuContent>
        </DropdownMenu>
    );
}
