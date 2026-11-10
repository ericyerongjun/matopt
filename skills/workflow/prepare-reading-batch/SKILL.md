---
name: prepare-reading-batch
description: >
  Prepare a JSON batch file of titles and URLs for a reading board or
  bookmark importer, from links the user supplies or from resources found on
  a topic. Use when the user gives resource links, asks to find reading
  resources, or asks for an import batch. Writes the JSON and a short source
  report only; does not import the file or edit application code.
license: Apache-2.0
metadata:
  author: matopt
  version: "1.0"
---

# Prepare a reading batch

Use real source titles and URLs; don't invent missing information. Keep source links and any uncertainty in a short
companion report. Don't import anything or change application code.

1. Collect candidates:
   - links the user gave, as given;
   - or, for a topic, search (`web_search`, or a connected docs MCP server such as Microsoft Learn
     `microsoft_docs_search`) and prefer official documentation and specifications.
2. Open each page (`web_fetch` or the MCP fetch tool) and take the title **the page itself uses**. If a page cannot
   be opened, keep the URL only when the user supplied it, and say so in the report.
3. Write `batches/<slug>.json`: a JSON array of `{"title": "...", "url": "https://..."}` in reading order. If the
   project has an importer spec (e.g. `openspec/specs/*/spec.md`), follow its field names and rules instead.
4. Check it: `python skills/prepare-reading-batch/scripts/reading_batch.py batches/<slug>.json`
   (trims, requires http(s) URLs and non-empty titles, flags duplicate URLs inside the batch and, with
   `--existing FILE`, URLs already saved). Fix what it reports.
5. Write `batches/<slug>.md`: one line per entry (title — URL — how the title was obtained), then anything omitted
   and why, and any uncertainty.
6. Reply with the two paths, the entries, and "not imported". Answer in the user's language.
