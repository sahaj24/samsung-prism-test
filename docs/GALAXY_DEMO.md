# Final Galaxy demo script

The presentation remains on `demo/oneui-redesign`; no commit or push is part of this change. Controller, model prompts, Google adapters and inference settings are preserved.

## Play

Open `http://127.0.0.1:8844/` (or run `./start-reprise.sh`). Choose **Demo → Full demo · 1:40 → Play**. The three individual scenes are also selectable. Next pauses autoplay and advances one frame; Restart begins the selected scene again.

The desktop composition and ivory/green palette remain unchanged: phone left, conversation right, compact execution strip below. The phone uses a consistent **One UI 7** reference direction. Gmail retains the Android Gmail design rather than adopting Samsung Calendar styling.

## Final scenes

1. **Chennai flight PNR — 30 seconds.** Karthick speaks while Reprise is on standby. Naithick says “Hey Reprise”; Gmail opens, searches and reads the synthetic IndiGo booking. The call screen displays `K7P2XQ`, `6E-5423`, Chennai, Saturday 7:10 PM. No assistant speech is added. Naithick reads the PNR to Karthick and Reprise returns to standby.
2. **Coffee — 40 seconds.** Naithick activates Reprise for Sunday 5 PM. Calendar checks availability and prepares an event. Karthick's objection pauses the pending action without authorizing a change. Naithick's correction supersedes the undispatched 5 PM proposal. The existing form changes from 5 PM to 6 PM with a two-second hold; 6 PM availability is checked, then one mock event is saved. The timeline retains the superseded 5 PM proposal and successful 6 PM outcome.
3. **Leave draft — 30 seconds.** Naithick activates Reprise, Contacts resolves Rajesh (Manager), Gmail compose shows the supplied subject and exact message, and the mock tool saves a draft. The call screen says “Draft saved.” No send is dispatched. Reprise returns to standby after the final dialogue.

## Simulation boundaries

**Demo is authored visual playback with synthetic data.** The wake phrase, speaker identity, activation, app interactions and tool results are simulated events, not microphone wake-word detection or actual phone automation. The subtle assistant overlay is custom Reprise UI. The underlying phone screens are HTML/SVG recreations, not an emulator, device mirror, Samsung binary, or pixel-identical native app. SamsungOne is used only if installed; otherwise the system font stack applies.

Every event retains `simulated: true`, fictional IDs use `demo-`, and Rajesh's address is reserved `example.com` data. Playback invokes no Google writes, Gemini session, real Calendar save, email send, or contact API. Manual phone browsing also stays local. Existing **Live** mode and real integrations remain separate. Demo entries are hidden when switching to Live; app navigation cannot replace a live/connecting session.

## Reference basis

- [Samsung One UI app bars](https://developer.samsung.com/one-ui/comp/app-bar.html): condensed headers, back/action placement, search and overflow.
- [Samsung One UI overview](https://developer.samsung.com/one-ui/index.html) and [One UI 8 support reference](https://www.samsung.com/us/support/answer/ANS10007435/): current Samsung component and cross-app reference. The existing demo retains its One UI 7 baseline rather than mixing in iPhone controls.
- [Samsung Galaxy S25 Phone support](https://www.samsung.com/us/support/answer/ANS10007259/): call-control tray and contact navigation. Public Samsung screenshot references were saved locally under `runs/final-script-20261025/` for comparison.
- [One UI 7 calling screen](https://r2.community.samsung.com/t5/Galaxy-S/One-UI-7-Calling-UI/td-p/17684579): six controls, call identity and end-call placement.
- [One UI 7 Calendar screenshots](https://r1.community.samsung.com/t5/others/calendar-app-one-ui-7/td-p/30378519): month layout and event editor references. Some full-size community images could not be fetched; reference retrieval is not equivalent to finished-screen verification.
- [Gmail Android instructions](https://support.google.com/a/users/answer/9259846?co=GENIE.Platform%3DAndroid): compose fields, recipient, subject and draft workflow.

No reference screenshots are shipped as background images or used to display personal data.

## Verification — 26 October refinement

- The exact 30/40/30-second script is preserved. Speaker attribution, the authored wake phrase and standby behavior are unchanged.
- Call proportions, default contact avatar, dark six-button tray, Call assist affordance, end-call button and three-button Samsung navigation were refined against Samsung's public Phone screenshots. The blue/lavender gradient now extends through the status and navigation bars. Reprise results remain small custom overlays.
- A green ongoing-call timer remains in the status bar during Gmail, Calendar and Contacts. It returns to the call when tapped. Recents/back preserve the current call time, and ending/resetting the simulation clears call state. Autoplay advances the displayed call timer continuously.
- The desktop background is slightly cooler; the phone is 586 pixels tall at 1366×768, compared with 548 before this refinement. The compact execution strip remains visible below it.
- Undispatched superseded steps strike through briefly and collapse over 1.8 seconds. Their original event data and details remain in memory and can be inspected through Settings → Inspect actions. Dispatched operations are not marked cancelled by this presentation behavior. Duplicate success rows are kept out of the quiet strip while original proposal markers update from their outcomes.
- Frontend asset URLs were versioned together to avoid a refreshed HTML document using an older cached phone renderer.

Focused verification logs and screenshots are stored locally under `runs/oneui-refinement-20261026/`. **18 phone/playback tests and 47 app tests pass**, including the fixed script, single 6 PM write, absence of email sending, same-node editor revision, call continuity, history retention and Live/Demo isolation. JavaScript syntax and whitespace checks pass. No full backend suite or paid/live-model benchmark was needed for this UI-only change.

The previous request's no-test checkpoint has been superseded by the current request to test all three scenes. All three scenes were inspected in the browser at 1366×768, with no page scrolling. Full 1:40 autoplay reached 31/31 and standby; after the final strip-ordering refinement, all 31 frames were stepped through again. Recorded UI events show one 6 PM event creation, zero 5 PM creations, one draft creation and no email send. Inspect actions restored all three retired 5 PM history entries; switching to Live hid the synthetic history. The 1920×1080 composition also fit at 100% zoom. No browser console errors were observed. Geometry, event records and screenshots are in that run directory. These scripted checks do not establish new live-model or actual-phone integration results.

Manual replay: refresh, choose **Demo → Full demo · 1:40 → Play**. Inspect the PNR return card; watch Karthick pause the 5 PM save and Naithick's correction change the same form to 6 PM; confirm the draft subject/body and the final “Draft saved” result. Settings → Inspect actions restores the superseded 5 PM history. **Live** still uses the existing real assistant.

The prior UI is preserved at `runs/oneui-refinement-20261026/before-ui.tar`, and the previous script pass is at `runs/final-script-20261025/before-demo.tar`. No controller changes, real Google writes, commits or pushes are part of this refinement.
