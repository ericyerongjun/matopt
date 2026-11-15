# Model-quality evals

`math_core.jsonl` holds graded problems across calculus, linear algebra,
probability, statistics, optimisation and number theory. Items tagged `hard`
need multi-step work. Every reference answer was computed with SymPy, SciPy or
NumPy and cross-checked numerically, not written from memory. Items with
`abs_tol` are graded numerically; the rest with `math-verify`, so equivalent
forms such as `e-2` and `-2+e` both pass.

## Optional developer setup

Start the application with [Docker Compose](../README.md#run-with-docker).
The evaluation runner is a separate host-side developer tool. Its examples
below require the Python environment from [native development setup](../docs/DEVELOPMENT.md#native-prerequisites);
you do not need to start a second native backend. It uses the running API at
`http://127.0.0.1:8000` by default.

The runner sends each item through the real harness and provider in a fresh
conversation (paid), grades the final `\boxed{}` answer and records wall time,
model and tool calls, tokens and prompt-cache reuse:

```bash
backend/.venv/bin/python scripts/run_evals.py --output evals/results/my-run.json
backend/.venv/bin/python scripts/run_evals.py --tag hard --baseline evals/results/baseline-deepseek-flash-high.json --output /tmp/hard.json
```

The runner currently defaults to `high` reasoning and does not enable the
browser's Ultrafast preset. Report results with their actual settings; saved
results are not a benchmark of the current Ultrafast experience. The runner
also assumes the local API does not require an authentication token.

Run it before and after a prompt, harness or model change and compare. A
change that keeps the pass rate but cuts tokens or time is still a win. When
the pass rate saturates, add harder items rather than reading noise.
