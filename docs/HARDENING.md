# Execution safety and traceability

This document describes current runtime contracts, not a new benchmark score or an exactly-once guarantee.

## Controller

`coordinator.py` and `intent.py` retain raw proposals, input IDs, revisions and ordered operation states. Audio transcription and typed input share the same observation path. Finality and acoustic speech state are separate: duplicate, interim and late transcript events do not create permission from silence alone. Settlement and authorization must hold at dispatch.

A genuine correction supersedes uncommitted affected work. Assistant speech interruption alone does not cancel valid intent. Dispatched operations retain their real outcome even when the response caller detaches. Stale results are not promoted as current results. Identifier checks are opt-in by entity/field; they do not fuzzy-match unrelated identifiers or rewrite free-form Workspace content.

Equivalent in-flight proposals coalesce. Only confirmed terminal results qualify for reuse; approval-required, pending, partial, failed and unknown operations are not successful completion. Non-cacheable planning/status/lookup tools re-read their source. The controller is room-local and does not read benchmark labels or retain scenario state across rooms.

## Workspace execution

The SQLite plan ledger owns consent, action/resource claims and receipts. Personal reversible actions can execute after settlement; email sends, invitations, deletions and flagged consequential changes require action-specific confirmation. Final affirmative input must refer to a contextual question; interim or unrelated acknowledgments cannot grant consent. The UI approval path is also bound to the current saved plan.

Plans use a 60-second lease with 20-second renewal. Authorization and ownership are rechecked immediately before a write, after any awaited token refresh. Corrections can stop undispatched actions; they do not retroactively cancel a request already sent to Google. Expired running plans recover to review states with in-flight actions unknown and unsent actions not executed. Recovery never automatically resumes the plan.

Calendar/Task updates/deletions require a confirmed actual ID. Lookup ambiguity must be resolved before preparation. Preflight GET verifies expected fields and obtains an ETag for `If-Match`; readback verifies the result. Canonical Task-list IDs and legacy alias checks preserve uncertainty locking. Completing, reopening and completing again uses a new verified resource revision rather than a cached old completion.

`reconcile_plan` reads remote state for supported known IDs and deterministic Calendar creation IDs. It never retries a write. Lost Tasks/Gmail creation receipts without an ID remain unresolved; matching a similar title is not proof. Failed undo is Failed, not Undone. No automatic undo of sent email or restoration of a prior update is exposed.

## Evidence and display

`runs/<room>/events.jsonl` is append-only schema-v2 JSONL. Events include wall/monotonic time, sequence, trace/event IDs, input/request/revision context, proposal/provider-call/response IDs where available, and plan/operation IDs. Google events retain HTTP outcomes and available request/resource IDs. Durations use monotonic clocks.

The causal path is transcript → revision → raw proposal → controller decision → dispatch → HTTP outcome → receipt/tool result → assistant response. Provider speech-handle links are best-effort; a fallback association is not proof of audio alignment. Missing fields remain unknown.

The browser uses actual events and native LiveKit transcript streams. It never manufactures action progression. Conversation timestamps are event/receipt times. SDK TTFT, user/agent state timing and request durations are useful diagnostics, but are not stream-aligned first-heard-audio latency. Cumulative usage snapshots must not be summed, and no billing cost is inferred from missing usage.

Credential-shaped fields are redacted, payloads bounded, and event/snapshot files owner-only. Transcripts, arguments and account content can still be private. `runs/`, OAuth tokens and credentials are not release files.

See [architecture](ARCHITECTURE.md), [Workspace operations](PRODUCTIVITY.md), and [offline trace commands](BENCHMARK.md). Live voice interpretation, remote API variants, disconnect/restart exercises and physical-device behavior remain manual validation boundaries.
