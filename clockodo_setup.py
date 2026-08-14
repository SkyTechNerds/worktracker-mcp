#!/usr/bin/env python3
"""
Interaktiver Einrichter: WorkTracker -> clockodo
================================================

Wird von den Doppelklick-Startern aufgerufen ("Clockodo einrichten.command" auf
macOS, "Clockodo einrichten.bat" auf Windows) und fuehrt den Nutzer durch die
komplette Einrichtung, ohne dass er Config-Dateien oder System-Jobs von Hand
anfassen muss:

  1. fragt clockodo-E-Mail + API-Key ab
  2. holt die eigene users_id automatisch (per E-Mail-Match ueber /v3/users)
  3. schreibt clockodo_config.json (mit Backup)
  4. prueft die WorkTracker-API
  5. installiert den 5-Minuten-Hintergrundjob (launchd auf macOS, Task Scheduler
     auf Windows)
  6. zeigt, welche Projekte noch nicht zugeordnet sind (Mapping)

Cross-platform: die Logik ist identisch, nur der Hintergrundjob ist OS-spezifisch.
"""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

try:
    import getpass
    _getpass = getpass.getpass
except Exception:  # pragma: no cover
    _getpass = input

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "clockodo_config.json"
SYNC = HERE / "clockodo_sync.py"
API_BASE = "https://my.clockodo.com/api"
ENDPOINTS = {"entries": "v2", "customers": "v3", "services": "v4", "projects": "v4", "absences": "v4"}
JOB_LABEL = "com.antegma.worktracker-clockodo-sync"
TASK_NAME = "WorkTracker clockodo Sync"


def ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


# --------------------------------------------------------------------------
# clockodo
# --------------------------------------------------------------------------

def _get(url: str, headers: dict) -> dict:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def fetch_users_id(email: str, key: str) -> int | None:
    """users_id ueber E-Mail-Match aus /v3/users (verifizierter Endpoint)."""
    headers = {
        "X-ClockodoApiUser": email,
        "X-ClockodoApiKey": key,
        "X-Clockodo-External-Application": f"worktracker-sync;{email}",
    }
    data = _get(f"{API_BASE}/v3/users", headers)
    rows = data.get("data") or data.get("users") or []
    for u in rows:
        if str(u.get("email", "")).lower() == email.lower():
            return u.get("id")
    return None


# --------------------------------------------------------------------------
# WorkTracker-Config (fuer den Health-Check)
# --------------------------------------------------------------------------

def _wt_config_path() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "worktracker" / "config.json"
    if sys.platform.startswith("win"):
        import os
        appdata = os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))
        return Path(appdata) / "worktracker" / "config.json"
    return Path.home() / ".config" / "worktracker" / "config.json"


def check_worktracker() -> None:
    try:
        cfg = json.loads(_wt_config_path().read_text(encoding="utf-8"))
        api = cfg.get("apiServer", {})
        port, token = api.get("port", 8787), api.get("token", "")
        r = _get(f"http://127.0.0.1:{port}/api/health", {"Authorization": f"Bearer {token}"})
        if r.get("ok"):
            print(f"OK   WorkTracker-App erreichbar (v{r.get('version', '?')})")
            return
    except Exception:
        pass
    print("WARN WorkTracker-API nicht erreichbar. In der App: Einstellungen -> API einschalten.")
    print("     (Die Einrichtung laeuft trotzdem weiter; der Sync greift, sobald die API an ist.)")


# --------------------------------------------------------------------------
# Hintergrundjob (alle 5 min)
# --------------------------------------------------------------------------

def install_job() -> None:
    py = sys.executable  # der venv-Python, mit dem dieses Skript laeuft
    if sys.platform == "darwin":
        _install_launchd(py)
    elif sys.platform.startswith("win"):
        _install_schtasks(py)
    else:
        print("WARN Automatischer Job nur fuer macOS/Windows. Bitte 'clockodo_sync.py --reconcile-window' per cron/systemd einrichten.")


def _install_launchd(py: str) -> None:
    plist_dir = Path.home() / "Library" / "LaunchAgents"
    plist_dir.mkdir(parents=True, exist_ok=True)
    plist = plist_dir / f"{JOB_LABEL}.plist"
    log = HERE / "clockodo_sync.log"
    plist.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{JOB_LABEL}</string>
  <key>ProgramArguments</key><array>
    <string>{py}</string><string>{SYNC}</string><string>--reconcile-window</string>
  </array>
  <key>StartInterval</key><integer>300</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>{log}</string>
  <key>StandardErrorPath</key><string>{log}</string>
</dict></plist>
""", encoding="utf-8")
    subprocess.run(["launchctl", "unload", str(plist)], capture_output=True)
    res = subprocess.run(["launchctl", "load", str(plist)], capture_output=True, text=True)
    if res.returncode == 0:
        print("OK   Hintergrundjob installiert (launchd, alle 5 Minuten).")
    else:
        print(f"WARN Job-Installation meldete: {res.stderr.strip() or res.stdout.strip()}")


def _install_schtasks(py: str) -> None:
    # pythonw statt python -> laeuft ohne aufpoppendes Konsolenfenster
    pyw = str(Path(py).with_name("pythonw.exe"))
    if not Path(pyw).exists():
        pyw = py
    cmd = f'"{pyw}" "{SYNC}" --reconcile-window'
    res = subprocess.run(
        ["schtasks", "/Create", "/TN", TASK_NAME, "/TR", cmd,
         "/SC", "MINUTE", "/MO", "5", "/F"],
        capture_output=True, text=True,
    )
    if res.returncode == 0:
        print("OK   Hintergrundjob installiert (Task Scheduler, alle 5 Minuten).")
    else:
        print(f"WARN Job-Installation meldete: {res.stderr.strip() or res.stdout.strip()}")


# --------------------------------------------------------------------------
# Mapping-Status (nur Anzeige)
# --------------------------------------------------------------------------

def show_mapping_status() -> None:
    try:
        import clockodo_reconcile as rec
        a = rec.analyze()
        uncovered = a["uncovered_customers"]
        local_unmapped = a["local_without_map"]
        if not uncovered and not local_unmapped:
            print("OK   Alle Projekte sind zugeordnet.")
            return
        if uncovered:
            print("HINWEIS: clockodo-Kunden ohne Zuordnung: "
                  + ", ".join(c["name"] for c in uncovered))
        if local_unmapped:
            print("HINWEIS: lokale Projekte ohne Zuordnung: " + ", ".join(local_unmapped))
        print("         -> im Chat 'reconcile_clockodo apply=true' ausfuehren oder einen Dev fragen.")
    except Exception as exc:
        print(f"(Mapping-Status uebersprungen: {exc})")


# --------------------------------------------------------------------------

def main() -> int:
    print("\n=== WorkTracker  ->  clockodo einrichten ===\n")
    print("Du brauchst gleich: deine clockodo-Login-E-Mail und deinen clockodo-API-Key")
    print("(in clockodo: dein Name oben rechts -> Persoenliche Daten -> API -> Schluessel).\n")

    email = ask("clockodo-E-Mail: ")
    key = _getpass("clockodo API-Key (Eingabe bleibt unsichtbar): ").strip()
    if not email or not key:
        print("\nAbbruch: E-Mail und API-Key sind noetig.")
        return 1

    print("\n-> Pruefe Zugang und hole deine User-ID ...")
    try:
        uid = fetch_users_id(email, key)
    except urllib.error.HTTPError as e:
        print(f"FEHLER clockodo lehnt den Zugang ab (HTTP {e.code}). E-Mail/API-Key pruefen.")
        return 1
    except Exception as e:
        print(f"FEHLER keine Verbindung zu clockodo: {e}")
        return 1
    if not uid:
        print("FEHLER kein clockodo-Benutzer mit dieser E-Mail gefunden (genau die Login-Mail nehmen).")
        manual = ask("Optional User-ID manuell eingeben (sonst Enter = Abbruch): ")
        if not manual.isdigit():
            return 1
        uid = int(manual)
    print(f"OK   Zugang bestaetigt, User-ID {uid}")

    if CONFIG.exists():
        bak = CONFIG.with_name(CONFIG.name + ".bak")
        bak.write_bytes(CONFIG.read_bytes())
        print(f"     (bisherige Zugangsdaten gesichert: {bak.name})")
    CONFIG.write_text(json.dumps({
        "api_user": email, "api_key": key, "users_id": uid,
        "external_application": f"worktracker-sync;{email}",
        "api_base": API_BASE, "endpoint_versions": ENDPOINTS,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"OK   Zugangsdaten gespeichert ({CONFIG.name})")

    check_worktracker()
    install_job()
    show_mapping_status()

    print("\n============================================================")
    print("FERTIG. Deine WorkTracker-Zeiten (und Urlaub/Krank) landen ab")
    print("jetzt automatisch in clockodo -- du buchst weiter nur in WorkTracker.")
    print("============================================================\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nAbgebrochen.")
        sys.exit(1)
