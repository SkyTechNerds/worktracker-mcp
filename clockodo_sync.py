#!/usr/bin/env python3
"""
WorkTracker <-> clockodo  Zwei-Wege-Sync
========================================

Haelt WorkTracker-App und clockodo pro Tag synchron. Beide Seiten koennen
Quelle einer Aenderung sein:

  * WorkTracker -> clockodo laeuft sofort (server.py ruft nach jedem Schreib-
    zugriff reconcile_day() auf).
  * clockodo -> WorkTracker laeuft per Hintergrund-Job (launchd, alle 5 min,
    `--reconcile-window`), weil clockodo keine Aenderungen pusht.

Modell: "Last-Writer-Wins pro Tag". Ueber Hashes wird erkannt, welche Seite
sich seit dem letzten Abgleich geaendert hat.
  - nur eine Seite geaendert -> diese Seite gewinnt, andere wird angeglichen
  - beide geaendert (echter Konflikt) -> die zeitlich neuere Aenderung gewinnt
    (clockodo: max(time_last_change); WorkTracker: Zeitpunkt des Laufs; bei
    Gleichstand gewinnt WorkTracker)
  - Erststart eines Tages (kein State) -> Union: nichts geht verloren

Vergleich passiert im "clockodo-Raum" (Kunde/Projekt/Leistung/abrechenbar/Text
je Zeitraum). WorkTracker-Segmente werden dafuer vorwaerts gemappt
(clockodo_map.json). Pausen und nicht gemappte Arbeits-Segmente existieren im
clockodo-Raum nicht -> sie werden bei clockodo->WorkTracker erhalten (ggf. um
clockodo-Intervalle beschnitten), gehen also nicht verloren.

CLI:
    python clockodo_sync.py --reconcile-window [--dry-run]   # rollierendes Fenster
    python clockodo_sync.py 2026-07-20 2026-07-21 [--dry-run]
    python clockodo_sync.py --today [--dry-run]
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

_HERE = Path(__file__).resolve().parent
_CONFIG = _HERE / "clockodo_config.json"
_MAP = _HERE / "clockodo_map.json"
_STATE = _HERE / "clockodo_state.json"

# Rollierendes Fenster fuer den Hintergrund-Job: Montag letzter Woche .. heute.
_WINDOW_BACK_DAYS = 13


# --------------------------------------------------------------------------
# Config / IO
# --------------------------------------------------------------------------

def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _clockodo_cfg() -> dict:
    return _load_json(_CONFIG)


def _wt_config_path() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "worktracker" / "config.json"
    if sys.platform.startswith("win"):
        appdata = os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))
        return Path(appdata) / "worktracker" / "config.json"
    return Path.home() / ".config" / "worktracker" / "config.json"


def _wt_api() -> tuple[str, str]:
    cfg = _load_json(_wt_config_path())
    api = cfg.get("apiServer", {})
    token = api.get("token", "")
    if not token:
        raise RuntimeError("Kein WorkTracker-API-Token (Einstellungen -> API).")
    return f"http://127.0.0.1:{api.get('port', 8787)}", token


def _wt_get_day(date: str) -> dict:
    base, token = _wt_api()
    with httpx.Client(timeout=10.0) as c:
        r = c.get(f"{base}/api/day?date={date}", headers={"Authorization": f"Bearer {token}"})
    r.raise_for_status()
    return r.json()


def _wt_set_day(date: str, segments: list[dict]) -> Any:
    base, token = _wt_api()
    with httpx.Client(timeout=10.0) as c:
        r = c.post(f"{base}/api/day",
                   headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                   json={"date": date, "segments": segments})
    r.raise_for_status()
    return r.json()


def _wt_assign(date: str, seg: dict) -> Any:
    """Einzelnes Segment additiv einschneiden (stoert das Live-Tracking von heute nicht)."""
    base, token = _wt_api()
    payload = {"date": date, "from": seg["from"], "to": seg["to"], "kind": seg.get("kind", "work")}
    for k in ("ticket", "project", "note"):
        if seg.get(k):
            payload[k] = seg[k]
    if seg.get("meeting"):
        payload["meeting"] = True
    with httpx.Client(timeout=10.0) as c:
        r = c.post(f"{base}/api/assign",
                   headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                   json=payload)
    r.raise_for_status()
    return r.json()


def _is_today(date: str) -> bool:
    return date == datetime.now().strftime("%Y-%m-%d")


# --------------------------------------------------------------------------
# clockodo-API
# --------------------------------------------------------------------------

def _headers(cfg: dict) -> dict:
    return {
        "X-ClockodoApiUser": cfg["api_user"],
        "X-ClockodoApiKey": cfg["api_key"],
        "X-Clockodo-External-Application": cfg["external_application"],
        "Content-Type": "application/json",
    }


def _url(cfg: dict, endpoint: str, suffix: str = "") -> str:
    ver = cfg["endpoint_versions"][endpoint]
    return f"{cfg['api_base']}/{ver}/{endpoint}{suffix}"


def _api(cfg: dict, method: str, url: str, body: dict | None = None) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=_headers(cfg))
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()[:400]
        raise RuntimeError(f"clockodo {method} {url} -> HTTP {exc.code}: {detail}") from exc


def _clockodo_entries(cfg: dict, date: str) -> list[dict]:
    """Alle eigenen Zeiteintraege eines Tages (lokaler Kalendertag)."""
    since = _iso_utc(_ms_from_local(date, "00:00"))
    until = _iso_utc(_ms_from_local(date, "00:00") + 86400_000)
    from urllib.parse import quote
    url = _url(cfg, "entries", f"?time_since={quote(since)}&time_until={quote(until)}")
    res = _api(cfg, "GET", url)
    me = cfg.get("users_id")
    out = []
    for e in res.get("entries", []):
        if me is not None and e.get("users_id") != me:
            continue
        # Nur echte Zeiteintraege (type 1), keine Lumpsum/Leistungs-Pauschalen.
        if e.get("type", 1) != 1:
            continue
        out.append(e)
    return out


def _create_entry(cfg: dict, entry: dict) -> int:
    return _api(cfg, "POST", _url(cfg, "entries"), entry)["entry"]["id"]


def _delete_entry(cfg: dict, entry_id: int) -> None:
    _api(cfg, "DELETE", _url(cfg, "entries", f"/{entry_id}"))


# --------------------------------------------------------------------------
# Zeit-Helfer (WorkTracker = lokale Zeit, clockodo = UTC)
# --------------------------------------------------------------------------

def _iso_utc(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ms_from_iso_utc(iso: str) -> int:
    dt = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _ms_from_local(date: str, hhmm: str) -> int:
    dt = datetime.strptime(f"{date} {hhmm}", "%Y-%m-%d %H:%M")  # naiv = lokale Zeit
    return int(dt.timestamp() * 1000)


def _local_hhmm(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000).strftime("%H:%M")


# --------------------------------------------------------------------------
# Mapping: WorkTracker-Segment -> clockodo-Eintrag (vorwaerts)
# --------------------------------------------------------------------------

def map_segment(seg: dict, mapping: dict) -> dict | None:
    """clockodo-Eintrags-Body (ohne Zeiten) oder None (Pause/nicht gemappt)."""
    if seg.get("kind") == "break":
        return None
    text = (seg.get("note") or seg.get("ticket") or "").strip()
    project = seg.get("project")
    meeting = bool(seg.get("meeting"))

    rule = mapping.get("byProject", {}).get(project) if project else None
    if rule:
        service = rule["service_meeting"] if meeting else rule["service_default"]
        entry = {"customers_id": rule["customers_id"], "services_id": service,
                 "billable": rule.get("billable", 0)}
        if rule.get("projects_id"):
            entry["projects_id"] = rule["projects_id"]
        if text:
            entry["text"] = text
        return entry

    ticket_lc = (seg.get("ticket") or "").lower()
    for r in mapping.get("byTicketContains", []):
        if r["match"].lower() in ticket_lc:
            entry = {"customers_id": r["customers_id"], "services_id": r["services_id"],
                     "billable": r.get("billable", 0)}
            if r.get("projects_id"):
                entry["projects_id"] = r["projects_id"]
            if text:
                entry["text"] = text
            return entry

    # Meeting-Default: jedes Meeting ohne Kundenzuordnung -> Standard-Kunde/Projekt
    # (nie als Pause behandeln). meeting-Flag ODER ticket enthaelt "meeting".
    md = mapping.get("meetingDefault")
    if md and (meeting or "meeting" in ticket_lc):
        entry = {"customers_id": md["customers_id"], "services_id": md["services_id"],
                 "billable": md.get("billable", 0)}
        if md.get("projects_id"):
            entry["projects_id"] = md["projects_id"]
        if text:
            entry["text"] = text
        return entry
    return None


# --------------------------------------------------------------------------
# Mapping: clockodo-Eintrag -> WorkTracker-Segment (rueckwaerts)
# --------------------------------------------------------------------------

def _reverse_index(mapping: dict) -> dict:
    proj = {}
    for wt_name, r in mapping.get("byProject", {}).items():
        proj[(r["customers_id"], r.get("projects_id"))] = {
            "wt_project": wt_name,
            "service_meeting": r.get("service_meeting"),
        }
    tickets = mapping.get("byTicketContains", [])
    by_cust_service = {(r["customers_id"], r["services_id"]): r for r in tickets}
    by_cust = {}
    for r in tickets:
        by_cust.setdefault(r["customers_id"], r)
    return {"proj": proj, "by_cust_service": by_cust_service, "by_cust": by_cust}


def reverse_entry(e: dict, rmap: dict) -> dict:
    """clockodo-Eintrag -> WorkTracker-Work-Segment {from,to,kind,ticket,project,note,meeting}."""
    frm = _local_hhmm(_ms_from_iso_utc(e["time_since"]))
    to = _local_hhmm(_ms_from_iso_utc(e["time_until"]))
    text = (e.get("text") or "").strip() or None
    cid, pid, sid = e.get("customers_id"), e.get("projects_id"), e.get("services_id")

    info = rmap["proj"].get((cid, pid))
    if info:
        return {"from": frm, "to": to, "kind": "work", "project": info["wt_project"],
                "ticket": text, "note": None,
                "meeting": bool(info["service_meeting"] and sid == info["service_meeting"])}

    rule = rmap["by_cust_service"].get((cid, sid)) or rmap["by_cust"].get(cid)
    if rule:
        return {"from": frm, "to": to, "kind": "work", "project": None,
                "ticket": rule.get("wt_ticket") or rule.get("match"), "note": text, "meeting": False}

    return {"from": frm, "to": to, "kind": "work", "project": None,
            "ticket": text, "note": None, "meeting": False}


# --------------------------------------------------------------------------
# Kanonische Repraesentation im clockodo-Raum (fuer Vergleich/Hash)
# --------------------------------------------------------------------------

def _canon_from_clockodo(e: dict) -> tuple:
    return (
        e["time_since"], e["time_until"],
        e.get("customers_id"), e.get("projects_id"), e.get("services_id"),
        int(e.get("billable", 0)), (e.get("text") or "").strip(),
    )


def _canon_from_wt_segment(seg: dict, mapping: dict) -> tuple | None:
    base = map_segment(seg, mapping)
    if base is None:
        return None
    return (
        _iso_utc(seg["start"]), _iso_utc(seg["end"]),
        base.get("customers_id"), base.get("projects_id"), base.get("services_id"),
        int(base.get("billable", 0)), (base.get("text") or "").strip(),
    )


def _hash(items: list[tuple]) -> str:
    payload = json.dumps(sorted(items), ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _canon_to_entry_body(c: tuple) -> dict:
    body = {"time_since": c[0], "time_until": c[1], "customers_id": c[2],
            "services_id": c[4], "billable": c[5]}
    if c[3]:
        body["projects_id"] = c[3]
    if c[6]:
        body["text"] = c[6]
    return body


# --------------------------------------------------------------------------
# Gesetzliche Pausenpruefung (ArbZG §4)
# --------------------------------------------------------------------------

_BREAK_RULES = _HERE / "break_rules.json"

_DEFAULT_BREAK_RULES = {
    "thresholds": [{"work_over_min": 540, "required_break_min": 45},
                   {"work_over_min": 360, "required_break_min": 30}],
    "min_chunk_min": 15,
    "max_continuous_work_min": 360,
}


def _load_break_rules() -> dict:
    return _load_json(_BREAK_RULES) if _BREAK_RULES.is_file() else _DEFAULT_BREAK_RULES


def _required_break(work_min: float, rules: dict) -> int:
    for t in sorted(rules.get("thresholds", []), key=lambda x: -x["work_over_min"]):
        if work_min > t["work_over_min"]:
            return t["required_break_min"]
    return 0


def check_breaks_segments(segments: list[dict], rules: dict | None = None) -> dict:
    """Prueft eine Segmentliste gegen die Pausenregeln. Rueckgabe mit
    compliant-Flag, Ist/Soll-Pause, laengster Arbeitsstrecke am Stueck und Fixes."""
    rules = rules or _load_break_rules()
    chunk = rules.get("min_chunk_min", 15)
    max_cont = rules.get("max_continuous_work_min", 360)
    segs = sorted(segments, key=lambda s: s["start"])

    def mins(s):
        return (s["end"] - s["start"]) / 60000

    work_min = sum(mins(s) for s in segs if s.get("kind") == "work")
    # Nur Pausenabschnitte >= chunk zaehlen als Ruhepause (gestueckelt erlaubt)
    qual_break = sum(mins(s) for s in segs if s.get("kind") == "break" and mins(s) >= chunk)
    required = _required_break(work_min, rules)

    # Laengste Arbeitsstrecke ohne qualifizierende Pause (kurze Pausen setzen NICHT zurueck)
    longest = cur = 0.0
    for s in segs:
        d = mins(s)
        if s.get("kind") == "work":
            cur += d
            longest = max(longest, cur)
        elif s.get("kind") == "break" and d >= chunk:
            cur = 0.0

    issues = []
    break_ok = qual_break + 1e-6 >= required
    if not break_ok:
        issues.append(f"Pause zu kurz: {qual_break:.0f} min statt {required} min "
                      f"(bei {work_min/60:.1f} h Arbeit); fehlend {required - qual_break:.0f} min")
    stretch_ok = longest <= max_cont + 1e-6
    if not stretch_ok:
        issues.append(f"{longest/60:.1f} h am Stueck ohne Pause (>= {chunk} min) — "
                      f"max. {max_cont/60:.0f} h erlaubt")

    return {
        "work_h": round(work_min / 60, 2),
        "break_min": round(qual_break),
        "required_break_min": required,
        "break_ok": break_ok,
        "longest_stretch_h": round(longest / 60, 2),
        "stretch_ok": stretch_ok,
        "compliant": break_ok and stretch_ok,
        "issues": issues,
    }


def check_breaks(date: str) -> dict:
    """Prueft einen Tag (YYYY-MM-DD) gegen die gesetzlichen Pausenregeln."""
    res = check_breaks_segments(_wt_get_day(date).get("segments", []))
    res["date"] = date
    return res


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------

def _load_state() -> dict:
    return _load_json(_STATE) if _STATE.is_file() else {}


def _save_state(state: dict) -> None:
    _STATE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


# --------------------------------------------------------------------------
# Intervall-Subtraktion (Pausen/nicht gemappte Segmente um clockodo beschneiden)
# --------------------------------------------------------------------------

def _subtract(seg_s: int, seg_e: int, cuts: list[tuple[int, int]]) -> list[tuple[int, int]]:
    parts = [(seg_s, seg_e)]
    for cs, ce in cuts:
        nxt = []
        for s, e in parts:
            if ce <= s or cs >= e:      # kein Ueberlapp
                nxt.append((s, e))
                continue
            if cs > s:
                nxt.append((s, min(cs, e)))
            if ce < e:
                nxt.append((max(ce, s), e))
        parts = nxt
    return [(s, e) for s, e in parts if e - s >= 60_000]  # >= 1 Minute behalten


def _clip_segments(segs: list[dict], cuts: list[tuple[int, int]]) -> list[dict]:
    """WorkTracker-Segmente um Schnittintervalle beschneiden (Reste als neue Segmente)."""
    out = []
    for seg in segs:
        for s, e in _subtract(seg["start"], seg["end"], cuts):
            out.append({"from": _local_hhmm(s), "to": _local_hhmm(e),
                        "kind": seg.get("kind", "work"),
                        "ticket": seg.get("ticket"), "project": seg.get("project"),
                        "note": seg.get("note"), "meeting": bool(seg.get("meeting"))})
    return out


# --------------------------------------------------------------------------
# Reconcile eines Tages
# --------------------------------------------------------------------------

def reconcile_day(date: str, dry_run: bool = False, source: str = "poll") -> dict:
    cfg = _clockodo_cfg()
    mapping = _load_json(_MAP)
    rmap = _reverse_index(mapping)

    day = _wt_get_day(date)
    wt_segs = day.get("segments", [])
    ck_entries = _clockodo_entries(cfg, date)

    # Kanonische Mengen im clockodo-Raum
    wt_canon = [c for c in (_canon_from_wt_segment(s, mapping) for s in wt_segs) if c]
    ck_canon = [_canon_from_clockodo(e) for e in ck_entries]
    wt_hash, ck_hash = _hash(wt_canon), _hash(ck_canon)

    st = _load_state().get(date)
    if not isinstance(st, dict):   # altes Format (Liste) oder nicht vorhanden
        st = None
    now_iso = _iso_utc(int(datetime.now(timezone.utc).timestamp() * 1000))

    # --- Richtung bestimmen ---
    if st is None:
        direction = "bootstrap" if set(wt_canon) != set(ck_canon) else "noop"
    else:
        wt_changed = wt_hash != st.get("wt_hash")
        ck_changed = ck_hash != st.get("ck_hash")
        if not wt_changed and not ck_changed:
            direction = "noop"
        elif wt_changed and not ck_changed:
            direction = "wt->ck"
        elif ck_changed and not wt_changed:
            direction = "ck->wt"
        else:
            ck_time = max((e.get("time_last_change") or "") for e in ck_entries) if ck_entries else ""
            wt_time = now_iso if source == "wt" else now_iso
            # Gleichstand / WorkTracker-Ausloeser -> WorkTracker gewinnt
            direction = "ck->wt" if ck_time > wt_time else "wt->ck"

    plan = {"date": date, "direction": direction, "source": source,
            "wt_segments": len(wt_segs), "clockodo_entries": len(ck_entries),
            "created": 0, "deleted": 0, "wt_rewritten": False, "notes": []}

    # Gesetzliche Pausenpruefung (nur Hinweis, aendert nichts)
    bc = check_breaks_segments(wt_segs)
    if not bc["compliant"]:
        plan["break_warning"] = bc
        plan["notes"].append("Pausen-Hinweis (ArbZG): " + "; ".join(bc["issues"]))

    if direction == "noop":
        if not dry_run:
            state = _load_state()
            state[date] = {"wt_hash": wt_hash, "ck_hash": ck_hash,
                           "clockodo_ids": [e["id"] for e in ck_entries], "last_sync": now_iso}
            _save_state(state)
        return plan

    # --- clockodo := WorkTracker-Stand (WT gewinnt, minimale Diff) ---
    if direction == "wt->ck":
        want = set(wt_canon)
        have = {_canon_from_clockodo(e): e for e in ck_entries}
        to_create = [c for c in want if c not in have]
        to_delete = [e["id"] for c, e in have.items() if c not in want]
        plan["created"], plan["deleted"] = len(to_create), len(to_delete)
        plan["create_preview"] = [_canon_to_entry_body(c) for c in to_create]
        if not dry_run:
            for eid in to_delete:
                try:
                    _delete_entry(cfg, eid)
                except RuntimeError as exc:
                    sys.stderr.write(f"[reconcile] delete {eid}: {exc}\n")
            for c in to_create:
                _create_entry(cfg, _canon_to_entry_body(c))

    # --- clockodo -> WorkTracker: NUR ADDITIV ---
    # WorkTracker ist die Quelle der Wahrheit; der Sync schreibt Tage NIE neu und
    # relabelt nichts. Er fuegt ausschliesslich clockodo-Eintraege ein, die in
    # WorkTracker noch nicht existieren (z. B. direkt in clockodo gebuchte Zeiten),
    # via assign_time (schneidet nur den betroffenen Bereich ein).
    elif direction == "ck->wt":
        want = set(wt_canon)
        ck_only = [e for e in ck_entries if _canon_from_clockodo(e) not in want]
        plan["created"] = len(ck_only)
        plan["notes"].append(
            f"additiv: {len(ck_only)} neue clockodo-Eintraege -> WorkTracker "
            f"(bestehende WT-Segmente unberuehrt, kein Neuschreiben)")
        if not dry_run:
            for e in ck_only:
                _wt_assign(date, reverse_entry(e, rmap))

    # --- Erststart: echte Union, nichts geht verloren ---
    elif direction == "bootstrap":
        want = set(wt_canon)
        have = {_canon_from_clockodo(e): e for e in ck_entries}
        wt_only = [c for c in want if c not in have]                 # -> in clockodo anlegen
        ck_only = [e for e in ck_entries if _canon_from_clockodo(e) not in want]  # -> in WT importieren
        plan["created"] = len(wt_only)
        plan["create_preview"] = [_canon_to_entry_body(c) for c in wt_only]
        plan["notes"].append(
            f"Erststart-Union: {len(wt_only)} WT-Eintraege -> clockodo"
            + (f", {len(ck_only)} clockodo-Eintraege -> WorkTracker (additiv)" if ck_only else ""))
        if not dry_run:
            for c in wt_only:
                _create_entry(cfg, _canon_to_entry_body(c))
            # clockodo -> WorkTracker immer additiv (nie Tag neu schreiben/relabeln)
            for e in ck_only:
                _wt_assign(date, reverse_entry(e, rmap))

    # --- State aus tatsaechlichem Nachher-Zustand neu schreiben ---
    if not dry_run:
        wt_after = [c for c in (_canon_from_wt_segment(s, mapping)
                                for s in _wt_get_day(date).get("segments", [])) if c]
        ck_after_entries = _clockodo_entries(cfg, date)
        ck_after = [_canon_from_clockodo(e) for e in ck_after_entries]
        state = _load_state()
        state[date] = {"wt_hash": _hash(wt_after), "ck_hash": _hash(ck_after),
                       "clockodo_ids": [e["id"] for e in ck_after_entries], "last_sync": now_iso}
        _save_state(state)

    return plan


# --------------------------------------------------------------------------
# Fenster / CLI  +  Rueckwaerts-Kompatibilitaet fuer server.py
# --------------------------------------------------------------------------

def window_dates() -> list[str]:
    today = datetime.now()
    start = today - timedelta(days=_WINDOW_BACK_DAYS)
    return [(start + timedelta(days=i)).strftime("%Y-%m-%d")
            for i in range((today - start).days + 1)]


def reconcile_window(dry_run: bool = False, source: str = "poll") -> list[dict]:
    """Robust: ein Fehler an einem Tag (z. B. WorkTracker-App zu) killt nicht den
    ganzen Lauf. Wichtig: schlaegt das Lesen einer Seite fehl, wirft reconcile_day
    VOR jeder Schreibaktion -> es wird nichts geloescht/ueberschrieben."""
    out = []
    for d in window_dates():
        try:
            out.append(reconcile_day(d, dry_run=dry_run, source=source))
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"[reconcile] {d}: {exc}\n")
            out.append({"date": d, "direction": "error", "error": str(exc), "notes": []})
    return out


def sync_day(date: str) -> dict:
    """Von server.py nach jedem WorkTracker-Schreibzugriff aufgerufen."""
    return reconcile_day(date, dry_run=False, source="wt")


def sync_days(dates: list[str]) -> list[dict]:
    return [reconcile_day(d, dry_run=False, source="wt") for d in dates]


def _fmt(p: dict) -> str:
    line = f"{p['date']}: {p['direction']}"
    if p["direction"] == "error":
        return line + f" | {p.get('error', '')}"
    if p["direction"] != "noop":
        line += f" | +{p.get('created', 0)} clk, -{p.get('deleted', 0)} clk"
        if p.get("wt_rewritten"):
            line += ", WT neu geschrieben"
    for n in p.get("notes", []):
        line += f"\n    - {n}"
    return line


def main() -> None:
    args = sys.argv[1:]
    dry = "--dry-run" in args
    args = [a for a in args if a != "--dry-run"]
    if not args:
        print(__doc__)
        return
    if args[0] == "--reconcile-window":
        plans = reconcile_window(dry_run=dry, source="poll")
    elif args[0] == "--today":
        plans = [reconcile_day(datetime.now().strftime("%Y-%m-%d"), dry_run=dry, source="poll")]
    else:
        plans = [reconcile_day(d, dry_run=dry, source="poll") for d in args]
    tag = " [DRY-RUN]" if dry else ""
    for p in plans:
        if p["direction"] not in ("noop",) or not args[0].startswith("--"):
            print(_fmt(p) + (tag if p["direction"] not in ("noop", "error") else ""))

    if args[0] == "--reconcile-window":
        # Heartbeat: eine Zeile pro Lauf, damit man im Log sieht, dass der Job lebt.
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        acted = sum(1 for p in plans if p["direction"] not in ("noop", "error"))
        errs = sum(1 for p in plans if p["direction"] == "error")
        print(f"{stamp} | {len(plans)} Tage geprueft, {acted} mit Aktion, {errs} Fehler{tag}")


if __name__ == "__main__":
    main()
