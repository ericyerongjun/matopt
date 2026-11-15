# Six-minute live demo

Use one chat throughout so its Python kernel, imports and uploaded data stay warm.
The sample in `examples/demo/lensing.csv` is **synthetic**, generated around
`deflection = 2 / impact_parameter`. It is a teaching dataset, not observations
or a relativistic ray-tracing simulation.

## Before the presentation

Complete [Docker setup](../README.md#run-with-docker) well before the slot.
From the repository root, start the services:

```sh
docker compose up --build -d
docker compose ps
```

Open **http://localhost:3000**. Leave `SPEECH_PREFETCH=true` enabled; the first
launch downloads speech models into the persistent data volume. No Python
installation on the host is needed. Keep all three Compose services running.

Allow the microphone, open the virtual tutor and test a short spoken exchange
before the audience arrives. This also makes a live DeepSeek request. Keep that
chat open. Confirm the avatar is visible and the browser can play its audio;
localhost or HTTPS is needed for microphone access. Check model loading at
http://localhost:3000/api/speech/status if voice is not ready. Close other
GPU-heavy tabs and plug the laptop into power.

Developers can also run the optional [speech preflight](DEVELOPMENT.md#speech-preflight)
from the host. It tests local recognition and synthesis without a paid model
request, but does not test browser permissions, avatar rendering or cloud inference.

Confirm **Ultrafast Demo** is enabled in the model menu (the fresh-browser default). It disables thinking, encourages
compact working results, avoids the title request, and reuses a completed
answer when no additional consolidation or checking is needed. This is an app
preset, not a DeepSeek service tier. Tool results and numerical checks remain
real; explicit **Check results**, published findings, interrupted output and
user updates keep the normal checking/consolidation path.

For a deeper proof, turn **Ultrafast Demo** off to use the model's configured
default. There are no manual thinking-effort choices. Do not promise a difficult proof can be
finished live. Network congestion and model time are not bounded by the preset.

## Run of show

| Time | Action |
| --- | --- |
| 0:00–0:30 | Show the tutor and microphone. Attach the CSV, then speak the first request below. |
| 0:30–1:50 | Show the computed fit and chart. Let the tutor read the concise conclusion. |
| 1:50–2:50 | Ask the mathematical follow-up in the same chat. Turn Ultrafast Demo off if needed. |
| 2:50–5:20 | Return to Ultrafast and generate the compact interactive black-hole illustration. |
| 5:20–6:00 | Move its controls, show the live response, and leave a buffer. |

Data request:

> Use Python to analyze the attached synthetic lensing.csv. Let x be
> impact_parameter and y be deflection. Fit the one-parameter model y=A/x by
> minimizing the unweighted sum of squared y residuals with no intercept. Keep
> x, y and A in the Python kernel for my next turn. Show one scatter plot with
> the fitted curve, and report only n, A and R² plus one sentence saying these
> are synthetic teaching data and do not identify a physical mass or Einstein
> radius. Do not add physical interpretations or next steps.

Mathematical follow-up:

> Reuse the existing Python x, y and A without reloading or redefining them.
> Run only print(len(x), A, A/50). Show the objective S(A)=sum_i(y_i-A/x_i)^2,
> its derivative set to zero, and the resulting estimate in one aligned LaTeX
> display with equation tag (1). Give the prediction at x=50. Stop after the
> formula and prediction; do not discuss weighting transformations or physics.

Interactive request (choose **Opt** or **Auto**):

> Create one compact, self-contained interactive Canvas mini-app inspired by
> the black hole in Interstellar: dark center, bright photon ring, accretion
> disk and a star field. Add working controls for apparent mass, disk rotation
> speed, and viewing angle. Use no external assets or packages. Keep the
> implementation small; pause animation when hidden and honor reduced motion.
> Keep angle units consistent, update each control's numeric label, synchronize
> host state changes, and use cumulative elapsed time for animation.
> Label it a cinematic illustration rather than accurate relativistic ray tracing.
> Show the working app first, followed by two sentences explaining the controls.

If a live generation takes longer than rehearsed, continue with the completed
data and mathematics views. A rehearsed artifact can be shown as a **previously
generated example**, never presented as newly generated output. Rehearse on
the actual network and hardware; these timeboxes are a presentation plan, not
measured latency guarantees.

## Rehearsal checks and fallback

For the supplied CSV, the expected values are n=12, A≈2.00644935,
R²≈0.99978978 and A/50≈0.0401289871. These values check the calculation,
not the truth of any extra model commentary. Stop and correct unsupported
interpretations before presenting them.

Exercise every generated control and compare its label with the drawing.
Confirm the app actually appears: a model statement that an app was published
is insufficient. Generated JavaScript can need a repair turn, even when the
container and preview runtime work correctly. Keep a completed chat open as a
fallback and call it a previously generated example. See the measured
[Docker rehearsal report](DEMO-VERIFICATION.md) for the tested scope and failures.
