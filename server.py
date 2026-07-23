#!/usr/bin/env python3
"""
WorkTracker MCP Server
======================

Stellt die lokale WorkTracker-HTTP-API (127.0.0.1, Token-geschützt) als
MCP-Tools bereit, damit ChatGPT Desktop (oder jeder andere MCP-Client)
Arbeitszeit-Blöcke lesen und schreiben kann — z. B. "buch mir 14–15:30
Figma-Arbeit für JUMO in WorkTracker".

Transport: Streamable HTTP (für ChatGPT-Desktop-Connector geeignet).
Default-URL: http://127.0.0.1:8765/mcp

Voraussetzung: WorkTracker läuft und API ist aktiviert
(Einstellungen → API). Port + Token werden aus der Config gelesen.
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

# --------------------------------------------------------------------------
# Config: Port + Token aus der WorkTracker-Config lesen
# --------------------------------------------------------------------------

DEFAULT_PORT = 8787


def _config_path() -> Path:
    """Plattform-abhängiger Pfad zur WorkTracker config.json."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "worktracker" / "config.json"
    if sys.platform.startswith("win"):
        appdata = os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))
        return Path(appdata) / "worktracker" / "config.json"
    # Linux / sonst
    return Path.home() / ".config" / "worktracker" / "config.json"


def _load_api() -> tuple[str, str]:
    """Liest (base_url, token) frisch aus der Config — bei jedem Call,
    damit ein App-Neustart mit neuem Token transparent funktioniert."""
    cfg_file = _config_path()
    if not cfg_file.is_file():
        raise RuntimeError(
            f"WorkTracker-Config nicht gefunden ({cfg_file}). "
            "Läuft die App? Ist die API aktiviert (Einstellungen → API)?"
        )
    cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
    api = cfg.get("apiServer", {})
    port = api.get("port", DEFAULT_PORT)
    token = api.get("token", "")
    if not token:
        raise RuntimeError(
            "Kein API-Token in der Config. Bitte in WorkTracker "
            "Einstellungen → API aktivieren."
        )
    return f"http://127.0.0.1:{port}", token


def _request(method: str, path: str, payload: dict | None = None) -> Any:
    """Führt einen HTTP-Request gegen die WorkTracker-API aus."""
    base, token = _load_api()
    headers = {"Authorization": f"Bearer {token}"}
    url = f"{base}{path}"
    try:
        with httpx.Client(timeout=10.0) as client:
            if method == "GET":
                resp = client.get(url, headers=headers)
            else:
                headers["Content-Type"] = "application/json"
                resp = client.post(url, headers=headers, json=payload or {})
    except httpx.ConnectError as exc:
        raise RuntimeError(
            f"Keine Verbindung zu {url}. Läuft WorkTracker und ist die API an?"
        ) from exc
    if resp.status_code == 401:
        raise RuntimeError("Unauthorized — Token falsch oder API neu gestartet.")
    resp.raise_for_status()
    return resp.json()


# --------------------------------------------------------------------------
# MCP-Server + Tools
# --------------------------------------------------------------------------

mcp = FastMCP(
    "worktracker",
    instructions=(
        "Liest und schreibt Arbeitszeit-Blöcke in der lokalen WorkTracker-App. "
        "Nutze diese Tools, wenn der User Arbeitszeit erfassen/korrigieren will "
        "('buch mir …', 'trag von X bis Y ein', 'war ein Meeting/eine Pause'). "
        "Workflow: bei Unsicherheit über gültige Projektnamen zuerst list_projects, "
        "für punktuelle Buchungen assign_time, danach dem User die gesetzten Blöcke "
        "bestätigen. Zeiten auf 15-Min-Raster runden, außer der User nennt exakte Zeiten."
    ),
)


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _push_clockodo(date: str) -> dict | None:
    """Spiegelt einen Tag nach clockodo (WorkTracker ist Quelle der Wahrheit).

    Bewusst fehlertolerant: ein clockodo-Problem darf NIE einen WorkTracker-
    Schreibzugriff scheitern lassen. Fehlt die clockodo-Config, passiert nichts.
    """
    try:
        import clockodo_sync  # lazy, damit der Server ohne clockodo weiterlaeuft
    except Exception:
        return None
    try:
        return clockodo_sync.sync_day(date)
    except Exception as exc:  # noqa: BLE001 - Sync ist best effort
        sys.stderr.write(f"[clockodo-sync] {date}: {exc}\n")
        return {"date": date, "error": str(exc)}


@mcp.tool()
def health() -> dict:
    """Prüft, ob die WorkTracker-API erreichbar ist. Gibt Status + Version zurück."""
    base, _ = _load_api()
    with httpx.Client(timeout=5.0) as client:
        return client.get(f"{base}/api/health").json()


@mcp.tool()
def list_projects() -> Any:
    """Listet alle gültigen Projektnamen (+ Farben). Vor dem Setzen eines
    `project` immer einen exakten Namen von hier verwenden, sonst fehlt die Farbe."""
    return _request("GET", "/api/projects")


@mcp.tool()
def get_day(date: str | None = None) -> Any:
    """Liest alle Zeit-Segmente eines Tages.

    Args:
        date: Datum als YYYY-MM-DD. Leer = heute.
    """
    return _request("GET", f"/api/day?date={date or _today()}")


@mcp.tool()
def assign_time(
    from_time: str,
    to_time: str,
    date: str | None = None,
    kind: str = "work",
    ticket: str | None = None,
    project: str | None = None,
    note: str | None = None,
    meeting: bool = False,
) -> Any:
    """Setzt EINEN Zeitbereich (schneidet ihn aus bestehenden Blöcken aus und
    belegt ihn neu). Mittel der Wahl für punktuelle Buchungen/Korrekturen.

    Args:
        from_time: Startzeit "HH:MM".
        to_time: Endzeit "HH:MM".
        date: Datum YYYY-MM-DD. Leer = heute.
        kind: "work" (Standard) oder "break" (Pause).
        ticket: Ticket/Titel, z. B. "WCMS-2155" oder "Figma Hero" (optional).
        project: exakter Projektname aus list_projects (bringt die Farbe; optional).
        note: kurze Beschreibung (optional).
        meeting: True markiert den Block als Meeting (darf ein project tragen
                 → wird dem Kunden abrechenbar zugerechnet).
    """
    payload: dict[str, Any] = {
        "date": date or _today(),
        "from": from_time,
        "to": to_time,
        "kind": kind,
    }
    if ticket is not None:
        payload["ticket"] = ticket
    if project is not None:
        payload["project"] = project
    if note is not None:
        payload["note"] = note
    if meeting:
        payload["meeting"] = True
    result = _request("POST", "/api/assign", payload)
    return {"worktracker": result, "clockodo_sync": _push_clockodo(payload["date"])}


@mcp.tool()
def set_day(segments: list[dict], date: str | None = None) -> Any:
    """Ersetzt den GANZEN Tag durch die übergebenen Segmente. Nur nehmen, wenn
    der Tag komplett neu aufgebaut werden soll — sonst assign_time verwenden.

    Args:
        segments: Liste von Blöcken, je
            {"from":"HH:MM","to":"HH:MM","kind":"work|break",
             "ticket":?, "project":?, "note":?, "meeting":?}.
        date: Datum YYYY-MM-DD. Leer = heute.
    """
    d = date or _today()
    result = _request("POST", "/api/day", {"date": d, "segments": segments})
    return {"worktracker": result, "clockodo_sync": _push_clockodo(d)}


@mcp.tool()
def sync_clockodo(dates: list[str] | None = None) -> Any:
    """Spiegelt einen oder mehrere Tage explizit nach clockodo (WorkTracker ist
    Quelle der Wahrheit). Normalerweise laeuft der Sync automatisch nach jedem
    Schreibzugriff; dieses Tool ist fuer Nachbuchungen/Backfill oder wenn ein Tag
    direkt in der WorkTracker-App (ohne MCP) geaendert wurde.

    Args:
        dates: Liste von Tagen YYYY-MM-DD. Leer = heute.
    """
    import clockodo_sync

    target = dates or [_today()]
    return clockodo_sync.sync_days(target)


@mcp.tool()
def check_breaks(dates: list[str] | None = None) -> Any:
    """Prueft Tage gegen die gesetzlichen Pausenregeln (ArbZG §4):
    >6 h Arbeit -> mind. 30 min Pause, >9 h -> 45 min; max. 6 h am Stueck ohne
    Pause; Pausen duerfen gestueckelt werden, zaehlen aber nur ab 15 min.
    Meldet je Tag Ist/Soll-Pause, laengste Arbeitsstrecke und Verstoesse.
    Regeln stehen in break_rules.json (anpassbar). Aendert nichts.

    Args:
        dates: Liste von Tagen YYYY-MM-DD. Leer = heute.
    """
    import clockodo_sync

    target = dates or [_today()]
    return [clockodo_sync.check_breaks(d) for d in target]


@mcp.tool()
def reset_day(date: str | None = None) -> Any:
    """Setzt einen Tag auf die automatische Erfassung zurück (verwirft manuelle Blöcke).

    Args:
        date: Datum YYYY-MM-DD. Leer = heute.
    """
    d = date or _today()
    result = _request("POST", "/api/reset", {"date": d})
    return {"worktracker": result, "clockodo_sync": _push_clockodo(d)}


# --------------------------------------------------------------------------
# Entrypoint
# --------------------------------------------------------------------------

def main() -> None:
    """Startet den Server. Transport per CLI/Env wählbar:

    - stdio (Default): für Agents, die den Prozess selbst starten
      (Claude Code, Cursor, Codex, Windsurf, …).
    - Streamable HTTP (--http): für Clients, die sich per URL verbinden
      (ChatGPT Desktop Connector). Default-URL http://127.0.0.1:8765/mcp.
    """
    import argparse

    parser = argparse.ArgumentParser(description="WorkTracker MCP-Server")
    parser.add_argument(
        "--http",
        action="store_true",
        help="Streamable HTTP statt stdio (für ChatGPT-Desktop-Connector).",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("WT_MCP_HOST", "127.0.0.1"),
        help="Host für HTTP-Transport (Default 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("WT_MCP_PORT", "8765")),
        help="Port für HTTP-Transport (Default 8765).",
    )
    args = parser.parse_args()

    # Env-Override erlaubt das Erzwingen von HTTP ohne CLI-Flag.
    use_http = args.http or os.environ.get("WT_MCP_TRANSPORT", "").lower() in {
        "http",
        "streamable-http",
    }

    if use_http:
        # Host/Port für den MCP-HTTP-Endpunkt (nicht die WorkTracker-API!)
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
