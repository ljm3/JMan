# Daily Reports Player

Native Android player for the Daily Reports audio published on
ljm3.me/daily. It exists because the web player could not survive
phone calls, Bluetooth handoffs, and backgrounding on the road; this
app plays through a Media3 foreground media-session service instead.

- Application ID: `net.aniello.dailyreports`
- Version: 1.0 (versionCode 1)
- Language/stack: Kotlin, single activity, Media3 ExoPlayer +
  MediaSessionService, min SDK 26, target/compile SDK 34.

## What it does

- Signs in with the estate dashboard credentials (username `luddy`).
  Credentials are entered in the app and stored only in app-private
  storage (EncryptedSharedPreferences, plain private-mode fallback).
  The password is never hardcoded.
- Fetches `https://ljm3.me/daily/manifest.json` (Basic Auth) — the
  same manifest the web Daily page is built from.
- Playlist mirrors the web Daily page: the newest date in the
  manifest, every report's Summary in schedule order, then every
  Full Text. Report order/names mirror `REPORTS` in
  `build_daily.py`. Rows can be tapped to start mid-playlist; the
  player auto-advances; prev / -15s / play-pause / +15s / next
  controls sit in the Now Playing bar; the three retained days are
  reachable with Prev Day / Next Day.
- Audio URLs are `https://ljm3.me/daily/audio/<prefix>-<date>.mp3`
  and `<prefix>-<date>-full.mp3`, streamed with the Basic Auth
  header. Audio focus is handled by ExoPlayer (calls pause, focus
  return resumes); becoming-noisy (headphone unplug) pauses.
- Self-update: on launch it reads
  `https://software.aniello.net/apps/daily-reports-player-version.json`;
  if a newer versionCode exists it offers a download of the APK from
  `https://software.aniello.net/apps/` and hands it to the Android
  package installer (one-tap confirm; Android does not allow silent
  installs for regular apps).

## Signing — READ BEFORE RELEASING ANY UPDATE

- Release keystore: `signing/daily-reports-player.jks`, alias `daily`.
- SHA-256 certificate fingerprint:
  `3E:E8:CF:8F:DF:EB:49:C9:D0:41:57:E9:AE:E8:BD:7C:71:12:10:B9:EE:FE:9D:F1:6D:9A:E1:6E:53:27:AD:3C`
- The keystore password lives only in `signing/signing.properties`
  (mode 600), which the Gradle release build reads. It is not in any
  source file.
- **Every future update MUST be signed with this same keystore.**
  Android refuses to install an update signed with a different key,
  and the self-update path would break permanently. Back this
  keystore up; if it is lost, the app can never be updated in place.

## Building (this sandbox)

The toolchain lives in `~/workspace/apps/toolchain/` (Temurin JDK
17.0.21, Android SDK platform 34 / build-tools 34.0.0 /
platform-tools 37.0.2, Gradle 8.7).

This sandbox blocks raw TCP connections from the JVM
("Other TCP connections is turned off"), so Gradle cannot download
dependencies here. Dependencies were fetched with curl into
`~/workspace/apps/toolchain/local-maven/` by
`~/workspace/apps/toolchain/fetch_closure.py`, and
`settings.gradle.kts` points at that file repository. On an
unrestricted machine, swap those repositories back to `google()`,
`mavenCentral()`, and `gradlePluginPortal()`.

Build and publish:

```sh
~/workspace/apps/toolchain/build.sh :app:assembleRelease
# APK lands in app/build/outputs/apk/release/app-release.apk
```

Sandbox build notes (learned on the first build, 2026-11-04):

- `androidx.security:security-crypto` is pinned at **1.1.0**, not
  1.0.0: the 1.0.0 AAR predates the `MasterKey` API this app uses
  (it only ships the deprecated `MasterKeys`).
- The manually unzipped SDK platform's `package.xml` named
  `platforms;android-34-ext12` while living in `platforms/android-34/`,
  which made AGP skip the platform ("failed to find target
  android-34"). Fixed by rewriting that one path attribute to
  `platforms;android-34` (original kept as `package.xml.bak-manual-unzip`
  in the platform directory) — exactly what sdkmanager writes on a
  normal install.
- `com.android.tools.build:aapt2` publishes no Gradle module metadata;
  its OS classifier jars (`aapt2-<ver>-linux.jar` etc.) were downloaded
  into the local repo by hand.
- `fetch_closure.py` originally requested a `.bundle` file for Maven
  "bundle"-packaging artifacts; those ship `.jar` files. Fixed.
- The Kotlin compile daemon cannot connect over loopback TCP in this
  sandbox; Kotlin falls back to in-process compilation automatically.
  Builds are slower but succeed.

## Releases

- **1.0 (versionCode 1)** — first release, 2026-11-04.
  APK: 5,224,312 bytes, SHA-256
  `c75770130c1f9cfb397f17c69197bf5a85b16ab93b793d959c84f3fd715f8ebc`.
  Published to `software/daily-reports-player.apk` on
  software.aniello.net with `daily-reports-player-version.json`.
  On-device report (Luddy, 2026-11-05): installs, Play Protect scan
  clean, sign-in prompt works — but after credentials were saved the
  app closed immediately on every launch.
- **1.1 (versionCode 2)** — 2026-11-05. Fixes the v1.0 launch crash and
  adds the AE eagle-A logo as the launcher icon (legacy + adaptive,
  background #0B0F16). Crash cause: Credentials used
  EncryptedSharedPreferences but only guarded store CREATION — a throw
  while READING the encrypted store back in a later process (a known
  keystore flakiness class on Samsung Galaxy devices) propagated out of
  onCreate and killed the app, but only once credentials existed.
  Credentials.kt is now fully guarded: any encrypted-store failure
  deletes the poisoned file and permanently falls back to a plain
  private-mode store for that install (worst case = one fresh sign-in
  prompt, never a crash loop); MainActivity.onCreate also guards the
  credential check. APK: 5,408,194 bytes, SHA-256
  `f0ece23f354ac7ddc63aeea46bcc2e97b7f9fb45f43b5ce7faa886af4da7fd22`,
  same signing key as 1.0; published and verified live 2026-11-05
  (live download hashes identically). Because v1.0 cannot stay open,
  the in-app update prompt never fires for it — the fix is installed by
  re-downloading the APK and installing over the top.
  On-device report (Luddy, 2026-11-05): v1.1 passes all gates, shows
  the AE icon, opens and STAYS open (crash-close fixed), but shows a
  blank screen and minimizes — a second, distinct fault not visible
  from the build sandbox (no emulator / no device log access here).
- **1.2 (versionCode 3)** — 2026-11-05, DIAGNOSTIC build. Adds Diag.kt:
  a phase trail plus an uncaught-exception handler that saves the
  stack trace to app-private storage and shows a summary dialog at the
  next launch ("Last launch ran into a problem"). MainActivity.onCreate
  is fully guarded (a startup throw lands on the sign-in screen with
  the error named in the status line). PlaybackService.onCreate is
  recorded-and-rethrown, and the becoming-noisy receiver now registers
  with the explicit export flag targetSdk 34 wants (a live suspect for
  the v1.1 blank/minimize behavior). APK: 5,409,710 bytes, SHA-256
  `cd72f4833f18901a37a0e1b7800da0d4e53e5925109e97481548af10f5540ec0`,
  same signing key; published and verified live 2026-11-05. Next step:
  Luddy installs over the top, launches, and reads back whatever the
  screen/dialog says — that evidence names the fault for the real fix.
- **1.3 (versionCode 4)** — 2026-11-05, THE FIX. Luddy's v1.2 diagnostic
  named it: "Failed to resolve SessionToken for ComponentInfo{…PlaybackService}
  — Manifest doesn't declare one. Use service's full name," and the app
  then bounced him to the login screen over and over. Root cause found
  by decompiling Media3 1.4.1's SessionToken class from the local AAR:
  its constructor builds an intent with action
  `androidx.media3.session.MediaSessionService` and asks PackageManager
  which services answer it; my manifest advertised the wrong string,
  `androidx.media.session.MediaSessionService` (media, not media3), so
  the lookup came back empty and the constructor threw on EVERY launch.
  The throw landed in onCreate's catch, which re-showed sign-in — the
  login loop (v1.0 hit an earlier crash first; v1.1's unguarded throw
  was the close/blank behavior). Fix: manifest action corrected to
  `androidx.media3.session.MediaSessionService` (framework action kept
  alongside); connectController() now runs in its own try/catch so a
  playback-connection failure can never tear down startup or bounce to
  sign-in again; the crash dialog now shows the full record with a Copy
  button (Luddy's suggestion). APK: 5,410,010 bytes, SHA-256
  `74f64e9dd95e70d3e70a1e17f2cba98fc0e83969bac091e6fba2284aa15f1cc2`,
  same signing key; published and verified live 2026-11-05 (live
  download hashes identically). Luddy confirmed on-device: v1.3 opens
  to the playlist; day tabs (today / previous day) praised.
- **1.4 (versionCode 5)** — 2026-11-05, playlist refresh fix. Luddy:
  the player "does not appear to update the available shows" — it
  showed only the morning's first report (News Summary) hours later.
  Server side verified current (daily-manifest.json carried that day's
  Legislature and AI News entries with matching durations), so the
  fault was the app: loadManifest() ran only at onCreate, sign-in, and
  settings save — never on resume — so while the process lived, the
  list was frozen at first launch. Fix: onResume re-fetches the
  manifest, throttled to once per 30 seconds, only when signed in with
  a list already loaded; the fetch now sets useCaches=false and a
  Cache-Control: no-cache header; a reload keeps the user's selected
  day tab when that date still exists, and the "Loading reports…"
  status shows only on the first load. APK: 5,410,334 bytes, SHA-256
  `c56f5c083476bd392e344f0ef59ac74df84de0bdd6fd460da272e58fef0fbe06`,
  same signing key; published and verified live 2026-11-05 (live
  download hashes identically). Because v1.3 stays open, its in-app
  update prompt can offer this one. Playback on-device still untested;
  Luddy planned to play the full list later that morning. On-device:
  no update prompt appeared for Luddy, though the data was current
  (Android had restarted the process, so the list loaded fresh).
- **1.5 (versionCode 6)** — 2026-11-05, at Luddy's direction. Three
  additions on top of 1.4: (1) the installed version shows in smaller
  text directly under the app title — "Version:  1.5" — read from the
  package info at runtime so it always matches the build on the phone;
  (2) Settings gains a "Check for Updates" button (manual check) that
  always reports the outcome — prompt when an update exists, a
  "you have the latest version" toast when not, a failure toast when
  the check cannot complete; (3) automatic update checks now run at
  launch AND whenever the app returns to the foreground (60-second
  throttle, no-cache fetch), prompting at most once per new version
  per process run so a "Later" is respected until restart. APK:
  5,410,954 bytes, SHA-256
  `47bc1c71a066c5e99d10435e35a6aadf993cd85d433d3f145b128113ee28747a`,
  same signing key; published and verified live 2026-11-05 (live
  download hashes identically).
- **1.27 (versionCode 28)** — 2026-11-07. Play order is now
  manifest-controlled at Luddy's direction: buildTracksFor reads
  the manifest's "order" list (stamped by publish_daily.py,
  preserved across publishes once present) and orders tracks by
  it, falling back to the built-in REPORTS order when the
  manifest carries none. The Daily web page takes the same list:
  build_daily.py bakes it into the web player's sort AND lays the
  page tiles out in it, so display order and play order match in
  both players. Future reorderings are a manifest edit only.
  APK: 5,522,238 bytes, SHA-256
  `40be551a10d3e8aee576e76649886d4cfb615db96c6c3291ef1caaa46943b85f`,
  same signing key; published to both version-JSON keys and
  verified live 2026-11-07 (live download hashes identically;
  software page shows 1.27).
- **1.26 (versionCode 27)** — 2026-11-07. Playlist order at
  Luddy's direction: the Operations and Health Report leads each
  day, and it counts as part of the day it publishes (it posts at
  12:10 AM recapping the day just ended). REPORTS in
  MainActivity.kt now lists nightly-ops first; the nightly-ops
  publish step passes the publish date to publish_daily.py, and
  the three retained editions were re-filed under their publish
  dates (the November 6 report now sits under November 7). The
  ljm3.me/daily web player applies the same lead position in its
  playlist sort; its tiles keep schedule order. APK: 5,521,862
  bytes, SHA-256
  `b1d3868dd31246490e989149f9ea193405841a51bc3aaa706a1fe266c95dacd2`,
  same signing key; published to both version-JSON keys and
  verified live 2026-11-07 (live download hashes identically;
  software page shows 1.26).
- **1.25 (versionCode 26)** — 2026-11-06. The app-side
  two-second breather from 1.23 is REMOVED at Luddy's direction:
  every report file now ends with two seconds of baked-in
  silence, so the stacked ~4-second gap became the file's own
  ~2 seconds. The PlaybackService transition listener is gone
  entirely; auto-advance plays straight through. (This settles
  the question left open on 2026-11-05.) APK: 5,521,910 bytes,
  SHA-256
  `e7a4c4b74001be180ee252320175e1aa42861c670958c365225def5dbac98cef`,
  same signing key; published to both version-JSON keys and
  verified live 2026-11-06 (live download hashes identically;
  software page shows 1.25).
- **1.24 (versionCode 25)** — 2026-11-06. Auto-scroll at Luddy's
  direction: the track list now keeps the playing item in view.
  On every media-item transition (auto-advance, the ⏮/⏭ buttons,
  a row tap) and on controller connect, the list scrolls just
  enough that the current row and the next one both show —
  scrolling up as playback advances, or back down after skipping
  to an earlier item. The gold name highlight on the current row
  is unchanged; the two-second auto-transition breather is
  unchanged (Luddy's separate call on that is still open). APK:
  5,523,094 bytes, SHA-256
  `f4c000556bc481c25d4890dc8d2f330c0aaa1690f4bc296cf8bb9eb70d1600cc`,
  same signing key; published to both version-JSON keys and
  verified live 2026-11-06 (live download hashes identically;
  software page shows 1.24).
- **1.23 (versionCode 24)** — 2026-11-05. Two player touches at
  Luddy's direction. (1) The AE eagle-A is now the media session
  artwork on every track (MediaMetadata artwork data, bundled logo),
  so Android's own player (lock screen, notification shade) shows
  the logo at the left of the report title and date text; the time
  points stay where the system places them. (2) A two-second
  breather between reports: a listener in PlaybackService pauses
  the player on AUTO item transitions and resumes after 2,000 ms;
  manual skips are never delayed, pressing play during the
  breather starts at once, and a manual skip during the breather
  cancels the hold and plays. APK: 5,522,394 bytes, SHA-256
  `367ca8630cd8dd940eeadd5a233ecc3d41e895a05942c3fe66d48a9b657fb930`,
  same signing key; published to both version-JSON keys and verified
  live 2026-11-05.
- **1.22 (versionCode 23)** — 2026-11-05. Settings menu order at
  Luddy's direction: User, Diagnostics, Background Playback, and
  Check for Update always at the bottom. APK: 5,521,066 bytes,
  SHA-256 `a0bacc6b9dc1443b77524954d002e0bf62ef106136674d6721befefc2bac0c47`,
  same signing key; published to both version-JSON keys and verified
  live 2026-11-05.
- **1.21 (versionCode 22)** — 2026-11-05. THE FLAW FIX (Luddy:
  "sometimes it disappears completely... when you go back in, it
  starts from the beginning, no matter where you were"). Two root
  causes: (1) no last-play point existed anywhere — a killed
  process rebuilt the playlist from track one; (2) ensurePlaylistLoaded
  rebuilt the list on every manifest refresh while showDate had
  already cleared playlistLoadedFor, so its keep-position guard
  never fired. Fixes: a per-day last-play point in SharedPreferences
  ("playstate": mid:<date> / pos:<date>), saved every 5 s while
  playing, on pause, on track change, and in onPause; restore via
  setMediaItems(items, index, pos) with a near-end rule (within 3 s
  of a track's end resumes at the next track); STATE_ENDED clears
  the day's point; ensurePlaylistLoaded now returns untouched when
  the loaded list already matches, and rebuilds restore the live
  position when the current track survives. Also: Settings gains
  "Background Playback" (battery-optimization exemption request;
  manifest gained REQUEST_IGNORE_BATTERY_OPTIMIZATIONS) against
  Samsung killing the player while the screen is dark. Same day,
  at Luddy's request, the ljm3.me/daily WEB player got the same
  memory (localStorage "dailyPlayPoint", build_daily.py). APK:
  5,521,062 bytes, SHA-256
  `c4fa0f658790b47af81abd906ab20207f3976a87d39971a063ba908bf6cf660c`,
  same signing key; published to both version-JSON keys and verified
  live 2026-11-05 (the software page's serve-time version already
  reads 1.21).
- **1.20 (versionCode 21)** — 2026-11-05. Seek placement corrected
  at Luddy's direction ("previously too close to title, now too
  close to buttons"): the spacers are now equal, 15dp above and
  15dp below the seek bar, putting it midway between the title
  line and the buttons. APK: 5,519,454 bytes, SHA-256
  `94cc2bc4e9321c93295446761aa7c42261bcbdad54dfc7a48cdc6b80a74b2869`,
  same signing key; published to both version-JSON keys and verified
  live 2026-11-05. OPEN at publish time: Luddy reports the update
  flow "identifies an update but doesn't execute — I have to
  manually check for updates and then, all installs"; diagnosis
  waits on his Settings → Diagnostics copy-paste (the log carries
  the installUpdate phase trail and any door/session failures).
- **1.19 (versionCode 20)** — 2026-11-05. Two player refinements
  at Luddy's direction: (1) the seek bar moves down to near the
  panel's vertical middle (the 30dp spacer redistributed: 26dp
  above the seek bar, 4dp below); (2) each transport button is
  about 10% smaller (minimum height 48dp → 43dp, text 15sp →
  13.5sp). The seek change was first built as 1.18 (versionCode
  19) but folded into 1.19 unpublished when the button request
  arrived mid-build. APK: 5,519,446 bytes, SHA-256
  `f2be1848e6c9c693542bd2c9c8f75ace028f436453d9ec84aa9f8dc4cd5ed79c`,
  same signing key; published to both version-JSON keys and verified
  live 2026-11-05.
- **1.17 (versionCode 18)** — 2026-11-05. Player height at Luddy's
  direction: the Now Playing panel is about 25% taller — a 30dp
  spacer between the seek bar and the controls, so the title, time,
  and seek bar ride at the top of the panel and the buttons stay at
  the bottom. APK: 5,519,394 bytes, SHA-256
  `e18dc7ff8d88c44933c493397d9f5961e0f005a7b90c05911a64c6c86f941c25`,
  same signing key; published to both version-JSON keys and verified
  live 2026-11-05.
- **1.16 (versionCode 17)** — 2026-11-05. Settings redesign at
  Luddy's direction: Settings is now a button menu — **User**
  (username/password in their own dialog, same save logic),
  **Diagnostics** (the FULL error log — previous and current —
  scrollable, with Copy and Clear; Clear wipes both the log and
  the last-crash file), and **Check for Update**; Sign Out and
  Close in the footer. APK: 5,519,538 bytes, SHA-256
  `f87a727c6c7c01229052fbff3ab962cfaaaec9d9fbcfe464cb73db5ff45abd7b`,
  same signing key; published to both version-JSON keys and verified
  live 2026-11-05.
- **1.15 (versionCode 16)** — 2026-11-05. FIX release. Luddy
  reported the 1.14 update hanging at "Downloading update…" with
  nothing after: 1.13's PackageInstaller session committed but its
  user-action prompt never appeared on his S21, so no install ever
  started. installUpdate is reverted to the classic ACTION_VIEW
  installer screen (proven through 1.12); the marker +
  UpdateRestartReceiver auto-restart stays (it never depended on
  the session). ALSO: Luddy reported "I don't have a diagnostics" —
  he was right: the app still WRITES diag.log (Diag.record/phase)
  but the Settings rewrite had dropped the viewer. 1.15 restores it:
  Settings → Diagnostics shows the log tail in a scrollable dialog
  with Copy and Clear (Diag.logTail/clearLog). Because 1.13's
  in-app install is the broken one, 1.15 reaches his phone via the
  browser link once; in-app updates resume from 1.15. APK:
  5,518,566 bytes, SHA-256
  `7af27faac4cddebb7d94df973c04360a12e9970a7a614b5536b987bf62ca6f9c`,
  same signing key; published to both version-JSON keys and verified
  live 2026-11-05.
- **1.14 (versionCode 15)** — 2026-11-05. Date label format at
  Luddy's direction: the weekday stands alone on the first line and
  "Month DD, YYYY" (zero-padded day) sits on the second — new
  prettyDateStacked(); prettyDate() keeps the one-line form for the
  media metadata. APK: 5,518,682 bytes, SHA-256
  `ef9691c1d9c9d3fc85569424aa575e309224538db1bb9b8f107355484c84a310`,
  same signing key; published to both version-JSON keys and verified
  live 2026-11-05. This is also the first update the 1.13
  auto-restart machinery can act on.
- **1.13 (versionCode 14)** — 2026-11-05. Auto-restart after update,
  at Luddy's request: an update replaces the package and kills the
  process, so installUpdate now (1) writes a "relaunch-after-update"
  marker in filesDir, (2) installs through a PackageInstaller
  session committed with a relaunch activity PendingIntent
  (FLAG_MUTABLE IntentSender) that the installer fires on success,
  and (3) falls back to the classic ACTION_VIEW installer intent if
  the session throws. New UpdateRestartReceiver (manifest,
  exported=false) consumes the marker on ACTION_MY_PACKAGE_REPLACED
  and relaunches MainActivity as a second net. NOTE: the restart
  machinery ships IN 1.13, so the 1.12→1.13 hop itself still closes
  without restarting; self-restart applies from the next update on.
  Whether the S21 permits the background relaunch is untested until
  that next update. APK: 5,518,630 bytes, SHA-256
  `fdad5dd789b84f92f2ceea1f93ebcf0ada63904580bea410d716e34ac5b0898c`,
  same signing key; published to both version-JSON keys and verified
  live 2026-11-05.
- **1.12 (versionCode 13)** — 2026-11-05. Header rebuilt after Luddy
  reported 1.11 moved nothing: the Settings pill shared the title's
  row and set its height, so trimming text padding shifted the text
  by ~2dp. Now the title + version live in their own full-height
  column (MATCH_PARENT against the 56dp logo, CENTER_VERTICAL
  gravity) and the Settings pill rides at the right end of the
  header row — the two-line stack's center is the logo's center by
  construction. APK: 5,517,074 bytes, SHA-256
  `292e92853f667757655f7db89277369c91a5bceab2bda77ec52050680e8f0456`,
  same signing key; published to both version-JSON keys and verified
  live 2026-11-05.
- **1.11 (versionCode 12)** — 2026-11-05. Header polish at Luddy's
  direction: the version sits directly under the title (font padding
  removed from both lines, 2dp gap), and the title + version stack
  is vertically centered on the AE logo (logo 54dp, header row
  center gravity). APK: 5,517,258 bytes, SHA-256
  `ac98168b54a0faab1fae7d0acfa3d2285f1c48d6587dbcd4971c9b1a0302d251`,
  same signing key; published to both version-JSON keys and verified
  live 2026-11-05. Luddy confirmed on-device the same morning that
  the in-app update works end to end (the 1.9 download fix), closing
  the update saga.
- **1.10 (versionCode 11)** — 2026-11-05. Second aesthetic pass, at
  Luddy's direction: (1) the AE eagle-A logo (res/drawable-nodpi/
  ae_logo.png, from ae-logo-a-transparent.png) sits at the left of
  the title + version block at 50dp, sized to those two lines; title
  and version shift right in a column, aligned at their start.
  (2) The day pills are now labeled "Previous" and "Next", fixed at
  an identical 104dp width, and highlight ONLY while pressed — the
  persistent position highlight from 1.8 is removed at his word
  (state_selected dropped from pillButton; showDate no longer sets
  selection). (3) Player: the elapsed/total time moved to the far
  right of the Now Playing title line ( Luddy's preferred option).
  (4) Every transport control (previous, −15s, play, +15s, next) is
  now a bordered button with distinct edges (new controlButton():
  rounded rect, 1dp border, gutters between). APK: 5,517,130 bytes,
  SHA-256
  `f07d5dc88dc07d33cabc984861862ff4441e1cb9945239f78180bbf6ae20c129`,
  same signing key; published to both version-JSON keys and verified
  live 2026-11-05 (live download hashes identically).
- **1.9 (versionCode 10)** — 2026-11-05. The update DOWNLOAD gets
  the two-door treatment the check got in 1.7: downloadUpdate()
  previously fetched the APK only from software.aniello.net — the
  host whose TLS returns garbled bytes on Luddy's network — so on
  his phone the 1.8 check succeeded (offered via the ljm3.me door)
  but the download failed every time ("Update download failed.").
  The download now tries https://ljm3.me/daily/<apk> first, carrying
  his saved sign-in, and falls back to software.aniello.net. Server
  side: the Worker gained a /daily/daily-reports-player.apk route
  behind the dashboard login, serving R2 software/daily-reports-player.apk
  as application/vnd.android.package-archive (deployment
  6d523223bec74d969df9710062716649; unauthenticated check returns
  401; backup src/worker.js.bak-2026-11-05-appapk). Diagnostics:
  checkForUpdate and downloadUpdate now record EACH door's failure
  separately (v1.7/1.8 kept only the last door's error, which hid
  why the ljm3.me door failed in the 2026-11-05 07:20:05 entry), and
  a manual check or failed download shows every door's error.
  Includes the 1.8 pill styling. APK: 5,413,194 bytes, SHA-256
  `9da2c5dcee027901e8ffc0b4fa14fb572a84fd7e772cd98201e49f49195558bb`,
  same signing key; published to both version-JSON keys and verified
  live 2026-11-05 (live download hashes identically). Bootstrap
  note: installs on 1.7/1.8 cannot download 1.9 in-app (their
  download is the broken single door) — Luddy installs 1.9 once from
  the phone browser at software.aniello.net/apps/daily-reports-player.apk
  (the browser reaches that host fine); in-app updates ride the
  ljm3.me door from 1.9 onward.
- **1.8 (versionCode 9)** — 2026-11-05. First aesthetic pass, at
  Luddy's direction: Settings, Previous Day, and Next Day are now
  bordered pills (fully rounded, thin border; pressed or selected
  state thickens the border to gold, warms the fill, and turns the
  text gold — new pillButton() helper with a StateListDrawable). The
  day pills show position: "Previous Day" stays highlighted while
  viewing an earlier day, "Next Day" while viewing the newest day
  (selection set in showDate()). The day buttons were renamed from
  "‹ Prev Day" / "Next Day ›" to "Previous Day" / "Next Day". The
  Version line moved a touch closer to the title (top padding
  removed, -3dp top margin). APK: 5,412,626 bytes, SHA-256
  `634f3acade19f3d0e3a818ecd835bdaf1385bcbcc77d66f419206f9b3a7e79ff`,
  same signing key; published to both version-JSON keys and verified
  live 2026-11-05 (live download hashes identically).
- **1.7 (versionCode 8)** — 2026-11-05. The v1.6 diagnostic dialog
  named the cause on Luddy's phone: "SSLException: Unable to parse
  TLS packet header" — the software.aniello.net request comes back
  with non-TLS bytes on his network path, while the browser reaches
  the same host fine and the app's ljm3.me requests work. The update
  check now has two doors: it asks ljm3.me first — a copy of the
  version file served at /daily/app-version.json behind the dashboard
  login, carrying his saved sign-in (Credentials.authHeader, the same
  header the playlist fetch uses) — and falls back to the direct
  software.aniello.net file. Server side: the version JSON is now
  published to BOTH R2 keys (software/daily-reports-player-version.json
  and daily/app-version.json), and the Worker gained a
  /daily/app-version.json route mirroring /daily/manifest.json
  (deployment b3cf5c2e3cc04f6bb80be618f9ddbb4c; unauthenticated check
  returns the 401 challenge, same as the manifest). APK: 5,411,694
  bytes, SHA-256
  `c7ab300a79a370067f1fed31baa6b6c9f636656afafdae75471da1abb79c703f`,
  same signing key; published and verified live 2026-11-05.
  Luddy confirmed on-device 2026-11-05 that the 1.7 update check
  works — update-check saga closed. Standing publish rule from this
  fix: every future version JSON goes to BOTH R2 keys.
- **Backup-host question (2026-11-05):** an aniello.net mirror of the
  playlist/audio (in case ljm3.me fails) was considered and Luddy
  decided to leave things as they are for now — do not build it or
  re-raise it without his direction. (For the record: both hosts run
  on the same Worker and bucket, so a mirror would only have covered
  a ljm3.me domain/DNS failure anyway.)
- **1.6 (versionCode 7)** — 2026-11-05. Luddy installed 1.5 and its
  manual Check for Updates reported failure — but every server-side
  variation (user agents, HTTP/1.1, IPv6, cache headers) answers 200,
  and the app's manifest fetch to ljm3.me works from the same phone;
  the failure was specific to the app's request to
  software.aniello.net and v1.5 swallowed the exception. In 1.6 the
  update fetch sends a plain app user agent, and every check failure
  is recorded in Diagnostics AND shown for manual checks in a dialog
  carrying the real cause (HTTP code or exception) with a Copy button,
  so one tap gives JMan the exact error. APK: 5,411,558 bytes,
  SHA-256 `30c97b871a3f038b6a546d37cfe1980054361dec55cc8ba4d07fbde16fed262b`,
  same signing key; published and verified live 2026-11-05 (live
  download hashes identically). Awaiting Luddy's retest: if the check
  now succeeds, the user agent was the fix; if it fails again, the
  dialog names the cause.

`build.sh` sets JAVA_HOME/ANDROID_HOME/GRADLE_USER_HOME and forces
IPv4 for the Gradle daemon (the sandbox network corrupts IPv6
loopback handshakes). Publishing is an R2 upload of the APK to
`software/daily-reports-player.apk` plus a
`software/daily-reports-player-version.json` bump — see the estate
Cloudflare tooling (cf.py r2-put).

## Backend plumbing (added 2026-11-04)

- `publish_daily.py` now also uploads `daily-manifest.json` to R2
  key `daily/manifest.json` on every publish.
- The Worker serves `GET https://ljm3.me/daily/manifest.json`
  behind the same dashboard Basic Auth as `/daily` and
  `/daily/audio`, with `Content-Type: application/json` and
  `Cache-Control: no-store`.
