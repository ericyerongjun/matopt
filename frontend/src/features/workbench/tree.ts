/** Migrated from ZCode packages/ui/src/workspace-file-tree/model.ts,
 * upstream 29628c9acdb81b703bbd4080c207a0e7ce5e276e (Apache-2.0).
 * The flatten/compact algorithm is copied; node types are narrowed to MatOpt's listed files.
 * buildWorkspaceTree is a MatOpt HTTP-list adapter. See THIRD_PARTY_NOTICES.md. */
import type { WorkspaceFile } from "../../types/protocol.ts";
export interface WorkspaceFileTreeNode {
    path: string; name: string; type: "directory" | "file"; depth: number; isSymbolicLink?: boolean;
}
export interface WorkspaceFileTreeRow extends WorkspaceFileTreeNode {
    expanded: boolean; loaded: boolean; loading: boolean; error: Error | null; compactedPaths?: string[];
}
export function isWorkspaceFileTreeAutoFlattenableDirectory(
    node: Pick<WorkspaceFileTreeNode, "type" | "isSymbolicLink"> | undefined,
): node is WorkspaceFileTreeNode {
    return node?.type === "directory" && node.isSymbolicLink !== true;
}
export function flattenWorkspaceFileTreeRows(params: {
  rootPath: string;
  childrenByDirectory: Map<string, WorkspaceFileTreeNode[]>;
  expandedPaths: Set<string>;
  loadedDirectoryPaths: Set<string>;
  loadingDirectoryPaths: Set<string>;
  errorByDirectory: Map<string, Error>;
  flattenEmptyDirectories?: boolean;
}): WorkspaceFileTreeRow[] {
  const rows: WorkspaceFileTreeRow[] = [];

  function compactDirectoryNode(node: WorkspaceFileTreeNode): {
    node: WorkspaceFileTreeNode;
    compactedPaths?: string[];
    childDepthOffset: number;
  } {
    if (!params.flattenEmptyDirectories || !isWorkspaceFileTreeAutoFlattenableDirectory(node)) {
      return { node, childDepthOffset: 0 };
    }

    const compactedNodes = [node];
    let currentNode = node;

    while (
      params.loadedDirectoryPaths.has(currentNode.path) &&
      !params.loadingDirectoryPaths.has(currentNode.path) &&
      !params.errorByDirectory.has(currentNode.path)
    ) {
      const children = params.childrenByDirectory.get(currentNode.path) ?? [];
      if (children.length !== 1 || !isWorkspaceFileTreeAutoFlattenableDirectory(children[0])) {
        break;
      }

      currentNode = children[0];
      compactedNodes.push(currentNode);
    }

    if (compactedNodes.length === 1) {
      return { node, childDepthOffset: 0 };
    }

    return {
      node: {
        ...currentNode,
        depth: node.depth,
        name: compactedNodes.map((compactedNode) => compactedNode.name).join("/"),
      },
      compactedPaths: compactedNodes.map((compactedNode) => compactedNode.path),
      childDepthOffset: currentNode.depth - node.depth,
    };
  }

  function visit(directoryPath: string, depthOffset = 0) {
    const children = params.childrenByDirectory.get(directoryPath) ?? [];
    for (const child of children) {
      const compacted = compactDirectoryNode({
        ...child,
        depth: child.depth - depthOffset,
      });
      const representedPaths = compacted.compactedPaths ?? [compacted.node.path];
      const expanded = representedPaths.some((path) => params.expandedPaths.has(path));
      rows.push({
        ...compacted.node,
        expanded,
        loaded: params.loadedDirectoryPaths.has(compacted.node.path),
        loading: params.loadingDirectoryPaths.has(compacted.node.path),
        error: params.errorByDirectory.get(compacted.node.path) ?? null,
        ...(compacted.compactedPaths ? { compactedPaths: compacted.compactedPaths } : {}),
      });

      if (compacted.node.type === "directory" && expanded) {
        visit(compacted.node.path, depthOffset + compacted.childDepthOffset);
      }
    }
  }

  visit(params.rootPath);
  return rows;
}

/** Directory-first ordering is the upstream model comparator. */
export function buildWorkspaceTree(files: WorkspaceFile[]) {
    const childrenByDirectory = new Map<string, WorkspaceFileTreeNode[]>();
    const directoryPaths = new Set<string>([""]);
    const nodes = new Map<string, WorkspaceFileTreeNode>();
    for (const file of files) {
        const segments = file.path.split("/").filter(Boolean);
        for (let depth = 0; depth < segments.length; depth++) {
            const path = segments.slice(0, depth + 1).join("/");
            const directory = depth < segments.length - 1;
            if (!nodes.has(path)) nodes.set(path, { path, name: segments[depth], type: directory ? "directory" : "file", depth });
            if (directory) directoryPaths.add(path);
        }
    }
    for (const node of nodes.values()) {
        const parent = node.path.includes("/") ? node.path.slice(0, node.path.lastIndexOf("/")) : "";
        const children = childrenByDirectory.get(parent) ?? [];
        children.push(node);
        childrenByDirectory.set(parent, children);
    }
    for (const children of childrenByDirectory.values()) children.sort((left, right) => {
        if (left.type !== right.type) return left.type === "directory" ? -1 : 1;
        return left.name.localeCompare(right.name);
    });
    return { childrenByDirectory, directoryPaths };
}
/** Direct migration of helpers.ts replaceSetValue. */
export function replaceSetValue(set: Set<string>, value: string, present: boolean): Set<string> {
    const next = new Set(set);
    if (present) next.add(value); else next.delete(value);
    return next;
}
