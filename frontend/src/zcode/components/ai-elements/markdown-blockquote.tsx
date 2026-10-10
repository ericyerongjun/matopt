/* Vendored whole file from ZCode 29628c9acdb81b703bbd4080c207a0e7ce5e276e: packages/ui/src/components/ai-elements/markdown-blockquote.tsx
 * Apache-2.0; original notices retained. MatOpt host adaptations are identified below. */
"use client";

import type { ComponentProps } from "react";
import { cn } from "@/components/lib/utils.js";

export type MarkdownBlockquoteProps = ComponentProps<"blockquote"> & {
  node?: unknown;
};

export function MarkdownBlockquote({ className, node: _node, ...props }: MarkdownBlockquoteProps) {
  return (
    <blockquote
      className={cn(
        "my-4 border-border border-l-2 pl-3 text-foreground-subtle",
        "[&_p]:my-0 [&_p+p]:mt-2",
        className,
      )}
      data-markdown-blockquote=""
      {...props}
    />
  );
}
