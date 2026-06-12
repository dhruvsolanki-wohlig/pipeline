import json
import os
from pathlib import Path
from datetime import datetime

SETTINGS_FILE = Path(__file__).resolve().parent / "schedule_settings.json"

DEFAULT_SETTINGS = {
    "recipients": ["chintan@wohlig.com", "jagruti@wohlig.com", "chirag@wohlig.com"],
    "next_run": None,
    "stop_run": None,
    "continuous": False,
    "active": False,
    "subject": "Company Workforce Report",
    "body_line": "Dear Team,\n\nPlease find the attached Company Workforce Report for your review.\n\nThis report summarizes the current workforce status, project allocations, and resource utilization across the organization.\n\nRegards,\n\nDhruv Solanki\nAryan Gupta",
    "interval_hours": 24,
    "last_run": None,
}


def load_settings():
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for k, v in DEFAULT_SETTINGS.items():
                if k not in data:
                    data[k] = v
            return data
        except Exception:
            pass
    return DEFAULT_SETTINGS.copy()


def save_settings(settings):
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, default=str)


def check_schedule_action(settings):
    if not settings.get("active"):
        return False

    now = datetime.now()
    next_run = settings.get("next_run")
    stop_run = settings.get("stop_run")

    if stop_run and isinstance(stop_run, str):
        try:
            sr = datetime.fromisoformat(stop_run.replace("Z", "+00:00"))
            if now >= sr:
                settings["active"] = False
                save_settings(settings)
                return False
        except Exception:
            pass

    if not settings.get("last_run"):
        return True

    if next_run and isinstance(next_run, str):
        try:
            nr = datetime.fromisoformat(next_run.replace("Z", "+00:00"))
            if now >= nr:
                return True
        except Exception:
            pass

    return False
