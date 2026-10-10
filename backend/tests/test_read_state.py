"""File freshness tests stay in-process: no real Jupyter kernels needed."""

import asyncio
import copy
import os

import pytest

from app.agent.tools.base import ToolContext
from app.agent.tools.file_tools import EditFileTool, ReadFileTool, WriteFileTool
from app.agent.tools.read_state import (
    STATE_KEY, clear_read_state, latest_read, parse_read_state_metadata,
    restore_read_state,
)
from app.agent.workspace import Workspace


@pytest.fixture
def ctx(tmp_path):
    async def emit(_):
        pass

    return ToolContext("conv", Workspace("conv", tmp_path), emit, asyncio.Event())


async def read(ctx, path="file.txt", **kwargs):
    return await ReadFileTool()(ctx, {"path": path, **kwargs})


async def edit(ctx, old="first", new="changed", **kwargs):
    return await EditFileTool()(ctx, {"path": "file.txt", "old_string": old, "new_string": new, **kwargs})


@pytest.mark.asyncio
async def test_unread_existing_file_cannot_be_edited_or_overwritten(ctx):
    p = ctx.workspace.root / "file.txt"
    p.write_text("first\nsecond\n")
    for result in (await edit(ctx), await WriteFileTool()(ctx, {"path": "file.txt", "content": "new"})):
        assert not result.ok and result.meta["code"] == "FS_NOT_OBSERVED"
        assert result.meta["path"] == "file.txt" and "Read" in result.error
    assert p.read_text() == "first\nsecond\n"


@pytest.mark.asyncio
async def test_new_write_establishes_fresh_state_and_edits_refresh_it(ctx):
    result = await WriteFileTool()(ctx, {"path": "file.txt", "content": "first\nsecond\n"})
    assert result.ok and result.meta["read_state"]["source_tool"] == "write_file"
    assert (await edit(ctx)).ok
    assert (await edit(ctx, old="second", new="third")).ok
    assert latest_read(ctx.state, "file.txt").source_tool == "edit_file"


@pytest.mark.asyncio
async def test_hash_detects_same_size_write_with_preserved_timestamp(ctx):
    p = ctx.workspace.root / "file.txt"
    p.write_text("first")
    assert (await read(ctx)).ok
    stamp = p.stat()
    p.write_text("other")
    os.utime(p, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    result = await edit(ctx, old="other")
    assert not result.ok and result.meta["code"] == "FS_STALE_VERSION"
    assert p.read_text() == "other"


@pytest.mark.asyncio
async def test_latest_range_read_replaces_older_full_revision(ctx):
    p = ctx.workspace.root / "file.txt"
    p.write_text("first\nsecond\nthird\n")
    await read(ctx)
    p.write_text("first\nnew second\nthird\n")
    ranged = await read(ctx, start_line=2, max_lines=1)
    entry = latest_read(ctx.state, "file.txt")
    assert ranged.ok and entry.offset == 2 and not entry.is_partial_view
    # Explicit policy: a complete range observes the whole file's revision,
    # so exact matching outside the viewed range is allowed.
    assert (await edit(ctx, old="third", new="outside range")).ok


@pytest.mark.asyncio
async def test_tool_truncation_does_not_satisfy_guard(ctx):
    p = ctx.workspace.root / "file.txt"
    p.write_text("first\n" + "z" * 50000)
    result = await read(ctx)
    assert result.meta["read_state"]["is_partial_view"]
    refusal = await edit(ctx)
    assert refusal.meta["code"] == "FS_NOT_OBSERVED" and "truncated" in refusal.error
    assert (await read(ctx, max_lines=1)).ok
    assert not latest_read(ctx.state, "file.txt").is_partial_view
    assert (await edit(ctx)).ok


@pytest.mark.asyncio
async def test_newer_truncated_read_overrides_older_full_view(ctx):
    p = ctx.workspace.root / "file.txt"
    p.write_text("first")
    await read(ctx)
    p.write_text("first\n" + "z" * 50000)
    await read(ctx)
    assert not (await WriteFileTool()(ctx, {"path": "file.txt", "content": "new"})).ok


@pytest.mark.asyncio
async def test_dedup_only_suppresses_fresh_same_range(ctx):
    p = ctx.workspace.root / "file.txt"
    p.write_text("first\nsecond\nthird\n")
    first = await read(ctx, max_lines=1)
    duplicate = await read(ctx, max_lines=1)
    different = await read(ctx, start_line=2, max_lines=1)
    assert first.ok and "first" in first.output
    assert duplicate.ok and duplicate.meta["file_unchanged"]
    assert "Wasted call" in duplicate.output
    assert different.ok and "second" in different.output and not different.meta.get("file_unchanged")


@pytest.mark.asyncio
async def test_identical_touch_is_allowed_only_for_full_view(ctx):
    p = ctx.workspace.root / "file.txt"
    p.write_text("first\nsecond\n")
    await read(ctx, max_lines=1)
    stamp = p.stat()
    os.utime(p, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 1_000_000))
    assert (await edit(ctx)).meta["code"] == "FS_STALE_VERSION"
    await read(ctx)
    stamp = p.stat()
    os.utime(p, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 1_000_000))
    assert (await edit(ctx)).ok


@pytest.mark.asyncio
async def test_replay_restores_guard_and_revalidates_current_bytes(ctx):
    p = ctx.workspace.root / "file.txt"
    p.write_text("first")
    result = await read(ctx)
    from app.agent.session.events import snapshot_json
    # Nanosecond timestamps are decimal strings, since durable JSON rejects
    # integers outside JavaScript's exact range. Real tool results must persist.
    assert snapshot_json(result.to_dict()) == result.to_dict()
    assert int(result.meta["read_state"]["mtime_ns"]) == p.stat().st_mtime_ns
    restored = {}
    restore_read_state(restored, ctx.workspace, [result.to_dict()])
    ctx.state = restored
    assert (await edit(ctx)).ok
    restore_read_state(ctx.state, ctx.workspace, [result.to_dict()])
    assert (await edit(ctx, old="changed")).meta["code"] == "FS_STALE_VERSION"


@pytest.mark.asyncio
async def test_failed_execution_invalidation_survives_replay(ctx):
    (ctx.workspace.root / "file.txt").write_text("first")
    result = await read(ctx)
    restore_read_state(ctx.state, ctx.workspace, [result.to_dict(), {
        "ok": False, "meta": {"read_state_invalidated": ["file.txt"]},
    }])
    assert (await edit(ctx)).meta["code"] == "FS_NOT_OBSERVED"
    clear_read_state(ctx.state)
    assert STATE_KEY not in ctx.state


@pytest.mark.asyncio
async def test_read_result_replays_from_durable_model_surface(ctx):
    from app.agent.session.derive import fold_surface
    from app.agent.session.log import SessionLog

    (ctx.workspace.root / "file.txt").write_text("first")
    result = await read(ctx)
    log = SessionLog()
    log.set_system({"role": "system", "content": "test"})
    log.append("turn/start", {"turn": 1})
    log.append("step/start", {"turn": 1, "step": 1})
    log.append("assistant/message", {"turn": 1, "step": 1, "stream": [], "message": {
        "role": "assistant", "content": "", "tool_calls": [{
            "id": "read", "type": "function", "function": {"name": "read_file", "arguments": "{}"},
        }],
    }}, surface_op="append")
    log.append("tool/call", {"turn": 1, "step": 1, "callId": "read", "name": "read_file", "arguments": "{}"})
    log.append("tool/result", {"turn": 1, "step": 1, "callId": "read", "name": "read_file", **result.to_dict(),
        "message": {"role": "tool", "tool_call_id": "read", "content": result.output}}, surface_op="append")
    events = log.events
    clear_read_state(ctx.state)
    restore_read_state(ctx.state, ctx.workspace, [events[seq]["data"] for seq in fold_surface(events)
        if events[seq]["type"] == "tool/result"])
    assert (await edit(ctx)).ok


@pytest.mark.asyncio
async def test_replay_refuses_incomplete_or_unsafe_metadata(ctx):
    (ctx.workspace.root / "file.txt").write_text("first")
    result = await read(ctx)
    for key in ("revision_id", "mtime_ns", "size_bytes", "is_partial_view"):
        meta = copy.deepcopy(result.meta)
        meta["read_state"].pop(key)
        assert parse_read_state_metadata(meta, ctx.workspace) is None
    for key, value in (("schema_version", 2), ("mtime_ns", True), ("read_at", float("nan")), ("path", "../escape")):
        meta = copy.deepcopy(result.meta)
        meta["read_state"][key] = value
        assert parse_read_state_metadata(meta, ctx.workspace) is None
    restore_read_state(ctx.state, ctx.workspace, [{"ok": True, "meta": {"path": "file.txt"}}])
    assert latest_read(ctx.state, "file.txt") is None


@pytest.mark.asyncio
async def test_read_state_does_not_leak_between_contexts(ctx):
    (ctx.workspace.root / "file.txt").write_text("first")
    await read(ctx)
    other = ToolContext(ctx.conversation_id, ctx.workspace, ctx.emit, asyncio.Event())
    assert (await edit(other)).meta["code"] == "FS_NOT_OBSERVED"


@pytest.mark.asyncio
async def test_create_publication_preserves_competing_file(ctx, monkeypatch):
    original_link = os.link

    def competing_link(source, target):
        target.write_text("competitor")
        return original_link(source, target)

    monkeypatch.setattr(os, "link", competing_link)
    result = await WriteFileTool()(ctx, {"path": "file.txt", "content": "ours"})
    assert not result.ok and result.meta["code"] == "FS_NOT_OBSERVED"
    assert (ctx.workspace.root / "file.txt").read_text() == "competitor"
    assert not list(ctx.workspace.root.glob(".matopt-write-*"))


@pytest.mark.asyncio
async def test_replace_rechecks_revision_after_staging(ctx, monkeypatch):
    p = ctx.workspace.root / "file.txt"
    p.write_text("first")
    await read(ctx)
    original_sync = os.fsync

    def competing_sync(fd):
        p.write_text("external")
        return original_sync(fd)

    monkeypatch.setattr(os, "fsync", competing_sync)
    result = await edit(ctx)
    assert not result.ok and result.meta["code"] == "FS_STALE_VERSION"
    assert p.read_text() == "external"
    assert not list(ctx.workspace.root.glob(".matopt-write-*"))


@pytest.mark.asyncio
async def test_edits_preserve_crlf_and_existing_file_mode(ctx):
    p = ctx.workspace.root / "file.txt"
    p.write_bytes(b"first\r\nsecond\r\n")
    p.chmod(0o640)
    await read(ctx)
    assert (await edit(ctx)).ok
    assert p.read_bytes() == b"changed\r\nsecond\r\n"
    assert p.stat().st_mode & 0o777 == 0o640


@pytest.mark.asyncio
async def test_empty_edit_does_not_insert_between_every_character(ctx):
    (ctx.workspace.root / "file.txt").write_text("first")
    await read(ctx)
    result = await edit(ctx, old="", replace_all=True)
    assert not result.ok and result.meta["code"] == "FS_EDIT_EMPTY"
