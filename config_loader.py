import json
import os

# Get context name from Azure App Settings, fallback to "local"
CONTEXT = os.getenv("SCANDROID_CONTEXT", "local")

# Base path for configs inside Azure
AZURE_BASE = f"/home/site/configs/{CONTEXT}"

# Local fallback paths
LOCAL_SYSTEM = "./system_config.json"
LOCAL_DISPLAY = "./display_config.json"

# Build final paths
SYSTEM_PATH = (
    LOCAL_SYSTEM if os.path.exists(LOCAL_SYSTEM)
    else f"{AZURE_BASE}/system_config.json"
)

DISPLAY_PATH = (
    LOCAL_DISPLAY if os.path.exists(LOCAL_DISPLAY)
    else f"{AZURE_BASE}/display_config.json"
)

def load_config():
    with open(SYSTEM_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def save_config(data):
    with open(SYSTEM_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def load_display_config():
    with open(DISPLAY_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def save_display_config(data):
    with open(DISPLAY_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
