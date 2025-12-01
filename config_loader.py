import json
import os

LOCAL_SYSTEM = "./system_config.json"
AZURE_SYSTEM = "/home/site/wwwroot/system_config.json"

LOCAL_DISPLAY = "./display_config.json"
AZURE_DISPLAY = "/home/site/wwwroot/display_config.json"

SYSTEM_PATH = LOCAL_SYSTEM if os.path.exists(LOCAL_SYSTEM) else AZURE_SYSTEM
DISPLAY_PATH = LOCAL_DISPLAY if os.path.exists(LOCAL_DISPLAY) else AZURE_DISPLAY


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
