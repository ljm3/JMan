# LJM3 Media Manager (Android app)

The phone companion to the ljm3.me personal media page — Luddy's gated
photo and video slideshow backed by the separate R2 bucket
`ljm3-multimedia`. The bucket IS the catalog (the page reads its live
listing; file name = display title and order, `NNN - Title - Year.ext`),
and this app is the manager for it: browse, reorder, delete, and add,
without touching the Cloudflare dashboard.

- Package: `net.aniello.mediamanager`
- Display name: "LJM3 Media Manager"
- Branding: the round LJM3 photo logo (launcher icon + header),
  generated from `~/workspace/print-format/ljm3-photo.jpg`
- Project: `~/workspace/apps/media-manager/`

## What it does

- **Sign-in**: the estate dashboard credentials (username `luddy` +
  dashboard password), stored on device exactly like the Approve app
  (EncryptedSharedPreferences with a guarded plain fallback — see
  Credentials.kt). The password is never hardcoded.
- **Library**: Photos and Videos tabs, each listing the bucket's items
  in server (page) order with position number, title, size, and a
  thumbnail (the photo itself, or the video's `-poster.jpg`).
  Pull-to-refresh.
- **Reorder**: drag the ≡ handle, then **Save Order** — the server
  renames the NNN key prefixes (two passes through `inbox/` temp keys;
  posters travel with their videos), so the page picks the new order
  up from the bucket alone.
- **Delete**: per item, with a confirmation; a video's poster goes
  with it.
- **Add** (per tab, Add Photo / Add Video → source sheet):
  - *From this phone — Photos/Videos*: system picker (multiple).
  - *From this phone — Files*: document picker (all areas of the
    phone Android exposes).
  - *From Dropbox or a web link*: pastes a URL; the WORKER fetches it
    server-side (Dropbox share links rewritten to direct download),
    so a multi-GB video never passes through the phone.
  - *From a network share (WebDAV)*: file URL + optional basic
    credentials; the phone downloads the file, then uploads it like a
    phone file. (Folder browsing is out of scope for v1.0.)
  - *Guided sources* — Amazon Photos, Facebook, Instagram: these
    providers offer no app API for library pulls, so each source shows
    plain steps (download items / create the provider's
    Download-Your-Information export and unzip it) and a button into
    the Files picker.
  - Every phone/WebDAV item asks Title + Year (prefilled when the
    file name already follows the pattern) and lands as the next
    number. Photos upload in one PUT; videos upload multipart in
    50 MiB parts with progress and server-side abort on failure.
- **Self-update**: the estate two-door pattern (see below).

## Server side (Worker `podcast-media`, added 2026-11-12)

All under `https://ljm3.me/media-admin/*`, in `handleMediaAdmin`:

- `GET /media-admin/list?type=photos|videos` — dashboard-authenticated;
  `[{key, title, size, poster}]` sorted by key.
- `GET /media-admin/file?k=` — dashboard-authenticated file serving
  (Range supported; same semantics as `/media/file`).
- `PUT /media-admin/put`, `POST /media-admin/mp-init|mp-part|mp-complete|mp-abort`
  — the migration's upload routes; they keep accepting the
  `X-MM-Upload-Token` header AND now also accept the dashboard
  sign-in, so the app uses one credential everywhere.
- `POST /media-admin/rename {from, to}` — same-folder stream-copy
  rename; a video's poster follows.
- `POST /media-admin/delete {key}` — deletes the item and its poster.
- `POST /media-admin/reorder {type, keys}` — keys must be exactly the
  current set; renumbers NNN prefixes in two passes through
  `inbox/mm-reorder-<ts>/` temp keys; returns the final key list.
- `POST /media-admin/import-url {url, type, title, year}` — server-side
  fetch (http/https only; Dropbox `dl` rewritten to 1; content-length
  over 5 GB rejected) into the next NNN key. No poster is generated
  for imported videos.

Everything except the upload routes is dashboard-auth ONLY (the
migration token deliberately does not open them).

## Signing

- Keystore: `signing/media-manager.jks`, alias `mediamanager`
  (mode 600; passwords in `signing/signing.properties` alongside).
  Every update MUST be signed with this keystore.
- Signer certificate SHA-256:
  `932ec7b1e9d80d519e0f02893e3383b7e65d14e5296618680a804f914eb96f88`
- NEVER commit the keystore or signing.properties to GitHub.

## Build and publish

```
cd ~/workspace/apps/media-manager
~/workspace/apps/toolchain/build.sh :app:assembleRelease
# APK lands in app/build/outputs/apk/release/app-release.apk
```

Publish (the estate pattern): copy the APK to `release/media-manager.apk`,
write `release/media-manager-version.json`
(`{"versionCode": N, "versionName": "X.Y", "apk": "media-manager.apk", "notes": "…"}`),
then upload with cf.py r2-put (account 03f794f8b95de686660d491bad4b2975):

- APK → R2 keys `software/media-manager.apk` AND `mediamanager/media-manager.apk`
- version JSON → R2 keys `software/media-manager-version.json` AND `mediamanager/app-version.json`

Verify by downloading https://software.aniello.net/apps/media-manager.apk
and comparing SHA-256 with the build.

## Self-update

Two doors, ljm3.me first (behind the saved dashboard sign-in), the
public software site second — checked at launch and on resume
(throttled to one automatic check a minute), with a manual
"Check for Updates" button:

- Version JSON: `https://ljm3.me/mediamanager/app-version.json`
  (Worker route, dashboard-gated) →
  `https://software.aniello.net/apps/media-manager-version.json`
- APK: `https://ljm3.me/mediamanager/media-manager.apk` →
  `https://software.aniello.net/apps/media-manager.apk`

## Release history

### v1.1 (versionCode 2) — 2026-11-12
- **Step-wise reorder replaces the bulk reorder.** v1.0's Save Order
  called the server's /media-admin/reorder, which copied every changed
  item twice inside one request; on multi-GB videos the request died
  partway and stranded `videos/007 - LJM3 Teeners Championship -
  1986.mp4` in a temp key. Save Order now plans the permutation on the
  phone and executes it as a sequence of single-item
  /media-admin/rename calls — direct moves whose target key is free,
  plus one parked item per permutation cycle at
  `inbox/mm-reorder-<yyyyMMddHHmmss>/<n><ext>` (the endpoint now permits
  that reserved prefix; posters follow server-side). A non-cancelable
  progress dialog ("Moving item N of M — do not close the app") runs
  during the save and the list is disabled. If a step fails, the parked
  item is restored best-effort (original key, else its final key), the
  error names the item, and the list reloads from the server — a partly
  applied order is acceptable, a lost item is not. Worker companion
  changes (deployments 03ca1ffa… / 772dd3e8…): rename accepts the temp
  prefix, and bulk /media-admin/reorder now refuses videos outright
  (photos keep the bulk path).
- **Stranded-item recovery:** the failure proved larger than first
  reported — two failed v1.0 bulk attempts ~67 s apart stranded FIVE
  videos in temp keys (one attempt had already landed the first five
  positions of the dragged order before dying mid-pass). All five were
  restored the same evening to the intended positions — 001 LJM3
  Teeners Championship - 1986, 006 LJM3 Graduation - 1990, 007 LJM3
  Accident - 1993, 008 Holidays - 1995, 009 Scott Wedding - 1996,
  posters included — via server-side renames through the extended
  rename endpoint, every size verified byte-exact against the
  migration fingerprints, and the one leftover duplicate temp copy
  deleted. Bucket confirmed whole: videos 001–012 with posters
  (012+ still arriving via the Dropbox migration), photos untouched,
  no mm-reorder leftovers.
- APK: 3,079,499 bytes, SHA-256
  `2c5ecde6a3c8a68ba3c5319e1bcc03ed2477d4bff272748d3eb92af1aa4824f9`.
  Badging verified (net.aniello.mediamanager / 2 / "1.1" / label
  "LJM3 Media Manager"); signer is the same media-manager keystore
  certificate as v1.0; the public download is byte-identical (SHA-256
  match) and the Software page tile shows Version 1.1.
- Nothing else changed from v1.0.

### v1.0 (versionCode 1) — 2026-11-12
- First release: library browse (Photos/Videos), drag reorder with
  Save Order, delete with poster cleanup, add from phone pickers,
  Dropbox/web-link server-side import, WebDAV relay import, guided
  Amazon Photos / Facebook / Instagram export paths, dashboard
  sign-in, two-door self-update.
- APK: 3,078,067 bytes, SHA-256
  `d81e40f8e6166df9c01b36a6589cdcd8aea7f5598a1d9e74225fab1ec113645e`.
  Badging verified (net.aniello.mediamanager / 1 / "1.0" / label
  "LJM3 Media Manager"); signer verified against the media-manager
  keystore; the public download is byte-identical (SHA-256 match).
- Tile live on software.aniello.net with serve-time version display.
- NOT yet verified on a device: the signed-in flows (list, thumbnails,
  reorder, uploads) need Luddy's dashboard sign-in, which the build
  sandbox cannot exercise (standing Basic Auth limitation). The
  GitHub snapshot follows Luddy's on-device verification, per the
  standing rule — nothing has been pushed.

## Stage 2 (not in v1.0)

- OAuth source connections for Dropbox, Google Drive, OneDrive and
  Google Photos (provider app registrations with Luddy, tokens held
  server-side) so those libraries can be browsed in-app instead of
  via share links.

## v1.2 (versionCode 3) — November 13, 2026
Luddy-directed layout changes: the header Sign Out button became an **Options** button opening a roll-out panel (User: <name> status line, Log Out, Refresh, Check For Updates); the two footer buttons were removed and the status text now lives in the footer, always prefixed "Status: " (all status writes route through setStatus()); the actions row is per-tab — Add Photo only on the Photos tab, Add Video only on the Videos tab, with **Rename Photo / Rename Video** in the middle and Save Order last; tapping a row selects it (gold outline) and enables Rename, which edits only the title portion of the file name (number, year, extension, and order position are preserved; the poster follows on the server rename path). All buttons use rounded-corner backgrounds (roundedBg helper; tabs included). APK SHA-256 eed4d2f500fa890671ebd1374b83ee19e0098e2f48421dfec1a411a72cebe34e, 3,082,539 bytes, same mediamanager keystore; published to both R2 APK keys and both version JSON keys; public download hash-verified identical. Awaiting Luddy on-device verification; GitHub mirror still follows verification.

## v1.3 (versionCode 4) — November 13, 2026
Luddy adjustment: the footer status is now **per tab**. setStatus(s, type) stores each message in statusByType and only paints the visible tab; switching tabs repaints that tab's own last status (showStatusForCurrentTab in updateTabs). Load/save/rename/upload messages are bound to the tab they belong to, so background work on the other tab no longer overwrites what you are looking at. APK SHA-256 bb001a87ced416a327ab10e46c0e250ef2aa16b5a4930f578c7d8648cb2b1a92, 3,082,599 bytes, same key; published to both R2 keys + version JSONs; public download hash-verified identical.

## v1.4 (versionCode 5) — November 13, 2026
Luddy directed a two-part fix for videos arriving without posters (the server-side import-url route cannot process video, so link imports got none, and dashboard uploads never got one either): (1) this app now makes the poster at import time, and (2) a bucket poster sweep on the server side backstops everything else. App side: after a video upload completes (gallery/Files/WebDAV all funnel through uploadStagedFile), posterFromFile grabs a frame at 5s (fallback: first frame) with MediaMetadataRetriever; after a link import, posterFromUrl attempts the same straight from the source link (Dropbox links normalized dl=0→dl=1, mirroring the server). Frames are scaled to at most 640 px on the long edge and JPEG-encoded at quality 80 (the migration posters' shape), then uploaded with the new Api.putBytes to the video's poster key (MediaItem.posterKey); the Worker's /media-admin/put accepts the key as-is (mmValidKey videos/ + mmContentType .jpg→image/jpeg), so no Worker change was needed. Both paths are best-effort — a failure is logged to Diag and only changes the toast ("· poster added" vs "· poster comes from the sweep"), never the import itself. Backups: MainActivity.kt.bak-2026-11-13-v14, Api.kt.bak-2026-11-13-v14. APK SHA-256 ae793f8732f0da85464f4989b472eeb804889bd24ce881bf60e3f76579862c10, 3,084,471 bytes, same mediamanager keystore (signer verified); published to both R2 APK keys and both version JSON keys; public download hash-verified byte-identical. Awaiting Luddy on-device verification; its GitHub mirror follows that verification.

## v1.5 (versionCode 6) — November 13, 2026
Luddy: "Please add the version number in same format as others to Media Manager app." Added the Daily Reports Player / Approve Player version line to the header: the title now sits in a vertical column with "Version:  <versionName>" under it at 12sp in the SUB color, read from the package manager at runtime so it can never drift from the installed build (the other two apps' exact construction, including the two spaces after the colon). Backup: MainActivity.kt.bak-2026-11-13-v15. APK SHA-256 5f94fb00eaaac8063f84ee9eb1a212cc6b54e802f4aadf15fb16aacf81649b6c, 3,084,571 bytes, same mediamanager keystore (signer verified); published to both R2 APK keys and both version JSON keys; public download hash-verified byte-identical. Awaiting Luddy on-device verification; its GitHub mirror follows that verification (the mirror currently stands at v1.3; v1.4 and v1.5 source snapshots both follow his word).

## v1.6 (versionCode 7) — November 14, 2026
Stage 2, first provider: **Browse my Dropbox**. The Dropbox side was set up the same night: a Dropbox developer app "LJM3 Media Manager" (app key 64up0fsfachzk8v, scoped access, Full Dropbox; permissions account_info.read, files.metadata.read, files.content.read; redirect URI https://ljm3.me/oauth/dropbox/callback) in Luddy's own Dropbox account. The Worker holds everything server-side in nl_config (dropbox_app_key / dropbox_app_secret via the one-time /dropbox-setup page; dropbox_refresh_token + dropbox_account_email from the /oauth/dropbox flow, connected as ljm3@hotmail.com). New Worker routes (dashboard auth; the migration upload token also accepted, same precedent as the upload routes): GET /media-admin/dropbox/list (list_folder with cursor paging, folders first) and POST /media-admin/dropbox/import (content download streamed into the bucket under the next NNN - Title - Year name; extension validated per type; 5 GB cap). Both were live-tested from the sandbox with the upload token before the app shipped: root listing returned his real folders, and an 11,258-byte screenshot import landed byte-exact as photos/014 and was deleted again, leaving the library untouched. App side: "Browse my Dropbox" is choice 3 on the Add sheet; the browser dialog walks folders (folders first, files filtered to the tab's media types with sizes, an Up row below the root), and picking a file asks Title + Year (prefilled from the file name) before Api.dropboxImport; the copy is server-side, so the file never passes through the phone, and a video's poster comes from the bucket sweep. The old "From Dropbox or a web link" share-link path is unchanged. Backups: worker.js.bak-2026-11-14-dropbox, worker.js.bak-2026-11-14-dbxapi. APK SHA-256 96e5b04b1c0b4c5c18835f2a3e40e574b0835bfab1bec06373f4be41612fe17f, 3,089,211 bytes, same mediamanager keystore (signer verified); published to both R2 APK keys and both version JSON keys; public download hash-verified byte-identical. Awaiting Luddy on-device verification; the GitHub mirror (standing at v1.3) takes the v1.4 + v1.5 + v1.6 source snapshot after his verification. Still ahead in Stage 2: Google Drive, OneDrive, Google Photos — same pattern each.

## v1.7 (versionCode 8) — November 14, 2026
- Unified import menu (Luddy's spec): one common list on both tabs, same ten entries in the same order — From Device - Photo/Video (first entry follows the tab), From Device - Files, From Web Link, From Network Share, From Dropbox, From Google Drive, From Microsoft OneDrive, From Amazon Photo, From Facebook, From Instagram. Google Drive and Microsoft OneDrive are listed but not yet connected; tapping one explains that. The WebDAV dialog title now reads "From Network Share".
- Place memory: the app stores the last browsed folder per source (SharedPreferences "nav_state") and resumes there for the whole session and up to 24 hours; after 24 hours browsing starts at the top level. If a remembered Dropbox folder no longer exists, the app forgets it and opens the top level instead of failing.
- APK SHA-256 d5f425773166db625365409ab4b40779a5c9cf93f8d79ca0b37e67dabf4b9610, 3,090,219 bytes, signer 932ec7b1… (mediamanager keystore). Published to both R2 APK keys (etag 1c3edc7c3e33889bd2e82b6bf920dabc) and both version JSON keys (etag f8c2b69a374f96fc1c2789c746258542); public download hash-verified byte-identical.

## v1.8 (versionCode 9) — November 14, 2026
- Place memory extended to every import function (Luddy: "That memory should be the case from any of those functions" — and from any device, a computer included, even though no desktop app exists yet; the principle is recorded as a standing design rule for every client). Device pickers: the folder of the last pick is stored (nav_state) and handed to the next picker as EXTRA_INITIAL_URI, best effort (media-store picks expose no folder and store nothing). Network Share: the dialog prefills the last-used share address. Same 24-hour window as Dropbox throughout.
- APK SHA-256 cc57af4ba70ea11fe809838ae1185723fc263a4c14065fde8246ad0f4fb52a23, 3,090,703 bytes, signer 932ec7b1… (mediamanager keystore). Published to both R2 APK keys (etag 1763452094a7c1a0c1de59c4f054a910) and both version JSON keys (etag 2c4fa76b784da07e598764a53045713e); public download hash-verified byte-identical.

## v1.9 (versionCode 10) — November 14, 2026
- Status fix (Luddy's report): the footer status now changes from "Loading..." to the tab's actual finished status once loading completes. Root cause: refresh() recorded the finished status only for the tab on screen, but both tabs load at startup — the background tab kept "Loading..." in its stored status and showed it when switched to. The outcome status (success count line and the failure line alike) is now stored for the tab unconditionally; only the list/swipe painting stays gated on the visible tab.
- APK SHA-256 43e6a057d12f6e61e946499a31e922363b000ca4e47036012376d5a73e82ad2e, 3,090,719 bytes, signer 932ec7b1… (mediamanager keystore). Published to both R2 APK keys (etag ab8619b22c0499f293ad14379b54adc7) and both version JSON keys (etag 93a64f4ec156612a9b74d23f98a70862); public download hash-verified byte-identical.
