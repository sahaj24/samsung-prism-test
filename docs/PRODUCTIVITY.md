# Spoken request → Google Workspace plan

This extension uses the existing real Gemini Live voice session and real Google REST APIs. There is no simulated execution mode. A plan preview writes only to the local ledger; after confirmation, Calendar, Tasks and Gmail calls use the connected Google account.

## Connect the account

1. In a Google Cloud project, enable **Google Calendar API**, **Google Tasks API** and **Gmail API**.
2. Configure the OAuth consent screen. For a project in testing, add your own Google account as a test user.
3. Create an OAuth **Web application** client. Add this exact authorized redirect URI:

   `http://127.0.0.1:8844/google/callback`

4. Fill these placeholders in your ignored `.env.local`:

   ```dotenv
   # Keep the existing Gemini and LiveKit credentials.
   GOOGLE_OAUTH_CLIENT_ID=YOUR_GOOGLE_OAUTH_CLIENT_ID
   GOOGLE_OAUTH_CLIENT_SECRET=YOUR_GOOGLE_OAUTH_CLIENT_SECRET
   GOOGLE_OAUTH_REDIRECT_URI=http://127.0.0.1:8844/google/callback
   GOOGLE_CALENDAR_ID=primary
   GOOGLE_TASK_LIST_ID=@default
   GOOGLE_SHOPPING_LIST_ID=
   REPRISE_TIME_ZONE=Asia/Kolkata
   ```

   Google OAuth credentials authorize access to your account. `GOOGLE_API_KEY` continues to be the Gemini model key; it cannot authorize Calendar, Tasks or Gmail.

5. Start the voice worker and dashboard in separate terminals:

   ```sh
   ./reproduce.sh agent
   ```

   ```sh
   npm ci --prefix demo
   ./reproduce.sh demo
   ```

6. Open [the local dashboard](http://127.0.0.1:8844), choose **Plan my day · Google Workspace**, press **Connect Google**, and approve the Google consent screen. Refresh the dashboard if needed. The private OAuth tokens are saved in `.google-workspace-token.json` with owner-only permissions; that file is ignored by Git.

The OAuth scopes are `calendar.events`, `tasks`, and `gmail.compose`. The last scope supports drafts and sending. The backend refreshes access tokens using the stored refresh token. [Google's OAuth documentation](https://developers.google.com/identity/protocols/oauth2/web-server) explains the authorization flow.

## Speak the test request

Replace the recipient placeholder with a real email address you control. Then say, naturally:

> Tomorrow, block four in the afternoon for working on my presentation—actually, make it five, for thirty minutes. Add a task to review the slides tomorrow, and put “finish by noon” in the notes. Draft an email to **YOUR REAL EMAIL ADDRESS** saying the presentation meeting is at five. And add two cartons of milk and one loaf of bread to my shopping list. Wait, make that three cartons of milk.

The assistant should resolve tomorrow using the configured timezone, retain the final **5:00–5:30** event and **three cartons** quantity, and show one complete plan. Review the recipient and content. Say **“confirm the plan”**, or press **Confirm plan**.

The resulting actions are a Calendar event, a task, a Gmail draft, and two shopping entries. Check their Google IDs in the plan panel and open your actual Google apps. No emails are sent in this draft example.

To test real sending, explicitly say **“send an email”** with the recipient, subject/message content, review the plan marked **Send email**, then confirm it. A message only counts as sent when Gmail returns its message ID.

## What each action does

| Spoken intent | Live API operation | Evidence |
| --- | --- | --- |
| Schedule a block | Calendar `events.insert` | Event ID and Google Calendar link |
| Add a task | Tasks `tasks.insert` | Task ID |
| Draft an email | Gmail `drafts.create` with a MIME message | Draft ID |
| Send an email | Gmail `messages.send` with a MIME message | Message ID and `sent` status |
| Add shopping items | Tasks `tasks.insert` in the shopping list | Task ID for each item |

A blank `GOOGLE_SHOPPING_LIST_ID` creates a **Reprise Shopping** task list on the first confirmed shopping action. You can supply an existing list ID instead. Shopping here means a shopping checklist; it does not purchase items.

Google Tasks stores a due **date**, not a time. Spoken time constraints are kept in task notes. Calendar times require a start and end; the assistant asks for missing timing details. Recipient addresses are never inferred from names. See the [Tasks resource documentation](https://developers.google.com/workspace/tasks/reference/rest/v1/tasks), [Calendar events](https://developers.google.com/workspace/calendar/api/v3/reference/events), and [Gmail draft guide](https://developers.google.com/workspace/gmail/api/guides/drafts).

## Execution and correction behavior

A corrected unexecuted plan replaces the previous preview and requires fresh confirmation. The local SQLite ledger is scoped to the voice room and persists action results across repeated tool calls. Only one executor can claim an approved plan. Repeated execution of that plan returns its saved results.

A rejected Google request is shown as failed. A dropped response or interrupted write is shown as **unknown**; it is not automatically retried. Some actions may already have succeeded, so a partial plan lists each outcome. There is no transaction that can atomically commit all three Google services together. Changes after execution should be treated as a new request; this first version creates actions and does not edit or delete already-created Google resources.

No Google action reports completion without a returned ID. Missing OAuth credentials or an unconnected account produces an error and leaves the preview unexecuted. Runtime receipts and plan state are stored locally under ignored `runs/`.

## Verification so far

The real Gemini Live connection accepted all four planning tools. Local tests cover Google request payloads, OAuth refresh, MIME encoding, plan approval, concurrent duplicate execution, corrections, unknown outcomes, and missing credentials. Those tests intercept HTTP requests and do not write to a real account. The participant's Google OAuth credentials are still placeholders, so live Google writes must be tested after account connection.

The earlier **77/100** benchmark belongs to the frozen benchmark agent in commit `0672c5d` and its archived submission ZIP. No new benchmark score is claimed for this Google productivity extension.
