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
