#!/usr/bin/env bash
# Einmalige Einrichtung: venv anlegen + Abhängigkeiten installieren.
# Danach den absoluten Python-Pfad ausgeben, den die Agent-Configs brauchen.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$DIR/.venv"

if [ ! -d "$VENV" ]; then
  echo "→ Lege venv an…"
  python3 -m venv "$VENV"
fi
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q -r "$DIR/requirements.txt"

echo "✓ Eingerichtet."
echo
echo "  Für stdio-Agents (Claude Code, Cursor, Codex, Windsurf) diese Werte nutzen:"
echo "    command: $VENV/bin/python"
echo "    args:    $DIR/server.py"
echo
echo "  Für ChatGPT Desktop (HTTP):  ./run.sh   → http://127.0.0.1:8765/mcp"
