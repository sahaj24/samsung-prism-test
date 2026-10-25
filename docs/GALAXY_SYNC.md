# Phase 4: Galaxy account synchronization

Reprise writes to Google Calendar, Gmail and Google Tasks through its existing OAuth connection. Galaxy apps display those services through the same Google account. This is account synchronization, not a native Samsung/Bixby integration. A Google API receipt confirms the server operation; it does **not** confirm that the phone has synced. Phone verification is pending until observed manually.

## Set up the phone

1. On Galaxy, open **Settings → Accounts and backup → Manage accounts**. Add the same Google account authorized by **Connect Google** in Reprise. With multiple accounts, check the exact account in each app.
2. Select that Google account → **Sync account** → enable **Calendar**. Open **Samsung Calendar → Menu → Manage calendars**, enable the matching Google calendar, and use **Sync now** if available. Menu names vary by One UI version. The event must be on the Google calendar, not “My phone” or a Samsung-account calendar. Reprise's `GOOGLE_CALENDAR_ID` defaults to `primary`; if configured differently, enable that specific calendar.
3. Open **Gmail**, select the same account, and enable **Settings → [account] → Sync Gmail**. Drafts appear under **Drafts**, sent mail under **Sent**. An API send receipt is not proof of inbox delivery.
4. Install/open **Google Tasks**, select the same account and correct task list. Reprise's `GOOGLE_TASK_LIST_ID` defaults to `@default`. Completed tasks may be collapsed under **Completed**. Google Tasks is separate from Samsung Reminder; no automatic Reminder sync or timed task notifications are claimed.
5. Keep the phone online. If an item is absent, refresh the app and check account/calendar/list selection before retrying Reprise. Do not repeat a write simply because the phone has not refreshed.

Sources: [Samsung Google Calendar sync](https://www.samsung.com/in/support/mobile-devices/how-to-sync-your-google-calendar-on-your-samsung-galaxy-device/), [Google Tasks across devices](https://support.google.com/tasks/answer/7675772), [Gmail Android sync settings](https://support.google.com/mail/answer/4780745).

## Start Reprise

From the repository root:

```bash
uv sync --frozen
npm ci --prefix demo
./reproduce.sh agent
# In a second terminal:
./reproduce.sh demo
```

Open http://127.0.0.1:8844. Connect Google if needed. **Gemini Live** remains the default model; it uses microphone and speaker. Conversation contains transcripts; Actions & Status contains real proposals, operations, receipts and errors.

For the optional text-only local model, follow [LOCAL_PLANNER.md](LOCAL_PLANNER.md). Both model choices use the same controller, OAuth account and Google tool adapters. A local model is not Bixby and this path is not full-duplex speech.

## Manual acceptance checkpoint (not yet verified on a phone)

Use only disposable items with a unique label, for example `Reprise P4 <date>-<time>`. Start with one model and finish its case before testing the other. Use different labels between models to avoid conflating legitimate second requests with duplicate execution. Record the resource ID, before/after values, phone app/account, observed time and any delay; do not record credentials.

| Check | Reprise action | Expected phone observation |
|---|---|---|
| Calendar creation | Ask for a personal event with explicit date, start and duration, no guests | One event on the selected Google calendar in Samsung Calendar |
| Post-dispatch correction | After Completed, say “Move that event one hour later” | Same Google event ID, changed time, one phone event |
| Pre-dispatch correction | Submit a correction immediately while Processing | Superseded old proposal if still pending; only final values appear. If already dispatched, retain its receipt and update the existing ID |
| Calendar deletion | Ask to delete that disposable event; confirm the identified deletion | Verified delete/Undone in Reprise; event disappears after phone sync |
| Draft | Ask for an **unsent draft** with an explicit recipient, subject and body | One draft in Gmail Drafts, no sent message |
| Send | Ask to send a disposable test email to your own address; review and confirm recipients/content | Sent entry; check inbox separately. No automatic sent-email undo |
| Task creation | Add a uniquely named task with a date | One task in the configured Google Tasks list |
| Task editing | Rename it/change date, then mark complete and incomplete | Same task ID; changes visible, including under Completed |
| Task deletion | Ask to delete the disposable task and confirm | Verified delete in Reprise; task disappears after refresh |
| Ambiguity | Ask to change a name shared by multiple items | Clarification before any write; select the numbered lookup result |
| Interruption | Interrupt Gemini's speech while an operation is running | Assistant playback stops; actual dispatch/result remains recorded. Correction updates by ID rather than blindly recreating |

No new objects were created automatically for this checkpoint. Earlier authorized test objects are preserved; do not delete them as part of cleanup without checking their identity. If an operation is **Outcome unknown**, do not retry: use remote reconciliation first. If undo fails, the original creation receipt remains valid.

Stop before Phase 5. Acceptance requires your phone observations; offline tests and desktop receipts cannot substitute for them.
