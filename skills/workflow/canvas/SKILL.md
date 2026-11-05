---
name: canvas
description: >
  Create an interactive panel (a canvas) that explains code, data or a
  process: selectable paths, clickable steps that reveal the rule behind
  them, illustrative rows run through the flow. Use for /canvas or "make an
  interactive walkthrough of this code".
disable-model-invocation: true
license: Apache-2.0
metadata:
  author: matopt
  version: "1.0"
---

# Canvas: understand the code interactively

Goal: the user selects a path and inspects a rule. Ground every node in the real source.

1. Read the code that implements the flow (e.g. an importer: File → Parse → Validate → Duplicate → Preview →
   Confirm → Save). For each step note the exact rule and the file/function (`app.js: classifyBatch`).
2. Pick 2–4 illustrative inputs that take different paths (Valid, Duplicate, Invalid) and one saved fixture they are
   compared against. Say they are illustrative.
3. Build the panel, in this order of preference:
   - `render_ui` (native): `tabs` for the input paths, `mermaid` for the flow, `disclosure` per step with its rule and
     source location, `table` for the fixture; a `select` bound to state switches the highlighted path.
   - `create_ui_app` when you need clickable nodes that highlight the reached steps: self-contained HTML/JS, no
     network, theme tokens `--matopt-*`, state through `window.matopt`.
   - If neither UI tool is available, write `canvas/<slug>.html` (single file, inline CSS/JS) and give the path.
4. Below the canvas, state the source ("Source: app.js import flow"), how to read it ("arrows run left to right; an
   invalid row skips Duplicate, Confirm and Save, then still appears in Preview"), and what it does not cover.
