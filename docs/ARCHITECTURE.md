# Architecture and request lifecycle

Reprise runs a Python LiveKit agent with Gemini Live and a local action controller. The working extension uses real Google Workspace APIs. The browser is plain HTML/CSS/JavaScript; FastAPI serves the page, local control endpoints and a polled event feed. LiveKit carries bidirectional audio and native transcription streams.

## State ownership

| Component | Owns |
| --- | --- |
| LiveKit/Gemini session | Audio transport, model generation, input/output transcription and SDK speech handles |
| `Coordinator` | Input/revision state, acoustic activity, proposal order, validation, settlement, cancellation and in-flight tool outcomes |
| `IntentMemory` | Small room-local, transcript-grounded retained fields for opted-in tools; not a general semantic parser |
| `ProductivityBackend` / `PlanStore` | Room-scoped durable plans, consent receipts, execution leases, resource ownership and Google receipts |
| `WorkspaceManagement` | Lookup snapshots, known Task-list IDs, numbered selection groups and focused resources |
| Google APIs | Actual mailbox, event and task state |
| Browser | Presentation, session controls and page-local history; it does not infer successful API completion |

## End-to-end request

1. The browser publishes microphone audio to a LiveKit room. The Python agent forwards the track to Gemini Live and subscribes to returned audio for browser playback.
2. Session hooks observe input transcription/finality and committed conversation items; the acoustic monitor observes user speech activity. Typed messages enter the controller before LiveKit generates the reply.
3. Input receives identifiers and revision context. Genuine corrections supersede stale uncommitted operations; additive context can retain unaffected work. Assistant speech interruption is tracked separately from intent replacement.
4. Gemini proposes structured tools. `make_tool` routes each call through the coordinator with provider call and speech-response IDs. The coordinator records raw arguments, validates schema and opted-in identifier constraints, and applies revision/settlement checks.
5. Async preparation and tool tasks allow the voice session to remain active. Mutations retain proposal ordering/dependencies. Cancellation before dispatch suppresses stale work; a dispatched mutation is shielded from caller detachment and retains its real terminal or uncertain result.
6. In Workspace mode, `prepare_plan` stages new actions; `prepare_change` or `prepare_resource_change` stages supported changes to saved/looked-up resources. Preparation writes only the local ledger. `execute_plan` applies the deterministic risk policy and claims eligible actions.
7. Google requests use the connected account. Authorization is checked again after awaited token acquisition and immediately before HTTP mutation. Calendar and Task changes GET the actual resource, verify expected values, use `If-Match`, then read back the outcome. Task-list aliases resolve to actual returned IDs; legacy uncertainty locks remain effective.
8. Actual results and receipts update the ledger and return to Gemini through the non-blocking tool flow. Old results retain their original revision and can be withheld from current speech. Model acknowledgments and plan preparation do not establish API completion.
9. Append-only correlated events feed Actions & Status. Native LiveKit streams show live transcript chunks; committed user/assistant items populate Conversation. All visible tool progression comes from backend events.

## Persistence and failure boundaries

The coordinator's conversational state is in memory per room. The Workspace SQLite ledger persists claims, known resources and receipts under `runs/`. A restart recovers expired running plans to review/unknown states; it does not resume unsent actions. New conversations look up existing Google resources again rather than reusing another room's selection references.

Plan leases are 60 seconds and renew every 20 seconds. The productivity tool timeout is 180 seconds. Controller settlement defaults are 1,000 ms and 900 ms for writes; its 30-second gate timeout rejects an unsettled operation rather than granting permission. Gemini VAD silence defaults to 650 ms. These are configuration values, not measured end-to-end latency or permission to execute unfinished speech.

There is no cross-service transaction. A Google timeout after dispatch can leave Outcome unknown. Resource ownership prevents blind retries; `reconcile_plan` performs supported ID-based reads. A lost Task/Gmail creation receipt cannot be resolved by guessing a similar title. Calendar creation has a deterministic operation ID usable for reconciliation.

## Modes

- **Productivity:** current Google Workspace tools and prompt; OAuth account writes are real.
- **Benchmark:** the pinned public FDB mock tool registry and benchmark prompt. Room-local state starts fresh; scenario labels and expected answers are not controller inputs.

The local demo chooses mode in room metadata. Workspace implementation changes do not replace the original benchmark tool schemas. Hosted Gemini and LiveKit are required; no local inference GPU is used.

See [execution safety](HARDENING.md), [Workspace setup](PRODUCTIVITY.md), and [evaluation commands](BENCHMARK.md).

## Optional call-listening gate

`CallActivation` guards Demo Live at proposal admission and immediately before dispatch. A versioned browser/server/worker handshake must confirm the backend presentation before microphone publication. Demo rooms with missing or obsolete metadata default to standby; ordinary Live is explicitly negotiated. Standby drains and suppresses assistant output while preserving input transcription/context. Final addressed requests open an activation interval; dismissal invalidates pending plans without cancelling dispatched receipts. Response epochs prevent standby generations from acquiring later activation authority. See [call listening](LIVE_CALL_ASSIST.md).
