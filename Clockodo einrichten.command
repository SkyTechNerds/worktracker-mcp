#!/bin/bash
# Doppelklick im Finder -> richtet WorkTracker->clockodo ein. Nichts von Hand noetig.
cd "$(dirname "$0")" || exit 1

echo "Richte WorkTracker -> clockodo ein ..."
echo

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 fehlt. Bitte einmalig installieren:"
  echo "  Terminal oeffnen und  xcode-select --install  ausfuehren, dann erneut doppelklicken."
  echo
  read -n1 -r -p "Enter zum Schliessen ..."
  exit 1
fi

# Virtuelle Umgebung + Abhaengigkeiten (nur beim ersten Mal spuerbar)
[ -d .venv ] || python3 -m venv .venv
./.venv/bin/python -m pip install -q --upgrade pip >/dev/null 2>&1
./.venv/bin/python -m pip install -q -r requirements.txt

# Der eigentliche, gefuehrte Einrichter
./.venv/bin/python clockodo_setup.py

echo
read -n1 -r -p "Enter zum Schliessen ..."
