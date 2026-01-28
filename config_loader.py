import json
import os

ENV = os.getenv("SCANDROID_ENV", "local")     # local | azure
CONTEXT = os.getenv("SCANDROID_CONTEXT", "local")

if ENV == "azure":
    BASE_PATH = f"/home/site/configs/{CONTEXT}"
else:
    BASE_PATH = os.path.join(
        os.path.dirname(__file__),
        "configs",
        CONTEXT
    )

os.makedirs(BASE_PATH, exist_ok=True)

SYSTEM_PATH = os.path.join(BASE_PATH, "system_config.json")
DISPLAY_PATH = os.path.join(BASE_PATH, "display_config.json")


def load_config():
    if not os.path.exists(SYSTEM_PATH):
        return {}
    with open(SYSTEM_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(data):
    with open(SYSTEM_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_display_config():
    if not os.path.exists(DISPLAY_PATH):
        return {}
    with open(DISPLAY_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_display_config(data):
    with open(DISPLAY_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
