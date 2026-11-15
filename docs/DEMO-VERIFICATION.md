# Docker demo rehearsal — 15 November 2026

The Docker application completed real DeepSeek calls, numerical computation,
cross-turn Python reuse, mathematical rendering and isolated HTML previews.
The prepared demo is usable with the rehearsed HTML fallbacks. Fresh visual
generation still needs inspection: this run found incorrect explanatory prose,
generated JavaScript defects and claims of publication without a matching tool
call. This is a rehearsal record, not a guarantee of model correctness or latency.

## Environment and evidence

- Docker Compose: backend, frontend and separate `ui-sandbox`, on loopback ports
  8000, 3000 and 3001. Backend healthy; frontend, API proxy and local Plotly bundle
  returned HTTP 200.
- Real provider: `deepseek`, model `deepseek-flash`, UI label DeepSeek V4.1 Flash.
  Ultrafast Demo enabled; stored turn metadata recorded reasoning `off`.
- Browser inspection covered both conversation rendering and workspace previews.
  No mocked model responses or injected browser state were used.
- Checks used the synthetic `examples/demo/lensing.csv`. Numerical expectations
  were independently calculated from the CSV using the closed-form estimator.

## Checked behavior

| Check | Observed result |
| --- | --- |
| Fit and chart | n=12, A=2.006449352686209, R²=0.9997897786390821; scatter plot and fitted curve rendered. |
| Persistent Python | The next turn executed exactly `print(len(x), A, A/50)` with no reload, imports or redefinition; returned `12 2.006449352686209 0.04012898705372418`. |
| Mathematics | Inline math, fractions, sums, aligned derivation, equation number (1) and prediction rendered; 20 KaTeX/MathML expressions and zero KaTeX error elements in the clean rehearsal. An earlier rendering test preserved literal `Prices are $5 and $10.` |
| Model menu | Only Ultrafast Demo is selectable; on uses the demo profile, off displays Auto. Restored to on after testing. |
| Canvas controls | After repair, mass, speed and angle labels changed with their controls. Published-surface state survived reload, and Reset restored the saved state. |
| Canvas file preview | The repaired HTML opened in Files on the isolated preview origin. Its corrected animation clock advances; zero speed freezes the drawing. |
| Plotly file preview | Rendered 12 points and the fitted curve. Moving amplitude to 2.501 changed its label, curve and axis range. The DOM loaded `/runtime.js` and `/plotly.min.js`, replacing the pinned CDN reference. |
| Preview boundary | Generated content used the separate localhost:3001 iframe with `sandbox="allow-scripts"`, without same-origin permission. This inspection is not an exhaustive security audit. |
| Speech | Local recognition/synthesis preflight passed. Browser Read aloud reached Stop reading playback state. A live microphone exchange and audio intelligibility still need a presenter check on the actual device. |

## Observed model-turn durations

Durations below are backend-recorded work time, rounded to two decimals. They
exclude initial Docker builds, speech-model downloads and manual interaction.

| Turn | Seconds | Tool calls | Result |
| --- | ---: | ---: | --- |
| Original fit request | 11.76 | 2 | Correct numbers/chart; inaccurate extra prose. |
| Original persistence/math follow-up | 5.57 | 1 | Persistent variables and rendering passed; extra weighting explanation was incorrect. |
| Initial Canvas generation | 21.36 | 3 | Rendered; stale labels and inconsistent angle initialization. |
| Canvas control repair | 34.19 | 9 | Labels, degrees and state synchronization fixed; later inspection found a non-advancing animation clock. |
| Plotly generation | 14.81 | 3 | Recovered from a Python formatting error and wrote HTML; claimed a result panel without publishing it. |
| Animation repair/publication retry | 9.02 | 2 | File clock repaired; publication claim still unsupported. Used verified file previews. |
| Revised fit in a fresh chat | 8.25 | 1 | Correct fit, chart and synthetic-data qualification; still more verbose than requested. |
| Revised persistence/math follow-up | 4.47 | 1 | Correct derivative, estimator and prediction; no unsupported physics commentary. |

## What changed after rehearsal

The demo prompts now specify unweighted residuals, retain named Python variables,
and constrain physical interpretation. The mathematical follow-up was rerun in a
fresh chat and checked. The exact generated HTML files, including the animation
repair, are preserved under `examples/demo/` with a model-free upload/preview path.
The packaged Plotly file includes a small manual title/legend spacing correction
and a consistent hidden toolbar; the corrected file was uploaded and checked
again in the browser.

The failed publication turns contained receipt-like text in the model's answer,
but their actual tool logs and UI did not show a matching publication. Treat
those claims as a failed action, not successful output. Saving HTML and publishing
a result panel are separate steps; the Files preview provides a checked fallback.
This behavior remains a model-reliability limitation; it was not fixed in the
agent harness as part of this documentation/UI change.

Hidden-tab and reduced-motion branches were inspected in generated source;
an OS reduced-motion switch was not exercised. Docker container isolation is
shared by conversations, and Python variables do not survive a backend restart.
Keep the warmed services and the rehearsed conversation open for the presentation.

## Automated checks

- Frontend suite: 402 tests covered successfully. The socket-listening sandbox
  server test required a rerun with local-socket permission after an environment
  `EPERM`; that rerun passed.
- Both frontend TypeScript configurations and frontend ESLint passed.
- Frontend provenance check passed for 1,356 files after recording the intentional
  MatOpt host-adapter edits. The optional external ZCode source checkout was not
  available for the additional source-repository comparison.
- Docker images built and the application ran with the real provider as above.

For delivery, use the current prompts in [DEMO.md](DEMO.md), verify generated
controls before speaking about them, and keep the [prepared examples](../examples/demo/README.md)
available if a fresh generation is slow or incorrect.
