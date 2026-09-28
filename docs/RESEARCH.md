# Research and design record

Reviewed 27 September 2026. Submission deadline supplied by the participant: 29 September 2026.

## Governing task

The updated Word guide requests a LiveKit agent evaluated on Full-Duplex-Bench v3, a working extension, a 3–5 minute demonstration, and a deck of at most eight slides. It allocates 60% to the organizer's normalized benchmark score, 20% to the extension and 20% to documentation/video. The older PDF describes a different hidden multimodal evaluation and weighting. This implementation follows the updated guide. The organizer must resolve that discrepancy and supply the final normalization/judge configuration.

## Evidence and decisions

| Source | Finding relevant to this project | Implementation decision |
| --- | --- | --- |
| [Full-Duplex-Bench v3](https://github.com/DanielLin94144/Full-Duplex-Bench/blob/main/v3/README.md) and [published results](https://daniellin94144.github.io/FDB-v3-demo/) | Strict completion penalizes omitted and extra calls. Corrections and harder multi-step requests remain difficult. | Preserve every executed call in telemetry; track dependencies and corrections. Use the original public mock tools and evaluator. |
| [Building Interactive Real-Time Agents with Asynchronous I/O and Speculative Tool Calling](https://arxiv.org/html/2605.13360v2) | Its executor distinguishes reads from writes, revises queued work and discards obsolete observations. On its own tasks, latency improves with some accuracy tradeoffs. | Keep tool preparation interruptible. Commit a mutation only after input settles; preserve already-dispatched mutations in the ledger. This is a design adaptation, not a replication of its trained model. |
| [Voice-Light](https://arxiv.org/html/2609.20995v1) | Explores speculative generation and cancellation around speech. Its evaluation scale and endpointing results limit broad performance claims. | Treat interruption as an execution concern. Keep spoken output and pending operations distinct; test race conditions explicitly. |
| [Front-end/back-end voice-agent study](https://arxiv.org/html/2609.19334v2) | The study's strongest text reasoning result and its hybrid voice result differ substantially; adding a second model does not by itself establish a better voice agent. | Begin with the user's requested native audio model. Add complexity only after measured failures justify it. |
| [Nemotron VoiceChat](https://arxiv.org/abs/2609.21967) | Recent work continues to evaluate tool selection separately from argument/task correctness. | Report strict task completion separately from tool counts. Do not imply that tool selection alone measures successful execution. |
| [LiveKit tool execution](https://docs.livekit.io/agents/logic/tools/definition/) | Speech interruption alone does not safely cancel arbitrary Python side effects. | Own cancellation, duplicate suppression and ambiguous write outcomes in the controller. |
| [Gemini Live capabilities](https://ai.google.dev/gemini-api/docs/live-api/capabilities) and [Gemini 3.8 Live](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-live) | Native audio, transcription and asynchronous tool responses fit this workload. Provider configuration differs across model generations. | Use exactly `gemini-3.8-live`; validate the actual WebSocket setup with tools. Avoid unsupported thinking/affective settings. |

## Reprise's proposed contribution

A small controller surrounds the audio model. It assigns an input revision to each proposal, validates the argument schema, checks dependent IDs against actual tool results, and waits for speech to settle. It coalesces duplicate operations within the active request. New input cancels obsolete preparation and reads. A mutation already sent to the backend remains recorded even if its explanation is interrupted. No action is silently undone or retried after an ambiguous failure.

The combination is an engineering hypothesis. The underlying ideas have precedents above; we make no claim of a new research result or a guaranteed winning score. The useful test is whether it improves completion under the same model and audio inputs without unacceptable delay.

## Evaluation discipline

1. Lock the upstream commit, dependencies, selected recordings and source digest before a run.
2. Use a deterministic sample across domain/difficulty groups to find integration failures.
3. Compare guarded and baseline execution on the same sample. Preserve failed attempts.
4. Freeze the candidate for the full released recording set. Treat that set as public development evidence, not hidden-test generalization.
5. Run independent correction, duplicate-action and tool-failure tests with newly authored inputs.
6. Use the organizer's pinned semantic judge and full original evaluation pipeline for the official score.

The local exact-match screening omits independent ASR and the semantic judge. Date aliases, spelling conventions and numerically equivalent strings may fail exact matching. Conversely, the original exact matcher skips dynamic `$...` references. Neither limitation is hidden. Missing or failed runs remain in the coverage report. Infrastructure errors are not counted as passes.

## Data separation

The running agent imports public tool contracts and mock implementations. It does not load scenario text, expected calls, input filenames or answer tables. Only the offline evaluator reads recording metadata. State is local to a room/request and does not carry across scenarios. The submission includes all call telemetry, including incorrect calls and dispatched calls subsequently cancelled.

## Reproduction boundary

Upstream commit: `3e799c45a045256f47d5f1c9cda90157e2d2ec9e`.

The mock API computation is unchanged. Its delay profiles run with asynchronous sleep so listening remains responsive. For writes the artificial delay precedes commitment. This execution change is disclosed; the official rerun must use the organizer's required latency configuration.

Hosted inference and LiveKit need network access and sufficient account quota. No alternate model or billing upgrade is enabled automatically. The official upstream judge currently needs `OPENAI_API_KEY`; no such credential has been supplied. The organizer's official score and ranking therefore remain unverified.
