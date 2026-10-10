#!/usr/bin/env python3
"""Offline retrieval/dispatch microbenchmark; not an agent-task-success benchmark.

No provider, MCP server, credentials, tool executions, or external writes. The
baseline reproduces the pre-discovery router (native-all + lexical MCP top 8,
three alphabetical tools on no match, fuzzy dispatch). Seeded synthetic catalog
expansion and fixed, inspectable query cases keep every comparison reproducible.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import math
import platform
import random
import re
import statistics
import sys
from pathlib import Path
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.agent.events import ToolResult
from app.agent.skills.router import tokenize
from app.agent.tools.base import Tool, ToolRegistry
from app.agent.tools.discovery_tool import LoadToolSchemasTool, SearchToolsTool
from app.agent.tools.router import ALIASES, ToolRouter


class FixtureTool(Tool):
    read_only = True

    def __init__(self, name, description, server="", keywords=()):
        self.name, self.description, self.mcp_server = name, description, server
        self.discovery_keywords = keywords
        self.parameters = {"type": "object", "properties": {
            "query": {"type": "string", "description": "Operation-specific query or target identifier."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
            "include_archived": {"type": "boolean", "default": False},
        }, "required": ["query"], "additionalProperties": False}

    async def run(self, ctx, **kwargs):
        raise AssertionError("A retrieval benchmark must never execute a tool")


# 4 native + 26 connector tools = 30. Descriptions and query cases are hand
# authored fixtures, not a sample of private user data or a real MCP deployment.
BASE = [
    ("python", "Run Python calculations and statistical analysis.", ""),
    ("read_file", "Read a file in the current workspace.", ""),
    ("list_files", "List files in the current workspace.", ""),
    ("web_search", "Search the public web for current information.", ""),
    ("gmail_search_messages", "Search email messages by sender, date and subject.", "gmail"),
    ("gmail_read_message", "Read the full body and headers of one email message.", "gmail"),
    ("gmail_send_message", "Send an email message to recipients.", "gmail"),
    ("gmail_create_draft", "Create an unsent email draft without sending it.", "gmail"),
    ("gmail_delete_draft", "Delete an unsent email draft; existing sent messages are unaffected.", "gmail"),
    ("calendar_list_events", "List calendar events within a date range.", "calendar"),
    ("calendar_create_event", "Create a calendar event with title, attendees and time.", "calendar"),
    ("calendar_update_event", "Update the time or title of an existing calendar event.", "calendar"),
    ("calendar_delete_event", "Delete a calendar event by its identifier.", "calendar"),
    ("drive_search_files", "Search cloud drive files and documents by name or contents.", "drive"),
    ("drive_download_file", "Download a cloud drive file to the workspace.", "drive"),
    ("drive_upload_file", "Upload a local file into cloud drive.", "drive"),
    ("drive_share_file", "Set permissions for collaborators to access a cloud drive file.", "drive"),
    ("drive_delete_file", "Delete a cloud drive file permanently.", "drive"),
    ("github_search_issues", "Search repository issues by text, label and status.", "github"),
    ("github_create_issue", "Create a repository issue with title and description.", "github"),
    ("github_read_file", "Read a file at a given path and revision in a repository.", "github"),
    ("github_delete_branch", "Delete a named repository branch.", "github"),
    ("slack_search_messages", "Search channel messages by text, author or date.", "slack"),
    ("slack_send_message", "Send a message into a Slack channel.", "slack"),
    ("notion_search_pages", "Search workspace pages by title or text.", "notion"),
    ("notion_create_page", "Create a Notion page containing text blocks.", "notion"),
    ("vision_extract_text", "Extract text from an image or scanned PDF with OCR.", "vision"),
    ("browser_fetch_page", "Fetch a public web page and extract readable text.", "browser"),
    ("crm_find_contact", "Find a customer contact by name, email or company.", "crm"),
    ("analytics_query_sales", "Query sales totals and revenue by region and date.", "analytics"),
]

CASES = [
    ("literal", "Search Gmail messages by sender Maya", "gmail_search_messages"),
    ("literal", "Read full Gmail message headers", "gmail_read_message"),
    ("literal", "Create a calendar event with attendees", "calendar_create_event"),
    ("literal", "Download a cloud drive file", "drive_download_file"),
    ("literal", "Search GitHub repository issues by label", "github_search_issues"),
    ("literal", "Create a Notion page", "notion_create_page"),
    ("literal", "Extract text from a scanned PDF", "vision_extract_text"),
    ("literal", "Query sales totals by region", "analytics_query_sales"),
    ("literal", "github_read_file", "github_read_file"),
    ("literal", "gmail_delete_draft", "gmail_delete_draft"),
    ("paraphrase", "Locate correspondence in my inbox", "gmail_search_messages"),
    ("paraphrase", "Add an appointment with my colleagues", "calendar_create_event"),
    ("paraphrase", "Modify the schedule of our existing meeting", "calendar_update_event"),
    ("paraphrase", "Remove the unsent mail draft", "gmail_delete_draft"),
    ("paraphrase", "Locate tickets in the repo", "github_search_issues"),
    ("paraphrase", "Retrieve repository document contents", "github_read_file"),
    ("paraphrase", "Read words in a photo", "vision_extract_text"),
    ("paraphrase", "Let collaborators see this drive document", "drive_share_file"),
    ("paraphrase", "Locate a customer using their company", "crm_find_contact"),
    ("paraphrase", "Prepare an email without delivering it", "gmail_create_draft"),
    ("confusing_names", "Remove a calendar event, keep the drive file", "calendar_delete_event"),
    ("confusing_names", "Find messages in Slack, not Gmail", "slack_search_messages"),
    ("confusing_names", "Read file contents in GitHub repository", "github_read_file"),
    ("confusing_names", "Write a draft in Gmail without sending a message", "gmail_create_draft"),
    ("confusing_names", "Upload a local file to drive, not download", "drive_upload_file"),
    ("confusing_names", "Delete repository branch, keep the issues", "github_delete_branch"),
    # These are next-step queries, not the initial query from a prior task.
    # Both methods receive the same new text; loaded-tool retention is separate.
    ("midtask_change", "Now inspect sales revenue by region", "analytics_query_sales"),
    ("midtask_change", "Now send the summary to a Slack channel", "slack_send_message"),
    ("midtask_change", "Now grant collaborator access to the drive file", "drive_share_file"),
    ("midtask_change", "Now fetch the public web page text", "browser_fetch_page"),
]


def catalog(size, seed=104729):
    assert size >= len(BASE)
    tools = [FixtureTool(*row) for row in BASE]
    rng = random.Random(seed)
    domains = ["warehouse", "support", "inventory", "billing", "delivery", "monitoring", "hr", "crm", "search", "archive"]
    entities = ["customer", "record", "message", "file", "event", "issue", "document", "contact", "invoice", "report"]
    actions = ["read", "create", "update", "delete", "search", "export", "list", "send", "upload", "download"]
    for index in range(size - len(tools)):
        service, entity, action = rng.choice(domains), rng.choice(entities), rng.choice(actions)
        # Homogeneous schemas deliberately isolate discovery effects from schema
        # complexity. Real installations can have much larger variance.
        description = f"{action.title()} {entity} data for the {service} service by identifier, date, name and status."
        tools.append(FixtureTool(f"{service}_{action}_{entity}_{index:04d}", description, service))
    return ToolRegistry(tools)


def baseline_names(registry, query, preferred=()):
    tokens = set(tokenize(query or ""))
    native, remote = [], []
    for tool in registry:
        if tool.mcp_server:
            score = len(tokens & set(tokenize(f"{tool.name} {tool.description}"))) / max(1, len(tokens))
            if tool.name in preferred:
                score = max(score, 0.8)
            remote.append((score, tool.name))
        else:
            native.append(tool.name)
    remote.sort(key=lambda item: (-item[0], item[1]))
    selected = [name for score, name in remote if score > 0][:8]
    if not selected:
        selected = [name for _, name in remote[:3]]
    return native + selected


def baseline_resolve(registry, name):
    direct = registry.get(name)
    if direct:
        return direct.name
    norm = lambda value: re.sub(r"[^a-z0-9]+", "", value.lower())
    alias = ALIASES.get(name.lower()) or ALIASES.get(norm(name))
    if alias and registry.get(alias):
        return alias
    for tool in registry:
        if norm(tool.name) == norm(name):
            return tool.name
    candidates = registry.names()
    close = difflib.get_close_matches(name, candidates, n=3, cutoff=0.62)
    if not close:
        close = difflib.get_close_matches(norm(name), [norm(candidate) for candidate in candidates], n=3, cutoff=0.72)
        inverse = {norm(candidate): candidate for candidate in candidates}
        close = [inverse[candidate] for candidate in close if candidate in inverse]
    return close[0] if len(close) == 1 else None


def schema_names(schemas):
    return [schema["function"]["name"] for schema in schemas]


def compact_bytes(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())


def timing(fn, queries, repeats):
    samples = []
    for _ in range(repeats):
        for _, query, _ in queries:
            start = perf_counter()
            fn(query)
            samples.append((perf_counter() - start) * 1000)
    ordered = sorted(samples)
    return {"median_ms": round(statistics.median(samples), 3), "p95_ms": round(ordered[math.ceil(len(ordered) * .95) - 1], 3), "samples": len(samples)}


def run(size, repeats):
    registry = catalog(size)
    router = ToolRouter()
    discovery = [SearchToolsTool(registry, router), LoadToolSchemasTool(registry, router)]
    discovery_schema_bytes = compact_bytes([tool.schema() for tool in discovery])
    cold = perf_counter()
    router.search(registry, CASES[0][1])
    cold_ms = (perf_counter() - cold) * 1000
    rows = []
    for category, query, expected in CASES:
        baseline = baseline_names(registry, query)
        selected = schema_names(router.schemas(registry, query))
        discovered = [match.name for match in router.search(registry, query)]
        rows.append({"category": category, "query": query, "expected": expected,
                     "baseline_hit": expected in baseline, "candidate_hit": expected in selected, "discovery_hit": expected in discovered,
                     "baseline_schema_bytes": compact_bytes([registry.get(name).schema() for name in baseline]),
                     "candidate_schema_bytes": compact_bytes([registry.get(name).schema() for name in selected] + [tool.schema() for tool in discovery]),
                     "baseline_count": len(baseline), "candidate_count": len(selected) + 2})
    # Unknown ID probes cover punctuation/case normalization and one-character
    # omissions; exact and explicit aliases are evaluated separately.
    probes = ["readfile", "Read_File", "read-file", "pythonn", "gmail_search_message", "drive_download_fil", "github_delete_brach", "vision_extract_txt", "calendar_deleteevent", "slack_sendmessage"]
    wrong_baseline = sum(baseline_resolve(registry, name) is not None for name in probes)
    wrong_candidate = sum(router.resolve(registry, name)[0] is not None for name in probes)
    known = [(row[0], row[0]) for row in BASE] + [("read", "read_file"), ("web", "web_search")]
    pins = [row[0] for row in BASE if row[2]][-12:]
    baseline_pins = baseline_names(registry, "find messages", pins)
    candidate_pins = schema_names(router.schemas(registry, "find messages", preferred_tools=pins))
    retrieval_router, schema_router = ToolRouter(), ToolRouter()
    retrieval_router.search(registry, CASES[0][1])
    schema_router.schemas(registry, CASES[0][1])
    result = {"catalog_size": size, "cases": len(rows), "cold_index_search_ms": round(cold_ms, 3),
        "baseline_initial_recall": sum(row["baseline_hit"] for row in rows) / len(rows),
        "candidate_initial_recall": sum(row["candidate_hit"] for row in rows) / len(rows),
        "candidate_discovery_recall_at_8": sum(row["discovery_hit"] for row in rows) / len(rows),
        "by_category": {category: {metric: sum(row[metric] for row in rows if row["category"] == category) / sum(row["category"] == category for row in rows)
                                  for metric in ("baseline_hit", "candidate_hit", "discovery_hit")} for category in dict.fromkeys(row["category"] for row in rows)},
        "wrong_dispatch": {"probes": len(probes), "baseline": wrong_baseline, "candidate": wrong_candidate},
        "known_dispatch_accuracy": {"cases": len(known), "baseline": sum(baseline_resolve(registry, name) == expected for name, expected in known) / len(known),
                                    "candidate": sum(router.resolve(registry, name)[0].name == expected for name, expected in known) / len(known)},
        "pinned_retained": {"selected": len(pins), "baseline": len(set(pins) & set(baseline_pins)), "candidate": len(set(pins) & set(candidate_pins))},
        "schema_prompt_bytes_mean": {"baseline": round(statistics.mean(row["baseline_schema_bytes"] for row in rows)),
                                     "candidate_including_discovery_tools": round(statistics.mean(row["candidate_schema_bytes"] for row in rows)),
                                     "full_catalog": compact_bytes(registry.schemas()), "discovery_tools_alone": discovery_schema_bytes},
        "schema_count_mean": {"baseline": round(statistics.mean(row["baseline_count"] for row in rows), 2),
                              "candidate": round(statistics.mean(row["candidate_count"] for row in rows), 2)},
        "latency": {"baseline_select": timing(lambda query: baseline_names(registry, query), CASES, repeats),
                    "candidate_select": timing(lambda query: schema_router.schemas(registry, query), CASES, repeats),
                    "candidate_metadata_search": timing(lambda query: retrieval_router.search(registry, query), CASES, repeats)},
        "cases_detail": rows}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", type=int, nargs="+", default=[30, 300, 3000])
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.repeats < 1 or any(size < 30 for size in args.sizes):
        parser.error("repeats >= 1 and sizes >= 30 required")
    report = {"benchmark": "deterministic-tool-discovery-v1", "seed": 104729,
              "python": platform.python_version(), "platform": platform.platform(),
              "fixture_sha256": hashlib.sha256(json.dumps([BASE, CASES], sort_keys=True).encode()).hexdigest(),
              "repeats": args.repeats, "results": [run(size, args.repeats) for size in args.sizes]}
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)
    print(json.dumps({**report, "results": [{key: value for key, value in result.items() if key != "cases_detail"} for result in report["results"]]}, indent=2))


if __name__ == "__main__":
    main()
