# WorkTracker ↔ clockodo verbinden — Schritt für Schritt

Diese Anleitung richtet den Sync von der lokalen **WorkTracker**-App nach **clockodo**
ein. Du arbeitest weiter nur in WorkTracker; die Zeiten (und Urlaub/Krank) landen
automatisch in clockodo.

> **Voraussetzungen:** macOS · die WorkTracker-App installiert · ein clockodo-Account,
> dessen Rolle einen **API-Key** erlaubt · dieses Repo lokal ausgecheckt.
>
> **Grundprinzip:** WorkTracker ist die Quelle der Wahrheit. Du buchst nie direkt in
> clockodo — immer in WorkTracker, der Rest passiert von selbst.

Jeder Schritt hat unten ein **✅ Kontrolle** — wenn das stimmt, weiter zum nächsten.

---

## Schritt 1 — WorkTracker-API einschalten

1. WorkTracker-App öffnen → Menüleisten-Icon → **Einstellungen**.
2. Reiter **API** → Häkchen **aktivieren**.

✅ **Kontrolle:** im Terminal
```bash
curl -s http://127.0.0.1:8787/api/health
```
→ Antwort `{"ok":true,"version":"…"}`. Kommt nichts, ist die API noch aus.

---

## Schritt 2 — Projekt einrichten (venv)

Im Terminal ins Repo wechseln und das Setup laufen lassen:
```bash
cd ~/Websites/worktracker-mcp
./setup.sh
```
Das legt `.venv/` an und installiert die Abhängigkeiten.

✅ **Kontrolle:**
```bash
./.venv/bin/python -c "import httpx; print('ok')"
```
→ gibt `ok` aus.

---

## Schritt 3 — clockodo API-Key holen

1. In clockodo einloggen → oben rechts auf **deinen Namen** → **Persönliche Daten**.
2. Abschnitt **API** → den **API-Schlüssel** kopieren (das ist NICHT dein Login-Passwort).

✅ **Kontrolle:** du hast eine lange Zeichenkette in der Zwischenablage.

---

## Schritt 4 — eigene clockodo-User-ID herausfinden

In clockodo: **Verwaltung → Benutzer → auf deinen Namen klicken**. In der Adresszeile
des Browsers steht dann eine Zahl (z. B. `…/users/469265`) — **das** ist deine `users_id`.

✅ **Kontrolle:** du hast eine reine Zahl (keine Mail).

> Wer den MCP schon eingerichtet hat, kann stattdessen im Chat das Tool
> `mcp__clockodo__get_me` aufrufen — die `users_id` steht in der Antwort.

---

## Schritt 5 — Zugangsdatei `clockodo_config.json` anlegen

Diese Datei ist **gitignored** (enthält den Key). Vorlage schreiben:
```bash
cd ~/Websites/worktracker-mcp
cat > clockodo_config.json <<'JSON'
{
  "api_user": "vorname.nachname@antegma.com",
  "api_key": "HIER_DEN_API_KEY",
  "users_id": 123456,
  "external_application": "worktracker-sync;vorname.nachname@antegma.com",
  "api_base": "https://my.clockodo.com/api",
  "endpoint_versions": { "entries": "v2", "customers": "v3", "services": "v4", "projects": "v4", "absences": "v4" }
}
JSON
```
Dann die Datei öffnen und **drei Werte** ersetzen: `api_user` (deine Mail, an **zwei**
Stellen — auch in `external_application`), `api_key` (aus Schritt 3), `users_id`
(aus Schritt 4). Der Rest bleibt.

✅ **Kontrolle:** die Datei ist gültiges JSON:
```bash
./.venv/bin/python -c "import json;json.load(open('clockodo_config.json'));print('config ok')"
```

---

## Schritt 6 — Testlauf (schreibt noch nichts)

```bash
./.venv/bin/python clockodo_sync.py --today --dry-run
```

✅ **Kontrolle:** es kommt eine Ausgabe ohne Fehler. Steht dort `clockodo GET … HTTP 401/403`,
stimmt einer der Werte in Schritt 5 nicht. „unmapped"-Hinweise sind hier normal (→ Schritt 7).

---

## Schritt 7 — Projekte zuordnen (Mapping)

Der Sync bucht nur, was er einem clockodo-Kunden zuordnen kann. Fehlstellen anzeigen:
```bash
./.venv/bin/python clockodo_reconcile.py
```
Fehlende Kunden/Projekte anlegen lassen (mit Backups, legt auch lokale WT-Projekte an):
```bash
./.venv/bin/python clockodo_reconcile.py --apply
```
Danach in `clockodo_map.json` die neu angelegten Stubs prüfen (jeder hat ein `_todo`:
stimmt Leistung/abrechenbar?).

✅ **Kontrolle:** erneuter Report zeigt bei „clockodo-Kunden OHNE Mapping" **(0)**:
```bash
./.venv/bin/python clockodo_reconcile.py | grep "OHNE Mapping"
```

---

## Schritt 8 — automatischen Hintergrund-Sync installieren (launchd)

Holt clockodo-seitige Änderungen zurück und heilt Tage, die direkt in der App geändert
wurden — alle 5 Minuten. Diesen Block **komplett** kopieren (er setzt die Pfade selbst):
```bash
cd ~/Websites/worktracker-mcp
PLIST=~/Library/LaunchAgents/com.antegma.worktracker-clockodo-sync.plist
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.antegma.worktracker-clockodo-sync</string>
  <key>ProgramArguments</key><array>
    <string>$PWD/.venv/bin/python</string>
    <string>$PWD/clockodo_sync.py</string>
    <string>--reconcile-window</string>
  </array>
  <key>StartInterval</key><integer>300</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$PWD/clockodo_sync.log</string>
  <key>StandardErrorPath</key><string>$PWD/clockodo_sync.log</string>
</dict></plist>
PL
launchctl unload "$PLIST" 2>/dev/null
launchctl load "$PLIST"
```

✅ **Kontrolle:**
```bash
launchctl list | grep worktracker-clockodo     # Zeile taucht auf
tail -3 clockodo_sync.log                        # nach <5 min: "… Tage geprueft …"
```

---

## Fertig — so läuft es im Alltag

- Du buchst in WorkTracker (oder per MCP-Tools `assign_time` / `set_day`). **WT → clockodo
  passiert sofort** nach jeder Änderung, zusätzlich alle 5 min der Hintergrund-Job.
- **Neuer Kunde in clockodo?** Einmal Schritt 7 wiederholen (oder im Chat das MCP-Tool
  `reconcile_clockodo` nutzen).
- **Tag direkt in der App geändert (ohne MCP)?** Der Job holt es binnen 5 min nach; sofort
  geht `./.venv/bin/python clockodo_sync.py 2026-08-06`.

## Wenn etwas klemmt

| Symptom | Ursache / Fix |
|---|---|
| `curl …/api/health` antwortet nicht | WorkTracker-API aus → Schritt 1 |
| `HTTP 401/403` beim Sync | `api_user` / `api_key` / `external_application` in `clockodo_config.json` prüfen |
| Zeit wird nicht gebucht | Segment „unmapped" → `clockodo_sync.log` ansehen, dann Schritt 7 |
| Uhrzeit in clockodo „falsch" | **kein Bug** bei Zeitzonen-Differenz: WT bucht in Rechnerzeit, clockodo zeigt Firmen-TZ (Berlin). Bulgarien (UTC+3): WT 08:00 → clockodo 07:00 (DE) |
| Job läuft nicht | `launchctl list \| grep worktracker` leer → Schritt 8 neu laden; Log ansehen |

## Dateien (Kurzreferenz)

| Datei | Zweck | in git? |
|---|---|---|
| `clockodo_sync.py` | Sync-Engine (Zeiten + Abwesenheiten) | ✅ |
| `clockodo_reconcile.py` | Stammdaten-Abgleich (Schritt 7) | ✅ |
| `clockodo_map.json` | WT-Projekt → Kunde/Projekt/Leistung | ✅ |
| `break_rules.json` | ArbZG-Pausenregeln (`check_breaks`) | ✅ |
| `clockodo_config.json` | **API-Zugang (Key!)** | 🚫 gitignored |
| `clockodo_state.json` · `clockodo_sync.log` | State / Log | 🚫 gitignored |
