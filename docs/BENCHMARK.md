# Full-Duplex-Bench v3 reproduction

The benchmark uses the public 12-tool schemas and FDB mock backend, independently of the real Google Workspace backend. The upstream source/evaluators are pinned to `3e799c45a045256f47d5f1c9cda90157e2d2ec9e`. Python dependencies are locked in `uv.lock`.

## Historical results

The [reviewed evidence bundle](../evidence/fdb-v3-20261019/README.md) preserves original released-v9 results (77/100 strict) and the subsequent repaired-controller run (78/100 strict, 97.63% micro tool-selection F1, 81.33% expected-call-weighted strict argument accuracy). Repaired call counts: 144 matched, 150 expected, 145 actual. F1 is `2PR/(P+R)`; missing expected calls receive zero argument score. All 100 repaired cases completed, with one retained failed client attempt followed by a successful retry.

The separate semantic comparison used `gemma-4-26b-a4b-it` consistently for both versions: 77/100 original and 82/100 repaired tool pass. These are Gemma-judged local results, not official GPT-4o scores. Semantic tool pass requires exact tool selection plus acceptable arguments; spoken-response correctness is scored separately. Three missing original response transcripts receive zero response scores. Judge failures were retried with a bounded shared queue, and no exact-match fallback was used for these historical Gemma results.

The original inference was reused; repaired inference was new and nondeterministic. The scores do not establish controller causation or describe the current release's unmeasured performance. Exact runtime sources, recorded configuration hashes, case checks and saved repaired outputs are included; raw audio, complete traces and raw judge caches remain local. The snapshot hash matches the historical configuration. Neither the historical Git base alone nor today's HEAD identifies that old working tree.

## Setup

Install Git, Python 3.12, uv and FFmpeg; configure Gemini and LiveKit credentials in `.env.local` as described in the README.

```sh
./reproduce.sh setup                 # Pinned source only; useful for controller tests
./reproduce.sh setup --data          # Also download the released recordings
```

## New voice inference

This command uses Gemini/LiveKit resources and starts its own worker. Stop unrelated workers attached to the same LiveKit project first.

```sh
./reproduce.sh all --model gemini-3.8-live --seed 20260927 \
  --concurrency 2 --name YOUR_UNIQUE_RUN
```

No sample/limit flag means all released recordings. `--sample ID` or `--limit N` selects a development subset and must not be reported as a full score. Names must be unique. The configured model must be available to your Google project; no provider fallback is performed.

Match policy, settlement, VAD silence, latency profile, dataset hashes and concurrency when comparing runs. Historical repaired settings were guarded policy, 1,000 ms settlement, 900 ms write settlement, 650 ms Gemini VAD silence, instant mock latency and concurrency 2. Its manifest, selected IDs and per-file hashes are in the evidence bundle. The recorded selection seed controls ordering, not deterministic voice generation.

## Saved-output scoring

```sh
# Offline strict/continuous scoring; no inference/judge
uv run --frozen reprise score runs/YOUR_UNIQUE_RUN --provider reprise

# Rescore the published historical repaired outputs offline
uv run --frozen reprise score evidence/fdb-v3-20261019/repaired --provider reprise \
  --output runs/repaired-historical-rescore.json

# Explicit upstream GPT-4o semantic scoring; requires OPENAI_API_KEY and may incur costs
uv run --frozen reprise score runs/YOUR_UNIQUE_RUN --provider reprise --use-llm

# Local event diagnostic summary
uv run --frozen reprise metrics runs/ROOM/events.jsonl --output runs/ROOM/metrics.json
```

The CLI verifies the evaluator checkout, loads benchmark ground truth, and scores saved executed calls. Its aggregate F1/argument fields average per-case scores; they are macro metrics, unlike the micro F1 and expected-call-weighted argument figures in the historical `tool-metrics.json`. Pending dispatches without terminal evidence remain uncertain; a proposal is not an executed call. Upstream judge errors may fall back to exact matching in `--use-llm`; inspect diagnostics before calling a score semantic. That path is different from the archived Gemma adapter in the historical source snapshot.

## Measurement boundaries

A strict pass is case-level: tool selection and arguments must pass, execution must be confirmed when recorded, and terminal infrastructure errors fail the case. Selection and argument metrics are continuous measures, not case pass rates. Semantic judgments and exact matches remain separate.

Controller gate, mock execution and SDK timing are observable diagnostics. They are not microphone-to-speech or official task-completion latency. Independent timestamped ASR/shared input-output audio-clock alignment was unavailable for the historical comparison. Do not invent those measurements from wall timestamps or SDK TTFT.

Full raw run directories remain ignored under `runs/`; runtime traces can contain private account data. Only the explicitly reviewed synthetic-benchmark bundle is intended for publication. Upstream data/code retain their original license and attribution.
