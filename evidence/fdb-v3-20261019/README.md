# Historical Full-Duplex-Bench v3 evidence

These are preserved **local historical results**, not a new evaluation of the current release. `original/` contains the released-v9 manifest and exact reports (77/100). `repaired/` contains the subsequent Gemini Live inference and strict/semantic reports (78/100 strict, 82/100 Gemma-judged). Input audio and metadata are referenced by their hashes and released sample IDs, not redistributed as recordings.

The pinned upstream is `3e799c45a045256f47d5f1c9cda90157e2d2ec9e`. Both runs use the same 100 released recordings, Gemini `gemini-3.8-live`, and FDB mock tools. Real Workspace actions are not part of these runs. `METHODS.md` preserves the original scoring and comparison methodology. Its run-directory references describe the local archive, not additional files promised in this bundle.

## Included records

- `original/manifest.json`, `report.json`, `upstream_exact_report.json`: original configuration, case checks and aggregate exact results.
- `repaired/manifest.json`: derived compatibility index for the current offline scorer; the original configuration below is unchanged.
- `repaired/run-configuration.json`: selected IDs, input/metadata/evaluator/lockfile hashes, settings, source hash and historical Git base. The historical working tree was modified; its Git base alone does not identify the evaluated controller.
- `repaired/source-snapshot/`: exact archived Python files, for inspection only. Do not replace the current runtime with these modules. The directory includes historical judge adapters and is not a supported second application.
- `repaired/cases/*/result_reprise.json`: saved executed calls and provider response transcripts for offline rescoring. These contain synthetic public-benchmark content, not user Workspace sessions. Referenced traces/audio are retained locally and deliberately omitted here.
- `repaired/strict-evaluation.json`, `tool-metrics.json`, `case-level-results.json`, `gemma-semantic-evaluation.json`, `version-summary.json`: scoring outcomes, counts, argument checks and uncertainty.
- `SHA256SUMS`: checksums for the copied records. Original local artifacts were copied without replacement or deletion.

Repaired source SHA-256: `93562be1d24da1e194c66e804b515dbb69fcbc4b54bf340f088a7357ccbeb258`. `source_digest` hashes each sorted Python filename followed by its bytes. The original manifest records its own source hash; its complete runtime snapshot is not included here. Raw judge prompts/responses, audio, worker logs and complete traces remain in local archives; they are not fully reproduced by this curated bundle.

## Offline verification

After `./reproduce.sh setup`, run:

```sh
uv run --frozen reprise score evidence/fdb-v3-20261019/repaired --provider reprise \
  --output runs/repaired-historical-rescore.json
```

This recomputes strict/continuous scores from saved call outputs. The current CLI reports per-case macro F1/argument averages; the headline historical F1 and argument figures in `tool-metrics.json` are micro/expected-call-weighted metrics and use different denominators. It does not rerun inference or invoke a judge. The historical semantic judgments are recorded results: the default CLI's `--use-llm` uses upstream GPT-4o, not the archived Gemma adapter. Never relabel these scores as official GPT-4o results.

## Limitations and attribution

The original inference was reused; the repaired inference was fresh and nondeterministic. A one-case difference does not establish controller causation. Missing original transcripts, retried infrastructure attempts, and unmeasured aligned audio latency remain limitations. Current controller/Workspace/activation changes postdate these evaluations.

Benchmark schemas, identifiers, expected actions and derivative evaluation content are attributed to [Full-Duplex-Bench](https://github.com/DanielLin94144/Full-Duplex-Bench/tree/3e799c45a045256f47d5f1c9cda90157e2d2ec9e), distributed under its [CC BY-NC 4.0 license](UPSTREAM_LICENSE.txt). The included reports are derivative evaluation outputs; upstream audio is not bundled. This attribution does not grant a new license to the Reprise application source.
