#!/usr/bin/env bash
# Startet den WorkTracker-MCP-Server im HTTP-Modus (für ChatGPT Desktop).
# Für stdio-Agents wird dieses Skript NICHT gebraucht — die starten
# server.py selbst (siehe setup.sh / README).
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$DIR/.venv"

if [ ! -d "$VENV" ]; then
  "$DIR/setup.sh"
fi

echo "→ WorkTracker-MCP (HTTP) auf http://${WT_MCP_HOST:-127.0.0.1}:${WT_MCP_PORT:-8765}/mcp"
exec "$VENV/bin/python" "$DIR/server.py" --http "$@"
