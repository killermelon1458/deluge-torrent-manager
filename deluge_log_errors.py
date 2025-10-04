#deluge_log_errors11.py
#!/usr/bin/env python3
import os
import json
import time
import traceback
from datetime import datetime
from deluge_client import DelugeRPCClient

# --- CONFIGURATION ---
DELUGE_HOST = "127.0.0.1"
DELUGE_PORT = 58846
DELUGE_USER = "localclient"
DELUGE_PASS = "supersecretpass"
LOG_DIR = "/plex/Movies/plex/deluge_logs"
ERROR_LOG_PATH = os.path.join(LOG_DIR, "errors.log")
STATS_LOG_PATH = os.path.join(LOG_DIR, "stats.json")
COMPLETED_LOG_PATH = os.path.join(LOG_DIR, "completed.log")
RESUME_ERROR_LOG_PATH = os.path.join(LOG_DIR, "resume_data_fix.log")
SCRIPT_ERROR_LOG_PATH = os.path.join(LOG_DIR, "script_errors.log")
SUMMARY_LOG_PATH = os.path.join(LOG_DIR, "summary.log")
MAX_ACTIVE_DOWNLOADS_DEFAULT = 5
SEEDING_POLICY_ALLOW_SEEDING = True  # True = allow seeding, False = remove finished

os.makedirs(LOG_DIR, exist_ok=True)

def log_script_error(e):
    with open(SCRIPT_ERROR_LOG_PATH, "a") as f:
        f.write(f"[{datetime.now()}] Script Error:\n")
        f.write("".join(traceback.format_exception(type(e), e, e.__traceback__)))
        f.write("\n")

def log_resume_fix(msg):
    with open(RESUME_ERROR_LOG_PATH, "a") as f:
        f.write(f"[{datetime.now()}] {msg}\n")

def log_summary(msg):
    with open(SUMMARY_LOG_PATH, "a") as f:
        f.write(f"[{datetime.now()}] {msg}\n")

def is_drive_mounted(path):
    while path != "/":
        if os.path.ismount(path):
            return True
        path = os.path.dirname(path)
    return False

try:
    client = DelugeRPCClient(DELUGE_HOST, DELUGE_PORT, DELUGE_USER, DELUGE_PASS)
    client.connect()

    try:
        MAX_ACTIVE_DOWNLOADS = client.call("core.get_config")["max_active_downloading"]
    except:
        MAX_ACTIVE_DOWNLOADS = MAX_ACTIVE_DOWNLOADS_DEFAULT

    def get_torrents():
        return client.call("core.get_torrents_status", {}, [
            "name", "state", "message", "tracker_status", "save_path"
        ])

    torrents = get_torrents()
    if os.path.exists(STATS_LOG_PATH):
        with open(STATS_LOG_PATH, "r") as f:
            stats = json.load(f)
    else:
        stats = {"total_errors": 0, "errors_by_torrent": {}, "errors_by_message": {}}

    new_errors = []
    to_remove = []
    completed_log_entries = []
    paused_torrents = []
    downloading_count = 0
    queued_count = 0

    for tid, data in torrents.items():
        state = data[b"state"].decode()
        name = data[b"name"].decode()
        path = data[b"save_path"].decode()
        message = data.get(b"message", b"").decode()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if state == "Downloading":
            downloading_count += 1
        elif state == "Paused":
            paused_torrents.append(tid)
        elif state == "Queued":
            queued_count += 1

        if state == "Error":
            new_errors.append({
                "name": name, "save_path": path, "message": message,
                "tracker_status": data.get(b"tracker_status", b"").decode(),
                "timestamp": now
            })

            stats["total_errors"] += 1
            stats["errors_by_torrent"][name] = stats["errors_by_torrent"].get(name, 0) + 1
            stats["errors_by_message"][message] = stats["errors_by_message"].get(message, 0) + 1

            message_lower = message.lower()
            if any(err in message_lower for err in ["resume data", "bad address", "resource temporarily unavailable"]):
                log_resume_fix(f"{name}: {message}")
                if os.path.exists(path) and os.access(path, os.W_OK) and is_drive_mounted(path):
                    log_resume_fix(f"{name}: Drive is accessible. Trying resume only first.")
                    client.call("core.resume_torrent", [tid])
                    time.sleep(60)
                    refreshed = get_torrents()
                    new_state = refreshed[tid][b"state"].decode()
                    if new_state == "Error":
                        log_resume_fix(f"{name}: Still in error. Forcing recheck.")
                        client.call("core.force_recheck", [tid])
                        client.call("core.resume_torrent", [tid])
                        log_summary(f"{name}: ERROR - {message} | Recheck triggered after failed resume")
                    else:
                        log_resume_fix(f"{name}: Resumed successfully. No recheck needed.")
                        log_summary(f"{name}: ERROR - {message} | Recovered with resume only")
                else:
                    log_resume_fix(f"{name}: Path not mounted or writable. Skipping.")
                    log_summary(f"{name}: ERROR - {message} | Path inaccessible | Skipped")
            else:
                client.call("core.resume_torrent", [tid])
                log_summary(f"{name}: ERROR - {message} | General error | Resumed")
        elif state in ["Seeding", "Finished"]:
            if SEEDING_POLICY_ALLOW_SEEDING and state == "Seeding":
                log_summary(f"{name}: Seeding | Allowed to stay")
                continue
            to_remove.append((tid, name, path, now))
            log_summary(f"{name}: {state} | Queued for removal")
        else:
            log_summary(f"{name}: {state} | No action needed")

    slots_available = MAX_ACTIVE_DOWNLOADS - downloading_count
    if slots_available > 0 and queued_count == 0 and paused_torrents:
        to_resume = paused_torrents[:slots_available]
        client.call("core.resume_torrent", to_resume)
        for tid in to_resume:
            name = torrents[tid][b"name"].decode()
            log_summary(f"{name}: Paused | Resumed due to available slot")

    if to_remove:
        active_ids = client.call("core.get_session_state")
        for tid, name, path, now in to_remove:
            if tid in active_ids:
                completed_log_entries.append({
                    "name": name, "save_path": path, "timestamp": now
                })
                client.call("core.remove_torrent", tid, False)
                log_summary(f"{name}: Removed from session")

        if completed_log_entries:
            with open(COMPLETED_LOG_PATH, "a") as f:
                for entry in completed_log_entries:
                    f.write(json.dumps(entry, indent=2) + "\n\n")

    if new_errors:
        with open(ERROR_LOG_PATH, "a") as f:
            for entry in new_errors:
                f.write(json.dumps(entry, indent=2) + "\n\n")
        with open(STATS_LOG_PATH, "w") as f:
            json.dump(stats, f, indent=2)

except Exception as e:
    log_script_error(e)
