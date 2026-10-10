from types import SimpleNamespace

import pytest

from app.agent.scheduler import ToolDependency, ToolScheduler, schedule_calls


def test_parallel_safety_and_cap():
    scheduler = ToolScheduler(max_concurrency=2)
    nodes = [ToolDependency(str(i), "read", read_only=True) for i in range(5)]
    assert scheduler.schedule(nodes).parallel_groups == [["0", "1"], ["2", "3"], ["4"]]
    assert not scheduler.can_run_in_parallel(ToolDependency("x", read_only=True, destructive=True))
    assert not scheduler.can_run_in_parallel(ToolDependency("x", read_only=True, concurrent_safe=False))
    assert scheduler.can_run_in_parallel(ToolDependency("x", side_effect_scope="none"))


def test_dependency_levels_and_invalid_graphs():
    scheduler = ToolScheduler()
    a, b = ToolDependency("a", concurrent_safe=True), ToolDependency("b", depends_on=("a",), concurrent_safe=True)
    assert scheduler.schedule([b, a]).parallel_groups == [["a"], ["b"]]
    with pytest.raises(ValueError, match="Circular"):
        scheduler.schedule([ToolDependency("a", depends_on=("b",)), ToolDependency("b", depends_on=("a",))])
    with pytest.raises(ValueError, match="Missing"):
        scheduler.schedule([ToolDependency("a", depends_on=("absent",))])
    with pytest.raises(ValueError, match="Duplicate tool"):
        scheduler.schedule([a, a])
    with pytest.raises(ValueError):
        ToolScheduler(0)


def test_read_write_barriers_preserve_observation_order():
    def call(id, safe):
        tool = SimpleNamespace(name="read" if safe else "edit", read_only=safe, concurrent_safe=safe,
                               destructive=False, side_effect_scope="none" if safe else "workspace")
        return SimpleNamespace(call=SimpleNamespace(id=id), tool=tool)
    schedule = schedule_calls([call("read-a", True), call("read-b", True), call("write", False), call("read-after", True)])
    assert schedule.parallel_groups == [["read-a", "read-b"], ["write"], ["read-after"]]
