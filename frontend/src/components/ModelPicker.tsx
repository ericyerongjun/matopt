/** Fixed Flash model with a single optional demo profile. */
import { useEffect, useState } from "react";
import { ChevronDown } from "lucide-react";
import { Button } from "../zcode/components/ui/button.js";
import {
    DropdownMenu, DropdownMenuCheckboxItem, DropdownMenuContent, DropdownMenuTrigger,
} from "../zcode/components/ui/dropdown-menu.js";

interface Props {
    label: string;
    disabled?: boolean;
    menuUp?: boolean;
    ultrafast: boolean;
    onUltrafastChange: (value: boolean) => void;
}

export default function ModelPicker({ label, disabled = false, menuUp = false, ultrafast, onUltrafastChange }: Props) {
    const [open, setOpen] = useState(false);
    useEffect(() => { if (disabled) setOpen(false); }, [disabled]);
    const modelLabel = label || "DeepSeek V4.1 Flash";
    const profileLabel = ultrafast ? "Ultrafast" : "Auto";
    return (
        <DropdownMenu open={open && !disabled} onOpenChange={setOpen}>
            <DropdownMenuTrigger asChild disabled={disabled}>
                <Button type="button" variant="ghost" size="sm" className="matopt-model-trigger" data-ultrafast={ultrafast} aria-label={`${modelLabel} response mode`} title={`${modelLabel} · ${profileLabel}`}>
                    <span className="matopt-model-name">{modelLabel}</span><span className="matopt-model-effort">{profileLabel}</span><ChevronDown className="size-3.5" aria-hidden="true" />
                </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent side={menuUp ? "top" : "bottom"} align="start" collisionPadding={8} className="matopt-model-menu">
                <div className="matopt-model-description"><strong>{modelLabel}</strong><span>{ultrafast ? "Quick, concise responses for live demos." : "Uses the model's configured default."}</span></div>
                <DropdownMenuCheckboxItem checked={ultrafast} onCheckedChange={(checked) => {
                    if (!disabled) onUltrafastChange(checked === true);
                }}>Ultrafast Demo</DropdownMenuCheckboxItem>
            </DropdownMenuContent>
        </DropdownMenu>
    );
}
