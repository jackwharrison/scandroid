import json
import os

LOCAL_PATH = "./system_config.json"
AZURE_PATH = "/home/site/wwwroot/system_config.json"

CONFIG_PATH = LOCAL_PATH if os.path.exists(LOCAL_PATH) else AZURE_PATH

def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def save_config(data):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
