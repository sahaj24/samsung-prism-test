# Tasks and Calendar management

Gemini Live receives lookup and resource-change tools alongside the existing plan, execution and Gmail tools. These manage Google Workspace data, not native Samsung Reminder.

## Tools

| Tool | Purpose |
| --- | --- |
| `list_task_lists(page_token?)` | Discover actual Google Task-list IDs |
| `list_tasks(task_list_id?, query?, due_from?, due_before?, task_status?, page_token?)` | List/search a configured or discovered list; dates use an inclusive start and exclusive end |
| `list_calendar_events(query?, time_min?, time_max?, page_token?)` | Search the configured Calendar using native query/time filters |
| `get_workspace_item(resource_ref)` | Read current details using a lookup-bound reference |
| `prepare_resource_change(resource_ref, operation, changes?)` | Stage an update/delete for the selected existing resource |
| `prepare_plan(...)` | Stage new Calendar events, Tasks, drafts/sends or shopping entries |
| `prepare_change(plan_id, action_index, operation, changes?)` | Correct/undo a saved Calendar/Task action; zero-based index |
| `execute_plan(plan_id)` | Execute eligible settled actions and hold consequential ones for confirmation |
| `get_plan(plan_id)`, `reconcile_plan(plan_id)` | Read local evidence or perform supported remote outcome verification |

Task edits support title, notes, due date and `task_status=completed|needsAction`. Empty `due_date` clears the date. Calendar edits support title, notes, location, start/end, attendees and reminders. Timed values require offsets matching the configured timezone; all-day values use dates with exclusive end. A start-only reschedule retains duration. Changing between all-day and timed needs explicit dates/times.

Calendar reminder settings use `useDefault=true`, or `useDefault=false` with up to five `popup`/`email` overrides, each 0–40320 minutes before the event. An empty override list disables custom reminders. Saved settings are not evidence that a notification was delivered. Task due dates do not implement time-of-day alerts.

## Identity and selection

Returned resources include their actual Google ID, container and room-bound `resource_ref`. The model cannot supply a guessed ID for mutation. Search groups preserve matches and numbering across pages/lists of the same search; `pending_pages` identifies outstanding container/page tokens. Details reads cannot manufacture selection of an ambiguous match. A final user selection, an unambiguous title, or an explicit unique ID is required when multiple items match.

Tasks search is a substring filter on each explicit page; it is not a global search index. Follow pages before claiming exhaustive results. Calendar search covers the configured calendar and expands recurring instances; recurring-series rule editing and discovery of all calendars are not exposed.

## Execution boundaries

Reads are automatic. Personal creation and ordinary reversible edits execute after settlement. Deletions, sends, invitations/changes affecting guests and flagged consequential resources require contextual confirmation. Updates use existing Google IDs. Preflight field checks, ETags, resource locks and post-write reads prevent silent replacement or completion claims without evidence. Unchanged Calendar fields are omitted from PATCH; retained guests preserve their writable metadata.

The `@default` Task-list alias resolves to Google's actual returned list ID; uncertainty locks also check persisted legacy alias owners. Repeated completion cycles use the current verified resource revision. A known unknown outcome cannot be bypassed by selecting the same task through another alias.

Deleting a looked-up resource reports Deleted after verified absence. Undo of a Reprise-created event/task reports Undone. Sent-mail reversal and restoration of an older update are not implemented. Unknown writes are not automatically replayed; use supported read-only reconciliation and check Google.

## Manual testing checkpoint

Refresh http://127.0.0.1:8844/ before starting a new conversation. Wait for LiveKit and Gemini connected labels. Use disposable Google data for writes.

1. Say “Read my tasks”, then “What's due tomorrow?” Compare titles, due dates and completion state with Google. Follow additional pages/lists when indicated.
2. Read a task's details. Mark it complete, incomplete, then complete again. Verify the same Google ID and no replacement task.
3. Create a disposable task. Rename it, change its due date, then clear the date. Confirm the real Google values.
4. Create two same-title disposable tasks. Ask to edit that title. The assistant must ask which item; choose the second. Only that ID may change. Repeat with matches spanning pages/lists where practical.
5. List events for a specific day and read one. Say “Move that event to six PM”. Verify its date, duration and ID. Guest-bearing changes must wait for consent.
6. Save a popup reminder on a disposable event. Verify the setting in Calendar. Also verify all-day date handling; do not infer notification delivery.
7. Correct a request before dispatch, then separately after Completed. Check stale suppression before dispatch and an update of the same ID afterward.
8. Request deletion, decline once, then request it again and approve its specific question. Deleted/Undone must follow verified absence; unrelated objects must remain intact.
9. Verify Conversation contains user/assistant text only and Actions & Status contains readable retrieved items and actual operations. Check long text and mobile stacking.

Offline regression tests cover payloads, ambiguity, selection, corrections, consent, uncertain outcomes, duplicate protection and dashboard events using HTTP fixtures. Live voice interpretation, restricted/assigned resource behavior, notification delivery and physical-device behavior remain manual validation boundaries. A new conversation should look up existing resources again; its selection context is not restored from another room.

See [setup and policy](PRODUCTIVITY.md), [execution safety](HARDENING.md), and the preserved [roadmap](ROADMAP.md).
