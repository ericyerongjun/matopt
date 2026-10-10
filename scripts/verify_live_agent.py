"""Opt-in live acceptance using the running app and its configured provider.

Creates one clearly labelled synthetic chat, uploads a tiny PDF and leaves the
result available for browser inspection. No user conversations are modified.
Run IDs and every received public event are saved so a disconnect can be
inspected without submitting the task again. This checks integration, not model
quality or a representative benchmark. It incurs a small number of model calls.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import time
import uuid

import httpx
import pymupdf


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    dest = args.output_dir
    dest.mkdir(parents=True, exist_ok=True)
    if (dest / "request.json").exists():
        raise SystemExit("This output directory already owns a request. Inspect its run before creating another test.")
    def save(name, value):
        (dest / name).write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    pdf = pymupdf.open()
    page = pdf.new_page()
    page.insert_text((60, 70), "Synthetic streaming acceptance assignment", fontsize=18)
    page.insert_text((60, 115), "Values: 2, 3, 5.\nQ1. Calculate the sum.\nQ2. Calculate the sum of squares and the mean.", fontsize=13)
    # A raster-only PDF forces visual reading; embedded text extraction cannot
    # accidentally make this acceptance run look like a successful OCR check.
    pixels = page.get_pixmap(matrix=pymupdf.Matrix(2, 2)).tobytes("png")
    scanned = pymupdf.open()
    scanned.new_page(width=page.rect.width, height=page.rect.height).insert_image(page.rect, stream=pixels)
    assert not scanned[0].get_text().strip()
    pdf_bytes = scanned.tobytes()
    scanned.close()
    pdf.close()
    events = []
    started = time.monotonic()
    with httpx.Client(base_url=args.base_url, timeout=httpx.Timeout(600, connect=10)) as client:
        response = client.post("/api/conversations", json={"title": "Agent streaming acceptance (synthetic)"})
        response.raise_for_status()
        cid = response.json()["id"]
        uploaded = client.post(f"/api/conversations/{cid}/files", files={"files": ("streaming-assignment.pdf", pdf_bytes, "application/pdf")})
        uploaded.raise_for_status()
        file = uploaded.json()["files"][0]
        request = {"conversation_id": cid, "client_run_id": "acceptance-" + uuid.uuid4().hex,
            "message": "Read the attached assignment. Use Python to calculate Q1, and publish its completed solution with publish_result before working on Q2. Then use a separate Python calculation for Q2, publish that section, and check the calculations. Give one concise final solution with a table and the mean formula. Do not browse. This is a synthetic streaming integration check.",
            "attachments": [file], "reasoning": "high", "verify": True, "web_search": False, "ui_version": 1}
        save("request.json", request)
        def consume(response, disconnect=False):
            response.raise_for_status()
            for line in response.iter_lines():
                if not line.startswith("data:"):
                    continue
                event = json.loads(line[5:].strip())
                event["received_ms"] = round((time.monotonic() - started) * 1000)
                events.append(event)
                save("events.json", events)
                if disconnect and event["type"] == "result_block":
                    return True
            return False
        with client.stream("POST", "/api/chat/stream", json=request) as response:
            run_id = response.headers.get("x-run-id")
            save("run.json", {"conversation_id": cid, "run_id": run_id})
            disconnected = consume(response, disconnect=True)
        if disconnected:
            assert run_id
            with client.stream("GET", f"/api/runs/{run_id}/events", params={"after": events[-1]["seq"]}) as response:
                consume(response)
        saved = client.get(f"/api/conversations/{cid}")
        saved.raise_for_status()
        save("saved.json", saved.json())
        files = client.get(f"/api/conversations/{cid}/files")
        files.raise_for_status()
        readings = []
        for output in files.json()["files"]:
            path = output.get("path", "")
            if path.startswith("parsed/streaming-assignment-") and path.endswith(".md"):
                reading = client.get(output["url"])
                reading.raise_for_status()
                readings.append({"path": path, "content": reading.text})
        # Read only artifacts from this synthetic chat. Diagnostics can remain
        # disabled. The app-authored header identifies the multimodal path;
        # the raster-only source has no native text to substitute for OCR.
        save("ocr.json", readings)
    end = next((event for event in events if event["type"] == "message_end"), None)
    blocks = [event for event in events if event["type"] == "result_block"]
    finals = [event for event in events if event["type"] == "text_delta"]
    first_final = next((event for event in events if event["type"] == "final_start"), None)
    content = end["content"] if end else {}
    text = "\n".join(part.get("text", "") for part in content.get("parts", []))
    canonical = next((msg["content"] for msg in saved.json()["messages"] if end and msg["id"] == end["message_id"]), None)
    checks = {
        "complete": bool(end and end["status"] == "complete"),
        "partial_before_final": bool(blocks and first_final and blocks[0]["seq"] < first_final["seq"]),
        "more_work_after_partial": bool(blocks and first_final and any(event["type"] == "activity" and event["item"]["kind"] == "tool" and event["item"]["status"] == "running" and blocks[0]["seq"] < event["seq"] < first_final["seq"] for event in events)),
        "real_final_deltas": len(finals) > 1 and bool(first_final and first_final.get("mode") == "streaming"),
        "reconnect_exercised": disconnected,
        "no_duplicate_sequence": len({event["seq"] for event in events}) == len(events),
        "canonical_reload_matches": canonical == content,
        "calculation_results_present": "10" in text and "38" in text,
        "multimodal_ocr_exercised": any(
            "Multimodal transcription" in reading["content"]
            and "Pages read: 1 of 1." in reading["content"]
            and "## Reading notes" not in reading["content"]
            for reading in readings),
        "no_private_event_types": not any(event["type"] in {"reasoning_delta", "tool_progress", "tool_call_ready", "verification"} for event in events),
    }
    report = {"conversation_id": cid, "run_id": run_id, "checks": checks,
        "event_counts": dict(Counter(event["type"] for event in events)),
        "first_partial_ms": blocks[0]["received_ms"] if blocks else None,
        "first_final_token_ms": finals[0]["received_ms"] if finals else None,
        "completed_ms": end["received_ms"] if end else None,
        "verification": content.get("work", {}).get("verification"),
        "scope": "Single synthetic integration run with explicit partial publication; not an autonomous quality benchmark."}
    save("report.json", report)
    print(json.dumps(report, indent=2))
    if not all(checks.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
