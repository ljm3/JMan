# Tracking Check

Mass-check **UPS, FedEx and USPS** tracking numbers from an **Excel** workbook or a **Google Sheet**.
The app identifies each number's carrier, gets its status and **complete tracking history** — either from
the **carrier's website** (no keys needed; the default) or the carrier's **official API** — saves **proof of
delivery** for delivered packages, and writes everything to
a new **Tracking Status** tab with one row per tracking number and one column per data point.
Every tracking number on your original tab then becomes a link to its row of results.

## What it does

1. **Pick the spreadsheet** (Browse for an `.xlsx`/`.xlsm`, or paste a Google Sheets link).
2. **It finds the tracking numbers.** Every column is scanned for tracking-number patterns; the best
   column is pre-selected and a preview shows e.g. *"142 unique tracking numbers: UPS 80 / FedEx 50 /
   USPS 12"*. Change the tab/column if needed, or choose *scan every column*.
3. **It identifies the carrier** from the number format **and the carrier's check-digit math**
   (UPS 1Z…, FedEx 12/15/22/34-digit, USPS IMpb 20–22-digit, `420+ZIP` prefixes, certified mail,
   S10 international). A "Carrier" / "Ship Via" column in your sheet is used as a hint. Numbers
   whose format is shared (e.g. `92…` FedEx Ground Economy parcels delivered by USPS) are tried
   with the next carrier automatically if the first says "not found".
4. **It looks up each number** — per carrier, Settings chooses the method:
   - **Automatic** (default): the official API when that carrier's keys are set, otherwise the website.
   - **Website**: the carrier's public tracking page in an Edge window, at a human pace (default one
     lookup every 4 s). The app reads the same data the page shows. If a site shows an "are you human"
     check, the run pauses and asks you to complete it in the window.
   - **API**: rate-limited, retried on errors, FedEx in batches of 30.
5. **Proof of delivery** for delivered packages goes into a `<workbook name> - POD` folder next
   to the workbook (Google Sheets: uploaded to a Drive folder): the carrier's own POD document
   when available (FedEx Signature Proof of Delivery PDF, UPS POD letter), the signature image /
   delivery photo, and a one-page **POD summary** with the signature embedded.
6. **"Anything specific to check?"** – type a plain-English request; each item becomes an extra
   column, plus *Matches Your Check* (Yes/No) and *Check Notes*. With an Anthropic API key,
   Claude interprets anything; without one, built-in rules handle common requests offline.
7. **Writes the results** to the *Tracking Status* tab (re-running replaces it), links each
   original tracking-number cell to its status row, and links each status row back.

### Columns on the Tracking Status tab

Tracking Number (→ original row) · Carrier · Identified By · Service · Status · Status Detail ·
Delivered? · Delivered Date/Time · Signed By · Left At / Delivery Location · Delivery Address ·
Ship Date · Origin · Destination · Estimated Delivery · Last Event · Last Event Date/Time ·
Last Event Location · Days in Transit · Delivery Attempts · Exception / Alert · Weight ·
Event Count · **Full Tracking History** (every scan, newest first) · **Proof of Delivery** (link) ·
POD Type · Other POD Files · **Carrier Tracking Page** (link) · Source Location · Data Source ·
Checked At · Error / Notes · *your extra-check columns* · Matches Your Check · Check Notes

Status is colour-coded (green delivered, blue out for delivery, yellow in transit, red
exception/returned, orange not found/error). The header of *Matches Your Check* carries a note
with your request and how it was interpreted.

### Built-in check phrases (no API key needed)

`not delivered yet` · `delivered after 9/1/2026` · `delivered before/by/on …` ·
`delivered between Sept 1 and Sept 5` · `delivered in the last 7 days` · `shipped before …` ·
`no update in 3 days` / `stuck` · `in transit more than 5 days` · `late` / `missed estimate` ·
`exception` / `delay` · `signed by Smith` · `no signature` · `delivered to Texas` / `to Dallas` ·
`left at the front door` · `returned to sender` · `more than 1 attempt` · `weekend delivery` ·
`out for delivery` · `weighs more than 20 lbs` · `POD missing` · `not found` / `errors` ·
`only FedEx` · `service is overnight`. Anything else is reported back as "not understood".

## Install

```powershell
cd C:\Users\<you>\.local\bin\tracking-checker
.\setup.ps1          # Python 3.12 venv + dependencies + Start Menu/Desktop shortcut + self-test
.\run.ps1            # or use the "Tracking Check" shortcut
```

UPS and USPS work straight away through their websites. For **signatures**, press *Sign in to UPS /
FedEx…* once and sign in with the accounts you ship on. For **FedEx**, add the free API keys
(Settings → FedEx; guide: [docs/CARRIER_API_SETUP.md](docs/CARRIER_API_SETUP.md)).
*Demo mode* (fake, clearly-labelled data) is there to try the app without touching any carrier.

## Command line

```powershell
$py = ".\.venv\Scripts\python"
& $py -m tracking_checker --cli --file "C:\Shipping\Sept.xlsx" --check "not delivered yet; no update in 3 days"
& $py -m tracking_checker --cli --gsheet "https://docs.google.com/spreadsheets/d/…/edit" --sheet Orders --column "Tracking #"
& $py -m tracking_checker --detect 1Z999AA10123456784 123456789012   # which carrier?
& $py -m tracking_checker --doctor                                  # keys / libraries / settings
& $py -m tracking_checker --selftest                                # offline end-to-end demo run
& $py -m tracking_checker --make-sample sample.xlsx                 # a workbook to play with
```

## Safety and data

- **API keys** live in Windows Credential Manager (`keyring`); other settings in
  `%APPDATA%\TrackingChecker\config.json`. Nothing secret is stored in this folder.
- **Your workbook is backed up** before every save to `%USERPROFILE%\TrackingChecker\backups`.
  Close the workbook in Excel before running (the app tells you if it's open).
- The Excel library (openpyxl) keeps formulas, formatting, images, charts, comments and tables,
  but can drop slicers, form controls, ActiveX and drawn shapes; the app warns when it sees them.
- Excel stores numbers with only 15 significant digits. If a 20–22-digit USPS number was typed
  into a *number*-formatted cell, digits are already lost — the app flags those cells instead of
  guessing. Format tracking columns as **Text**.
- Logs of every run: `%USERPROFILE%\TrackingChecker\logs`. Delivered results are cached in
  `…\cache\delivered.json` so re-runs don't spend API quota on finished packages.

## Carrier notes

**Website lookups** (no keys) — verified live on 2026-09-19:

| | Works? | What you get | Proof of delivery |
|---|---|---|---|
| **UPS** | yes | status, every scan, service, received-by, delivered-to | PDF capture of the ups.com tracking page; **signed in** (button *Sign in to UPS / FedEx*): signature image + UPS POD letter for your own shipments |
| **USPS** | yes | status, every scan, product, delivery location | PDF capture of the USPS page (USPS never shows signatures online) |
| **FedEx** | **no — fedex.com blocks automated browsers** | — | add the free FedEx API keys; with *Automatic*, FedEx then uses the API |

Websites change without notice. When a page can't be read, the app saves a debug snapshot (HTML,
screenshot, data) in `%USERPROFILE%\TrackingChecker\logs\web-debug` so the reader can be fixed.
Automated use may be restricted by the carriers' website terms; the API route is the sanctioned one.

**API lookups:**

| | Status + full history | Signer / location | Signature image / carrier POD | Limits |
|---|---|---|---|---|
| **UPS** | yes | yes | signature + POD letter for shipments on your linked shipper account | generous |
| **FedEx** | yes | yes | SPOD PDF when released to your account | 30 numbers per request |
| **USPS** | yes | name when captured | **not available via API** (summary page instead) | ~60 requests/hour until USPS raises it |

## Development

```powershell
.\setup.ps1 -Dev
.\.venv\Scripts\python -m pytest -q
```

Layout: `tracking_checker/detect.py` (carrier identification), `carriers/` (UPS, FedEx, USPS API
clients, demo), `carriers/web/` (Edge browser worker + UPS/USPS/FedEx website readers), `sheets/` (Excel + Google adapters, column detection), `pod.py`, `checks/` (rules +
Claude), `report.py` (columns), `pipeline.py`, `gui.py`, `settings_dialog.py`.
