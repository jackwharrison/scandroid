import json
import os
from cryptography.fernet import Fernet, InvalidToken

# Optional: load a local .env file during development so the env-managed fields
# below can be set without exporting them in your shell. On Azure these values
# come from App Settings, so this block is a harmless no-op there (and silently
# skips if python-dotenv isn't installed).
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


# ---------------------------------------------------------------------------
# Fields read from environment variables instead of system_config.json.
#
#       config key      ->   ENV VAR NAME
#
# Anything NOT listed here stays in system_config.json. Right now that means
# the fields editable from the System Config page (KOBO_SERVER, KOBO_TOKEN,
# PROGRAMS) plus COLUMN_TO_MATCH_PER_PROGRAM, which is written at runtime from
# the program-config page and therefore must remain writable in the JSON file.
#
# To move a field in or out of env management, just edit this dict.
# ---------------------------------------------------------------------------
ENV_MANAGED_FIELDS = {
    "url121": "URL_121",
    "username121": "USERNAME_121",
    "password121": "PASSWORD_121",
    "ENCRYPTION_KEY": "ENCRYPTION_KEY",
    "programCurrency": "PROGRAM_CURRENCY",
    "programTitle": "PROGRAM_TITLE",
    "COLUMN_TO_MATCH": "COLUMN_TO_MATCH",
    "nationalSociety": "NATIONAL_SOCIETY",
}

# KoBo credentials are encrypted at rest in system_config.json
ENCRYPTED_FIELDS = {"KOBO_TOKEN", "KOBO_SERVER"}


def _get_cipher():
    """Get or create a Fernet cipher using ENCRYPTION_KEY from environment.

    If ENCRYPTION_KEY is not set, encryption/decryption is skipped (plaintext mode).
    """
    key = os.getenv("ENCRYPTION_KEY")
    if not key:
        return None
    try:
        return Fernet(key.encode())
    except ValueError as e:
        print(f"Warning: Invalid ENCRYPTION_KEY format: {e}")
        return None


def _encrypt_value(value, cipher=None):
    """Encrypt a string value using Fernet, if cipher is available."""
    if not cipher or not value:
        return value
    try:
        encrypted = cipher.encrypt(value.encode())
        return encrypted.decode()
    except (InvalidToken, UnicodeDecodeError) as e:
        print(f"Warning: Encryption failed: {e}")
        return value


def _decrypt_value(value, cipher=None):
    """Decrypt a string value using Fernet if it looks encrypted."""
    if not cipher or not value or not isinstance(value, str):
        return value
    try:
        decrypted = cipher.decrypt(value.encode())
        return decrypted.decode()
    except InvalidToken:
        # If decryption fails, assume it's plaintext (backward compatibility)
        return value
    except (UnicodeDecodeError, ValueError) as e:
        # Handle encoding errors gracefully
        print(f"Warning: Failed to decode decrypted value: {e}")
        return value


def _get_paths():
    env = os.getenv("SCANDROID_ENV", "local")
    context = os.getenv("SCANDROID_CONTEXT", "local")

    if env == "azure":
        base = f"/home/site/configs/{context}"
    else:
        base = os.path.join(os.path.dirname(__file__), "configs", context)

    os.makedirs(base, exist_ok=True)
    return (
        os.path.join(base, "system_config.json"),
        os.path.join(base, "display_config.json"),
    )


def _apply_env_overrides(data):
    """Overlay env-managed fields onto the JSON data.

    The environment variable wins whenever it is set; otherwise any existing
    value already in the JSON is kept as a fallback. This lets Azure drive
    everything from App Settings while local dev can rely on a .env file (or,
    until the next save, the values still sitting in the JSON on disk).
    """
    for key, env_name in ENV_MANAGED_FIELDS.items():
        value = os.getenv(env_name)
        if value is not None:
            data[key] = value
    return data


def load_config():
    system_path, _ = _get_paths()
    data = {}
    if os.path.exists(system_path):
        with open(system_path, "r", encoding="utf-8") as f:
            data = json.load(f)

    # Decrypt KoBo credentials if present
    cipher = _get_cipher()
    for field in ENCRYPTED_FIELDS:
        if field in data:
            data[field] = _decrypt_value(data[field], cipher)

    return _apply_env_overrides(data)


def save_config(data):
    """Persist only the JSON-resident fields.

    Env-managed fields are stripped before writing, so values that come from
    the environment (including secrets like password121 and ENCRYPTION_KEY) are
    never baked back into system_config.json by a UI save or a runtime write.

    KoBo credentials (KOBO_SERVER, KOBO_TOKEN) are encrypted before saving.
    """
    system_path, _ = _get_paths()
    to_save = {k: v for k, v in data.items() if k not in ENV_MANAGED_FIELDS}

    # Encrypt KoBo credentials before saving
    cipher = _get_cipher()
    for field in ENCRYPTED_FIELDS:
        if field in to_save:
            to_save[field] = _encrypt_value(to_save[field], cipher)

    with open(system_path, "w", encoding="utf-8") as f:
        json.dump(to_save, f, indent=2, ensure_ascii=False)


def load_display_config():
    _, display_path = _get_paths()
    if not os.path.exists(display_path):
        return {}
    with open(display_path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_display_config(data):
    _, display_path = _get_paths()
    with open(display_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
