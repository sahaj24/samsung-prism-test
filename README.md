<h1 align="center">Reprise: Interruptible Voice Agent</h1>

<p align="center">
  <em>An interruptible, real-time voice agent built with Gemini Live and LiveKit.</em>
</p>

<p align="center">
  <a href="#-features"><strong>Features</strong></a> ·
  <a href="#-architecture"><strong>Architecture</strong></a> ·
  <a href="#-benchmark-reproduction"><strong>Benchmark</strong></a> ·
  <a href="#-google-workspace-extension"><strong>Extension Use Case</strong></a>
</p>

---

Reprise listens to spoken requests, handles interruptions and changes of mind, gracefully executes multi-step tool calls, and answers aloud. 

## Features

- **Low Latency Voice:** Operates directly on the WebRTC audio stream via Gemini Live.
- **Asynchronous Execution:** Backgrounds tool calls so the conversation never "freezes".
- **Interruption Recovery:** Employs a Local Action Controller with an active ledger to drop stale intents if you change your mind mid-sentence.
- **Google Workspace Ready:** Out-of-the-box support for Gmail, Calendar, and Tasks via live OAuth.

---

## Architecture

```mermaid
flowchart LR
    User([User Speech]) <-->|Audio Stream| LiveKit[LiveKit WebRTC]
    LiveKit <-->|Real-time Audio| Gemini[Gemini 3.8 Live API]
    Gemini -->|Tool Calls| Controller[Local Action Controller]
    Controller -->|Validates & Deduplicates| Controller
    Controller -->|Executes Plan| Workspace[Google Workspace APIs]
    Workspace -->|Results| Controller
    Controller -->|Tool Responses| Gemini
```

### Under the Hood

**1. Stay Responsive:** 
Because it operates natively on audio, speech latency is exceptionally low. It acknowledges user intent immediately without waiting for background operations to complete.

**2. Work Asynchronously:** 
When Gemini decides to call a tool, the request is intercepted by the **Local Action Controller**. The controller executes these actions in the background.

**3. Recover Cleanly (Interruption Handling):** 
If the user stumbles or changes their mind (e.g., *"Set a meeting for 3 PM—no wait, make it 4 PM"*), the agent processes the correction. Crucially, it validates incoming tool arguments against the updated intent and intercepts stale or superseded tool calls before they hit the live APIs.

---

## Benchmark Reproduction

![Local exact-match result: 77 of 100 released recordings passed. easy 30/36, medium 27/34, hard 20/30.](docs/benchmark-summary.svg)

Evaluated against **Full-Duplex-Bench v3**. The frozen benchmark version in this repository achieved **77/100**.

| Check | Result |
| --- | ---: |
| Strict tool-task passes | **77 / 100** |
| Completed recordings | 100 / 100 |
| Easy tasks | 30 / 36 |
| Medium tasks | 27 / 34 |
| Hard tasks | 20 / 30 |

*Note: This is a local exact-match result using the benchmark's literal argument matcher. See the [Full Report](evidence/released-v9/report.json) and [Case-by-case Results](docs/benchmark-cases.md).*

### Quickstart Evaluation

Requirements: Python 3.12 (via `uv`), Git, FFmpeg, a Gemini API key with Live access, and a LiveKit Cloud project. 

```sh
# 1. Setup environment
cp .env.example .env.local
chmod 600 .env.local

# 2. Fill in your credentials in .env.local

# 3. Download benchmark data and run doctor check
./reproduce.sh setup --data
./reproduce.sh doctor

# 4. Run the full evaluation suite
./reproduce.sh all --seed 20260927 --concurrency 2 --name my-eval-run
```

To manually recheck the new run with the upstream scorer:
```sh
uv run --frozen python third_party/fdb/v3/evaluate_pass_rate.py \
  --benchmark third_party/fdb/v3/benchmark_data_v2.json \
  --results-dir runs/my-eval-run \
  --provider reprise \
  --output runs/my-eval-run/upstream_exact_report.json
```

---

## Google Workspace Extension

This repository extends the core benchmark with a real-world **Google Workspace Voice Planner**. It turns spoken requests into Calendar events, Tasks, and Gmail drafts using live Google APIs.

### Running the Planner

To test the extension end-to-end, start the agent backend and the web dashboard in two separate terminals. *(Requires Node.js for the dashboard, and a Google Workspace OAuth Client ID in `.env.local`)*.

**Terminal 1 (Backend Agent):**
```sh
./reproduce.sh agent
```

**Terminal 2 (Web Dashboard):**
```sh
npm ci --prefix demo
./reproduce.sh demo
```

Open `http://127.0.0.1:8844`, connect your Google account, and start speaking.

---

## Repository Structure

- `src/reprise/agent.py`: LiveKit and Gemini voice session integration.
- `src/reprise/coordinator.py`: Action ledgers, interruption recovery, and validation.
- `src/reprise/google_workspace.py`: Live Google OAuth and REST adapters.
- `demo/`: Companion dashboard for the extension.
