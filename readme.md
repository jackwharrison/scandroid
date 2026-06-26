# 121 Scan — Scandroid

**Offline-capable QR code scanning and payment verification app for humanitarian cash transfer programmes.**

Built by [510](https://www.510.global/) @ The Netherlands Red Cross, 121 Scan lets field-based Financial Service Providers (FSPs) verify the identity of crisis-affected people and record payment decisions — even with no internet connection. It bridges two external platforms: [121](https://www.121.global/) (the cash transfer management system) and [Kobo Toolbox](https://www.kobotoolbox.org/) (registration and data collection).

> **⚠️ Status — temporary tool, limited rollout.** 121 Scan is a temporary Flask-based workaround currently used in a single National Society pilot. The functionality is planned for integration into the core 121 product roadmap, so it should **not** be scaled widely. See [Responsible Use](#responsible-use) before deploying to a new context.

---

## What it does

- **Beneficiary card / voucher generation** — produces printable QR code vouchers using fields stored in 121.
- **Offline validation** — lets FSPs validate beneficiaries offline and syncs the results back to 121 once connectivity returns.
- **FSP-site verification** — staff scan (or manually enter) a beneficiary reference ID to verify eligibility and update the payment status in 121 during cash collection.

---

## How it works (high level)

```
┌─────────────────────────────────────────────────┐
│                   Flask (app.py)                 │
│  Admin routes   │  FSP routes   │  API routes    │
└────────┬────────┴───────┬───────┴───────┬────────┘
         │                │               │
   121 Platform      IndexedDB       Kobo Toolbox
   (auth, txns,      (encrypted      (registration
    payments)         offline cache)  photos)
```

121 Scan sits between 121 and Kobo. An admin connects the deployment to both platforms and configures programmes; FSPs then sync beneficiary data to their device, scan vouchers offline, and submit payment outcomes back to 121 when they regain connectivity. All personal data is encrypted with Fernet before it ever leaves the server, and stays encrypted at rest in the browser's IndexedDB.

---

## Technology stack

**Backend (Python / Flask)**
- **Flask** — serves all routes, handles authentication, processes API calls, and generates files
- **Flask-Session** — server-side session management (filesystem)
- **ReportLab** — voucher PDF generation (landscape A5, dynamic QR codes and fields)
- **qrcode** — QR code image generation for voucher PDFs
- **openpyxl / xlrd** — reading uploaded `.xlsx` / `.xls` files for voucher generation
- **cryptography (Fernet)** — symmetric encryption of all sensitive personal data before it is written to disk or sent to the browser
- **requests** — outbound HTTP calls to the 121 and Kobo APIs
- **concurrent.futures (ThreadPoolExecutor)** — parallel fetching of registrations and photos during sync (default 8 workers, configurable via `OFFLINE_SYNC_WORKERS`)

**Frontend**
- **Jinja2** — server-rendered HTML templates
- **Vanilla JavaScript** — QR scanning, IndexedDB management, online/offline detection, payment submission
- **jsQR** (via CDN) — in-browser camera QR decoding
- **IndexedDB** (via the `idb` library) — local store for encrypted records, payment outcomes, and transactions
- **Service Worker** — caching strategy and offline availability of the app shell and key routes

**Configuration**
- **JSON config files** — `system_config.json` (API credentials, programme mappings, encryption key) and `display_config.json` (per-programme field and photo display settings)
- **Environment variables** — `SCANDROID_ENV` (`local` vs `azure`) and `SCANDROID_CONTEXT` select which config folder is loaded, allowing multiple deployment contexts on one instance

---

## Project structure

```
.
├── app.py                  # Flask application — all routes, translations, voucher + payment logic
├── offline_sync.py         # Subprocess script: syncs + encrypts beneficiary data from 121 + Kobo
├── config_loader.py        # Loads system_config.json and display_config.json for the active context
├── service-worker.js       # PWA service worker (caching + offline)
├── system_config.json      # Runtime config (API URLs, keys, programme IDs)
├── display_config.json     # Per-programme field + photo display config
│
├── templates/
│   ├── home.html                 # Landing page (role selector)
│   ├── admin_login.html          # Red Cross staff login
│   ├── admin_dashboard.html      # Admin dashboard
│   ├── admin_base.html
│   ├── system_config.html        # Admin: system configuration editor
│   ├── config.html               # Admin: display field configuration editor
│   ├── fsp_login.html            # FSP login
│   ├── fsp_programs.html         # FSP programme selector
│   ├── fsp_admin.html            # FSP dashboard (sync / scan / send)
│   ├── scan.html                 # QR scanner
│   ├── beneficiary_offline.html  # Offline beneficiary detail + approve/reject
│   ├── success_offline.html      # Confirmation screen
│   ├── invalid-qr.html           # Invalid / already-used QR message
│   └── vouchers.html             # Voucher PDF generator
│
└── static/
    ├── scandroid.png
    ├── scandroid_banner.png
    ├── ns1.png             # National Society logo (left)
    ├── ns2.png             # Partner organisation logo (right)
    └── favicon.ico
```

---

## User flows

### FSP (field agent)

1. **Log in** at `/fsp-login` with an email and the password issued by the programme admin.
2. **Select a programme** from the list assigned to the deployment (e.g. *Cash On The Move*).
3. **Dashboard** shows three indicators for the selected programme:
   - *Ready to scan* — records downloaded to the device and ready for offline verification (with last-sync time)
   - *People scanned* — people processed this session and the total payment value in local currency
   - *Payments ready to send* — approved payments queued for submission (with last-submission time)
4. **Sync** — before going offline, tap **Sync** to download the latest records. From then on no internet is needed for scanning.
5. **Scan** — tap **Scan** to open the camera and read the beneficiary's QR voucher automatically. If the code is damaged, the reference ID can be entered manually.
6. **Verify & decide** — the app shows the person's photo, name, and any admin-configured fields. The FSP confirms identity, then taps **Payment Approved** or **Payment Rejected**.
7. **Confirm / next** — a success screen updates the pending-submission counter; the FSP can scan the next person or return to the dashboard.
8. **Send** — once back online, tap **Send** to transmit all queued payment outcomes to 121 in one operation. The queue is cleared on success.

Invalid, already-used, or wrong-programme QR codes produce a clear error message so the FSP can move on.

### Red Cross staff (admin)

1. **Log in** at `/admin-login` using 121 platform credentials (a separate entry point from the FSP login).
2. **System Configuration** — connect the deployment to 121 and Kobo: select the Kobo server, enter the Kobo API key, then pair each 121 programme with its Kobo Asset ID. Multiple programmes can be added; FSPs only see the programmes defined here.
3. **Configure Fields to Display** — choose which 121 registration fields FSPs see when scanning, with labels in English, French, and Arabic. A separate *Photo Field Configuration* section enables/disables the photo and names the Kobo field that holds it.
4. **Generate Vouchers** — upload the FSP payment list exported from 121 (CSV or Excel) to produce a printable QR voucher PDF. Every column in the export appears on the voucher, so remove unwanted columns before uploading.

> The FSP payment-list export and its fields are configured **in the 121 platform** during FSP setup, not in 121 Scan.

---

## The sync process

When an FSP starts a sync, the server runs `offline_sync.py` as a subprocess for the selected programme. It:

1. Authenticates with the 121 API.
2. Retrieves all transactions for the programme and filters to eligible records (status `waiting`, not deleted, created within the preceding 14 days).
3. Deduplicates to one record per individual, keeping the most recent transaction.
4. Fetches full registration data for each individual from 121 in parallel (default 8 threads).
5. Extracts only the fields named in `display_config.json` for that programme, plus the matching field needed for payment submission.
6. Encrypts all extracted values with Fernet.
7. Downloads and encrypts each person's photo from Kobo in parallel (medium resolution, to keep sync times reasonable).
8. Saves a numbered batch directory under `offline-cache/` containing `registrations_cache.json`, `transactions.json`, `batch_info.json`, and a `photos/` subdirectory.

The cache is served to the FSP's browser as a ZIP, which is unpacked and stored in IndexedDB.

---

## Encryption & offline operation

All personal data in IndexedDB is protected with Fernet symmetric encryption; the key lives in `system_config.json` on the server. When a QR code is scanned:

1. The browser looks up the scanned UUID in IndexedDB.
2. On a match, the stored data is decrypted client-side using the key bundled with the offline data at sync time.
3. The decrypted details are shown to the FSP for verification.
4. Photos are also stored encrypted and decrypted client-side via the Web Crypto API (AES-CBC with HMAC-SHA-256 verification) before display.

This means a physically compromised device still does not expose readable data without the key.

### PWA caching strategy

The service worker uses a layered strategy:
- **Pre-cached at install** — home page, FSP login, beneficiary display, success page, static assets, and the jsQR library
- **Network-first for navigation** — live pages are fetched fresh when online; cache is the offline fallback
- **Cache-first for static assets** — images, JS, CSS served from cache immediately
- **Always network-only** — `/ping` (connectivity check) and the FSP admin pages bypass the cache entirely
- **Connectivity polling** — pings `/ping` every 60 seconds and requires 3 consecutive failures before switching to offline mode, avoiding false negatives from brief drops

### Payment submission flow

When an FSP taps **Send**:
1. The browser collects all pending payments (status `success`) from IndexedDB for the active programme.
2. It decrypts the stored match value (e.g. phone number) for each record.
3. It builds a CSV with two columns: the configured matching field and the payment status.
4. The CSV is POSTed to `/submit-payments`.
5. The server cross-references each record against the offline cache to resolve the 121 `paymentId` and `registrationId`.
6. It calls the 121 API to update each transaction status.
7. On success, the local IndexedDB payment store is cleared.

---

## Configuration

### `system_config.json`

```json
{
  "KOBO_SERVER": "https://kobo.ifrc.org",
  "KOBO_TOKEN": "<your-kobo-api-token>",
  "PROGRAMS": [
    {
      "programId": 10,
      "koboAssetId": "aAPxCYoyNnTDBhqyoZ28Zv",
      "koboFormName": "My Kobo Form",
      "koboFormOwner": "kobo_username"
    }
  ],
  "COLUMN_TO_MATCH_PER_PROGRAM": {
    "10": "phoneNumber"
  }
}
```

At minimum, a fresh instance needs `url121`, `username121`, and `password121` present in this file for the app to start. The remaining fields (Kobo server/token, encryption key, FSP password, programme mappings) can be filled in afterwards via the admin **System Configuration** screen.

Generate a Fernet key with:

```python
from cryptography.fernet import Fernet
print(Fernet.generate_key().decode())
```

### `display_config.json`

Controls which Kobo/121 fields are shown to FSPs per programme, with per-language labels, and whether to show the beneficiary photo:

```json
{
  "programs": {
    "10": {
      "fields": [
        { "key": "fullName", "label": { "en": "Full Name", "fr": "Nom complet", "ar": "الاسم الكامل" } }
      ],
      "photo": { "enabled": true, "field_name": "photo" }
    }
  }
}
```

---

## External integrations

**121 platform**
- Authenticates against the 121 REST API with the credentials in `system_config.json`, receiving a session token (`access_token_general`).
- During sync, calls the transactions API, filters to `waiting` transactions from the last 14 days, deduplicates per individual, and fetches full registration records in parallel.
- On payment submission, POSTs a CSV of scanned outcomes to `/submit-payments`, which updates the corresponding transaction statuses in 121.

**Kobo**
- Serves as the authoritative source for registration photographs.
- During sync, photo attachments are fetched per individual using the configured asset ID and API token.
- Photos are downloaded at medium resolution and immediately encrypted before being written to disk.
- The Kobo server (e.g. IFRC Kobo) is configurable.

---

## Multilingual support

Full support for English, French, and Arabic (including RTL). All UI strings are managed via a `translations` dictionary in `app.py`, injected into templates through a `lang` URL parameter. FSP-facing field labels are independently configurable per language in `display_config.json`. The voucher PDF renderer uses the DejaVu Sans font so Arabic script and accented characters render correctly.

| Language | Code |
|----------|------|
| English  | `en` |
| French   | `fr` |
| Arabic   | `ar` (RTL) |

---

## Local development

### Prerequisites
- Python 3.11
- pip

### Setup
```bash
git clone <repo-url>
cd scandroid

pip install -r requirements.txt

# Configure the app for local use
cp system_config.json.example system_config.json
# Edit system_config.json with your 121 and Kobo credentials

export SCANDROID_ENV=local
flask run
```

The app runs on `http://localhost:5000` by default.

To run a sync manually:
```bash
PROGRAM_ID=10 python offline_sync.py
```

---

## Deploying a new instance (Azure App Service)

121 Scan is designed for Azure App Service, with a single deployed instance able to serve multiple independent contexts (e.g. separate country operations) selected via `SCANDROID_CONTEXT`. Config files live at `/home/site/configs/{CONTEXT}/`.

1. **Create a Web App** — in the Azure Portal, create a new Web App.
2. **Configure it** — Publish: `Code`; Runtime: `Python 3.11`; OS: `Linux`; pick a subscription, resource group, name, region, and App Service plan.
3. **Set up continuous deployment** — under *Deployment*, enable continuous deployment and connect the GitHub account, organisation, `scandroid` repository, and branch (e.g. `main`). Azure generates a GitHub Actions workflow that redeploys on every push to that branch.
4. **Set environment variables** — under *Settings → Environment Variables → App Settings* add:
   - `SCANDROID_ENV` = `azure`
   - `SCANDROID_CONTEXT` = a short identifier for this deployment (e.g. `chad`), matching the config folder name
   - `PASSWORD_121`
   - `URL_121`
   - `USERNAME_121`
   - `ENCRYPTION_KEY`
   - If application insights are set up `APPLICATIONINSIGHTS_CONNECTION_STRING`
5. **Create the folders** — via SSH, create `/home/site/configs`, then `/home/site/configs/{SCANDROID_CONTEXT}`, then `/home/site/configs/{SCANDROID_CONTEXT}/static`.
6. **Add the system config file** — place `system_config.json` at `/home/site/configs/{SCANDROID_CONTEXT}/system_config.json` (via the App Service File Manager under *Development Tools*, or a deployment pipeline). It must include at least `url121`, `username121`, and `password121`.
7. **Add National Society logos** — upload to `/home/site/configs/{SCANDROID_CONTEXT}/static/`, named exactly `ns1.png` (left, local NS) and `ns2.png` (right, partner org). Both PNG; a missing file leaves that logo position blank.
8. **Finish in the admin panel** — once running, complete the remaining settings (Kobo asset IDs, display fields, FSP password, etc.) through the 121 Scan admin interface.

---

## Responsible use

121 Scan is a **temporary workaround**, not a long-term product, and works well only in specific contexts. Expansion to a new National Society should happen **only** when the functionality cannot be handled effectively by standard 121 or Kobo workflows, and when these minimum operational conditions are met:

- A designated **IM focal point** is responsible for 121 and 121 Scan.
- A clear **CVA process** exists for validation, verification, and reconciliation.
- Devices have **periodic internet access** to sync with 121.
- There is an **agreed process with the FSP** on verification and reconciliation.
- **Red Cross staff are present** at the distribution site during verification.

Each use case should be confirmed within the CVA team before deployment.

### Key risks and mitigations

| Activity | Risk | Mitigation |
|----------|------|------------|
| Card generation | Incorrect template setup | IM focal point configures and tests the template |
| Offline validation | Devices not synced → outdated data | Sync devices before and after fieldwork |
| FSP verification | Mismatch between FSP records and 121 | Define a reconciliation process |
| FSP verification | Connectivity issues during operations | Ensure internet access before deployment |
| FSP verification | No match for a scanned ID | Define a fallback verification process |
| Overall | No clear IM ownership | Assign a dedicated IM focal point |

---

## Support

Developed by **510 @ The Netherlands Red Cross**.
For support, contact: jharrison@redcross.nl
