# Remaining demo roadmap

The current release includes the two-panel voice UI, Google Workspace reads/writes, contextual consent, lookup-bound Calendar/Task management and supported correction/undo operations. Live manual acceptance remains a checkpoint; offline tests and API receipts are not physical-device verification.

## Phase 4 — isolated Galaxy Clock prototype (not started)

- Build a Kotlin companion using public `AlarmClock.ACTION_SET_ALARM` where supported.
- Use an authenticated development bridge and confirm actual device outcomes.
- Keep it isolated from the existing Google backend/controller contracts.
- Do not treat launching an Android intent as proof that an alarm exists.
- Preserve a manual Galaxy-device checkpoint. No private Bixby API, root or accessibility workaround is assumed.

## Phase 5 — combined demonstration (not started)

Integrate only accepted, individually verified flows after the Workspace and Galaxy-device checkpoints. Maintain honest status labels and distinguish the current Google validation backend from the Galaxy AI/Bixby product direction.

This publication does not begin either phase. Native Samsung Reminder, Bixby integration and timed Task notifications are not implemented.
