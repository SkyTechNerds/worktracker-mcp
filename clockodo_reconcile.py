#!/usr/bin/env python3
"""
clockodo <-> WorkTracker  Stammdaten-Abgleich
=============================================

Der Zeit-/Abwesenheits-Sync (clockodo_sync.py) gleicht KEINE Stammdaten ab:
neue clockodo-Kunden/Projekte tauchen weder lokal noch im Mapping automatisch
auf. Dieses Tool schliesst die Luecke.

Es liest die aktiven Kunden/Projekte/Leistungen aus clockodo und vergleicht:
  * clockodo_map.json  (byProject-Regeln + _clockodo_ref-Schnappschuss)
  * WorkTracker config.json (lokale Projekte, im "+"-Dialog waehlbar)

Default = nur Bericht (aendert nichts). Anwenden nur mit Flags:

    python clockodo_reconcile.py                 # Bericht
    python clockodo_reconcile.py --json          # Bericht als JSON
    python clockodo_reconcile.py --refresh-ref   # _clockodo_ref auf Live-Stand
    python clockodo_reconcile.py --add-map       # byProject-Stubs fuer ungemappte Kunden
    python clockodo_reconcile.py --add-local     # fehlende lokale WT-Projekte anlegen
    python clockodo_reconcile.py --apply         # alle drei

Sicherheit:
  * Jede Datei wird vor dem Schreiben gesichert (*.bak.reconcile.<ts>).
  * --add-local beendet die WorkTracker-App, editiert config.json und startet
    sie neu (sonst ueberschreibt die laufende App die Aenderung).
  * byProject-Stubs sind bewusst konservativ (Kunde + Standard-Leistung); jeder
    Stub bekommt "_todo": pruefen/anpassen. Kunden mit genau EINEM aktiven Projekt
    bekommen dessen projects_id, sonst keine (dann auf Kundenebene gebucht).
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import uuid
from pathlib import Path

import clockodo_sync as cs  # wiederverwendete Config-/API-Helfer

_HERE = Path(__file__).resolve().parent
_MAP = _HERE / "clockodo_map.json"

# Palette identisch zur WorkTracker-App (types.ts PROJECT_COLORS).
_COLORS = ["#34c759", "#ff9500", "#ff2d55", "#5ac8fa", "#af52de",
           "#ffcc00", "#00c7be", "#ff3b30", "#a2845e", "#30b0c7"]


# --------------------------------------------------------------------------
# clockodo-Stammdaten laden
# --------------------------------------------------------------------------

def _list(cfg: dict, endpoint: str, key: str) -> list[dict]:
    """Aktive Eintraege eines Stammdaten-Endpoints (customers/projects/services)."""
    url = cs._url(cfg, endpoint, "?items_per_page=5000")
    res = cs._api(cfg, "GET", url)
    rows = res.get(key) or res.get("data") or []
    return [r for r in rows if r.get("active", True)]


def load_clockodo() -> dict:
    cfg = cs._clockodo_cfg()
    customers = _list(cfg, "customers", "customers")
    projects = _list(cfg, "projects", "projects")
    services = _list(cfg, "services", "services")
    return {"customers": customers, "projects": projects, "services": services}


# --------------------------------------------------------------------------
# Analyse
# --------------------------------------------------------------------------

def _mapped_customer_ids(m: dict) -> set[int]:
    ids: set[int] = set()
    for r in m.get("byProject", {}).values():
        ids.add(r["customers_id"])
    for r in m.get("byTicketContains", []):
        ids.add(r["customers_id"])
    if m.get("meetingDefault"):
        ids.add(m["meetingDefault"]["customers_id"])
    return ids


def _mapped_project_ids(m: dict) -> set[int]:
    ids: set[int] = set()
    for r in list(m.get("byProject", {}).values()) + list(m.get("byTicketContains", [])):
        if r.get("projects_id"):
            ids.add(r["projects_id"])
    return ids


def analyze() -> dict:
    live = load_clockodo()
    m = json.loads(_MAP.read_text(encoding="utf-8"))
    wt = json.loads(cs._wt_config_path().read_text(encoding="utf-8"))
    wt_names = [p["name"] for p in wt.get("projects", [])]

    mapped_cust = _mapped_customer_ids(m)
    mapped_proj = _mapped_project_ids(m)
    by_proj_keys = set(m.get("byProject", {}).keys())

    cust_by_id = {c["id"]: c for c in live["customers"]}
    projs_by_cust: dict[int, list[dict]] = {}
    for p in live["projects"]:
        projs_by_cust.setdefault(p["customers_id"], []).append(p)

    # Kunden ohne jegliches Mapping
    uncovered_customers = [c for c in live["customers"] if c["id"] not in mapped_cust]
    # aktive Projekte ohne Mapping (Kunde egal)
    uncovered_projects = [p for p in live["projects"] if p["id"] not in mapped_proj]
    # byProject-Regeln, die lokal kein WT-Projekt haben (Segment kann Projekt nie tragen)
    map_without_local = sorted(by_proj_keys - set(wt_names))
    # lokale WT-Projekte, die nirgends gemappt sind (wuerden nicht gebucht)
    local_without_map = [n for n in wt_names if n not in by_proj_keys]

    return {
        "live": live, "map": m, "wt_names": wt_names,
        "cust_by_id": cust_by_id, "projs_by_cust": projs_by_cust,
        "uncovered_customers": uncovered_customers,
        "uncovered_projects": uncovered_projects,
        "map_without_local": map_without_local,
        "local_without_map": local_without_map,
    }


# --------------------------------------------------------------------------
# Vorschlaege
# --------------------------------------------------------------------------

def _service_id(services: list[dict], name: str, fallback: int) -> int:
    for s in services:
        if s.get("name") == name:
            return s["id"]
    return fallback


def propose_map_stubs(a: dict) -> dict[str, dict]:
    """byProject-Stub je ungemapptem Kunden. Kunde mit genau 1 aktivem Projekt ->
    dessen projects_id, sonst Kundenebene. Leistung = Entwicklung/PM (per Name)."""
    services = a["live"]["services"]
    dev = _service_id(services, "Entwicklung", 1628653)
    pm = _service_id(services, "Projektmanagement", 1628651)
    intern = _service_id(services, "Interne Arbeitszeit", 1628650)
    stubs: dict[str, dict] = {}
    for c in a["uncovered_customers"]:
        projs = a["projs_by_cust"].get(c["id"], [])
        internal = not c.get("billable_default", True)
        stub = {
            "customers_id": c["id"],
            "billable": 0 if internal else 1,
            "service_meeting": intern if internal else pm,
            "service_default": intern if internal else dev,
            "_todo": "vom Helfer vorgeschlagen - Leistung/abrechenbar pruefen",
        }
        if len(projs) == 1:
            stub["projects_id"] = projs[0]["id"]
        stubs[c["name"]] = stub
    return stubs


def build_ref(live: dict) -> dict:
    return {
        "customers": {c["name"]: c["id"] for c in sorted(live["customers"], key=lambda x: x["name"])},
        "services": {s["name"]: s["id"] for s in sorted(live["services"], key=lambda x: x["name"])},
        "projects": {f'{next((c["name"].split(" ")[0] for c in live["customers"] if c["id"] == p["customers_id"]), "?")} / {p["name"]}': p["id"]
                     for p in sorted(live["projects"], key=lambda x: x["name"])},
    }


# --------------------------------------------------------------------------
# Anwenden
# --------------------------------------------------------------------------

def _backup(path: Path) -> None:
    path.with_suffix(path.suffix + f".bak.reconcile.{int(time.time())}").write_bytes(path.read_bytes())


def apply_map(a: dict, refresh_ref: bool, add_map: bool) -> list[str]:
    m = a["map"]
    log: list[str] = []
    if refresh_ref:
        m["_clockodo_ref"] = build_ref(a["live"])
        log.append("_clockodo_ref auf Live-Stand aktualisiert")
    if add_map:
        stubs = propose_map_stubs(a)
        for name, stub in stubs.items():
            if name not in m.setdefault("byProject", {}):
                m["byProject"][name] = stub
                log.append(f"byProject-Stub angelegt: {name}")
    if log:
        _backup(_MAP)
        _MAP.write_text(json.dumps(m, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return log


def apply_local(a: dict) -> list[str]:
    """Fehlende lokale WT-Projekte anlegen (App beenden -> editieren -> neu starten)."""
    cfg_path = cs._wt_config_path()
    m = json.loads(_MAP.read_text(encoding="utf-8"))  # ggf. gerade erweitert
    by_keys = list(m.get("byProject", {}).keys())
    wt = json.loads(cfg_path.read_text(encoding="utf-8"))
    have = {p["name"] for p in wt.get("projects", [])}
    missing = [k for k in by_keys if k not in have]
    if not missing:
        return ["keine fehlenden lokalen Projekte"]

    subprocess.run(["osascript", "-e", 'quit app "WorkTracker"'], check=False)
    time.sleep(2)
    subprocess.run(["pkill", "-x", "WorkTracker"], check=False)
    time.sleep(1)

    _backup(cfg_path)
    used = {p.get("color") for p in wt["projects"]}
    free = [c for c in _COLORS if c not in used] or _COLORS
    log = []
    for i, name in enumerate(missing):
        wt["projects"].append({
            "gitUserEmail": "", "id": str(uuid.uuid4()), "name": name,
            "repoPath": "", "color": free[i % len(free)],
        })
        log.append(f"lokales WT-Projekt angelegt: {name}")
    cfg_path.write_text(json.dumps(wt, indent=2, ensure_ascii=False), encoding="utf-8")
    subprocess.run(["open", "/Applications/WorkTracker.app"], check=False)
    log.append("WorkTracker neu gestartet")
    return log


# --------------------------------------------------------------------------
# Ausgabe
# --------------------------------------------------------------------------

def print_report(a: dict) -> None:
    def cust_line(c):
        projs = a["projs_by_cust"].get(c["id"], [])
        pj = f" ({len(projs)} Projekt(e): {', '.join(p['name'] for p in projs)})" if projs else " (kein Projekt)"
        return f"  - {c['name']}  [id {c['id']}]{pj}"

    print("=== clockodo <-> WorkTracker Stammdaten-Abgleich ===\n")
    print(f"clockodo aktiv: {len(a['live']['customers'])} Kunden, "
          f"{len(a['live']['projects'])} Projekte, {len(a['live']['services'])} Leistungen")
    print(f"lokal: {len(a['wt_names'])} WT-Projekte, "
          f"{len(a['map'].get('byProject', {}))} byProject-Regeln\n")

    print(f"[1] clockodo-Kunden OHNE Mapping ({len(a['uncovered_customers'])}):")
    print("\n".join(cust_line(c) for c in a["uncovered_customers"]) or "  (alle gemappt)")
    if a["uncovered_customers"]:
        print("\n    Vorschlag (nach --add-map landet das in byProject):")
        print("    " + json.dumps(propose_map_stubs(a), indent=2, ensure_ascii=False).replace("\n", "\n    "))

    print(f"\n[2] aktive clockodo-Projekte OHNE Mapping ({len(a['uncovered_projects'])}):")
    print("\n".join(f"  - {a['cust_by_id'].get(p['customers_id'], {}).get('name', '?')} / {p['name']}  [id {p['id']}]"
                    for p in a["uncovered_projects"]) or "  (alle gemappt)")

    print(f"\n[3] byProject-Regeln OHNE lokales WT-Projekt ({len(a['map_without_local'])}):")
    print("\n".join(f"  - {n}  (nicht im '+'-Dialog waehlbar -> --add-local)" for n in a["map_without_local"]) or "  (alle vorhanden)")

    print(f"\n[4] lokale WT-Projekte OHNE Mapping ({len(a['local_without_map'])}):")
    print("\n".join(f"  - {n}  (Zeit darauf wird NICHT nach clockodo gebucht)" for n in a["local_without_map"]) or "  (alle gemappt)")

    print("\nAnwenden: --refresh-ref | --add-map | --add-local | --apply (alle)")


def main() -> None:
    args = set(sys.argv[1:])
    a = analyze()
    if "--json" in args:
        out = {k: a[k] for k in ("uncovered_customers", "uncovered_projects",
                                 "map_without_local", "local_without_map")}
        out["proposed_map_stubs"] = propose_map_stubs(a)
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return

    apply_all = "--apply" in args
    log: list[str] = []
    if apply_all or "--refresh-ref" in args or "--add-map" in args:
        log += apply_map(a, refresh_ref=apply_all or "--refresh-ref" in args,
                         add_map=apply_all or "--add-map" in args)
    if apply_all or "--add-local" in args:
        log += apply_local(analyze())  # frisch, damit gerade angelegte Stubs zaehlen

    if log:
        print("Angewendet:")
        for line in log:
            print(f"  + {line}")
        print("\nNeuer Stand:\n")
        print_report(analyze())
    else:
        print_report(a)


if __name__ == "__main__":
    main()
