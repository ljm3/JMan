# Approve (Android app)

Native Android client for the Approve page at ljm3.me/approve — everything
the site can do, on the phone. Built at Luddy's direction on November 8,
2026: review what awaits approval wherever he is, instead of at the desk.

- Application ID: `net.aniello.approve`
- Label: "Approve"
- Version: 1.0 (versionCode 1)
- minSdk 26 / targetSdk 34 / compileSdk 34, Kotlin, no Jetpack Compose —
  programmatic views, the same construction as the Daily Reports Player.

## What it does

- Signs in with the same dashboard credentials as the website (HTTP Basic,
  username "luddy"), stored encrypted on-device (Android Keystore via
  security-crypto; any encrypted-store failure deletes the poisoned file
  and falls back to a plain private store — the player's lesson).
- Lists every entry awaiting approval, newest first: kind chip, status,
  season/episode, full date, and duration.
- Detail view per entry:
  - Audio and video entries play in an embedded player fed by a
    MediaController bound to a MediaSessionService (audio focus,
    becoming-noisy pause, wake lock) — playback survives phone calls and
    Bluetooth handoffs. Video renders in the player surface; some future
    entries will be video, and the app is ready for them.
  - Text-only entries (newsletters) show their text with no player.
  - Description, script (episodes show the "Pre-Approved Script" when one
    exists), sources, and artifacts, all readable in place.
  - Actions in the site's order: Adjust / Edit / Approve for episodes;
    Pre-Adjust / Pre-Edit / Pre-Approve for Wednesday content entries;
    breaker placeholders carry Include / Leave out. Approve confirms use
    the site's exact words ("Commence Publish Activity?" for episodes;
    "Pre-Approve this content for Saturday production?" for content).
  - A Download link for the media file.
- After every successful action the app refetches and re-renders the full
  entry list — no stale state (the Daily Reports Player v1.28 lesson).
  Any 401 returns the app to the sign-in screen.

## Server side (Worker `podcast-media`)

All routes sit behind the dashboard Basic Auth, same as the page:

- `GET /approve/api/entries` — full entry list from the R2 drafts
  manifest (kind, category, title, dates, media type, description,
  script, sources, artifacts, status, placeholder state).
- `GET /approve/media/<file>` — serves the entry's media from
  `drafts/<file>` with the content type by extension (audio and video).
- `GET /approve/app-version.json` — R2 `approve/app-version.json`.
- `GET /approve/approve.apk` — R2 `software/approve.apk` (one APK
  object, two doors, the player's pattern).
- Actions reuse the page's endpoints unchanged: POST
  `/approve/api/description`, `/approve/api/adjustment`,
  `/approve/api/approve`, `/approve/api/script`, `/approve/api/placeholder`.

## Signing

Dedicated keystore `signing/approve.jks`, alias "approve"; passwords in
`signing/signing.properties` (mode 600; the keystore is mode 600 too).
**Every future update MUST be signed with this same keystore**, or
Android will refuse the update. Signing material NEVER goes to GitHub.

Signer certificate SHA-256 (v1.0):
44d4b1d19e1fcb5d10199dc8e98e7bc6371d9d86d39f9e240ccfb8af36dc302e

## Build and publish

```sh
cd ~/workspace/apps/approve
~/workspace/apps/toolchain/build.sh :app:assembleRelease
# APK lands in app/build/outputs/apk/release/app-release.apk
```

Builds run OFFLINE against `~/workspace/apps/toolchain/local-maven`
(Gradle cannot download in the sandbox; new dependencies are fetched
with `fetch_closure.py` first). Always verify the artifact before
publishing: `aapt2 dump badging` for package/version, SHA-256 of the
file, and `apksigner verify --print-certs` for the signer.

Publish (one APK object, two doors; version JSON to BOTH keys):

```sh
cp app/build/outputs/apk/release/app-release.apk release/approve.apk
cf.py r2-put <acct> software/approve.apk release/approve.apk
cf.py r2-put <acct> software/approve-version.json release/approve-version.json
cf.py r2-put <acct> approve/app-version.json release/approve-version.json
```

Then verify live: `https://software.aniello.net/apps/approve.apk`
hashes identically to the local release copy, and
`https://software.aniello.net/apps/approve-version.json` returns the
new version JSON.

## Self-update

The app checks for updates on start (throttled to once a minute) and
from the list screen's "Check for Updates" button. Version check tries
`https://ljm3.me/approve/app-version.json` (Basic auth) first, then the
public `https://software.aniello.net/apps/approve-version.json`; the
APK download tries `https://ljm3.me/approve/approve.apk` (auth), then
`https://software.aniello.net/apps/approve.apk`. Install goes through
the classic installer with a relaunch-after-update marker, exactly as
the player does.

## Build notes (v1.0)

- `MainActivity` extends plain `android.app.Activity` (the player's
  pattern, framework Material theme) — NOT AppCompatActivity:
  appcompat is not in the offline repo and nothing here needs it.
- media3-ui 1.4.1 pulls `androidx.recyclerview:recyclerview:1.3.0`,
  which the media3-ui closure fetch missed once (transient); fetching
  `androidx.recyclerview:recyclerview:1.3.0` directly completed it.
- Version-code comparisons use `optLong`/`longVersionCode`
  consistently — Int/Long mixing fails compilation.
- PlaybackService resolves the Authorization header FRESH per request
  (ResolvingDataSource) instead of capturing it at service creation,
  so a sign-in change takes effect for playback immediately.

## Release history

- **1.16 (versionCode 17)** — 2026-11-09. Issue markers
  now show their Found/Done resolution history under
  the note — one line per correction pass, oldest
  first — matching the Approve page (the resolutions
  live on the marker in the drafts manifest, written by
  each correction pass through publish_draft.py's
  resolve command). Display only; no endpoint or action
  changes. APK 6,129,877 bytes, SHA-256
  8c5a4a5e79965fa8010497fd2aefa3460a776e8c42fea7498917569bb8f846e2,
  signed with the "approve" keystore; published to
  `software/approve.apk` with the version JSON at both
  R2 keys; live download verified byte-identical.

- **1.15 (versionCode 16)** — 2026-11-09. The Adjust /
  Pre-Adjust helper button is renamed "Correct Markers"
  (was "Correct per Marker") at Luddy's direction; the
  note it fills into the adjustment box matches the new
  wording. The Approve page's button was renamed the
  same morning (builder change, page rebuilt and
  re-uploaded). APK 6,129,621 bytes, SHA-256
  0039e4e343a2857d09a79679e039b542514c8516697c9705758d384fafb80d86,
  signed with the "approve" keystore; published to
  `software/approve.apk` with the version JSON at both
  R2 keys; live download verified byte-identical.

- **1.14 (versionCode 15)** — 2026-11-09. Three display
  changes at Luddy's direction the same morning: the
  Adjust, Edit, Approve, and Mark issue buttons get
  rounded corners (10dp radius, the control strip's
  radius; the rounding is opt-in on the shared button
  factory, so every other button is unchanged), Back to
  the List becomes a pill (the app's existing pill
  button), and every status label is capitalized —
  "Adjustment Requested", "Pre-Adjustment Requested",
  "Publish Commenced", "Awaiting Your Approval",
  "Awaiting Pre-Approval", "Left Out" — in the app and,
  for consistency, on the Approve page (builder badges,
  live state updates, and the adjustment-note label;
  page rebuilt and re-uploaded the same morning). APK
  6,129,657 bytes, SHA-256
  9aed7099e7b535dbf1a5546513a1dc7652f207d79fa64c0bd7d7201132c209bb,
  signed with the "approve" keystore; published to
  `software/approve.apk` with the version JSON at both
  R2 keys; live download verified byte-identical.

- **1.13 (versionCode 14)** — 2026-11-09. Status-label
  fix Luddy flagged the same morning ("App still shows
  adj req"): once corrected audio was re-listed, the
  entry's status label kept reading "Adjustment
  requested" (gold and bold on the entry screen) next to
  the separate "Awaiting Correction Approval" badge.
  The label itself now carries the correction state —
  awaiting-correction entries read "Awaiting Correction
  Approval" on the list (gold, bold) and on the entry
  screen, and the duplicate badge views are gone. The
  app also re-fetches the entries whenever it returns to
  the foreground (list re-renders when it is the screen
  showing), so state changes that land while the app is
  backgrounded appear without a manual Refresh. APK
  6,129,233 bytes, SHA-256 593cd445…, approve
  keystore; published and live-verified the standard
  way.
- **1.12 (versionCode 13)** — 2026-11-09. The app is
  renamed Approve Player, at Luddy's direction
  ("Let's name it the Approve Player") — a display-name
  change only: the launcher label, the sign-in screen
  title, the list header title, and the update prompt
  now say Approve Player, and the Approve page's
  <title> and sidebar title changed to match. The
  package (net.aniello.approve), the /approve routes,
  R2 keys, file names, and signing are untouched, so
  the app updates in place; the Approve / Pre-Approve
  ACTION keeps the verb exactly as it was. APK
  6,129,425 bytes, SHA-256 f68d9ca5…, approve
  keystore; published and live-verified the standard
  way.
- **1.11 (versionCode 12)** — 2026-11-09. Two marker-row
  changes Luddy directed the same morning, mirrored on
  the Approve page in the same pass. First, the marker
  controls shrink to plain squares so the note text gets
  the room: the time chip is slimmer (12sp, tighter
  padding), Edit becomes a pencil glyph (✎) in a fixed
  30dp × 30dp square, and the × close is a fixed
  24dp × 24dp square just large enough to see the mark
  (page parity: 26px Edit square, 22px close square,
  12px time chip). Second, tapping a marker time now
  starts playback ten seconds BEFORE the marker — by
  the time he taps, he has just heard the issue, so it
  is usually just before — clamped at zero, play
  behavior otherwise unchanged (the page seeks
  Math.max(0, pos - 10) the same way). Seek behavior
  apart from the pre-roll and all marker endpoints
  unchanged; no Worker change. APK 6,129,461 bytes,
  SHA-256 2534de04…, approve keystore; published and
  live-verified the standard way.
- **1.10 (versionCode 11)** — 2026-11-09. Marker note
  editing, at Luddy's direction ("Allow markers text edits
  after input. Might be to find a word or identify specific
  words."), mirrored from the Approve page (built the same
  day): each marker row gains an Edit action beside ✕,
  opening a dialog in the app's style with the note
  prefilled; Save posts to the Worker's new
  POST /approve/api/marker-edit and the entry view
  refreshes through the standard postAction refetch. Only
  the note text changes — the marker's position never
  moves. Server side: the Worker stamps edited_at on the
  marker (id, position_secs, created_at untouched; same
  1–2000-char limits and decided-entry 409 as marker
  add/delete; Worker deployment
  4b4478002b554a678476c940690f2929), and the 5-minute
  actions watch re-hands an edited marker to the parent
  exactly once as a MARKERS block carrying the updated
  text, so a refinement reaches the correction flow even
  when the marker itself was already reported. APK
  6,129,185 bytes, SHA-256 1212ada5…, approve keystore;
  published and live-verified the standard way.
- **1.9 (versionCode 10)** — 2026-11-09. Two additions
  Luddy directed the night of November 8–9, mirrored from
  the Approve page (built the same night): the Adjust and
  Pre-Adjust dialogs gain a "Correct per Marker" button
  on the left of the note area — one tap fills the note
  with exactly "Correct per Marker" when the markers on
  an entry are the problem set to solve (the note stays
  editable; Send is unchanged) — and a corrected entry
  whose audio awaits Luddy's correction approval carries
  an "Awaiting Correction Approval" badge at the far
  right of its header, in the list cards and on the entry
  screen, driven by the drafts manifest's new
  corrected_at / awaiting_correction_approval fields
  (Worker deployment 146098de6be24f4b89bbe1c9094735f3
  carries them through both approve APIs and clears the
  state server-side on approval or a fresh adjustment).
  APK 6,128,781 bytes, SHA-256 cea39fd5…, approve
  keystore; published and live-verified the standard way.
- **1.8 (versionCode 9)** — 2026-11-08. Luddy: v1.7's bar
  still did not show movement; "Controls should be the same
  as on the daily player, if possible." The strip is now a
  mirror of the Daily Reports Player's Now Playing panel:
  time text at the strip's right (the player's nowTime
  position), SeekBar max 1000 with per-mille progress
  (refreshProgress semantics copied), 15dp-style spacing,
  and the five-button row ⏮ / −15s / ▶⏸ / +15s / ⏭ built
  with the player's controlButton metrics (13.5sp, 43dp min
  height, rounded GradientDrawable, #1b2637 fill) — primary
  in this app's gold with dark text instead of the player's
  blue. Play handler copied from the player: on STATE_IDLE
  or STATE_ENDED it reloads the entry, prepares, and plays.
  Seek maps per-mille × duration (player duration, entry
  duration_secs fallback). The strip time text is driven by
  the same updatePositionLabel as the footer readout, so
  movement is visible numerically beside the bar. APK
  6,127,889 bytes, SHA-256 59800af2…, approve keystore;
  published and live-verified the standard way.
- **1.7 (versionCode 8)** — 2026-11-08. Luddy reported the
  status (progress) bar still did not move visually in 1.6 —
  "same as when was in video box." Diagnosis: serveFile serves
  proper ranges (206/Content-Range/Accept-Ranges) and the
  PlaybackService is a functional mirror of the Daily player's
  working service (only the data-source factory differs), so
  the frozen element in BOTH placements was the media3 control
  view's own progress display. Fix: the strip below the window
  is now app-built — a Play/Pause actionButton and a SeekBar
  driven by updatePositionLabel() (the 500ms ticker + Player
  events; bar scale = player duration, falling back to the
  entry's duration_secs; drag seeks via controller.seekTo).
  PlayerControlView and its binding are removed entirely. Also:
  Player.Listener.onPlayerError now writes "Playback problem:
  <message>" to the status line, toasts, and records to Diag —
  through 1.6 a playback error froze every visual with no
  on-screen explanation. APK 6,126,629 bytes, SHA-256
  e3196cbd…, approve keystore; published and live-verified the
  standard way.
- **1.6 (versionCode 7)** — 2026-11-08. Luddy, same day: "we
  don't need the spinning component. Just the logo in that
  window is fine, and the status bar and controls should be
  below that window, not in it" — then "No shield required...
  just the logo image in the window." Changes: (1) all spin code
  removed (animator, updateSpin/stopSpin); (2) the badge/stage
  overlay removed — for audio entries the PlayerView's artwork
  IS the window image (the show cover once fetched; AE logo
  placeholder), player height 200dp; (3) PlayerView
  useController=false and a PlayerControlView strip (CARD
  background, showTimeoutMs=0 so it never auto-hides) sits
  directly below the window for both audio and video, bound to
  the controller at build and again in the connect listener
  (controlView field). The pinned bottom status line + Mark
  footer from 1.4 is unchanged. First build attempt failed on an
  unresolved PANEL constant (this app's panel color is CARD) —
  fixed before the successful build. APK 6,125,189 bytes,
  SHA-256 15cfe5b8…, approve keystore; published and
  live-verified the standard way.
- **1.5 (versionCode 6)** — 2026-11-08. Luddy: "It should show
  the show logo" + "the logo as a shield, spinning, to prove
  video." Audio entries now carry a round shield badge over the
  player center showing the entry's OWN show cover
  (coverUrlFor(): category → https://podcasts.aniello.net/covers/
  <show>-cover.png, the feeds' images, all six verified live).
  fetchArtwork() fetches public, downscales to 512px, caches in
  memory + cacheDir; refreshEntries prefetches all covers so they
  are ready at open. The badge spins (ObjectAnimator rotation,
  14s/rev, linear, infinite) only while the controller isPlaying
  — updateSpin() driven by the Player.Listener — and freezes on
  pause; stopSpin() is folded into stopPositionTicker(). The
  cover also becomes PlayerView.defaultArtwork and the media
  metadata artworkData (loadMediaFor uses cached art, AE logo
  fallback; the AE logo is also the badge placeholder). Video
  entries unchanged (their own video fills the surface, no
  badge). APK 6,126,053 bytes, SHA-256 c72497d2…, approve
  keystore; published and live-verified the standard way.
- **1.4 (versionCode 5)** — 2026-11-08. From Luddy's first
  on-device testing. (1) Position readout fix: it never elapsed
  because startPositionTicker() called stopPositionTicker(), which
  nulls positionLabel for teardown — the ticker ran against a
  nulled label from v1.1 on. Start no longer routes through stop;
  the readout also refreshes on Player.Listener events, and the
  controller-connect path now loads the open entry's media (an
  entry opened before connect previously never loaded). Media
  loading is factored into loadMediaFor(). (2) Status line (the
  readout) + Mark button moved to a fixed footer pinned at the
  bottom of the detail screen — readout directly above Mark,
  visible without scrolling; it leaves only with the lock screen,
  screen saver, or full screen. Markers list stays in the scroll
  content. (3) Status bar: icon appearance set explicitly (light
  icons; WindowInsetsController on API 30+, legacy flag below)
  instead of SYSTEM_UI_FLAG_VISIBLE; the app never hides the bar.
  (4) AE logo as visual: PlayerView defaultArtwork = ae_logo for
  audio entries, artworkData on the media metadata (notification),
  audio player height 96dp → 200dp. APK 6,123,385 bytes, SHA-256
  38ae446d5bd01f2888e6c02df1cb1f52cab0c1faf415f9f0cd0d4bdb2c6f7ce0,
  approve keystore; published and live-verified the standard way.
- **1.3 (versionCode 4)** — 2026-11-08. Luddy's direction: the
  Options menu's sign-in item moved to the top of the list and
  renamed "User" (was "Account — Change Sign-In", last). Menu order
  is now User / Refresh / Check for Updates, mirroring the Daily
  app's Settings menu. APK 6,121,965 bytes, SHA-256
  d887033b5544217c0b1c6be8b566ecdeee0c8b2e8494f3bedf87cece548f7024,
  approve keystore; published and live-verified the standard way.
- **1.2 (versionCode 3)** — 2026-11-08. Luddy's direction: the
  button options moved to the upper right like the Daily Reports
  Player — an "Options" pill in the Daily Settings-button format
  (fully rounded, thin border, gold thickening at the tap) opening a
  button menu (Refresh / Check for Updates / Account — Change
  Sign-In; Close in the footer). The list header mirrors the Daily
  header: logo left, title + version in a center-gravity stack, pill
  at the right. The installed version sits under the Approve title
  in the Daily format — "Version:  " + versionName read from the
  package, 12sp subtext, no font padding — on the list and sign-in
  screens; the old bottom buttons and footer version line are gone.
  APK 6,121,989 bytes, SHA-256
  f6011c4fec695d144d5001e09fa04b42c78b7e19d1b0248555d8c4bfaa8ea1d5,
  signed with the "approve" keystore; published to
  `software/approve.apk` with the version JSON at both R2 keys; live
  download verified byte-identical. (The first build attempt was cut
  by an interleaved score ping; the rerun in the background
  completed cleanly — see Luddy's hold-notifications rule of the
  same day.)
- **1.1 (versionCode 2)** — 2026-11-08. Luddy's direction: the
  status bar stays visible at all times, an always-on playback
  position readout sits under the player (half-second ticker off the
  MediaController; it never auto-hides like the player controller),
  and the issue-marker system: a "⚑ Mark issue at this position"
  button opens a note dialog titled with the captured position and
  posts to `/approve/api/marker`; markers render under the player —
  tap a marker's time to seek and play, ✕ deletes via
  `/approve/api/marker-delete`. Markers live on the drafts-manifest
  entry, shared with the Approve page (the same system was built into
  ljm3.me/approve the same day). APK 6,120,729 bytes, SHA-256
  c2e9d600f7a7dc61402459f1983e85db23031441b549a38004eda475cc3ee8bc,
  signed with the "approve" keystore; published to
  `software/approve.apk` with the version JSON at both R2 keys; live
  download verified byte-identical.
- **1.0 (versionCode 1)** — 2026-11-08. First release. APK 6,117,937
  bytes, SHA-256
  5fc2c5368387b1ef2ff74a09903a6390035788a49b7b4dee47f74e4ec91eb17d,
  signed with the "approve" keystore. Published to
  `software/approve.apk` on software.aniello.net with
  `approve-version.json` at both R2 keys; live download verified
  byte-identical. Awaiting Luddy's on-device test — his sign-in is the
  first authenticated exercise of the app endpoints (the sandbox
  cannot complete native HTTP Basic Auth).

- **1.17 (versionCode 18)** — 2026-11-09. The update
  check now also runs when the app returns to the
  foreground (onResume, keeping the 60-second throttle
  and the once-per-version prompt rule), mirroring the
  Daily Reports Player. Diagnosis: the check ran only
  in onCreate, and this app's process lives across
  uses, so the v1.14–v1.16 updates never offered
  themselves — Luddy flagged that Approve did not
  auto-update like the Player. Reaching 1.17 takes one
  manual Check for Updates; from 1.17 on, updates
  offer themselves on return to the app. APK 6,129,937
  bytes, SHA-256
  3cd47058eb66f87d2fd3e589f90d7d659b71c6ba2ac3a0e54785ba6d8e1e0e67,
  signed with the "approve" keystore; published to
  `software/approve.apk` with the version JSON at both
  R2 keys; live download verified byte-identical.
