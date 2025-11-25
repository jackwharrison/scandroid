import requests
import json
import os
import shutil
from datetime import datetime
from config_loader import load_config
from cryptography.fernet import Fernet

# Load config
config = load_config()
API_BASE = config["url121"] + "/api"
KOBO_TOKEN = config["KOBO_TOKEN"]
KOBO_BASE = config.get("KOBO_SERVER")
ASSET_ID = config["ASSET_ID"]
PROGRAM_ID = config["programId"]
ENCRYPTION_KEY = config["ENCRYPTION_KEY"]

# Load display config
with open("display_config.json", "r", encoding="utf-8") as f:
    display_config = json.load(f)

FIELD_KEYS = [field["key"] for field in display_config.get("fields", [])]
PHOTO_FIELD_NAME = display_config.get("photo", {}).get("field_name", "photo")

fernet = Fernet(ENCRYPTION_KEY.encode())

def encrypt_data(data_dict):
    encrypted = {}
    for key, value in data_dict.items():
        plain = str(value) if value is not None else ""
        encrypted[key] = fernet.encrypt(plain.encode()).decode()
    return encrypted

def encrypt_photo(photo_bytes):
    return fernet.encrypt(photo_bytes)

def login_and_get_token():
    login_url = f"{API_BASE}/users/login"
    credentials = {
        "username": config["username121"],
        "password": config["password121"]
    }
    response = requests.post(login_url, headers={"Content-Type": "application/json"}, json=credentials)
    response.raise_for_status()
    token = response.json().get("access_token_general")
    if not token:
        raise Exception("Login successful but token missing.")
    return token

TOKEN = login_and_get_token()
COOKIES = {"access_token_general": TOKEN}
HEADERS_KOBO = {"Authorization": f"Token {KOBO_TOKEN}"}

def get_transactions(program_id, payment_id):
    url = f"{API_BASE}/programs/{program_id}/payments/{payment_id}/transactions"
    response = requests.get(url, cookies=COOKIES)
    response.raise_for_status()
    return response.json()

def get_registration(program_id, registration_id):
    url = f"{API_BASE}/programs/{program_id}/registrations/{registration_id}"
    response = requests.get(url, cookies=COOKIES)
    response.raise_for_status()
    return response.json()

def get_kobo_submission(uuid):
    url = f"{KOBO_BASE}/api/v2/assets/{ASSET_ID}/data.json?query={{\"_uuid\":\"{uuid}\"}}"
    response = requests.get(url, headers=HEADERS_KOBO)
    response.raise_for_status()
    results = response.json().get("results", [])
    return results[0] if results else None

def download_and_encrypt_photo(uuid, save_path):
    """
    Download and encrypt the photo for a given submission UUID.
    Saves the encrypted image bytes to save_path.
    Handles:
    - Kobo's direct *_URL field
    - Kobo _attachments list
    - IFRC Kobo /attachments/<uid>/ format
    """

    # --- 1) Fetch Kobo submission ---
    submission = get_kobo_submission(uuid)
    if not submission:
        print(f"[!] No Kobo submission found for UUID {uuid}")
        return

    photo_field = PHOTO_FIELD_NAME  # "photo"
    photo_filename = submission.get(photo_field)

    # --- 2) New Kobo way: direct photo URL (BEST METHOD) ---
    photo_url_field = f"{photo_field}_URL"   # "photo_URL"
    photo_url = submission.get(photo_url_field)

    if photo_url:
        print(f"[OK] Direct Kobo photo URL found for UUID {uuid}: {photo_url}")

        # Download directly
        res = requests.get(photo_url, headers=HEADERS_KOBO)
        if res.status_code != 200:
            print(f"[!] Direct photo download failed for UUID {uuid}: {res.status_code}")
            return

        encrypted_bytes = encrypt_photo(res.content)
        with open(save_path, "wb") as f:
            f.write(encrypted_bytes)

        print(f"[OK] Photo downloaded & encrypted (direct URL) for UUID {uuid}")
        return

    # --- 3) Fallback: match against _attachments (older Kobo submissions) ---
    if not photo_filename:
        print(f"[!] No '{photo_field}' value for UUID {uuid}")
        return

    attachments = submission.get("_attachments", [])
    if not attachments:
        print(f"[!] No attachments in submission for UUID {uuid}")
        return

    photo_base = photo_filename.split(".")[0]

    matching = [
        a for a in attachments
        if photo_base in a.get("filename", "")
    ]

    if not matching:
        print(f"[!] No matching attachment for '{photo_filename}' (UUID {uuid})")
        return

    att = matching[0]
    attach_uid = att.get("uid")

    if not attach_uid:
        print(f"[!] Attachment UID missing for UUID {uuid}")
        return

    submission_id = submission["_id"]

    # --- 4) Correct IFRC Kobo attachment URL format ---
    file_url = (
        f"{KOBO_BASE}/api/v2/assets/{ASSET_ID}/data/"
        f"{submission_id}/attachments/{attach_uid}/"
    )

    print(f"[OK] Using IFRC Kobo attachment URL for UUID {uuid}: {file_url}")

    # --- 5) Download ---
    res = requests.get(file_url, headers=HEADERS_KOBO)
    if res.status_code != 200:
        print(f"[!] Failed to download from IFRC Kobo for UUID {uuid}: {res.status_code}")
        return

    # --- 6) Encrypt & save ---
    encrypted_bytes = encrypt_photo(res.content)
    with open(save_path, "wb") as f:
        f.write(encrypted_bytes)

    print(f"[OK] Photo downloaded & encrypted for UUID {uuid}")


def get_next_batch_dir(base_path, payment_id):
    batch_number = 1
    while True:
        batch_path = os.path.join(base_path, f"payment-{payment_id}-batch-{batch_number}")
        if not os.path.exists(batch_path):
            os.makedirs(os.path.join(batch_path, "photos"), exist_ok=True)
            return batch_path
        batch_number += 1

def download_cache(program_id, payment_id):
    base_path = "offline-cache"
    os.makedirs(base_path, exist_ok=True)
    batch_dir = get_next_batch_dir(base_path, payment_id)

    transactions = get_transactions(program_id, payment_id)
    cache_data = []

    for t in transactions:
        reg_id = t["registrationId"]
        uuid = t["registrationReferenceId"]

        try:
            reg = get_registration(program_id, reg_id)
        except Exception as e:
            print(f"[!] Failed to get registration {reg_id}: {e}")
            continue

        filtered_data = {key: reg.get(key) for key in FIELD_KEYS}
        encrypted_data = encrypt_data(filtered_data)

        photo_filename = f"{uuid}.enc"
        photo_path = os.path.join(batch_dir, "photos", photo_filename)
        download_and_encrypt_photo(uuid, photo_path)

        status = (t.get("status") or t.get("transactionStatus") or "").lower()
        deleted = (t.get("registrationStatus") or "").lower() == "deleted"

        is_valid = status == "waiting" and not deleted
        reason = "ok"
        if not is_valid:
            if status != "waiting":
                reason = f"status={status}"
            elif deleted:
                reason = "deleted"

        record = {
            "uuid": uuid,
            "registrationId": reg_id,
            "photo_filename": photo_filename,
            "paymentId": t.get("paymentId"),
            "amount": t.get("amount", 0),   
            "data": encrypted_data,
            "valid": is_valid,
            "reason": reason
        }

        cache_data.append(record)

    json_path = os.path.join(batch_dir, "registrations_cache.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(cache_data, f, indent=2)
    
    tx_path = os.path.join(batch_dir, "transactions.json")
    with open(tx_path, "w", encoding="utf-8") as f:
        json.dump(transactions, f, indent=2)

    print(f"\n[OK] Done. Batch saved to: {batch_dir}")
    print(f"{len(cache_data)} beneficiaries ready for offline validation.")
    return len(cache_data)

from collections import defaultdict
from datetime import timedelta

from datetime import datetime, timedelta

from datetime import datetime, timedelta  # Make sure this is at the top if not already

def download_recent_payments_cache(program_id):
    base_path = "offline-cache"
    os.makedirs(base_path, exist_ok=True)
    batch_dir = get_next_batch_dir(base_path, "recent")

    # Get ALL transactions
    url = f"{API_BASE}/programs/{program_id}/transactions"
    response = requests.get(url, cookies=COOKIES)
    response.raise_for_status()
    data = response.json()

    # Handle multiple possible 121 API formats
    if isinstance(data, dict):
        if "transactions" in data:
            all_transactions = data["transactions"]
        elif "data" in data:
            all_transactions = data["data"]
        else:
            print("[ERROR] Unexpected transaction structure:", data.keys())
            return 0
    elif isinstance(data, list):
        all_transactions = data
    else:
        print("[ERROR] Unknown transaction data type")
        return 0

    print(f"[INFO] Total transactions fetched: {len(all_transactions)}")

    fourteen_days_ago = datetime.utcnow() - timedelta(days=14)
    filtered = []

    # Debug counters
    counts = {
        "not_dict": 0,
        "not_waiting": 0,
        "deleted": 0,
        "missing_created": 0,
        "invalid_date": 0,
        "too_old": 0,
        "valid": 0
    }

    for t in all_transactions:
        if not isinstance(t, dict):
            counts["not_dict"] += 1
            continue

        status = (t.get("status") or t.get("transactionStatus") or "").lower()
        created = t.get("created", "")
        deleted = (t.get("registrationStatus") or "").lower() == "deleted"

        if status != "waiting":
            counts["not_waiting"] += 1
            continue

        if deleted:
            counts["deleted"] += 1
            continue

        if not created:
            counts["missing_created"] += 1
            continue

        try:
            # Try parsing timestamp formats
            try:
                created_dt = datetime.strptime(created, "%Y-%m-%dT%H:%M:%S.%fZ")
            except ValueError:
                created_dt = datetime.strptime(created, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            counts["invalid_date"] += 1
            print(f"[SKIP] Invalid date: {created}")
            continue

        if created_dt < fourteen_days_ago:
            counts["too_old"] += 1
            continue

        filtered.append(t)
        counts["valid"] += 1

    print("\n[DEBUG] Filter counts:")
    for k, v in counts.items():
        print(f"  - {k}: {v}")
    print(f"[INFO] Filtered transactions: {len(filtered)}")

    # Keep only the latest tranche per UUID
    latest_by_uuid = {}
    for t in filtered:
        uuid = t.get("registrationReferenceId")
        if not uuid:
            continue
        existing = latest_by_uuid.get(uuid)
        if not existing or t["created"] > existing["created"]:
            latest_by_uuid[uuid] = t

    print(f"[INFO] Final unique transactions to cache: {len(latest_by_uuid)}")

    cache_data = []

    for t in latest_by_uuid.values():
        reg_id = t.get("registrationId")
        uuid = t.get("registrationReferenceId")

        if not reg_id or not uuid:
            print("[SKIP] Missing reg_id or uuid")
            continue

        # ✅ FIX — compute THIS RECORD'S status
        status = (t.get("status") or t.get("transactionStatus") or "").lower()
        deleted = (t.get("registrationStatus") or "").lower() == "deleted"

        # ✅ FIX — compute THIS RECORD'S created date
        created = t.get("created", "")
        try:
            try:
                created_dt = datetime.strptime(created, "%Y-%m-%dT%H:%M:%S.%fZ")
            except ValueError:
                created_dt = datetime.strptime(created, "%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            created_dt = datetime.min

        try:
            reg = get_registration(program_id, reg_id)
        except Exception as e:
            print(f"[!] Failed registration fetch for {reg_id}: {e}")
            continue

        filtered_data = {key: reg.get(key) for key in FIELD_KEYS}
        encrypted_data = encrypt_data(filtered_data)

        photo_filename = f"{uuid}.enc"
        photo_path = os.path.join(batch_dir, "photos", photo_filename)
        download_and_encrypt_photo(uuid, photo_path)

        # ✅ FIX — compute validity *correctly for this record*
        is_valid = status == "waiting" and not deleted and created_dt >= fourteen_days_ago

        reason = "ok"
        if not is_valid:
            if status != "waiting":
                reason = f"status={status}"
            elif deleted:
                reason = "deleted"
            elif created_dt < fourteen_days_ago:
                reason = "too_old"


        record = {
            "uuid": uuid,
            "registrationId": reg_id,
            "photo_filename": photo_filename,
            "paymentId": t.get("paymentId"),
            "amount": t.get("amount", 0),         # <-- ADD THIS BACK
            "data": encrypted_data,
            "valid": is_valid,
            "reason": reason
        }


        cache_data.append(record)

    # Save encrypted registration data
    json_path = os.path.join(batch_dir, "registrations_cache.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(cache_data, f, indent=2)
    # NEW: save filtered transactions for amount lookup
    tx_path = os.path.join(batch_dir, "transactions.json")
    with open(tx_path, "w", encoding="utf-8") as f:
        json.dump(list(latest_by_uuid.values()), f, indent=2)


    print(f"\n[OK] Batch saved to: {batch_dir}")
    print(f"{len(cache_data)} beneficiaries ready.")

    batch_info = {
        "batchType": "payment-recent",
        "programId": program_id,
        "recordCount": len(cache_data),
        "generatedAt": datetime.utcnow().isoformat() + "Z"
    }

    with open(os.path.join(batch_dir, "batch_info.json"), "w", encoding="utf-8") as f:
        json.dump(batch_info, f, indent=2)

    return len(cache_data)


if __name__ == "__main__":
    download_recent_payments_cache(PROGRAM_ID)
