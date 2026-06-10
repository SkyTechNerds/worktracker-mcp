# WorkTracker – MCP-Server

Ein **MCP-Server** für [WorkTracker](https://github.com/SkyTechNerds/WorkTracker), der die
lokale HTTP-API der App (`127.0.0.1`, Token-geschützt) als Tools bereitstellt.

[MCP (Model Context Protocol)](https://modelcontextprotocol.io) ist ein **offener Standard** —
nicht an einen Anbieter gebunden. Derselbe Server funktioniert daher in **jedem MCP-fähigen
Agent**: Claude Code, ChatGPT Desktop, Cursor, Codex CLI, Windsurf, Zed u. a. Damit kannst du
überall natürlichsprachlich buchen:

> „Buch mir von 14 bis 15:30 Figma-Arbeit für JUMO in WorkTracker."
> „Die Zeit von 12:30 bis 13 war Pause."
> „09–10 Uhr war das Jumo Daily, war ein Meeting."

> **Ersetzt den Claude-Skill.** Dieser MCP-Server deckt alles ab, was der frühere
> [`worktracker`-Claude-Skill](https://github.com/SkyTechNerds/WorkTracker-Claude) konnte —
> in Claude Code bindest du ihn jetzt einfach als MCP-Server ein (siehe unten) statt als Skill.

---

## Zwei Transports — ein Server

| Transport | Wann | Wie der Client startet |
|---|---|---|
| **stdio** (Default) | lokale Agents, die den Prozess selbst spawnen | Client ruft `python server.py` |
| **Streamable HTTP** (`--http`) | URL-basierte Connectors (ChatGPT Desktop) | `./run.sh` → `http://127.0.0.1:8765/mcp` |

---

## Voraussetzungen

1. **WorkTracker** läuft und **Einstellungen → API** ist aktiviert (erzeugt Port + Token).
   Der Server liest beides bei jedem Aufruf automatisch aus der Config.
2. **Python 3.10+**.

API + MCP-Server hören ausschließlich auf `127.0.0.1`.

## Einrichtung (einmalig)

```bash
cd worktracker-mcp
./setup.sh      # legt .venv an, installiert Abhängigkeiten, gibt die Pfade aus
```

`setup.sh` druckt den absoluten `python`-Pfad und den `server.py`-Pfad, die du unten in
den Client-Configs einträgst.

---

## Einbindung pro Client

> In allen stdio-Beispielen `ABS` = Ausgabe von `setup.sh`, also
> `command = <repo>/.venv/bin/python`, `args = [<repo>/server.py]`.

### Claude Code (stdio)
```bash
claude mcp add worktracker -- /ABS/.venv/bin/python /ABS/server.py
```
oder in `~/.claude.json` bzw. projektlokaler `.mcp.json`:
```jsonc
{
  "mcpServers": {
    "worktracker": {
      "command": "/ABS/.venv/bin/python",
      "args": ["/ABS/server.py"]
    }
  }
}
```

### Cursor / Windsurf / Zed (stdio)
`~/.cursor/mcp.json` (bzw. das jeweilige MCP-Config-File):
```jsonc
{
  "mcpServers": {
    "worktracker": {
      "command": "/ABS/.venv/bin/python",
      "args": ["/ABS/server.py"]
    }
  }
}
```

### Codex CLI (stdio)
In `~/.codex/config.toml`:
```toml
[mcp_servers.worktracker]
command = "/ABS/.venv/bin/python"
args = ["/ABS/server.py"]
```

### ChatGPT Desktop (HTTP)
1. Server starten — Terminal offen lassen:
   ```bash
   ./run.sh        # → http://127.0.0.1:8765/mcp
   ```
2. ChatGPT Desktop → **Settings → Connectors** → Developer Mode aktivieren →
   **Custom Connector**.
3. **MCP Server URL:** `http://127.0.0.1:8765/mcp` · **Authentication:** *None*.
4. Connector aktivieren, im Chat z. B.:
   *„Buch mir 13:00–17:00 WCMS-2639 für JUMO-Website-CMS in WorkTracker."*

> Custom-MCP-Connector im Developer Mode setzt einen passenden ChatGPT-Plan voraus
> (Plus/Pro/Team). Ohne das: stdio-Variante über Codex CLI nutzen.

---

## Tools

| Tool | Zweck | API-Route |
|---|---|---|
| `health` | Erreichbarkeit + Version | `GET /api/health` |
| `list_projects` | gültige Projektnamen + Farben | `GET /api/projects` |
| `get_day` | Tag lesen (alle Segmente) | `GET /api/day` |
| `assign_time` | **einen Zeitbereich setzen** (ausschneiden + belegen) | `POST /api/assign` |
| `set_day` | ganzen Tag ersetzen | `POST /api/day` |
| `reset_day` | Tag auf Auto-Erfassung zurücksetzen | `POST /api/reset` |

### Datenmodell

- **date**: `YYYY-MM-DD` (leer = heute)
- **from/to**: `"HH:MM"`
- **kind**: `"work"` (Standard) oder `"break"` (Pause)
- **ticket**: Ticket/Titel, z. B. `"WCMS-2155"` oder `"Figma Hero"`
- **project**: exakter Name aus `list_projects` (bringt die Farbe)
- **meeting**: `true` → als Meeting markiert; darf ein `project` (Kunde) tragen → abrechenbar

`assign_time` ist das Mittel der Wahl für punktuelle Buchungen. `set_day` nur, wenn der ganze
Tag neu aufgebaut wird. Zeiten möglichst auf 15-Min-Raster, außer du nennst exakte Werte.

---

## Troubleshooting

- **„Keine Verbindung zu 127.0.0.1:8787"** → WorkTracker läuft nicht oder API ist aus.
- **„Unauthorized"** → App neu gestartet → neues Token; der Server liest es bei jedem Call
  frisch, ein Client-Reload genügt (kein Server-Neustart nötig).
- **stdio-Client findet den Server nicht** → absolute Pfade aus `setup.sh` verwenden, nicht
  relative; `python` muss der venv-Python sein.
- **ChatGPT erreicht den Server nicht** → `run.sh`-Terminal offen? URL exakt `…/mcp`?
  Developer Mode an?

## Lizenz

MIT
