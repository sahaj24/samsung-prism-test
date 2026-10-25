# Task execution repair checkpoint

20 October 2026. Gemini Live remains the default; transport, Google adapters, controller architecture and the two-panel UI are retained. No live Google data was modified while implementing/testing these fixes. The duplicate Exercise tasks from the incident remain untouched.

## Confirmed incident and fixes

Source evidence: `runs/reprise-demo-529a1dbcd38e47/events.jsonl`.

| Evidence | Cause | Current handling |
|---|---|---|
| Events 311/336 created a new task under a plan named Update; the assistant then claimed an update | Plan title was mistaken for operation semantics; creation was allowed during an existing-task edit | Backend retains explicit edit intent through clarification and rejects replacement creations at preparation and at the final write boundary. Existing tasks must be looked up/selected and changed with `prepare_resource_change` → `execute_plan`, using their actual ID. Explicit new creations remain supported, including mixed edit/create requests with distinct new-item phrases. |
| 116 `list_tasks` invalid-prerequisite rejections | The list ID matched a nested field in a superseded saved plan, creating an incidental dependency | A configured/listed task-list ID is a validated read locator, not authority inherited from that plan. Invented list IDs still fail before Google access. |
| Three detail-read and one edit-preparation stale-prerequisite rejection | Previously invalidated source references | Stale-source checks remain; recovery must obtain current lookup evidence. Repeated errors cannot loop indefinitely. |
| 17 undecided-alternative rejections on delegated warm-up wording | “Warm up or something like that” was interpreted as an unresolved action alternative | Filler alternatives in free-form task title/notes/body wording are distinguished from structural alternatives. Actual unresolved action/time choices retain their existing checks. The incident's new-task proposal would still be rejected when it should edit the existing task. |
| Repeated attempts to delete ambiguous tasks | No valid user selection and no completed authorized deletion | Numbered selection is bound to the actual item ID before a model refresh can reorder results. Duplicate names still require selection; dates containing ordinals do not select an item. After three failed attempts for an input, further calls pause and the Live adapter requests one clarification with tools disabled. New substantive input permits recovery. |
| “Updated” after creation; unsupported deletion-progress promises | Spoken interpretation was not checked against actual operation receipts | Tool results include per-action operation, status, confirmed resource ID and authoritative summary. Explicit completion claims are checked against matching resource/kind receipts; generic claims require current-input evidence. Completed named work remains available when another action awaits consent. Unsupported claims are logged and trigger a bounded corrective response without executing tools. |

No failed/unknown write is automatically repeated. Deletion still requires action-specific authorization; preparing deletion is not deleting. Existing Calendar/Task preflight GET, ETag-bound PATCH/DELETE, readback and uncertainty locking remain in place. The actual task due date survives a notes/time correction. A Google Tasks time in notes is text, not a timed notification.

## Verification

`tests/test_task_execution_recovery.py` adds 22 offline incident/edge cases. It checks actual HTTP methods, returned IDs, final state, consent, failed deletion, ambiguous/numbered selection, date ordinals, mixed creation/edit requests, title overlap, stale locator recovery, delegated wording, error budgets, tool-free clarification, truthful receipt summaries and correction wiring. Existing relevant regressions also pass.

Final full offline run: **303 passed in 11.66 seconds**. Focused Ruff syntax/name checks and `git diff --check` pass. Evidence and starting source copies are in `runs/task-recovery-20261020/`; no paid benchmark, real model evaluation or actual Google mutations were performed. Experimental planner/script and frontend checksums match their starting state.

```sh
cd /home/naithick/Prism/samsung-prism-test
.venv/bin/pytest -q tests/test_task_execution_recovery.py
.venv/bin/pytest -q
```

## Manual verification

Use disposable items with unique names, leaving the existing Exercise duplicates alone. End any previous conversation before loading the updated worker.

1. Create “Task repair check” for tomorrow with `7:00 AM` in notes. Record its Google ID. Ask “Move my Task repair check task from seven to six.” Expect a lookup and **PATCH of the same ID**, unchanged due date and no replacement POST. Check the task in Google Tasks.
2. Create two disposable tasks with the same title. Request an edit without selecting one: expect clarification and no write. Select the second result. Even if the model refreshes the list, the chosen actual ID must remain the target. “November second” must not select item #2.
3. Request an existing-task edit plus a distinct new task in one turn. Only the explicit new item may be created. Also try a new-item name containing the existing task's title; that substring must not allow a replacement creation.
4. Request deletion of one selected disposable item. Before confirmation, expect a prepared/pending deletion. Confirm the specific question; verify the DELETE receipt and remote absence. Decline another deletion and verify that item remains. The offline suite exercises API denial and unknown outcomes without changing your real account permissions.
5. Interrupt the assistant, correct one constraint and inspect the operation history. Already completed work must stay recorded; stale dependent work must not dispatch. Recovery must not repeat writes.
6. Compare speech with Actions & Status. A creation must be reported as creation, a pending/failed deletion as unexecuted/failed, and an unresolved target as needing clarification. Unsupported recognized claims should remain in the actual transcript and be followed by a correction, not disappear from history. Do not infer duplicate causes from dates alone.

## Remaining live boundary

Native Gemini audio may be played before its final transcript is available. The response checker can interrupt remaining playback and request a correction **after detection**; it cannot retract speech already heard. It checks explicit supported operation claims, not every paraphrase, argument, language or causal explanation. Duplicate-cause explanations are also constrained in the model instructions. This is not a universal semantic truth guarantee, and the offline tests do not establish actual spoken wording or audio alignment. Verify these boundaries in a live session and preserve its room trace if anything differs.

Known ambiguous mixed requests are handled conservatively. While an existing-resource edit is active, a new-item exception must match the explicit complete new-item phrase; uncertain paraphrases may require clarification. No task/event is silently substituted to avoid that question.

No commit, push or Phase 5 work is part of this checkpoint.
