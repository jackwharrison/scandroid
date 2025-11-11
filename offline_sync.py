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
KOBO_BASE = "https://kobo.ifrc.org"
ASSET_ID = config["ASSET_ID"]
PROGRAM_ID = config["programId"]
PAYMENT_ID = config["PAYMENT_ID"]
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
    submission = get_kobo_submission(uuid)
    if not submission:
        print(f"[!] No Kobo submission found for UUID {uuid}")
        return

    attachments = submission.get("_attachments", [])
    if not attachments:
        print(f"[!] No attachments found for UUID {uuid}")
        return

    for attach in attachments:
        media_file = attach.get("filename")
        if not media_file:
            continue

        ext = os.path.splitext(media_file)[1]
        download_url = f"{KOBO_BASE}/media/original?media_file={media_file}"

        res = requests.get(download_url, headers=HEADERS_KOBO)
        if res.status_code == 200:
            encrypted_bytes = encrypt_photo(res.content)
            with open(save_path, "wb") as f:
                f.write(encrypted_bytes)
            print(f"[OK] Encrypted photo saved for UUID {uuid}")
            return
        else:
            print(f"[!] Failed to download photo for UUID {uuid}: {res.status_code}")
            return

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
            "data": encrypted_data,
            "valid": is_valid,
            "reason": reason
        }

        cache_data.append(record)

    json_path = os.path.join(batch_dir, "registrations_cache.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(cache_data, f, indent=2)

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
            "data": encrypted_data,
            "valid": is_valid,
            "reason": reason
        }

        cache_data.append(record)

    # Save encrypted registration data
    json_path = os.path.join(batch_dir, "registrations_cache.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(cache_data, f, indent=2)

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
