import json
import os

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

def load_config():
    system_path, _ = _get_paths()
    if not os.path.exists(system_path):
        return {}
    with open(system_path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_config(data):
    system_path, _ = _get_paths()
    with open(system_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

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