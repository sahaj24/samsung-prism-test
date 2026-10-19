# FDB-v3 reproduction boundary

Theme 05 uses [Full-Duplex-Bench v3](https://github.com/DanielLin94144/Full-Duplex-Bench/tree/main/v3). Reprise retains the public 12-tool schemas and mock backend separately from the Google Workspace demo. The upstream checkout is pinned to `3e799c45a045256f47d5f1c9cda90157e2d2ec9e`; dependency versions are in `uv.lock`. Controller execution does not consume scenario labels.

## Setup and one-command run

Configure Gemini Live and LiveKit credentials as in the README. Install Git, Python 3.12, uv and FFmpeg. The reproduction wrapper synchronizes dependencies and the `all` command fetches the pinned upstream source/data, checks services, starts its worker and runs the selected benchmark.

```sh
# Source only: useful for offline controller tests; no inference
./reproduce.sh setup

# Explicit full evaluation: consumes Gemini/LiveKit resources
./reproduce.sh all --model gemini-3.8-live --seed 20260927 --concurrency 1 --name YOUR_UNIQUE_RUN_NAME
```

Use a unique run name. Do not run an unrelated agent worker against the same LiveKit project during evaluation. The model must be available to the configured Google project; no silent provider fallback is performed. `all` selects benchmark mode and defaults to all released recordings; sample/limit options are development screening only. The latency profile and settlement settings are configurable in `.env.local` and recorded in manifests; they must be matched for comparisons.

Run artifacts include source/input/lockfile hashes, settings, attempts, event traces, saved outputs and provisional exact reports under ignored `runs/`. Generation is nondeterministic despite the recorded selection seed. No new evaluation was run or score claimed as part of this demo publication. Historical submission artifacts remain in earlier Git history and local archives; this release does not publish new reports.

## Exact versus semantic evaluation

```sh
# Offline scoring of saved results against the pinned evaluators
uv run --frozen reprise score runs/YOUR_RUN --provider reprise

# Explicit semantic scoring; requires OPENAI_API_KEY and can incur charges
uv run --frozen reprise score runs/YOUR_RUN --provider reprise --use-llm

# Offline diagnostic summary; no model call
uv run --frozen reprise metrics runs/ROOM/events.jsonl --output runs/ROOM/metrics.json
```

`all` does not invoke a judge merely because a judging key exists. The semantic option uses the pinned upstream GPT-4o judging logic for argument/response evaluation; upstream error handling can fall back to exact matching, so inspect the diagnostics. It is not presented as a completed official evaluation or a guarantee that every case received semantic judging. Experimental alternative-judge/comparison runners and their reports are outside this demo release.

Reported exact metrics, continuous tool-selection F1/argument metrics and semantic response quality have different meanings. Infrastructure failures, missing cases and uncertain tool outcomes remain visible. No missing audio-alignment measurements are fabricated. SDK/proxy durations are distinct from upstream audio latency.

The updated participant guide requires an architecture explanation, exact setup/run steps, a real extension and separately supplied evaluation/submission evidence. This branch prioritizes the working demo and reproducible source. Additional submission assets and refreshed benchmark evidence remain a later publication task.
