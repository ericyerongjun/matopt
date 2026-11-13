# Six-minute live demo

Use one chat throughout so its Python kernel, imports and uploaded data stay warm.
The sample in `examples/demo/lensing.csv` is **synthetic**, generated around
`deflection = 2 / impact_parameter`. It is a teaching dataset, not observations
or a relativistic ray-tracing simulation.

## Before the presentation

Start the backend well before the slot. Leave `SPEECH_PREFETCH=true` enabled;
the first launch downloads local speech models. From the repository root run:

```sh
python3 scripts/demo_preflight.py --warmup --language en
```

Use `--language zh` for Mandarin, `--base-url` for another backend, and export
`API_AUTH_TOKEN` if the server requires it. The check exercises real local
recognition and synthesis but makes no paid model request. It does not prove
browser or cloud readiness: allow the microphone, open the virtual tutor,
test a short spoken exchange, and run one short live DeepSeek request before
the audience arrives. Keep that chat open. Confirm the avatar is visible and
the browser can play its audio; an HTTPS origin or localhost is needed for
microphone access. Close other GPU-heavy tabs and plug the laptop into power.

Choose **Ultrafast demo** in the model menu. It disables thinking, encourages
compact working results, avoids the title request, and reuses a completed
answer when no additional consolidation or checking is needed. This is an app
preset, not a DeepSeek service tier. Tool results and numerical checks remain
real; explicit **Check results**, published findings, interrupted output and
user updates keep the normal checking/consolidation path.

For a deeper proof choose **Low** or **High** thinking instead; selecting a
thinking level exits the demo preset. Do not promise a difficult proof can be
finished live. Network congestion and model time are not bounded by the preset.

## Run of show

| Time | Action |
| --- | --- |
| 0:00–0:30 | Show the tutor and microphone. Attach the CSV, then speak the first request below. |
| 0:30–1:50 | Show the computed fit and chart. Let the tutor read the concise conclusion. |
| 1:50–2:50 | Ask the mathematical follow-up in the same chat. Use Low thinking if needed. |
| 2:50–5:20 | Return to Ultrafast and generate the compact interactive black-hole illustration. |
| 5:20–6:00 | Move its controls, show the live response, and leave a buffer. |

Data request:

> Analyze lensing.csv using Python. Fit deflection = A / impact_parameter,
> report A and R², and show one scatter plot with the fitted curve. State that
> these are synthetic measurements. Explain the result in at most five sentences.

Mathematical follow-up:

> Derive the least-squares estimate of A for that model. Show the essential
> steps in LaTeX, then use the fitted model to calculate the deflection at an
> impact parameter of 50. Check the calculation with Python.

Interactive request (choose **Opt** or **Auto**):

> Create one compact, self-contained interactive Canvas mini-app inspired by
> the black hole in Interstellar: dark center, bright photon ring, accretion
> disk and a star field. Add working controls for apparent mass, disk rotation
> speed, and viewing angle. Use no external assets or packages. Keep the
> implementation small; pause animation when hidden and honor reduced motion.
> Label it a cinematic illustration rather than accurate relativistic ray tracing.
> Show the working app first, followed by two sentences explaining the controls.

If a live generation takes longer than rehearsed, continue with the completed
data and mathematics views. A rehearsed artifact can be shown as a **previously
generated example**, never presented as newly generated output. Rehearse on
the actual network and hardware; these timeboxes are a presentation plan, not
measured latency guarantees.
