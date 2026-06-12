import json
import os
import requests
from pathlib import Path
from datetime import datetime
from typing import List, Optional

import tempfile
SETTINGS_FILE = Path(tempfile.gettempdir() if os.environ.get("VERCEL") else str(Path(__file__).resolve().parent)) / "schedule_settings.json"

DEFAULT_SETTINGS = {
    "recipients": ["chintan@wohlig.com", "jagruti@wohlig.com", "chirag@wohlig.com"],
    "next_run": None,
    "stop_run": None,
    "continuous": False,
    "active": False,
    "subject": "Company Workforce Report",
    "body_line": "Dear Team,\n\nPlease find the attached Company Workforce Report for your review.\n\nThis report summarizes the current workforce status, project allocations, and resource utilization across the organization.\n\nRegards,\n\nDhruv Solanki\nAryan Gupta",
    "interval_hours": 24,
    "cron_expression": "",
    "last_run": None,
}


def load_settings():
    kv_url = os.environ.get("KV_REST_API_URL")
    kv_token = os.environ.get("KV_REST_API_TOKEN")
    
    if kv_url and kv_token:
        try:
            resp = requests.get(f"{kv_url}/get/schedule_settings", headers={"Authorization": f"Bearer {kv_token}"})
            data = resp.json()
            if data and data.get("result"):
                saved = json.loads(data["result"])
                for k, v in DEFAULT_SETTINGS.items():
                    if k not in saved:
                        saved[k] = v
                return saved
        except Exception:
            pass

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
    kv_url = os.environ.get("KV_REST_API_URL")
    kv_token = os.environ.get("KV_REST_API_TOKEN")

    if kv_url and kv_token:
        try:
            requests.post(
                f"{kv_url}/set/schedule_settings", 
                headers={"Authorization": f"Bearer {kv_token}"}, 
                data=json.dumps(json.dumps(settings, default=str))
            )
        except Exception:
            pass

    try:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2, default=str)
    except Exception:
        pass


def check_schedule_action(settings):
    if not settings.get("active"):
        return False
    
    now = datetime.now()
    next_run = settings.get("next_run")
    stop_run = settings.get("stop_run")
    continuous = settings.get("continuous", False)

    if stop_run and isinstance(stop_run, str) and not continuous:
        try:
            stop_run = datetime.fromisoformat(stop_run.replace("Z", "+00:00"))
            if now >= stop_run:
                settings["active"] = False
                save_settings(settings)
                return False
        except Exception:
            pass

    # Force immediate run if newly activated and last_run is cleared
    if not settings.get("last_run"):
        return True

    if next_run and isinstance(next_run, str):
        try:
            nr = datetime.fromisoformat(next_run.replace("Z", "+00:00"))
        except Exception:
            return False
        
        # Is it time to generate and send?
        if now >= nr:
            return True

    return False
