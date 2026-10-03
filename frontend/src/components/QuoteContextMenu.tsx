/**
 * Right-click menu for a text selection in the thread — "Quote" is the
 * ChatGPT-style action that pins the selection above the composer.
 */

import React, { useEffect, useRef } from "react";
import { Copy, Quote } from "lucide-react";
import "./QuoteContextMenu.css";

interface Props {
    x: number;
    y: number;
    onCopy: () => void;
    onQuote: () => void;
    onClose: () => void;
}

const WIDTH = 168;
const HEIGHT = 96;

export default function QuoteContextMenu({ x, y, onCopy, onQuote, onClose }: Props) {
    const ref = useRef<HTMLDivElement>(null);

    useEffect(() => {
        const onPointer = (event: MouseEvent) => {
            if (!ref.current?.contains(event.target as Node)) onClose();
        };
        const onKey = (event: KeyboardEvent) => {
            if (event.key === "Escape") onClose();
        };
        document.addEventListener("mousedown", onPointer);
        document.addEventListener("keydown", onKey);
        window.addEventListener("scroll", onClose, true);
        window.addEventListener("resize", onClose);
        return () => {
            document.removeEventListener("mousedown", onPointer);
            document.removeEventListener("keydown", onKey);
            window.removeEventListener("scroll", onClose, true);
            window.removeEventListener("resize", onClose);
        };
    }, [onClose]);

    const left = Math.max(8, Math.min(x, window.innerWidth - WIDTH - 8));
    const top = Math.max(8, Math.min(y, window.innerHeight - HEIGHT - 8));

    return (
        <div className="quote-menu" ref={ref} style={{ left, top }} role="menu">
            <button type="button" className="quote-menu__item" role="menuitem" onClick={onCopy}>
                <Copy size={15} />
                <span>Copy</span>
            </button>
            <button type="button" className="quote-menu__item" role="menuitem" onClick={onQuote}>
                <Quote size={15} />
                <span>Quote</span>
            </button>
        </div>
    );
}
