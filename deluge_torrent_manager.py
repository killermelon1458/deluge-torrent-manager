#!/usr/bin/env python3
# V15: SAFER MOVES — only move files that belong to the current torrent (using Deluge 'files'),
#      preserves overwrite/resume behavior, never deletes download roots, logs live.

from deluge_client import DelugeRPCClient
import os
import shutil
import traceback
import re
import time
from datetime import datetime

# how long to wait for a destination directory to appear/respond (network/USB spin-up)
DEST_WAIT_TIMEOUT = 30  # seconds total
DEST_WAIT_INTERVAL = 2  # seconds between retries

# Configurable base directories
CONFIG = {
    "TV": {
        "DOWNLOAD_DIR": "/home/torbox/Temp_Plex/TV_Shows",
        "FINAL_DIR": "/plex/TV_Shows/Plex/TV_Shows"
    },
    "Movies": {
        "DOWNLOAD_DIR": "/home/torbox/Temp_Plex/Movies",
        "FINAL_DIR": "/plex/Movies/Plex/Movies"
    },
    "ARCHIVE_DIR": "/mnt/Plex_file_list/torrent_archive",
    "DELUGE": {
        "HOST": "localhost",
        "PORT": 58846,
        "USERNAME": "localclient",
        "PASSWORD": "a60f7c1ecee3d0ebea9f9d0dcfcbe8aba7225b95"
    }
}

LOG_ENTRIES = []

# ---------------- util/logging ----------------

def log(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{timestamp}] {message}"
    LOG_ENTRIES.append(entry)
    print(entry, flush=True)


def write_log():
    if not LOG_ENTRIES:
        return
    archive_dir = CONFIG["ARCHIVE_DIR"]
    os.makedirs(archive_dir, exist_ok=True)
    log_file = os.path.join(archive_dir, "torrent_manager.log")
    try:
        with open(log_file, "a") as f:
            f.write("\n".join(LOG_ENTRIES) + "\n")
    except Exception as e:
        print(f"Failed to write log to {log_file}: {e}")


def connect_to_deluge():
    client = DelugeRPCClient(
        CONFIG["DELUGE"]["HOST"],
        CONFIG["DELUGE"]["PORT"],
        CONFIG["DELUGE"]["USERNAME"],
        CONFIG["DELUGE"]["PASSWORD"]
    )
    client.connect()
    return client


def sanitize_filename(name):
    name = name.strip()
    return re.sub(r'[\\/:"*?<>|]+', '_', name)


def wait_for_directory(path, timeout=DEST_WAIT_TIMEOUT, interval=DEST_WAIT_INTERVAL):
    """Wait until `path` exists and is listable, or give up after timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if os.path.isdir(path):
                next(os.scandir(path), None)
                return True
        except Exception:
            pass
        time.sleep(interval)
    return False


def normalize_tid(tid):
    if isinstance(tid, (bytes, bytearray)):
        try:
            return tid.decode("utf-8")
        except Exception:
            # fallback to hex if it's binary
            return tid.hex()
    return str(tid)

# ---------------- archival ----------------

def save_torrent_file(torrent_info, torrent_id, archive_path):
    try:
        if os.path.exists(archive_path):
            log(f"Using previously archived .torrent, skipping copy: {archive_path}")
            return True
        tid = normalize_tid(torrent_id)
        home = os.path.expanduser("~")
        src = os.path.join(home, ".config", "deluge", "state", f"{tid}.torrent")
        if os.path.exists(src):
            shutil.copy2(src, archive_path)
            log(f"Copied .torrent from {src} to {archive_path}")
            return True
        else:
            name = torrent_info.get(b"name", b"<unknown>").decode('utf-8', errors='ignore') if isinstance(torrent_info.get(b"name", b""), (bytes, bytearray)) else torrent_info.get("name", "<unknown>")
            log(f".torrent for '{name}' ({tid}) missing, skipping archive.")
            return False
    except Exception as e:
        nm = torrent_info.get(b"name", b"<unknown>").decode('utf-8', errors='ignore') if isinstance(torrent_info.get(b"name", b""), (bytes, bytearray)) else torrent_info.get("name", "<unknown>")
        log(f"Error archiving .torrent for {nm}: {e}")
        return False

# ---------------- moving logic (SAFER) ----------------

def get_top_components(file_list):
    """Return a set of top-level components for the torrent's files (relative to torrent root)."""
    tops = set()
    for f in file_list or []:
        # handle bytes/str keys
        p = f.get(b"path") if isinstance(f, dict) else None
        if p is None:
            p = f.get("path") if isinstance(f, dict) else None
        if p is None:
            continue
        if isinstance(p, (bytes, bytearray)):
            p = p.decode("utf-8", errors="ignore")
        p = p.replace("\\", "/")  # normalize separators
        top = p.split("/", 1)[0]
        if top:
            tops.add(top)
    return tops


def move_torrent_contents(tid, name, save_path, download_base, final_base, file_list):
    """Move only the current torrent's items from save_path to the destination.

    - If save_path == download_base: move only the top components reported by Deluge into
      final_base/<sanitized name>/
    - Else: move only those components from within save_path into final_base/<relpath>/
    - Overwrite existing dest paths; never delete the download base; only remove empty dirs.
    """
    abs_save = os.path.abspath(save_path)
    abs_download = os.path.abspath(download_base)

    # Determine relative root under download base
    try:
        rel_root = os.path.relpath(abs_save, abs_download)
    except ValueError:
        if not abs_save.startswith(abs_download):
            log(f"Skipped move: {save_path} not under {download_base}")
            return False
        rel_root = abs_save[len(abs_download):].lstrip(os.sep)

    tops = sorted(get_top_components(file_list))
    if not tops:
        # nothing to move; may be a weird state
        log(f"No file list found for '{name}' ({normalize_tid(tid)}); nothing to move.")
        return False

    # Destination directory selection
    
    if rel_root in (".", ""):
    	# saving directly in the download base → mirror structure directly under FINAL_DIR (no extra folder)
        dest_dir = final_base
    else:
        dest_dir = os.path.join(final_base, rel_root)

    os.makedirs(dest_dir, exist_ok=True)

    moved_any = False
    for top in tops:
        src = os.path.join(abs_save, top)
        dst = os.path.join(dest_dir, top)
        if not os.path.exists(src):
            log(f"Source missing for '{name}': {src} (skipping this component)")
            continue
        # overwrite destination if present
        if os.path.exists(dst):
            try:
                if os.path.isdir(dst):
                    shutil.rmtree(dst)
                else:
                    os.remove(dst)
                log(f"Overwriting existing destination {dst}")
            except Exception as e:
                log(f"Error removing existing {dst}: {e}")
        try:
            shutil.move(src, dst)
            log(f"Moved {src} -> {dst}")
            moved_any = True
        except Exception as e:
            log(f"Error moving {src} -> {dst}: {e}")

    # cleanup: remove now-empty save_path if it's not the download base or a root
    try:
        if os.path.isdir(abs_save) and abs_save != abs_download:
            # remove empty directories up the chain until reaching download_base
            cur = abs_save
            while cur.startswith(abs_download):
                try:
                    entries = os.listdir(cur)
                except Exception:
                    break
                if entries:
                    break
                os.rmdir(cur)
                log(f"Removed empty directory {cur}")
                if cur == abs_download:
                    break
                cur = os.path.dirname(cur)
    except Exception as e:
        log(f"Error during cleanup under {abs_save}: {e}")

    return moved_any

# ---------------- main ----------------

def main():
    log("Script started V15")
    try:
        client = connect_to_deluge()
        log("Connected to Deluge RPC")
        # Ask for 'files' so we can move only what's ours
        torrents = client.call("core.get_torrents_status", {}, ["name","is_finished","save_path","files"]) 
        did_work = False

        for tid, data in torrents.items():
            if not data.get(b"is_finished"):
                continue
            # decode fields safely
            name = data.get(b"name", b"")
            name = name.decode('utf-8', errors='ignore') if isinstance(name, (bytes, bytearray)) else str(name)
            save_path = data.get(b"save_path", b"")
            save_path = save_path.decode('utf-8', errors='ignore') if isinstance(save_path, (bytes, bytearray)) else str(save_path)
            files = data.get(b"files") if b"files" in data else data.get("files")

            matched = False
            for cat in ("TV","Movies"):
                src_base = CONFIG[cat]["DOWNLOAD_DIR"]
                final_base = CONFIG[cat]["FINAL_DIR"]
                abs_path = os.path.abspath(save_path)
                try:
                    in_tree = os.path.commonpath([abs_path, os.path.abspath(src_base)]) == os.path.abspath(src_base)
                except ValueError:
                    in_tree = abs_path.startswith(os.path.abspath(src_base))
                if in_tree:
                    matched = True
                    if not wait_for_directory(final_base):
                        log(f"Destination {final_base} unavailable, skipping '{name}'")
                        break
                    log(f"Starting transfer for '{name}' (ID: {normalize_tid(tid)}) to '{final_base}'")

                    # archive .torrent
                    sanitized = sanitize_filename(name)
                    archive_path = os.path.join(CONFIG["ARCHIVE_DIR"], f"{sanitized}_{normalize_tid(tid)}.torrent")
                    a_ok = save_torrent_file(data, tid, archive_path)

                    # move only this torrent's files
                    m_ok = move_torrent_contents(tid, name, save_path, src_base, final_base, files)

                    if a_ok and m_ok:
                        client.call("core.remove_torrent", tid, False)
                        log(f"Removed '{name}'")
                        did_work = True
                    else:
                        log(f"Skipped removal for '{name}' a_ok={a_ok}, m_ok={m_ok}")
                    break
            if not matched:
                log(f"Skipped '{name}' — outside monitored dirs (save_path={save_path})")

        if not did_work:
            log("No torrents processed.")
        write_log()
    except Exception as e:
        log(f"Fatal: {e}\n{traceback.format_exc()}")
        write_log()

if __name__ == "__main__":
    main()

