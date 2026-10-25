# Call listening and Calendar test transport

The presentation selector offers **Live**, **Demo**, and **Demo Live**. Live uses the real agent without a wake requirement. Demo is isolated scripted playback. Demo Live uses the same Gemini/LiveKit session with a deterministic listening gate, an empty phone-sized recording area, actual transcripts and actual controller events.

Start with `./start-reprise.sh`, open `http://127.0.0.1:8844/`, select Demo Live, and start a conversation. The worker must acknowledge the activation protocol before microphone publication. Unknown demo room metadata defaults to standby, and outdated servers/workers fail the handshake rather than silently running unrestricted.

## Activation and authority

- Standby continues transcription but blocks assistant audio/transcript output and all tool execution at the Coordinator boundary.
- A final addressed request beginning with Reprise/Hey Reprise activates assistance. Active follow-ups and corrections use the existing Coordinator without another wake phrase.
- Addressed thanks/that's all, or standalone final ok/okay/done, return to standby. Generic thanks does not. An interim acknowledgement prefix cannot dismiss a longer corrected instruction.
- Pending work is invalidated on dismissal. Dispatched outcomes remain recorded; no automatic compensating Google mutation is inferred.
- Switching Live/Demo Live negotiates the backend gate while preserving the room and history. Wake phrases do not identify speakers or grant consequential-action consent.

Gemini emits final input transcription at turn completion in the installed plugin. The standby generation is suppressed; activation schedules one response after the addressed request is committed. Proactive audio is not used as an authorization mechanism. Live microphone acceptance of the latest fix remains pending; offline boundary tests are not a substitute.

## Optional Calendar-only backend

Settings → Tools → **Calendar · Call Assist** selects the existing scoped Calendar backend. This is separate from presentation mode. It supports one Calendar event per plan and retains stricter local-owner confirmation because shared-microphone speech does not establish owner authority.

Default `REPRISE_CALL_ASSIST_BACKEND=mock` uses the real Google serialization/authorization code with `httpx.MockTransport`; it cannot send network requests. Resources and HTTP receipts persist in `runs/<room>/mock-calendar.sqlite3`. PlanStore remains room-scoped in `runs/productivity.sqlite3`.

Real Calendar is opt-in: set both `REPRISE_CALL_ASSIST_BACKEND=google` and `REPRISE_CALL_ASSIST_GOOGLE_WRITES=1`, then restart the worker. Existing OAuth is used. Ordinary Google Workspace mode retains its normal risk-based policy; it does not acquire extra owner-confirmation clicks from the listening gate.

## Manual checkpoint

1. In standby, say “Hey Karthick, what plans tomorrow?” Confirm a transcript, zero dispatches and zero assistant speech.
2. Say “Hey Reprise, what plans tomorrow?” Confirm activation and a receipt-grounded read response.
3. Correct an active request; inspect supersession and final arguments in the real trace.
4. Say “Thanks, Reprise,” standalone “ok,” or “done”; confirm standby. Ordinary thanks should leave the active state unchanged.
5. For the scoped mock Calendar flow, request a dated 5–5:30 PM event, correct it to 6–6:30 PM before owner confirmation, approve only the replacement, and inspect the persisted receipt for one 6 PM creation and no 5 PM resource.

The earlier microphone connection was observed, but the complete corrected-write sequence was not completed. No end-to-end success is claimed for that attempt. Private traces and development snapshots remain local under `runs/`.
