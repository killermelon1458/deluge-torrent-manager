from pathlib import Path

# Markdown content for the deluge-vpn-setup.md file
markdown_content = """
# Deluge VPN Setup on Headless Linux (Virtualenv + Systemd + UFW)

This document outlines the complete setup of a secure Deluge torrent box running on a headless Linux system. The configuration includes:

- Python virtual environment
- Deluge daemon autostart via systemd user service
- Firewall (UFW) configuration for VPN
- Cron jobs for torrent health/error handling
- Logging and auto-recovery

---

## 🧰 Environment Summary

| Component       | Description                      |
|----------------|----------------------------------|
| OS             | Headless Linux (User: `torbox`)  |
| VPN            | tun0 interface (e.g. Surfshark)  |
| Deluge Version | 2.2.0 (via virtualenv)           |
| Libtorrent     | 2.0.11.0                         |
| Python         | 3.10 (virtualenv)                |

---

## 🗃️ File Structure

