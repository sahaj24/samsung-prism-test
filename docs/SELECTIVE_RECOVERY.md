# Selective interruption recovery

Checkpoint: 20 October 2026. Implementation is ready for manual voice validation. No new FDB score, model-quality result or live latency improvement is claimed.

## Existing behavior retained

The Coordinator already separated acoustic speech state from committed transcripts, settled proposals before dispatch, deduplicated equivalent calls and preserved dispatched operations when assistant playback was interrupted. The Workspace ledger already owned confirmation, leases, actual Google IDs, uncertain outcomes and supported Calendar/Tasks update/delete flows. Those mechanisms remain in place; there is no replacement planner, second model or new UI.

Risk policy is unchanged: reads and settled personal reversible writes are automatic; sends, invitations, deletions and consequential edits need contextual confirmation. A correction revokes old approval. An API request already dispatched is never retroactively labelled cancelled or automatically retried. Supported post-dispatch corrections inspect the actual receipt, reconcile uncertainty and update the existing resource ID. Sent email has no automatic inverse.

## Changes

| Component | Behavior |
|---|---|
| `intent.py` | Scopes corrections using explicit entity/field evidence or a unique last-clause referent. Uses the replacement tail of an extended transcript. Global abandonment discards the goal. Unresolved alternatives cannot authorize dispatch; a comparison in one clause cannot exempt an unrelated ambiguous clause. Arguments are not guessed or rewritten by this scoping logic. |
| `coordinator.py` | Preserves unaffected reads, including in-flight reads. Shares actual outcomes with equivalent proposals and reuses eligible successful session snapshots. Captures exact typed ID/ref dependencies; superseded sources invalidate dependent proposals and responses. Fresh reads can use a known locator to refresh expired data; writes cannot use an expired snapshot as authority. |
| `catalog.py` | Adds internal scope and opt-in snapshot lifetime metadata. Public tool declarations are unchanged. FDB tools retain their existing cache settings and do not opt into cross-transaction snapshot reuse. |
| `productivity.py` | Applies corrections to affected actions instead of the whole mixed plan. Retains actual in-flight receipts. Transfers overlapping pending actions to a replacement plan in one SQLite transaction, revokes old consent, and preserves unrelated pending work. Matching already-dispatched actions in active plans cannot be transferred into duplicate creations. Remaining risky actions stay approvable after fresh consent, including when the old plan had previously been approved. |

Gmail search/details, Tasks lists and Calendar lists opt into **15 seconds** of session snapshot reuse. This is a bounded local snapshot, not proof that Google has not changed remotely. Planning context, saved-plan inspection, reconciliation and item detail/selection tools remain fresh reads. Reuse is scoped to the same room and compatible request/follow-up. Explicit refresh and relevant write attempts invalidate snapshots. A Tasks/Calendar search must still own the current result group and numbering; older results cannot silently acquire a newer selection. Invalidated in-flight reads retain their real API outcome in the ledger, but cannot repopulate current results or deliver an outdated answer.

No interim transcript, acoustic silence, timeout or assistant speech interruption grants permission to execute incomplete intent. Stable final clauses may start useful reads while other operations continue. Every actual dispatch remains visible in normal traces and benchmark tool logs; speculative work is not hidden from scoring.

## Deterministic comparison

`tests/test_recovery_comparison.py` supplies ordered transcript/proposal events and a virtual backend clock. It compares waiting until the last correction, the existing eager/baseline policy, and guarded selective recovery. The three scripted scenarios cover an in-flight read with a time correction, successive corrections with an unrelated parallel task, and undecided alternatives.

| Strategy | Correct scenarios | Wasted calls | Stale writes | Duplicate creations | Reused read proposals |
|---|---:|---:|---:|---:|---:|
| Wait for complete request | 3/3 | 0 | 0 | 0 | 0 |
| Eager baseline | 0/3 | 8 | 3 | 3 | 0 |
| Selective recovery | 3/3 | 0 | 0 | 0 | 3 |

Wasted calls count excess identical reads plus writes containing superseded arguments. Duplicate creations count excess event creations. Stale writes and duplicates can describe the same call and must not be added together. Reuse counts shared proposals, including waiters for an in-flight outcome, not three independent cached snapshots.

Time to all correct required outcomes was **5 → 4** scripted seconds for the first scenario and **6 → 5** for the second when comparing waiting with selective recovery. Incorrect eager runs have no time-to-correct-completion value. The undecided scenario requires no API calls. These numbers follow the fixture's chosen delays; they are **not measured Gemini, Google, speech or user-perceived latency**, and are not a representative success-rate estimate.

Raw calls, events and metrics for the final source are preserved locally under `runs/selective-recovery-20261020/verified-comparison/` and `verified-comparison.json`. Earlier attempts and pre-change source snapshots remain alongside them.

## Verification and limits

Focused tests cover interrupted in-flight reads, pause/finality safety, successive and elliptical corrections, full-transcript extensions, identifier scope, unknown/expired dependency authority, explicit refresh, write invalidation, result-group ownership, unrelated actions, mixed-risk consent, replacement ownership and actual HTTP-boundary receipts. Existing update/delete, preflight conflict, failed undo, uncertain outcome, cancellation, duplicate and provider-event regressions run offline with mocked Google HTTP boundaries.

```sh
cd /home/naithick/Prism/samsung-prism-test
.venv/bin/pytest -q tests/test_selective_recovery.py tests/test_recovery_comparison.py
.venv/bin/pytest -q
.venv/bin/ruff check --isolated --select E4,E7,E9,F --ignore E741 \
  src/reprise/catalog.py src/reprise/coordinator.py src/reprise/intent.py \
  src/reprise/productivity.py tests/test_selective_recovery.py tests/test_recovery_comparison.py
git diff --check
```

The lint command checks syntax/name errors while preserving the existing schema constant `I`. Broader style-policy findings are recorded separately in the local report. No cloud/model calls, real Google writes, paid benchmark or phone checks were performed for this change. Gemini/Gemma tool declarations match the saved pre-change declarations; the experimental planner, its comparison script and the two-panel frontend were not edited.

Scoping is intentionally lexical and conservative. It cannot prove arbitrary semantic relationships, resolve every pronoun, or infer that a date copied from message text depends on that message. Dependency tracking covers explicit returned IDs/refs and existing declared references, not all model reasoning. Arbitrarily different descriptions of the same intended creation still require correct model planning; this is not a global exactly-once guarantee. Speech recognition, Gemini's chosen arguments, supported update/undo selection, actual remote latency and phone synchronization need live verification. Existing instructions require grounded completion claims, but there is no semantic validator for every word Gemini speaks.

## Manual voice checkpoint

End the current conversation before restarting the existing worker so new sessions load the changed code. Restart the agent process with `./reproduce.sh agent`; restart the dashboard with `./reproduce.sh demo` if its process predates these changes. Keep Gemini Live selected, Google connected, and both connection labels ready. The experimental llama.cpp process need not be restarted. Use uniquely labelled disposable items and inspect actual Google IDs in Actions & Status.

1. **Selective correction:** ask to find an existing flight email and schedule “Recovery test coffee” tomorrow at five, then say “actually coffee at six.” If correction precedes dispatch, only six should be created; the valid mail query should not repeat within the snapshot window. Empty search results are valid results, not flight facts.
2. **Pause and post-dispatch correction:** pause naturally, then correct six to seven. If creation already completed, expect an update of the same Google event ID and preserved duration, not a second event. If still running/unknown, expect inspection/reconciliation before a further write.
3. **Assistant interruption:** speak while the assistant describes a result. Playback interruption alone must not discard the valid read or committed action. Then change one named constraint; only affected pending work should be superseded.
4. **Several corrections and parallel work:** request coffee and a separate task, then change coffee twice. The independent task should complete once. Check pending, dispatch and receipt history rather than relying only on the assistant's spoken summary.
5. **Ambiguity:** say “Schedule coffee at five or six, I haven't decided.” Expect clarification without an event write. Also try an explicit comparison of two messages followed by the undecided coffee request; the comparison must not authorize the event.
6. **Mixed risk:** request a personal task plus sending a specifically addressed test email. The task can finish automatically; the email must wait for an action-specific question and your affirmative. Correct the task before answering; old approval must not authorize a changed send. Preparing a complete revised plan must not leave two executable copies of the unchanged email.
7. **Refresh, abandonment and undo:** explicitly ask for a fresh lookup; expect a real new read. Cancel the entire goal while work is pending; unrelated abandoned pending mutations must not execute. For a confirmed disposable Calendar/Task creation, request undo, confirm its specific deletion and verify absence remotely. Failed undo must retain the original receipt.

Conversation remains transcript-only; Actions & Status remains actual tool activity. Save the room trace and note the Google IDs, transcript timing and any mismatches. Stop at this checkpoint before Phase 5; no commit or push is part of this work.
