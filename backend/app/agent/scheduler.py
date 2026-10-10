"""Tool scheduler transliterated from ZCode @ 29628c9acdb81b703bbd4080c207a0e7ce5e276e.

Source: apps/zcode-cli/packages/core/src/tool/scheduler.ts (Apache-2.0).
Preserves topological ordering, parallel eligibility, dependency levels and
DEFAULT_MAX_CONCURRENCY. MatOpt adds explicit duplicate/missing-edge errors
and a barrier chain around unsafe calls so a dependency cannot reorder a
read across a prior write. Call results still return in model call order.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

DEFAULT_MAX_CONCURRENCY = 10
READ_ONLY_TOOLS = frozenset({"Read", "Glob", "Grep", "WebSearch", "WebFetch", "TodoRead", "TodoWrite", "AskUserQuestion", "Skill"})


@dataclass(frozen=True)
class ToolDependency:
    tool_call_id: str
    tool_name: str | None = None
    depends_on: tuple[str, ...] = ()
    read_only: bool | None = None
    destructive: bool | None = None
    concurrent_safe: bool | None = None
    side_effect_scope: str | None = None


@dataclass
class ToolSchedule:
    items: list[ToolDependency]
    parallel_groups: list[list[str]] = field(default_factory=list)
    execution_order: list[str] = field(default_factory=list)


class ToolScheduler:
    def __init__(self, max_concurrency: int = DEFAULT_MAX_CONCURRENCY, read_only_tools=READ_ONLY_TOOLS):
        if type(max_concurrency) is not int or max_concurrency < 1:
            raise ValueError("max_concurrency must be a positive integer")
        self.max_concurrency = max_concurrency
        self.read_only_tools = read_only_tools

    def can_run_in_parallel(self, tool: ToolDependency) -> bool:
        has_name = bool(tool.tool_name)
        has_metadata = any(v is not None for v in (tool.read_only, tool.destructive, tool.concurrent_safe, tool.side_effect_scope))
        if not has_name and not has_metadata:
            return True
        read_only = tool.read_only if tool.read_only is not None else tool.tool_name in self.read_only_tools
        if tool.destructive:
            return False
        if tool.concurrent_safe is not None:
            return tool.concurrent_safe
        return bool(read_only or tool.side_effect_scope == "none")

    def schedule(self, tools: list[ToolDependency]) -> ToolSchedule:
        ids = {tool.tool_call_id for tool in tools}
        if len(ids) != len(tools):
            raise ValueError("Duplicate tool call id in tool scheduling")
        for tool in tools:
            if len(set(tool.depends_on)) != len(tool.depends_on):
                raise ValueError("Duplicate dependency in tool scheduling")
            if any(dep not in ids for dep in tool.depends_on):
                raise ValueError("Missing dependency in tool scheduling")
        remaining = {tool.tool_call_id: tool for tool in tools}
        in_degree = {tool.tool_call_id: len(tool.depends_on) for tool in tools}
        queue = deque(tool.tool_call_id for tool in tools if not tool.depends_on)
        sorted_items = []
        while queue:
            current = queue.popleft()
            item = remaining.pop(current, None)
            if item is None:
                continue
            sorted_items.append(item)
            for tool_id, dependent in remaining.items():
                if current in dependent.depends_on:
                    in_degree[tool_id] -= 1
                    if in_degree[tool_id] == 0:
                        queue.append(tool_id)
        if remaining:
            raise ValueError("Circular dependency detected in tool scheduling")

        levels: dict[str, int] = {}
        by_level: dict[int, list[ToolDependency]] = {}
        for item in sorted_items:
            level = max((levels[dep] for dep in item.depends_on), default=-1) + 1
            levels[item.tool_call_id] = level
            by_level.setdefault(level, []).append(item)
        groups: list[list[str]] = []
        for level in sorted(by_level):
            group: list[str] = []
            for item in by_level[level]:
                if self.can_run_in_parallel(item):
                    if len(group) >= self.max_concurrency:
                        groups.append(group)
                        group = []
                    group.append(item.tool_call_id)
                else:
                    if group:
                        groups.append(group)
                        group = []
                    groups.append([item.tool_call_id])
            if group:
                groups.append(group)
        for group in groups:
            for tool_id in group:
                item = next(t for t in tools if t.tool_call_id == tool_id)
                if set(item.depends_on).intersection(group):
                    raise ValueError("Dependency scheduled in the same parallel group")
        return ToolSchedule(tools, groups, [tool_id for group in groups for tool_id in group])


def schedule_calls(calls, max_concurrency: int = DEFAULT_MAX_CONCURRENCY) -> ToolSchedule:
    """Adapt normalized MatOpt calls; unsafe/unknown tools form write barriers."""
    scheduler = ToolScheduler(max_concurrency)
    dependencies: list[ToolDependency] = []
    pending: list[str] = []
    last_barrier: str | None = None
    for nc in calls:
        tool = nc.tool
        metadata = dict(tool_name=tool.name if tool else nc.call.name,
                        read_only=tool.read_only if tool else False,
                        destructive=tool.destructive if tool else False,
                        concurrent_safe=tool.concurrent_safe if tool else False,
                        side_effect_scope=tool.side_effect_scope if tool else "system")
        item = ToolDependency(nc.call.id, **metadata)
        edges = tuple(pending) if not scheduler.can_run_in_parallel(item) else ()
        if last_barrier is not None:
            edges = tuple(dict.fromkeys((*edges, last_barrier)))
        dependencies.append(ToolDependency(nc.call.id, depends_on=edges, **metadata))
        if scheduler.can_run_in_parallel(item):
            pending.append(nc.call.id)
        else:
            last_barrier = nc.call.id
            pending = []
    return scheduler.schedule(dependencies)
