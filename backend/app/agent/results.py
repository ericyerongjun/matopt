"""Explicit public answer blocks; private worker text never enters this channel."""
from __future__ import annotations

from copy import deepcopy
import math
import re
import time

KINDS = frozenset({"markdown", "source"})
STATUSES = frozenset({"provisional", "accepted", "superseded", "withdrawn"})
LOCAL_ID = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")


def public_result(value) -> dict | None:
    """The same bounded allowlist serves SSE, persisted history and recovery."""
    if not isinstance(value, dict):
        return None
    identifier, revision = value.get("id"), value.get("revision")
    content, refs, created = value.get("content"), value.get("evidence_refs"), value.get("created_at")
    if (not isinstance(identifier, str) or not identifier.strip() or len(identifier) > 160
            or type(revision) is not int or not 1 <= revision <= 2**53 - 1
            or value.get("kind") not in KINDS or value.get("status") not in STATUSES
            or not isinstance(content, str) or len(content) > 50_000
            or not isinstance(refs, list) or len(refs) > 50
            or any(not isinstance(ref, str) or not ref.strip() or len(ref) > 500 for ref in refs)
            or type(created) not in (int, float) or not math.isfinite(created) or created < 0):
        return None
    if value["status"] != "withdrawn" and not content.strip():
        return None
    return {"id": identifier, "revision": revision, "kind": value["kind"], "status": value["status"],
            "content": "" if value["status"] == "withdrawn" else content,
            "evidence_refs": [] if value["status"] == "withdrawn" else list(dict.fromkeys(refs)),
            "created_at": created}


def result_parts(parts) -> list[dict]:
    """Keep newest revisions, including withdrawal tombstones, in stable order."""
    latest = {}
    for part in parts if isinstance(parts, list) else []:
        if not isinstance(part, dict) or part.get("type") != "result_block":
            continue
        block = public_result(part.get("block"))
        if block and (block["id"] not in latest or block["revision"] > latest[block["id"]]["revision"]):
            latest[block["id"]] = block
    return [{"type": "result_block", "block": block} for block in latest.values()]


class ResultPublisher:
    """Harness-owned publication, revisions and invalidation for one message."""
    def __init__(self, message_id, parts, *, publish, observed_refs, allowed):
        self.message_id = message_id
        self.parts = parts
        self.publish = publish
        self.observed_refs = observed_refs
        self.allowed = allowed
        self.blocks = {}
        self.closed = False

    async def record(self, identifier, content, kind="markdown", evidence_refs=None, status="provisional"):
        if self.closed or not self.allowed():
            raise ValueError("Result publication is unavailable in this phase.")
        if not isinstance(identifier, str) or not LOCAL_ID.fullmatch(identifier):
            raise ValueError("Use a stable result id of 1–80 letters, digits, underscores or hyphens.")
        if status not in {"provisional", "withdrawn"}:
            raise ValueError("Only provisional publication or withdrawal is available to the worker.")
        key = f"{self.message_id}:result:{identifier}"
        previous = self.blocks.get(key)
        if previous is None and status == "withdrawn":
            raise ValueError("Only an existing result can be withdrawn.")
        if previous is None and len(self.blocks) >= 32:
            raise ValueError("The publication limit was reached; update an existing result instead.")
        block = public_result({"id": key, "revision": previous["revision"] + 1 if previous else 1,
            "kind": kind, "status": status, "content": content, "evidence_refs": [] if evidence_refs is None else evidence_refs,
            "created_at": previous["created_at"] if previous else time.time()})
        if block is None:
            raise ValueError("Invalid public result: use markdown/source, 1–50000 characters and at most 50 evidence references.")
        unknown = set(block["evidence_refs"]) - self.observed_refs()
        if unknown:
            raise ValueError("Evidence references must identify sources or workspace files already observed in this run.")
        size = sum(len(item["content"]) for name, item in self.blocks.items() if name != key) + len(block["content"])
        if size > 200_000:
            raise ValueError("The public result size limit was reached; shorten the result or save a deliverable.")
        await self._store(block)
        return deepcopy(block)

    async def _store(self, block):
        # Persist and publish before changing the in-memory accepted revision.
        # A failed durable write must not silently advance revision state.
        await self.publish(deepcopy(block))
        self.blocks[block["id"]] = block
        existing = next((part for part in self.parts if part.get("type") == "result_block"
                         and part["block"]["id"] == block["id"]), None)
        if existing is None:
            self.parts.append({"type": "result_block", "block": deepcopy(block)})
        else:
            existing["block"] = deepcopy(block)

    async def supersede(self):
        for block in list(self.blocks.values()):
            if block["status"] == "provisional":
                await self._store({**block, "revision": block["revision"] + 1, "status": "superseded"})

    async def finalize(self):
        self.closed = True
        await self.supersede()
