# Getting your API keys

> **Do I need keys at all?** Not for UPS and USPS — by default (*Automatic*), carriers without keys are
> looked up on their websites. **FedEx does need keys**, because fedex.com blocks automated browsers.
> Keys are still worth adding for UPS/USPS if you want faster, sturdier lookups.

For API lookups, Tracking Check talks to each carrier's **official** tracking API. Every carrier requires a
free developer account and an "app" that gives you a **Client ID** and **Client Secret**.
Paste them into **Settings / API keys…** and press **Test connection** on each tab.

Keys are saved to **Windows Credential Manager** (not to the app folder, and never to git).

> Carrier portals change their screens from time to time. If a label below doesn't match
> exactly, look for the equivalent — the things you need are always "Client ID/API key",
> "Client Secret/Secret key", and (for signature proof of delivery) your account number.

---

## UPS

1. Go to **https://developer.ups.com** and sign in with (or create) your ups.com login.
2. **Apps → Add Apps** (or "Create Application").
   - Choose *"I want to integrate UPS technology into my business"*.
   - Link your **UPS shipper account number** when asked (this is what unlocks signature
     images and the POD letter for packages shipped on that account).
3. On the product list add **Tracking**.
4. Open the app → copy **Client ID** and **Client Secret**.
5. In Tracking Check → Settings → **UPS**: paste both, and put your 6-character
   **shipper number** in *Account / shipper number*. Environment = **production**.

What you get: full scan history, delivered date/time, "left at" location, signer name,
and — for packages shipped on your linked account — the **signature image** and UPS's
**Proof of Delivery letter**. For packages shipped by someone else, UPS returns the status
and history but withholds the signature.

## FedEx

1. Go to **https://developer.fedex.com** → **Sign Up / Log In** with your fedex.com user.
2. **My Projects → Create API Project**. Choose **Track API**.
3. Associate your **FedEx account number** when prompted (needed for signature proof of delivery).
4. The project starts with **Test (sandbox)** keys. Request/confirm **Production** keys on the
   project page (usually instant for Track API).
5. Copy the **API Key** (= Client ID) and **Secret Key** (= Client Secret).
6. In Tracking Check → Settings → **FedEx**: paste both, enter the 9-digit account number,
   Environment = **production** (use **sandbox** only with the test keys — sandbox returns
   canned sample data, not your shipments).

What you get: full scan history, delivered date/time, received-by name, delivery location,
attempts, and the official **Signature Proof of Delivery (SPOD) PDF** when FedEx releases it
to your account.

## USPS

1. Go to **https://developers.usps.com** → **Get Started** and create a USPS Business
   (Customer Onboarding Portal) account.
2. **Apps → Add App**. Enable the **Tracking** API.
3. Copy the **Consumer Key** (= Client ID) and **Consumer Secret** (= Client Secret).
4. In Tracking Check → Settings → **USPS**: paste both. Environment = **production**.

Important limits:
- **Quota:** new USPS apps are limited to about **60 requests per hour**. The app ships set to
  **1 request per minute** for USPS so it never trips the limit — a sheet with 120 USPS
  numbers therefore takes about 2 hours. Ask USPS (through the developer portal support
  form) for a quota increase, then raise *Max requests per minute* in Settings.
- **Proof of delivery:** the USPS API returns the delivery scan (and the recipient's name when
  one was captured) but **not the signature image**. USPS only issues signature proof through
  its Return Receipt / Proof of Delivery letter services. Tracking Check therefore builds a
  POD summary page for USPS deliveries (date, time, location, name, full history).
- Tip: turn on *"Re-use results for packages already delivered on an earlier run"* (default)
  so delivered USPS packages are not re-queried and don't use quota.

## Claude (optional – for the "anything specific to check?" box)

1. **https://console.anthropic.com** → sign in → **API keys → Create key**.
2. Settings → **Claude**: paste the key. Model defaults to `claude-opus-5`.

With a key, Claude interprets any free-form request ("flag anything signed for by someone
other than the recipient", "which ones missed their delivery window?") and adds a column
per item. Without a key, built-in rules handle common requests offline and tell you which
parts they didn't understand. Typical cost: a few cents per run.

## Google Sheets (only if you use Google Sheets)

Pick **one** of the two methods.

### A. Your own Google account (recommended)
1. **https://console.cloud.google.com** → create a project (any name).
2. **APIs & Services → Library** → enable **Google Sheets API** and **Google Drive API**.
3. **APIs & Services → OAuth consent screen** → *External* → fill in the app name and your
   e-mail → add yourself under **Test users**.
4. **Credentials → Create credentials → OAuth client ID → Desktop app** → **Download JSON**.
5. Settings → **Google Sheets**: *My Google account*, choose that JSON file.
   The first run opens your browser once to sign in; the token is cached afterwards.

### B. Service account
1. Same project, **Credentials → Create credentials → Service account** → **Keys → Add key → JSON**.
2. Open your spreadsheet → **Share** → add the service account's e-mail as **Editor**.
3. Settings → **Google Sheets**: *Service account*, choose the key JSON.
4. Service accounts have no Drive storage of their own: to upload POD files, create a folder in a
   **Shared Drive**, add the service account to it, and paste the folder's ID (the last part of
   its URL) into *Drive folder ID*. Otherwise POD files stay on your PC and the sheet lists their paths.

Keep these JSON files **outside** the tracking-checker folder (e.g. in Documents). The
`.gitignore` also blocks `client_secret*.json`, `credentials*.json` and `*token*.json`.
