# Galaxy concept UI checkpoint — 25 October 2026

## Current implementation

Branch: `demo/oneui-redesign`, based on unchanged HEAD `844100255df909a0bef4a3fc137feb13947ca555`.

The redesign changes `demo/index.html`, presentation code in `demo/app.js`, frontend regression tests and documentation. The controller, model prompts, backend routes, Google adapters, local Gemma planner, mock tool schemas and inference configuration were not changed. No dependencies were added.

- Left: titanium-framed, One UI-inspired browser phone, actual session and microphone controls, real audio playback toggle, live transcript preview, current backend plan, confirmation and receipt IDs.
- Right: live/final conversation only, with speaker labels, timestamps and interrupted speech marked separately.
- Bottom: horizontal action-event timeline with state colors, connectors, expandable arguments/results and history navigation. Earlier activity remains available across sessions.
- Intent revisions and superseded work remain visible on the phone while a replacement action is displayed. Audio interruption alone never displays an action as cancelled. Unknown operations do not claim Google confirmation merely because an ID is available.
- Desktop/tablet use two columns; mobile stacks the phone, conversation and timeline. Reduced motion disables decorative animation and smooth navigation.

The phone is a browser concept. ADB exists locally, but this project has no mirroring integration, scrcpy or configured Android emulator. No phone bridge, native Samsung telemetry or new integration was built. Existing Google synchronization remains the actual phone integration boundary. The repository did not contain a scripted frontend scenario player; existing live/mock-tool transports were preserved.

## Preservation and rollback

Private backup outside the repository:

`/home/naithick/Prism/reprise-backup-before-oneui-20261025-200206/`

- `checkout/`: full copy including original `.git`, tracked, untracked and ignored files, local credentials, recordings and experiment outputs.
- `history.bundle`: all original Git refs/history; `metadata.json` records original branch and HEAD.
- `working-files.tar`: independent pre-redesign archive of all tracked/untracked non-ignored working files.
- `changes.patch`, `status.txt`: original working diff and status.

Keep this backup private: it contains local credentials. Original `main`, remotes and historical tags remain unchanged. No commit, staging or push was performed. Switching branches alone does not restore uncommitted frontend files.

For a UI-only rollback, first preserve the redesigned frontend, then restore the three original working files from the backup:

```sh
cd /home/naithick/Prism/samsung-prism-test
# Use a fresh archive filename if one already exists.
tar -cf /tmp/reprise-redesigned-ui-before-rollback.tar demo/index.html demo/app.js demo/app.test.cjs
cp /home/naithick/Prism/reprise-backup-before-oneui-20261025-200206/checkout/demo/index.html demo/index.html
cp /home/naithick/Prism/reprise-backup-before-oneui-20261025-200206/checkout/demo/app.js demo/app.js
cp /home/naithick/Prism/reprise-backup-before-oneui-20261025-200206/checkout/demo/app.test.cjs demo/app.test.cjs
```

This restores the original uncommitted UI, rather than the older committed release. The complete untouched checkout is also available in the backup. No rollback commands were executed.

## Verification

- Original frontend baseline: 18 tests passed.
- Updated frontend suite: **30 passed**. Covers session/polling, live transcription, local text transport, consent revocation, receipt states, safe text rendering, phone synchronization, correction marker retention, unknown outcomes, actual audio element muting, late audio callbacks and reduced motion.
- Full offline Python regression suite: 303 passed, 12.54 seconds. Google HTTP uses existing mocks.
- JavaScript syntax check and `git diff --check` passed.
- Actual dashboard rendered in the in-app browser, retaining Google account status and model/tool settings.
- Separate localhost-only offline fixture exercised proposal → interrupted speech → correction → superseded proposal → replacement → dispatch → receipt → completion, using the real frontend render/session code with mocked transport. Its permanent yellow banner identifies synthetic events. It is not a live model or Google API test.
- Desktop and stacked mobile layouts inspected; narrow mobile and tablet widths checked for page overflow. Screenshots, frontend/Python logs and isolated fixture code are under ignored `runs/oneui-redesign-20261025/`.

## Manual live checkpoint

1. Open `http://127.0.0.1:8844/`, or run `./start-reprise.sh`. Hard refresh if an old page is already open.
2. Start a conversation, permit the microphone, and verify LiveKit and Gemini separately report connected.
3. Read tasks/events. Verify readable results and raw details in the timeline, with only speech in Conversation.
4. Ask for a disposable personal event, then interrupt with a new time. Compare the superseded proposal and actual dispatched arguments; inspect the real Google receipt and phone calendar separately.
5. Correct a completed disposable event. Verify the existing resource ID is retained. Test a deletion only when explicitly ready to confirm it.
6. Mute/unmute microphone and playback separately; End and restart. Verify no old response/track changes the replacement session.
7. Review timeline history with arrows and keyboard; check confirmation remains disabled after revocation.
8. Optionally select Gemma E2B with the existing local server running. The phone must say Text assistant; audio controls remain disabled.

No paid evaluation, live voice inference or Google mutation was run for this UI task. Existing duplicate Tasks were left untouched. Live voice behavior and phone synchronization remain a manual acceptance step. No Phase 5 backend work was started.

## Later launch presentation revision

The current composed stage supersedes the first dashboard layout described above. See [Galaxy launch demo](GALAXY_DEMO.md) for native app storytelling, the quiet execution strip, current controls and simulation boundaries. Historical backup and rollback information above is retained.

## Final script and One UI reference pass — 25 October 2026

The earlier flight destination-change story is superseded by the user's final three scenes: Chennai PNR (`K7P2XQ`, IndiGo `6E-5423`), Sunday coffee revised from 5 PM to 6 PM, and a leave draft for Rajesh. Playback includes authored Naithick/Karthick attribution, simulated wake activation/standby, a non-authorizing pause for Karthick, and a two-second same-form time correction. The existing desktop composition and colors are preserved. Phone screens use One UI 7 reference layouts and distinct Android Gmail styling.

No offline checks/tests were run for this revision at the user's request. Browser access is blocked in this chat's tool connection; visual comparison and end-to-end playback remain pending. Earlier verification results do not cover this revision. See [GALAXY_DEMO.md](GALAXY_DEMO.md) for exact script, reference sources, simulation boundaries and manual acceptance steps. No controller changes, real account writes, commits or pushes are part of this presentation pass.
