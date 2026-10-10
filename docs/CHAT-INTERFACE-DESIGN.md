# Chat interface refinement

Reference inspected on 9 October 2026: the current public [ChatGPT web interface](https://chatgpt.com/). The reference is the rendered web UI, including its computed typography and control dimensions; no proprietary assets or account-only features are copied. The supplied frontend-design skill guides implementation, with the user's request for close ChatGPT alignment taking priority over novelty.

## Design system

| Role | Value |
| --- | --- |
| Canvas | `#fcfcfc` |
| Control surface | `#ffffff` |
| Sidebar / quiet surface | `#f9f9f9` |
| Hover | `#ebebeb` |
| Primary text | `#0d0d0d` |
| Secondary text | `#707070` |

Use the same system sans stack observed on ChatGPT: `-apple-system-body, ui-sans-serif, -apple-system, system-ui, "Segoe UI", Helvetica, "Apple Color Emoji", Arial, sans-serif, "Segoe UI Emoji", "Segoe UI Symbol"`. Body and composer start at 16px, navigation at 14px, the centered welcome heading at 24px/400. Saved text-size preferences scale semantic roles. Math retains KaTeX metrics; source retains monospace.

```
260px navigation | model / effort menu                 activity · settings
                 |
                 |          768px reading area
                 |          formatted final answer
                 |
                 |          +  Ask anything                 ↑
```

Controls share restrained line icons, 1.8px strokes, readable labels and visible focus. Navigation rows use 10px corners; popovers 14px; the compact composer 28px. Icon hit areas are at least 36px on desktop, increasing to 44px in mobile chrome. Use ordinary functional labels instead of decorative eyebrows, slogans, gradients or repeated starter cards.

The slash character remains the entry point for tools and skills. Menu rows and selected chips use descriptive names and semantic icons; canonical command names remain available as secondary hints and retain their dispatch semantics. The header owns the model/effort menu so the composer stays focused on the draft, selected commands and attachments.

## Output contract

- Ordinary Markdown renders headings, tables, links and LaTeX. An explicitly requested Markdown or LaTeX source fence stays literal.
- Ordinary HTML/SVG code examples retain source first and offer a Preview control. Explicit `html-preview` / `svg-preview` fences, complete standalone visual documents, and typed visual artifacts open rendered when complete.
- Incomplete streaming source is never executed. HTML previews stay in the existing isolated runtime; source remains available if preview cannot load. SVG is displayed as an image document, not inserted into the host DOM.
- The single collapsed work summary, bounded verification, private agent communications, checked final answer streaming and canonical conversation refresh remain unchanged.

## Verification scope

Review the welcome page, slash menu, pinned commands, conversation output, settings/model menus, file browser, file previews and interactive canvas. Exercise keyboard selection, mobile drawer/focus, light/dark appearance, draft preservation and source/preview switching. Validate Markdown tables, numeric and symbolic math, source examples, HTML and SVG output; run component tests, production build and vendor provenance checks.

Validated on 9 October 2026:

- Full regression suites: 232 frontend tests and 261 backend tests passed. The final renderer check passed all 35 targeted tests after the language-label polish.
- Production build, generated UI contract and all 1,373 vendored-file provenance checks passed.
- Browser interactions confirmed semantic slash selection, a 390px mobile layout without horizontal overflow, 44px mobile header controls, light/dark theme switching, rendered tables and equations, and literal source preservation.
- HTML previews executed in the isolated runtime; SVG rendered as an image. HTML file previews retained their source through the Code switch. Updating the Taylor example from degree 3 to 7 recomputed its sampled error from 1.64112 to 0.050049 and synchronized the canvas.
- The local backend was rebuilt and its health endpoint returned 200. Existing chats and files were preserved. The final screenshot capture was unavailable in the browser tool; the earlier visual review was supplemented with DOM and interaction checks.
