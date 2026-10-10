# Native ZCode frontend provenance

The consolidated [manifest](zcode-frontend-manifest.json) records the frontend snapshot from [ZCode commit 29628c9acdb81b703bbd4080c207a0e7ce5e276e](https://github.com/zai-org/ZCode/tree/29628c9acdb81b703bbd4080c207a0e7ce5e276e). It covers every non-test file under `frontend/src/zcode`, every public asset, and the listed MatOpt app/component/helper/type bindings. The rendering-specific manifest remains a narrower migration reference; this consolidated manifest is the integrity gate.

Each upstream source has its immutable Git blob ID and SHA-256. Each local target has the SHA-256 of the reviewed version. The manifest distinguishes these forms:

| Mode | What the check establishes |
| --- | --- |
| `exact-copy` | Every byte matches the original pinned Git blob, including original license notices. |
| `banner-only-copy` | The only added bytes are the declared provenance banner; the remaining complete file matches the original blob. |
| `css-path-adapted` | The complete native stylesheet matches the original after reversing the declared Streamdown and MatOpt source-scan path changes. |
| `whole-file-adapted` | The pinned whole file is the source; the target is the reviewed version with the described browser, chart, math, or capability binding. This does not claim byte equivalence. |
| `maintained-render-subtree` | Named native shell/sidebar/header/task rendering subtrees were migrated with MatOpt metadata and capability slots. These are maintained adaptations, not verbatim whole files. |
| `extraction` | Named source ranges were extracted, with the stated export adaptation. |
| `license-excerpt` | The Material Extensions MIT notice is a literal excerpt of the pinned upstream third-party notices. |
| `host-adapter`, `host-asset`, `provenance-map` | Original MatOpt bindings, brand assets, and provenance metadata; no upstream-copy claim. |

Of the 25 UI primitive files, 24 are exact copies. The whole native CodeViewer adds the supported `File disableWorkerPool` option to use main-thread WASM highlighting after the browser worker route produced empty code output. PromptInput now has a reviewed adaptation: submission acceptance clears only the submitted text/files/references, preserving edits made while the request was pending. Its companion components, attachments, task, chat-loading, shimmer, and `v4/composer/escapeStop.ts` retain their recorded copy modes. The stylesheet adapts source scanning and restores accessible keyboard focus outlines. These two files are explicitly classified as `whole-file-adapted`. Material icon SVGs match the pinned assets; MatOpt logos remain host assets.

MatOpt's Chart.js and Plotly renderers, their CSS, `chartTheme.ts`, `plotResize.ts`, and the Plotly type declaration are recorded as host adapters. They resolve defaults from the native theme variables while preserving explicit figure options and dimensions; they are MatOpt implementations, not copied ZCode components. The copied worker-pool provider remains a reference file; the browser does not mount it while CodeViewer uses the supported main-thread path.

`MatOptApp.tsx` is the host orchestrator for the new MatOpt shell, optional canvas and Activity view. `features/shell`, `features/intelligent-ui`, `matopt.css` and the isolated static runtime are MatOpt-owned code, outside the upstream-copy inventory. The earlier maintained native shell remains available as a component. Visible nested menus/dialogs receive the first Escape. The original MatOpt `markdownNormalize.ts` helper protects existing math/code spans before normalizing bare prose math. These adaptations are described and hashed separately from exact source copies.

The October 10 frontend design review updates the local hashes and descriptions of five existing host adapters: MatOptApp, MathInput, MessageList, ModelPicker, and the current MarkdownRenderer binding. The latter already differed at the start of the redesign and was reviewed without changing its implementation. No upstream revision, original source hash, copy classification, license, or copied primitive was changed. MatOpt's semantic design tokens and component styling remain host-owned. [DESIGN.md](../../DESIGN.md) records the visual review and preservation checks.

The subsequent October 10 attachment refinement updates only MessageList's host-adapter hash: sent files now use compact document cards with type labels, full accessible filenames, and existing native image previews. The copied attachment primitive remains unchanged; card styling is scoped to MatOpt's conversation stylesheet.

Run the read-only check without a network or upstream checkout:

```sh
python3 scripts/vendor_zcode_frontend.py --check
```

This verifies local hashes and complete file inventory. For exact copies, banner-only copies and any declared path-only adaptations, it reconstructs original bytes and verifies both upstream hashes. Adapted files are checked against their reviewed local hashes; offline verification does not claim that their original upstream files can be reconstructed.

To additionally verify every original source, including the sources of adapters, subtrees, extractions, and the literal icon license, use an existing Git checkout containing the pinned revision:

```sh
python3 scripts/vendor_zcode_frontend.py --check --source-repo /path/to/ZCode
```

The checker reads immutable Git blobs in one batch. It does not read mutable source working-tree files, fetch repositories, regenerate copied files, or update expected hashes. The existing `scripts/vendor_check.sh` discovers its `--check` mode.

For intentional changes, review the pinned source and the local diff, retain the correct copy/adaptation category and original source hashes, and update the target hash and adaptation description in the manifest. A source upgrade must explicitly update the pinned revision and affected source hashes. Re-run both verification modes after updating the reviewed snapshot.

The lightweight gate regressions check binary assets, unrecorded files, edits disguised as exact copies, strict banner removal, stylesheet changes outside scan paths, adapter hash drift, literal license spans, duplicate targets, and path containment:

```sh
python3 -m unittest discover -s scripts/tests -p test_vendor_zcode_frontend.py
```
