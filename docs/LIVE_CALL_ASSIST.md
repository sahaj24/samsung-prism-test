# Live Call Assist — current handoff

The polished demo now has three separate choices: existing **Live**, unchanged
scripted **Demo**, and **Live Call Assist**. This addition is limited to one
Calendar event per plan. Gemini Live remains the voice backend.

## Run

```bash
cd /home/naithick/Prism/samsung-prism-test
./start-reprise.sh
```

Open `http://127.0.0.1:8844/`, select **Live Call Assist**, then Start conversation.
The existing LiveKit audio/transcription handlers, Gemini Live, independent Silero
microphone monitor, Coordinator, ProductivityBackend and PlanStore remain in use.
Live phone previews derive from `plan_updated`; completion cards require the
action's actual completed status and matching receipt/resource ID. The execution
strip and conversation receive real backend/SDK events, never scripted frames.

## Safety and receipts

- Default: `REPRISE_CALL_ASSIST_BACKEND=mock`. The existing GoogleWorkspace HTTP
  adapter uses `httpx.MockTransport`, the mechanism already used by integration
  tests. This client cannot make network requests. Calendar serialization,
  dispatch authorization, error handling and trace emission still run normally.
- Per-room mock resources and HTTP receipts persist in
  `runs/<room>/mock-calendar.sqlite3` (`events` and `requests` tables). The normal
  PlanStore remains `runs/productivity.sqlite3`, scoped to the room.
- All Call Assist mutations require **Owner confirm** on the current plan revision.
  Spoken names/affirmatives on a shared microphone are not owner authorization.
  Corrections invalidate stale pending plans through the existing controller and
  store; an old approval cannot authorize the replacement revision.
- The same event editor shows the current pending action. Confirmation is hidden
  during execution; an unsaved/superseded plan is never displayed as completed.
  Already dispatched outcomes are handled by the existing receipt/undo pipeline.
- Real Calendar is separately opt-in: both
  `REPRISE_CALL_ASSIST_BACKEND=google` and
  `REPRISE_CALL_ASSIST_GOOGLE_WRITES=1` are required before restarting the worker.
  Existing OAuth is then used. Do not enable these for a mock-only demonstration.
- Scripted preview data, playback timings, recordings and the legacy Google
  Workspace mode are preserved. No new model or framework was added.

## Implementation files

- `src/reprise/call_assist.py`: scoped tools/prompt, owner-only plan preparation,
  persistent mock Calendar transport using the existing Google adapter.
- `src/reprise/agent.py`: session metadata selects this scoped productivity wiring.
- `src/reprise/productivity.py`: owner-required action flag and atomic, revision-bound
  dashboard approval; ordinary existing policies retain their defaults.
- `src/reprise/demo_server.py`: Call Assist session configuration and approval body.
- `demo/app.js`, `demo/phone.js`, `demo/index.html`: live event projection, native
  microphone/audio/end controls, current-plan confirmation and separate mode.
- `.env.example`: the two opt-in backend settings. Actual credential files unchanged.

## Verification state — do not overclaim

A genuine browser microphone session was started in room
`reprise-demo-a73ede6cfbf347`. The trace recorded the existing Gemini 3.8 Live
model, Call Assist/mock mode, agent readiness and an actual Gemini connection
handshake. Browser microphone publication and the LiveKit connection completed.
No prerecorded/TTS input, authored tool events, test suite or benchmark was used.

The user requested that testing stop before completing the spoken correction and
owner-confirmed write sequence. The microphone session was disconnected and the
page reloaded. **The complete 5 PM → 6 PM microphone scenario is not verified.**
Do not claim an E2E pass or a successful Calendar write from this attempt.

Evidence: `runs/reprise-demo-a73ede6cfbf347/events.jsonl` and the launcher logs.
Backup of the preceding working files: `runs/live-call-assist-development/before-source.tar`.

If explicitly requested later, the remaining human verification is: request a
dated 5–5:30 PM event, correct it to 6–6:30 PM before approving, confirm only that
replacement in the UI, and inspect the real trace/database for one successful
creation and no 5 PM resource. This is pending, not an automated/faked result.

No commit, push, new benchmark or Phase 5 work was performed.
