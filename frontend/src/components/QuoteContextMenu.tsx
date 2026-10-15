/** Native menu keyboard/focus behavior for a selected conversation passage. */
import { Copy, Quote } from "lucide-react";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "../zcode/components/ui/dropdown-menu.js";

export default function QuoteContextMenu({ x, y, onQuote, onCopy, onClose }: {
    x: number; y: number; onQuote: () => void; onCopy: () => void; onClose: () => void;
}) {
    return <DropdownMenu open onOpenChange={(open) => { if (!open) onClose(); }} modal={false}>
        <DropdownMenuTrigger asChild><button type="button" tabIndex={-1} aria-label="Selected passage actions"
            style={{ position: "fixed", left: x, top: y, width: 1, height: 1, opacity: 0, pointerEvents: "none" }} /></DropdownMenuTrigger>
        <DropdownMenuContent aria-label="Selected passage" onCloseAutoFocus={(event) => event.preventDefault()} collisionPadding={8}>
            <DropdownMenuItem onSelect={onQuote}><Quote aria-hidden="true" />Quote in reply</DropdownMenuItem>
            <DropdownMenuItem onSelect={onCopy}><Copy aria-hidden="true" />Copy selection</DropdownMenuItem>
        </DropdownMenuContent>
    </DropdownMenu>;
}
