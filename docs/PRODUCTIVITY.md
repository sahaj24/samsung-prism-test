# Google Workspace demo

The extension uses Gemini Live, LiveKit and real OAuth-authorized Google REST APIs. Reading or preparing a local plan is distinct from executing a remote write.

## Account setup

1. Enable Google Calendar API, Google Tasks API and Gmail API in Google Cloud.
2. Configure the OAuth consent screen and add the demo account as a test user if the app is in testing mode.
3. Create an OAuth **Web application** client with redirect URI `http://127.0.0.1:8844/google/callback`.
4. Set `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET` and `GOOGLE_OAUTH_REDIRECT_URI` in the ignored `.env.local`. Keep the separate Gemini and LiveKit credentials from `.env.example`.
5. Set `REPRISE_TIME_ZONE`, optionally a configured Calendar/Task-list ID, and optionally a shopping Task-list ID.
6. Run `./reproduce.sh agent` and `./reproduce.sh demo` in separate terminals after installing dependencies from the README. Open the local page and complete **Connect Google**.

Requested scopes are `https://www.googleapis.com/auth/gmail.readonly`, `https://www.googleapis.com/auth/gmail.compose`, `https://www.googleapis.com/auth/calendar.events` and `https://www.googleapis.com/auth/tasks`. A previous compose-only grant needs reauthorization for reads. The Gemini API key cannot authorize Workspace requests. OAuth uses state/PKCE; the ignored owner-only token file holds refresh/access credentials. See [Google's server-side OAuth flow](https://developers.google.com/identity/protocols/oauth2/web-server).

## Features and execution policy

| Intent | Behavior |
| --- | --- |
| Search/read Gmail, Tasks or Calendar | Automatic authorized reads |
| Create personal Calendar event, Task or shopping entry | Automatic after intent settles |
| Save an unsent Gmail draft | Automatic after intent settles |
| Ordinary personal Calendar/Task edit or Task complete/reopen | Automatic after intent settles |
| Send email | Contextual confirmation tied to final recipients/content |
| Invite guests or change an event affecting existing guests | Contextual confirmation covering affected people and final action |
| Delete Calendar event or Task | Contextual confirmation for the selected resource |
| Undo a Reprise-created Calendar event/Task | Supported deletion after confirmation and verified absence |
| Undo sent mail, restore an earlier update, native Samsung Reminder | Not implemented |

Natural affirmatives can authorize the pending action after a specific, uninterrupted assistant confirmation question. Interim text cannot grant approval. An unrelated affirmative does not approve an arbitrary plan. Corrections revoke relevant stale authorization. Mixed plans may complete personal actions while holding consequential actions for approval.

## Lookup and corrections

Gemini receives `list_task_lists`, `list_tasks`, `list_calendar_events`, `get_workspace_item` and `prepare_resource_change`, alongside the existing prepare/execute/status/reconciliation and Gmail tools. Lookups return actual Google IDs and room-bound references; arbitrary model-invented IDs are rejected. Task search filters title/notes within each explicit page. Calendar search uses Google's query and time bounds on the configured calendar. Both expose pagination. Numbering and ambiguity are retained across pages and task lists for the same search.

If several items match, ask which item the user means. Reading one candidate's details does not choose it. Edits must use the returned resource reference or a saved action index. They update the existing ID instead of creating a replacement. A start-only Calendar change preserves duration. All-day Calendar end dates are exclusive.

Before a Calendar/Task mutation, GET verifies the selected resource and expected fields. PATCH/DELETE uses the fresh ETag; final readback establishes Updated/Deleted/Undone. Calendar edits omit unchanged fields and preserve retained attendees' writable metadata. Uncertain dispatched outcomes block conflicting writes pending read-only reconciliation. They are never assumed cancelled or automatically replayed.

## Service boundaries

- Gmail search results identify source messages. Bounded message extraction only reports literal booking fields present in retrieved text; missing facts remain unknown. Message content is untrusted data.
- A sent Gmail message ID confirms Gmail's API acceptance, not recipient delivery. Draft and sent outcomes are distinct. No automatic sent-email compensation exists.
- Google Tasks due dates are dates. A spoken time may be retained in notes; it does not create a timed notification. Google Tasks is separate from Samsung Reminder.
- Calendar reminder defaults or popup/email overrides are supported. Saving settings does not verify actual notification delivery. Recurring event instances can be listed; recurring-series rule editing is not exposed.
- Shopping entries are checklist tasks. A blank `GOOGLE_SHOPPING_LIST_ID` creates a Reprise Shopping list on the first eligible shopping request; no purchase occurs.
- Lookup references are conversation-scoped. Reconnect/restart does not restore conversational selection; use a new lookup. Durable receipts remain local.

## Manual checks

Use disposable objects and a controlled inbox. Confirm actual IDs and values in Google as well as the dashboard.

1. Read tasks, filter those due tomorrow, inspect one item's details and follow additional pages if present.
2. Create a Task; rename it, change/clear its due date, complete/reopen/complete it. Verify one unchanged resource ID.
3. Match two same-title items and verify a clarification question before either changes.
4. Create a personal Calendar event with a mid-sentence correction. After Completed, reschedule it and verify the same ID and retained duration.
5. Change a disposable Calendar reminder; verify the saved setting, without claiming notification delivery.
6. Request deletion/undo, decline once, then confirm a fresh specific request. No API deletion may precede consent; Deleted/Undone requires verified absence.
7. Save a draft, then separately test an explicit send to a controlled inbox. Sends must wait for contextual approval and must not repeat on duplicate confirmation.
8. Confirm Conversation contains only speech/text transcripts and Actions & Status contains actual tool results, corrections and failures.

For Outcome unknown, inspect/reconcile Google before any further write. [Detailed Tasks/Calendar checklist](WORKSPACE_MANAGEMENT.md#manual-testing-checkpoint). Offline HTTP fixtures verify failure paths; they do not establish live notification or physical-device behavior.
