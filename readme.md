# 121 Scan — Scandroid

**Offline-capable QR code scanning and payment validation app for humanitarian cash programmes.**

Built by [510](https://www.510.global/) @ The Netherlands Red Cross, Scandroid connects field-based Financial Service Providers (FSPs) with the [121 Platform](https://www.121.global/) to validate beneficiary payments — even without an internet connection.

---

## Overview

Scandroid is a Flask-based Progressive Web App (PWA) that allows FSPs to:

1. **Sync** beneficiary records from the 121 platform and Kobo Toolbox before going into the field
2. **Scan** beneficiary QR code vouchers offline using the device camera
3. **Validate** beneficiary identity and eligibility using encrypted local data
4. **Submit** payment decisions back to 121 when connectivity is restored

All beneficiary data is encrypted at rest using [Fernet symmetric encryption](https://cryptography.io/en/latest/fernet/) and stored in the browser's IndexedDB, keeping sensitive information secure on the device.

---

## Key Features

- **Offline-first PWA** — a Service Worker pre-caches the app shell and beneficiary data so scanning works with no internet connection
- **QR code scanning** — camera-based scanning powered by [jsQR](https://github.com/cozmo/jsqr)
- **Fernet encryption** — all beneficiary fields and photos are encrypted before being stored offline
- **Multi-programme support** — admins can configure multiple 121 programmes, each with their own Kobo asset and field mappings
- **Role-based access** — separate login flows for Red Cross admin staff and FSP field agents
- **Multilingual UI** — English, French, and Arabic (including RTL layout)
- **Voucher PDF generation** — upload a CSV/Excel file of beneficiaries to generate printable QR voucher PDFs (A5 landscape)
- **Payment submission** — FSPs generate a payment CSV from scanned decisions; admins submit it directly to the 121 API

---

## Architecture

```
┌─────────────────────────────────────────────────┐
│                   Flask (app.py)                │
│  Admin routes   │  FSP routes   │  API routes   │
└────────┬────────┴───────┬───────┴───────┬───────┘
         │                │               │
   121 Platform      IndexedDB       Kobo Toolbox
   (auth, payments)  (offline cache) (registrations,
                                      photos)
```

**Backend:** Python / Flask  
**Frontend:** Vanilla JS, HTML/CSS (Jinja2 templates)  
**Offline storage:** IndexedDB (records, photos, payments, decisions)  
**Sync script:** `offline_sync.py` — downloads and encrypts beneficiary data server-side into a ZIP served to FSP devices  
**Service Worker:** `service-worker.js` — cache-first for offline routes, network-first for admin/FSP login

---

## Project Structure

```
.
├── app.py                  # Flask application — all routes and business logic
├── offline_sync.py         # CLI script to sync beneficiary data from 121 + Kobo
├── config_loader.py        # Helpers to load system_config.json and display_config.json
├── service-worker.js       # PWA service worker
├── system_config.json      # Runtime configuration (API URLs, keys, programme IDs)
├── display_config.json     # Per-programme field display configuration
│
├── templates/
│   ├── home.html           # Landing page (role selector)
│   ├── admin_login.html    # Red Cross staff login
│   ├── admin_dashboard.html
│   ├── admin_base.html
│   ├── system_config.html  # Admin: system config editor
│   ├── config.html         # Admin: display field config editor
│   ├── fsp_login.html      # FSP login
│   ├── fsp_programs.html   # FSP programme selector
│   ├── fsp_admin.html      # FSP dashboard (sync, scan, submit)
│   ├── scan.html           # QR scanner
│   ├── beneficiary_offline.html  # Offline beneficiary detail + decision
│   ├── success_offline.html
│   ├── invalid-qr.html
│   └── vouchers.html       # Voucher PDF generator
│
└── static/
    ├── scandroid.png
    ├── scandroid_banner.png
    ├── ns1.png             # National Society logo (left)
    ├── ns2.png             # National Society logo (right)
    └── favicon.ico
```

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

Generate a Fernet key with:

```python
from cryptography.fernet import Fernet
print(Fernet.generate_key().decode())
```

### `display_config.json`

Defines which Kobo fields are shown to FSPs per programme, and whether to show the beneficiary photo:

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

## Installation

### Prerequisites

- Python 3.10+
- pip

### Setup

```bash
git clone <repo-url>
cd scandroid

pip install -r requirements.txt

# Configure the app
cp system_config.json.example system_config.json
# Edit system_config.json with your 121 and Kobo credentials

flask run
```

The app runs on `http://localhost:5000` by default.

---

## Offline Sync

The `offline_sync.py` script pulls the latest beneficiary and transaction data from 121 and Kobo, encrypts it, and packages it for FSP devices.

```bash
PROGRAM_ID=10 python offline_sync.py
```

This creates a batch directory under `offline-cache/` containing:
- `registrations_cache.json` — encrypted beneficiary records
- `transactions.json` — payment transactions
- `photos/` — encrypted beneficiary photos
- `batch_info.json` — metadata about the batch

FSP devices download this cache as a ZIP via `/api/offline/latest.zip` during the sync step.

---

## User Flows

### Admin (Red Cross Staff)
1. Log in at `/admin-login` using 121 credentials
2. Configure system settings at `/system-config`
3. Configure display fields at `/config`
4. Generate QR voucher PDFs at `/vouchers`

### FSP (Field Agent)
1. Log in at `/fsp-login` with the FSP password set by the admin
2. Select a programme
3. **Step 1:** Sync — downloads and stores the latest beneficiary cache to IndexedDB
4. **Step 2:** Save latest records to the device
5. **Step 3:** Scan QR codes — camera scans the voucher, validates the beneficiary offline, records a payment decision
6. **Step 4:** Generate a payments CSV from scanned decisions
7. **Step 5:** Submit payments to 121 (requires connectivity)

---

## Deployment

The app is designed for deployment on **Azure App Service** and supports multiple named contexts (e.g. different National Societies) via the `SCANDROID_CONTEXT` environment variable. Instance-specific logos are served from `/home/site/configs/<context>/static/`.

For local development, static files fall back to the `static/` directory.

---

## Supported Languages

| Language | Code |
|----------|------|
| English  | `en` |
| French   | `fr` |
| Arabic   | `ar` (RTL) |

Language is controlled via the `?lang=` query parameter and persisted in the session.

---

## Support

Developed by **510 @ The Netherlands Red Cross**  
For support, contact: jharrison@redcross.nl
