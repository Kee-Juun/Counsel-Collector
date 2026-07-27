import json
import os
from pathlib import Path

from config.app_settings import APP_NAME


SETTINGS_FILE = Path(os.getenv("APPDATA", Path.home() / "AppData" / "Roaming")) / APP_NAME / "settings.json"


def load_saved_user_id():
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return str(data.get("lexis_id", "")).strip()


def save_user_id(user_id):
    user_id = user_id.strip()
    if not user_id:
        clear_saved_user_id()
        return

    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps({"lexis_id": user_id}, indent=2), encoding="utf-8")


def clear_saved_user_id():
    try:
        SETTINGS_FILE.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass
