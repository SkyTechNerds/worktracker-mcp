# WorkTracker ↔ clockodo verbinden

Deine WorkTracker-Zeiten (und Urlaub/Krank) automatisch nach **clockodo** spiegeln.
Du arbeitest weiter nur in WorkTracker — der Rest passiert von selbst.

---

## Einrichten in 3 Schritten (empfohlen)

Kein Terminal nötig — der Installer macht alles. Du brauchst nur zweimal etwas einfügen.

### 1. In der WorkTracker-App die API einschalten
Menüleisten-Icon (Mac) bzw. Tray-Icon (Windows) → **Einstellungen → API → aktivieren**.

### 2. Deinen clockodo-API-Key kopieren
In clockodo: **dein Name oben rechts → Persönliche Daten → Abschnitt API → API-Schlüssel** kopieren.
(Das ist **nicht** dein Login-Passwort. Der Key ist persönlich — teile ihn mit niemandem.)

### 3. Den Installer doppelklicken
Im entpackten Projektordner:

| Betriebssystem | Datei zum Doppelklicken |
|---|---|
| **macOS** | `Clockodo einrichten.command` |
| **Windows** | `Clockodo einrichten.bat` |

Es öffnet sich ein Fenster und fragt nach **E-Mail** und **API-Key** (aus Schritt 2, einfügen).
Danach läuft alles automatisch und endet mit **„FERTIG"**.

> **macOS-Hinweis:** Wenn beim ersten Mal „nicht geöffnet, unbekannter Entwickler" kommt →
> **Rechtsklick auf die Datei → „Öffnen" → „Öffnen"**. Danach nie wieder.
>
> **Windows-Hinweis:** Braucht einmalig [Python](https://www.python.org/downloads/) (beim Setup
> **„Add Python to PATH"** anhaken). Bei „Windows hat den PC geschützt" → **„Weitere Informationen → Trotzdem ausführen"**.

**Das war's.** Ab jetzt landen deine Buchungen automatisch in clockodo.

### Was der Installer im Hintergrund erledigt
1. richtet die Programmumgebung ein · 2. prüft deinen Zugang und holt deine User-ID automatisch ·
3. speichert die Zugangsdaten (`clockodo_config.json`, bleibt lokal) · 4. prüft die WorkTracker-API ·
5. installiert den 5-Minuten-Sync-Job (launchd bzw. Task Scheduler) · 6. sagt dir, ob noch Projekte
zuzuordnen sind.

---

## Wenn etwas klemmt

| Symptom | Lösung |
|---|---|
| „WorkTracker-API nicht erreichbar" | Schritt 1 — API in der App einschalten, dann Installer erneut. |
| „clockodo lehnt den Zugang ab (HTTP 401/403)" | E-Mail = deine **Login**-Mail? API-Key frisch aus clockodo kopiert? |
| „kein Benutzer mit dieser E-Mail gefunden" | Genau die clockodo-Login-Mail verwenden (Groß/klein egal). |
| Eine Zeit taucht in clockodo nicht auf | Projekt ist noch **nicht zugeordnet** → siehe „Projekte zuordnen" unten. |
| Uhrzeit in clockodo „falsch" | **kein Fehler** bei Zeitzonen-Differenz: WorkTracker bucht in Rechnerzeit, clockodo zeigt Firmenzeit (Berlin). Rechner in Bulgarien (UTC+3): WT 08:00 → clockodo 07:00 (DE). |

## Projekte zuordnen (falls „noch nicht zugeordnet")

Der Sync bucht nur, was einem clockodo-Kunden zugeordnet ist. Neue Kunden/Projekte nachziehen —
am einfachsten im Chat über das MCP-Tool **`reconcile_clockodo`** (`apply=true`), oder einen Dev fragen.
Manuell: `./.venv/bin/python clockodo_reconcile.py --apply` (mit Backups).

---

## Anhang: manuelle Einrichtung (für Entwickler)

Wer den Installer nicht nutzen will, macht dieselben Schritte von Hand:

1. **WorkTracker-API an** (App → Einstellungen → API). Kontrolle: `curl -s http://127.0.0.1:8787/api/health`.
2. **venv:** `./setup.sh` (legt `.venv/` an, installiert Abhängigkeiten).
3. **Zugang `clockodo_config.json`** (gitignored) anlegen:
   ```json
   {
     "api_user": "vorname.nachname@antegma.com",
     "api_key": "<clockodo API-Key>",
     "users_id": 123456,
     "external_application": "worktracker-sync;vorname.nachname@antegma.com",
     "api_base": "https://my.clockodo.com/api",
     "endpoint_versions": { "entries": "v2", "customers": "v3", "services": "v4", "projects": "v4", "absences": "v4" }
   }
   ```
   `users_id` = eigene clockodo-ID (clockodo → Verwaltung → Benutzer → dein Eintrag, Zahl in der URL).
4. **Testlauf:** `./.venv/bin/python clockodo_sync.py --today --dry-run` (schreibt nichts).
5. **Mapping:** `./.venv/bin/python clockodo_reconcile.py --apply`.
6. **Hintergrundjob:** launchd-`.plist` unter `~/Library/LaunchAgents/` (macOS) bzw. `schtasks`
   (Windows), Aufruf `clockodo_sync.py --reconcile-window` alle 300 s. Der Installer
   (`clockodo_setup.py`) erzeugt genau das automatisch — als Vorlage dort nachschauen.

### Wie es funktioniert
- **WT → clockodo:** sofort nach jedem MCP-Schreibzugriff (`server.py` → `_push_clockodo`).
- **clockodo → WT:** der 5-Minuten-Job (rollierendes 14-Tage-Fenster), weil clockodo nicht pusht.
- **WT ist Quelle der Wahrheit** („Last-Writer-Wins pro Tag"). Abwesenheiten werden nur **angelegt**, nie storniert.
- **Mapping** (`clockodo_map.json`): pro WT-Segment → `kind==break` nie · `byProject` · `byTicketContains` ·
  `meetingDefault` · sonst *unmapped* (wird nicht gebucht, nur im Log gewarnt).

### Dateien

| Datei | Zweck | in git? |
|---|---|---|
| `Clockodo einrichten.command` / `.bat` | Doppelklick-Installer (Mac/Windows) | ✅ |
| `clockodo_setup.py` | geführter Einrichter (von den Startern aufgerufen) | ✅ |
| `clockodo_sync.py` | Sync-Engine (Zeiten + Abwesenheiten) | ✅ |
| `clockodo_reconcile.py` | Stammdaten-Abgleich | ✅ |
| `clockodo_map.json` | WT-Projekt → Kunde/Projekt/Leistung | ✅ |
| `break_rules.json` | ArbZG-Pausenregeln | ✅ |
| `clockodo_config.json` | **API-Zugang (Key!)** | 🚫 gitignored |
| `clockodo_state.json` · `clockodo_sync.log` | State / Log | 🚫 gitignored |
