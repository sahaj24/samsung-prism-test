# Remaining demo roadmap

The working demo retains Gemini Live, LiveKit, the two-panel UI and existing Google Workspace APIs. Phone synchronization remains a manual acceptance checkpoint; API receipts do not prove device visibility.

## Phase 4 — synced Galaxy apps and optional local planner (in progress)

- Samsung Calendar displays the configured Google Calendar through the same Google account.
- Gmail drafts/messages are displayed in the Gmail app; Tasks changes in Google Tasks.
- No Google Tasks → Samsung Reminder synchronization is assumed.
- Optional Gemma 4 E2B QAT/llama.cpp planner is text-only and proposes existing Google tools through the existing controller. Gemini Live stays the default voice backend.
- [Galaxy setup and manual checks](GALAXY_SYNC.md); [local planner setup and limits](LOCAL_PLANNER.md).
- Verify actual create/update/delete synchronization on the phone, consent, corrections, resource identity and duplicate prevention before acceptance.
- Kotlin companion, ADB bridge, Clock alarms/timers, Calendar Provider, battery and other phone-specific infrastructure are explicitly out of scope for this phase.

## Phase 5 — combined demonstration (not started)

Start only after the Phase 4 manual checkpoint is accepted. Combine verified flows and distinguish desktop/API evidence from phone observations. No native Bixby integration, Samsung Reminder or timed Task notification support is claimed.

Phase 5 remains a separate development decision after manual acceptance. Publishing the current repository does not start it.
