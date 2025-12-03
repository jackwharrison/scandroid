import os
import json
import requests
from datetime import datetime, timedelta
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

from cryptography.fernet import Fernet
from config_loader import load_config, load_display_config


# ----------------------------------------------------------------------
# CONFIG & GLOBALS
# ----------------------------------------------------------------------

config = load_config()
API_BASE = config["url121"] + "/api"
KOBO_TOKEN = config["KOBO_TOKEN"]
KOBO_BASE = config.get("KOBO_SERVER")
ASSET_ID = config["ASSET_ID"]
PROGRAM_ID = config["programId"]
ENCRYPTION_KEY = config["ENCRYPTION_KEY"]

display_config = load_display_config()
FIELD_KEYS = [field["key"] for field in display_config.get("fields", [])]
PHOTO_FIELD_NAME = display_config.get("photo", {}).get("field_name", "photo")

fernet = Fernet(ENCRYPTION_KEY.encode())

# Thread pool size (can be overridden by env var)
MAX_WORKERS = int(os.getenv("OFFLINE_SYNC_WORKERS", "8"))

COOKIES = None
HEADERS_KOBO = {"Authorization": f"Token {KOBO_TOKEN}"}


# ----------------------------------------------------------------------
# ENCRYPTION HELPERS
# ----------------------------------------------------------------------

def encrypt_data(data_dict):
    """
    Encrypt all values in a dict with Fernet.
    Values are cast to string; None becomes "".
    """
    encrypted = {}
    for key, value in data_dict.items():
        plain = str(value) if value is not None else ""
        encrypted[key] = fernet.encrypt(plain.encode()).decode()
    return encrypted


def encrypt_photo(photo_bytes):
    return fernet.encrypt(photo_bytes)


# ----------------------------------------------------------------------
# AUTH / SESSION
# ----------------------------------------------------------------------

def login_and_get_token():
    """
    Log in to 121 API and obtain access_token_general.
    """
    global COOKIES
    login_url = f"{API_BASE}/users/login"
    credentials = {
        "username": config["username121"],
        "password": config["password121"],
    }
    response = requests.post(
        login_url,
        headers={"Content-Type": "application/json"},
        json=credentials,
    )
    response.raise_for_status()
    token = response.json().get("access_token_general")
    if not token:
        raise Exception("Login successful but token missing.")
    COOKIES = {"access_token_general": token}
    return token


# Initialise token/cookies at import-time
login_and_get_token()


# ----------------------------------------------------------------------
# 121 API HELPERS
# ----------------------------------------------------------------------

def get_transactions(program_id, payment_id):
    url = f"{API_BASE}/programs/{program_id}/payments/{payment_id}/transactions"
    response = requests.get(url, cookies=COOKIES)
    response.raise_for_status()
    return response.json()


def get_all_transactions(program_id):
    """
    Get ALL transactions for a program.
    This is used by download_recent_payments_cache.
    """
    url = f"{API_BASE}/programs/{program_id}/transactions"
    response = requests.get(url, cookies=COOKIES)
    response.raise_for_status()
    data = response.json()

    if isinstance(data, dict):
        if "transactions" in data:
            return data["transactions"]
        if "data" in data:
            return data["data"]
        print("[ERROR] Unexpected transaction structure:", data.keys())
        return []
    elif isinstance(data, list):
        return data
    else:
        print("[ERROR] Unknown transaction data type")
        return []


def get_registration(program_id, registration_id):
    url = f"{API_BASE}/programs/{program_id}/registrations/{registration_id}"
    response = requests.get(url, cookies=COOKIES)
    response.raise_for_status()
    return response.json()


def fetch_registrations_bulk(program_id, registration_ids):
    """
    Fetch registrations in parallel for a set of registrationIds.
    Returns dict: {registrationId: registration_json}
    """
    results = {}
    unique_ids = list(set(registration_ids))

    if not unique_ids:
        return results

    def worker(rid):
        try:
            reg = get_registration(program_id, rid)
            return rid, reg
        except Exception as e:
            print(f"[!] Failed to get registration {rid}: {e}")
            return rid, None

    max_workers = min(MAX_WORKERS, len(unique_ids)) or 1
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(worker, rid) for rid in unique_ids]
        for fut in as_completed(futures):
            rid, reg = fut.result()
            if reg is not None:
                results[rid] = reg

    return results


# ----------------------------------------------------------------------
# KOBO HELPERS
# ----------------------------------------------------------------------

def get_kobo_submission(uuid):
    """
    Fetch a single Kobo submission by _uuid.
    Keeps behaviour identical but now all photo optimisations
    are handled in download_and_encrypt_photo.
    """
    # Keep it simple & safe: full submission (no fields filter),
    # since we rely on photo field, *_URL, _attachments, and _id.
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
    - Kobo's direct *_URL field (photo_URL)
    - Kobo _attachments list
    - IFRC Kobo /attachments/<uid>/ format
    Uses a smaller 'medium' view to speed up sync.
    """

    # If photo already exists and is non-empty, you *could* skip.
    # For now, we always refresh since each batch dir is unique.
    # if os.path.exists(save_path) and os.path.getsize(save_path) > 0:
    #     print(f"[SKIP] Photo already exists for UUID {uuid}")
    #     return

    # --- 1) Fetch Kobo submission ---
    submission = get_kobo_submission(uuid)
    if not submission:
        print(f"[!] No Kobo submission found for UUID {uuid}")
        return

    photo_field = PHOTO_FIELD_NAME  # e.g. "photo"
    photo_filename = submission.get(photo_field)

    # --- 2) New Kobo way: direct photo URL (BEST METHOD) ---
    photo_url_field = f"{photo_field}_URL"  # e.g. "photo_URL"
    photo_url = submission.get(photo_url_field)

    if photo_url:
        print(f"[OK] Direct Kobo photo URL found for UUID {uuid}: {photo_url}")

        # Use smaller 'medium' image instead of original
        photo_url = photo_url.replace("/original/", "/medium/")

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

    # --- 4) Correct IFRC Kobo attachment URL format, with medium view ---
    file_url = (
        f"{KOBO_BASE}/api/v2/assets/{ASSET_ID}/data/"
        f"{submission_id}/attachments/{attach_uid}/?view=medium"
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


def download_photos_bulk(records, photos_dir):
    """
    Download & encrypt photos for all records in parallel.
    Each record should have 'uuid' and 'photo_filename'.
    """
    if not records:
        return

    os.makedirs(photos_dir, exist_ok=True)

    def worker(rec):
        uuid = rec["uuid"]
        photo_filename = rec["photo_filename"]
        save_path = os.path.join(photos_dir, photo_filename)
        download_and_encrypt_photo(uuid, save_path)

    max_workers = min(MAX_WORKERS, len(records)) or 1
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(worker, rec) for rec in records]
        for _ in as_completed(futures):
            # We don't need the result; download_and_encrypt_photo handles logging
            pass


# ----------------------------------------------------------------------
# BATCH DIRECTORY HELPERS
# ----------------------------------------------------------------------

def get_next_batch_dir(base_path, payment_id):
    """
    Create a unique batch directory for a given payment or "recent",
    with a 'photos' subfolder.
    """
    batch_number = 1
    while True:
        batch_path = os.path.join(base_path, f"payment-{payment_id}-batch-{batch_number}")
        if not os.path.exists(batch_path):
            os.makedirs(os.path.join(batch_path, "photos"), exist_ok=True)
            return batch_path
        batch_number += 1


# ----------------------------------------------------------------------
# MAIN: SPECIFIC PAYMENT BATCH
# ----------------------------------------------------------------------

def download_cache(program_id, payment_id):
    """
    Original behaviour: download cache for a single paymentId.

    - Fetch transactions for this payment
    - Fetch registrations (now in parallel)
    - Fetch + encrypt photos (now in parallel, medium-size)
    - Save registrations_cache.json and transactions.json
    """
    base_path = "offline-cache"
    os.makedirs(base_path, exist_ok=True)
    batch_dir = get_next_batch_dir(base_path, payment_id)
    photos_dir = os.path.join(batch_dir, "photos")

    transactions = get_transactions(program_id, payment_id)
    cache_data = []

    # 1) Collect registrationIds & uuids from transactions
    reg_ids = []
    for t in transactions:
        if "registrationId" in t and "registrationReferenceId" in t:
            reg_ids.append(t["registrationId"])

    # 2) Fetch registrations in bulk (parallel)
    registrations_map = fetch_registrations_bulk(program_id, reg_ids)

    # 3) Build records (encryption, validity checks)
    for t in transactions:
        reg_id = t.get("registrationId")
        uuid = t.get("registrationReferenceId")

        if not reg_id or not uuid:
            print("[SKIP] Missing reg_id or uuid in transaction")
            continue

        reg = registrations_map.get(reg_id)
        if not reg:
            print(f"[!] No registration data for {reg_id}")
            continue

        filtered_data = {key: reg.get(key) for key in FIELD_KEYS}
        match_key = config.get("COLUMN_TO_MATCH")
        if match_key:
            filtered_data[match_key] = reg.get(match_key)

        encrypted_data = encrypt_data(filtered_data)

        photo_filename = f"{uuid}.enc"

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
            "reason": reason,
        }

        cache_data.append(record)

    # 4) Download & encrypt all photos in parallel
    download_photos_bulk(cache_data, photos_dir)

    # 5) Save encrypted registration data & transactions
    json_path = os.path.join(batch_dir, "registrations_cache.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(cache_data, f, indent=2)

    tx_path = os.path.join(batch_dir, "transactions.json")
    with open(tx_path, "w", encoding="utf-8") as f:
        json.dump(transactions, f, indent=2)

    print(f"\n[OK] Done. Batch saved to: {batch_dir}")
    print(f"{len(cache_data)} beneficiaries ready for offline validation.")
    return len(cache_data)


# ----------------------------------------------------------------------
# MAIN: RECENT PAYMENTS BATCH (last 14 days)
# ----------------------------------------------------------------------

def download_recent_payments_cache(program_id):
    """
    Build a "recent" offline batch:
    - Get ALL transactions for a program
    - Filter to status=waiting, not deleted, created in last 14 days
    - Keep only the latest transaction per UUID
    - Fetch registrations in bulk (parallel)
    - Download & encrypt photos in parallel (medium-size)
    - Save:
        - registrations_cache.json
        - transactions.json (latest transactions per uuid)
        - batch_info.json
    """
    base_path = "offline-cache"
    os.makedirs(base_path, exist_ok=True)
    batch_dir = get_next_batch_dir(base_path, "recent")
    photos_dir = os.path.join(batch_dir, "photos")

    all_transactions = get_all_transactions(program_id)
    print(f"[INFO] Total transactions fetched: {len(all_transactions)}")

    fourteen_days_ago = datetime.utcnow() - timedelta(days=14)
    filtered = []

    counts = {
        "not_dict": 0,
        "not_waiting": 0,
        "deleted": 0,
        "missing_created": 0,
        "invalid_date": 0,
        "too_old": 0,
        "valid": 0,
    }

    # 1) Filter by status, not deleted, and date window
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

    # 2) Keep only the latest transaction per UUID
    latest_by_uuid = {}
    for t in filtered:
        uuid = t.get("registrationReferenceId")
        if not uuid:
            continue
        existing = latest_by_uuid.get(uuid)
        if not existing or t.get("created", "") > existing.get("created", ""):
            latest_by_uuid[uuid] = t

    print(f"[INFO] Final unique transactions to cache: {len(latest_by_uuid)}")

    # 3) Fetch registrations in bulk (parallel)
    reg_ids = [
        t.get("registrationId")
        for t in latest_by_uuid.values()
        if t.get("registrationId")
    ]
    registrations_map = fetch_registrations_bulk(program_id, reg_ids)

    cache_data = []

    # 4) Build records
    for t in latest_by_uuid.values():
        reg_id = t.get("registrationId")
        uuid = t.get("registrationReferenceId")

        if not reg_id or not uuid:
            print("[SKIP] Missing reg_id or uuid")
            continue

        status = (t.get("status") or t.get("transactionStatus") or "").lower()
        deleted = (t.get("registrationStatus") or "").lower() == "deleted"
        created = t.get("created", "")

        try:
            try:
                created_dt = datetime.strptime(created, "%Y-%m-%dT%H:%M:%S.%fZ")
            except ValueError:
                created_dt = datetime.strptime(created, "%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            created_dt = datetime.min

        reg = registrations_map.get(reg_id)
        if not reg:
            print(f"[!] Failed registration fetch for {reg_id}")
            continue

        filtered_data = {key: reg.get(key) for key in FIELD_KEYS}
        match_key = config.get("COLUMN_TO_MATCH")
        if match_key:
            filtered_data[match_key] = reg.get(match_key)
        encrypted_data = encrypt_data(filtered_data)

        photo_filename = f"{uuid}.enc"

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
            "amount": t.get("amount", 0),
            "data": encrypted_data,
            "valid": is_valid,
            "reason": reason,
        }

        cache_data.append(record)

    # 5) Download & encrypt photos in parallel
    download_photos_bulk(cache_data, photos_dir)

    # 6) Save encrypted registration data
    json_path = os.path.join(batch_dir, "registrations_cache.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(cache_data, f, indent=2)

    # Save filtered latest transactions
    tx_path = os.path.join(batch_dir, "transactions.json")
    with open(tx_path, "w", encoding="utf-8") as f:
        json.dump(list(latest_by_uuid.values()), f, indent=2)

    print(f"\n[OK] Batch saved to: {batch_dir}")
    print(f"{len(cache_data)} beneficiaries ready.")

    batch_info = {
        "batchType": "payment-recent",
        "programId": program_id,
        "recordCount": len(cache_data),
        "generatedAt": datetime.utcnow().isoformat() + "Z",
    }

    with open(os.path.join(batch_dir, "batch_info.json"), "w", encoding="utf-8") as f:
        json.dump(batch_info, f, indent=2)

    return len(cache_data)


# ----------------------------------------------------------------------
# CLI ENTRY
# ----------------------------------------------------------------------

if __name__ == "__main__":
    # Default behaviour: generate the "recent" batch
    download_recent_payments_cache(PROGRAM_ID)
