# Experimental Gemma text planner

Gemini Live remains the default realtime voice backend. The optional Gemma 4 E2B planner accepts **typed text only** and runs through a loopback llama.cpp server. There is no local ASR, TTS or full-duplex speech implementation. This is a small assistant-planner experiment, not Bixby.

## Safety and integration boundary

`typed input → committed transcript/revision → local JSON proposals → existing Coordinator → existing ProductivityBackend → Google APIs → receipts/events`

The local model cannot call Google itself. Its entire output batch is validated against existing tool schemas before proposals are registered. Corrections enter the controller immediately even while a generation is running; obsolete generations cannot acquire the latest request's authority. Already dispatched writes are not retroactively cancelled. Transient model HTTP failures use bounded inference-only retries; tool writes are not retried by this mechanism. There is no silent Gemini fallback. The existing policy still controls personal writes, contextual consent for sends/invitations/deletion, lookup-bound IDs, duplicate prevention, corrections and uncertain outcomes.

Results, requests and private Workspace content can enter the local model context and ignored `runs/` traces. Do not expose the llama.cpp port to the network. The experimental backend currently has bounded context/history and eight planning steps; long conversations may exceed the context or step limit and fail visibly. Close sessions with **End**. Do not restart a session to bypass an uncertain write.

## Reproducible local setup

Requirements: Linux x86-64, Python 3.12, curl, an NVIDIA driver compatible with CUDA 12.8. A CUDA compiler is not required by the pinned binary distribution. Downloads use approximately 4.1 GB plus extracted runtime and must pass their pinned SHA-256 checksums. Model data and binaries stay under ignored `runs/local-planner/`.

```bash
uv sync --frozen
uv run python scripts/setup_local_planner.py
# Start only one model server:
runs/local-planner/serve.sh
# In another terminal, start the existing demo server:
./reproduce.sh demo
```

The downloader pins llama.cpp `b11753` and Google's `gemma-4-E2B-it-qat-q4_0-gguf` revision `675cff42a74c774d6cb76f76d8eacb49b48c9b93`. It omits the multimodal projector. The launch script binds `127.0.0.1:8081`, aliases the model `reprise-gemma-e2b`, uses one inference slot, 8,192 context tokens, batch/microbatch 128 and four CPU threads. Initial partial offload is 20 layers; inspect actual startup buffers and `nvidia-smi`, not the nominal weight size alone.

```bash
nvidia-smi --query-gpu=name,memory.total,memory.free,driver_version --format=csv
# Lower offload if measured GPU memory is insufficient:
REPRISE_GPU_LAYERS=12 runs/local-planner/serve.sh
```

Only one server should own port 8081. Do not start both commands simultaneously. Default client settings, optionally placed in `.env.local`:

```dotenv
REPRISE_LOCAL_URL=http://127.0.0.1:8081
REPRISE_LOCAL_MODEL=reprise-gemma-e2b
```

Open the demo → Session settings → Model → **Gemma E2B · text only**, Tools → **Google Workspace**, then Start conversation. Microphone is disabled in this mode. Send typed requests/corrections in Conversation; observe actual tool stages and Google receipts in Actions & Status. Choose **Gemini Live** in a new session for normal voice. Local mode does not require LiveKit or a Gemini key; real Workspace writes still require Google OAuth.

The local endpoint has a 120-second inference timeout, deterministic sampling settings (temperature 0, seed 42), a 768-token generation cap, eight calls per batch and eight steps per turn. It rejects unfinished/malformed output rather than executing a partial JSON object. Sampling settings do not guarantee GPU bit-for-bit determinism.

## Verification

```bash
uv run pytest tests/test_local_planner.py tests/test_demo_events.py -q
node --test --test-isolation=none demo/app.test.cjs
```

Use the [Galaxy sync checklist](GALAXY_SYNC.md) for actual account writes and phone inspection. Fixture comparisons are text-to-tool tests, not voice tests, Google sync tests, or a new FDB score. Hardware/inference observations and comparison outcomes are recorded separately after execution; do not infer results from the existence of this adapter.

Official references: [Google QAT GGUF](https://huggingface.co/google/gemma-4-E2B-it-qat-q4_0-gguf), [pinned llama.cpp release](https://github.com/ggml-org/llama.cpp/releases/tag/b11753).

## Observed checkpoint on this laptop

The pinned server loaded successfully with 20 GPU layers and 8K context. A sampled idle GPU reading was 1,156 MiB used / 2,559 MiB free; this is not a peak-memory measurement. The runtime creates a private `runs/local-planner/api-key` automatically. Reprise reads it server-side; it is never sent to the browser. The launcher restricts CORS and binds loopback only.

The initial seven-case text/fixture comparison exposed unnecessary clarification/confirmation, repeated reads and a failed post-creation reference. One task was created through the controller, but its rename was rejected because the model supplied a raw ID where a lookup-bound reference was required. A generated summary also confused operation completion with task completion; the actual task remained incomplete. Model prose must not be treated as a Google receipt. This planner is experimental, not equivalent in reliability or latency to the working Gemini path. Cold inference and multi-step dialogues were slow; physical voice performance was not tested.

Full requests, results, traces, timing denominators, the preserved invalid infrastructure attempt and manual-checkpoint status are in the local `runs/phase4-20261020/PHASE4_REPORT.md`. No prompt/controller changes were made to tune against those comparison outcomes.

The default prompt profile remains `legacy`. `REPRISE_LOCAL_PROMPT_PROFILE=compact` selects an experimental alternative, not a measured reliability improvement. Actual inference attempts, token usage and timing are traced. Unsupported completion claims are corrected from backend receipts.
