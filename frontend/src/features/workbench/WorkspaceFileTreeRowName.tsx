/** Direct migration from ZCode packages/ui/src/workspace-file-tree/WorkspaceFileTreeRowName.tsx.
 * Upstream 29628c9acdb81b703bbd4080c207a0e7ce5e276e, Apache-2.0; see THIRD_PARTY_NOTICES.md. */
import { Fragment } from "react";

export function WorkspaceFileTreeRowName({ name, className, slashClassName }: {
    name: string; className?: string; slashClassName?: string;
}) {
    const segments = name.split("/");
    if (segments.length <= 1) return <span className={className}>{name}</span>;
    return <span className={className}>{segments.map((segment, index) => (
        <Fragment key={`${segment}-${index}`}>
            {index > 0 ? <span className={slashClassName}>/</span> : null}
            <span>{segment}</span>
        </Fragment>
    ))}</span>;
}
