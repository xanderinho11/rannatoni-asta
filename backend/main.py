from __future__ import annotations

import asyncio
import datetime
import io
import json
import os
import re
import secrets
import time
import unicodedata
from typing import Optional

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import auction as auction_module
import csv_parser
import db
import storage
import push_service

# ========= CONFIG =========
DEFAULT_ADMIN_PASSWORD = "asta2026"
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", DEFAULT_ADMIN_PASSWORD)
if os.environ.get("RAILWAY_ENVIRONMENT_ID") and ADMIN_PASSWORD == DEFAULT_ADMIN_PASSWORD:
    raise RuntimeError("Imposta ADMIN_PASSWORD nelle Variables di Railway prima di avviare Rannatoni.")
FRONTEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend"))
DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data"))
SESSIONS_PATH = os.path.join(DATA_DIR, "sessions.json")
SESSION_SECONDS = 24 * 60 * 60
SIMULATION_PATH = os.path.join(DATA_DIR, "simulation_base.db")

app = FastAPI(title="Rannatoni - Asta di riparazione")
db.init_db()
try:
    push_service.ensure_vapid_keys()
except Exception as exc:
    print(f"[push] inizializzazione non riuscita: {exc}")
auction = auction_module.auction
auction.load_persisted()

# ========= SESSIONI =========
def _load_sessions():
    try:
        with open(SESSIONS_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        now = time.time()
        return {k: v for k, v in raw.items() if float(v.get("expires_at", 0)) > now}
    except Exception:
        return {}


def _save_sessions():
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(SESSIONS_PATH, "w", encoding="utf-8") as f:
            json.dump(SESSIONS, f)
    except Exception as exc:
        print(f"[sessioni] salvataggio non riuscito: {exc}")


SESSIONS: dict[str, dict] = _load_sessions()


def _new_session(role: str, team: str | None):
    token = secrets.token_urlsafe(32)
    now = time.time()
    SESSIONS[token] = {
        "role": role,
        "team": team,
        "created_at": now,
        "expires_at": now + SESSION_SECONDS,
    }
    _save_sessions()
    return token


def _invalidate_team_sessions(team: str, keep_token: str | None = None):
    removed = 0
    for tok, sess in list(SESSIONS.items()):
        if sess.get("role") == "team" and sess.get("team") == team and tok != keep_token:
            SESSIONS.pop(tok, None)
            removed += 1
    if removed:
        _save_sessions()
    return removed


def _token_from_header(authorization: Optional[str]):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Non autenticato.")
    return authorization.removeprefix("Bearer ").strip()


def get_session(authorization: Optional[str] = Header(None)):
    token = _token_from_header(authorization)
    session = SESSIONS.get(token)
    if not session or float(session.get("expires_at", 0)) <= time.time():
        SESSIONS.pop(token, None)
        _save_sessions()
        raise HTTPException(401, "Sessione non valida o scaduta.")
    session = dict(session)
    session["token"] = token
    return session


def require_superadmin(session: dict = Depends(get_session)):
    if session.get("role") != "superadmin":
        raise HTTPException(403, "Riservato al Super Admin.")
    return session


def require_team(session: dict = Depends(get_session)):
    if session.get("role") != "team" or not session.get("team"):
        raise HTTPException(403, "Riservato alle squadre.")
    if not db.get_team(session["team"]):
        raise HTTPException(401, "Squadra non piu' valida.")
    return session


def require_spectator(session: dict = Depends(get_session)):
    if session.get("role") != "spectator":
        raise HTTPException(403, "Riservato agli spettatori.")
    return session


def _ensure_pin_changed(session: dict):
    team = db.get_team(session["team"])
    if team and team.get("pin_must_change"):
        raise HTTPException(403, "Prima imposta il tuo PIN personale.")
    return team


def require_auction_manager(session: dict = Depends(require_team)):
    team = _ensure_pin_changed(session)
    if not team or not team.get("is_admin"):
        raise HTTPException(403, "Riservato alla squadra che gestisce l'asta.")
    return session


# ========= PRESENZA / WEBSOCKET =========
class ConnectionManager:
    def __init__(self):
        self.connections: dict[WebSocket, dict] = {}
        self.last_presence: dict[str, dict] = {}

    async def connect(self, ws: WebSocket, session: dict):
        await ws.accept()
        team = session.get("team") if session.get("role") == "team" else None
        self.connections[ws] = {
            "team": team,
            "role": session.get("role"),
            "visibility": "visible",
            "last_seen": time.time(),
        }
        if team:
            self.last_presence[team] = {"visibility": "visible", "last_seen": time.time()}

    def touch(self, ws: WebSocket, visibility: str):
        info = self.connections.get(ws)
        if not info:
            return False
        visibility = "hidden" if visibility == "hidden" else "visible"
        changed = visibility != info.get("visibility")
        info["visibility"] = visibility
        info["last_seen"] = time.time()
        if info.get("team"):
            self.last_presence[info["team"]] = {
                "visibility": visibility,
                "last_seen": info["last_seen"],
            }
        return changed

    def disconnect(self, ws: WebSocket):
        info = self.connections.pop(ws, None)
        if info and info.get("team"):
            self.last_presence[info["team"]] = {
                "visibility": info.get("visibility", "hidden"),
                "last_seen": time.time(),
            }

    def presence(self, team: str):
        now = time.time()
        active = [i for i in self.connections.values() if i.get("team") == team]
        if any(i.get("visibility") == "visible" and now - i.get("last_seen", 0) < 25 for i in active):
            return "online"
        if any(now - i.get("last_seen", 0) < 90 for i in active):
            return "background"
        last = self.last_presence.get(team)
        if last and now - last.get("last_seen", 0) < 90:
            return "background"
        return "offline"

    def spectator_count(self):
        return sum(1 for info in self.connections.values() if info.get("role") == "spectator")

    async def send_state(self, ws: WebSocket):
        info = self.connections.get(ws) or {}
        team = info.get("team")
        await ws.send_json({"type": "state", "data": build_state(team)})

    async def broadcast_state(self):
        dead = []
        for ws in list(self.connections):
            try:
                await self.send_state(ws)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    async def broadcast_result(self, result: dict):
        dead = []
        for ws in list(self.connections):
            try:
                await ws.send_json({"type": "result", "data": result})
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


manager = ConnectionManager()
AUCTION_LOCK = asyncio.Lock()
_timer_task = None
_presence_task = None
_auto_random_task = None
_auto_random_deadline = None
AUTO_RANDOM_DELAY = 10


def simulation_active():
    return db.get_setting("simulation_active", "0") == "1"


def backup_if_real(reason: str):
    if simulation_active():
        return True
    return storage.salva(reason)


def auto_random_enabled():
    return db.get_setting("auto_random", "0") == "1"


def auto_random_seconds():
    if not _auto_random_deadline:
        return None
    return max(0, int(_auto_random_deadline - time.time() + 0.999))


def _teams_with_presence():
    teams = db.get_public_teams()
    for t in teams:
        t["ready"] = bool(t["ready"])
        t["market_finished"] = bool(t.get("market_finished"))
        t["is_manager"] = bool(t.pop("is_admin"))
        t["presence"] = manager.presence(t["name"])
    return teams


def build_state(viewer_team: str | None = None):
    state = auction.snapshot(viewer_team)
    state["simulation"] = simulation_active()
    state["auto_random"] = auto_random_enabled()
    state["auto_random_seconds"] = auto_random_seconds()
    state["teams"] = _teams_with_presence()
    state["active_market_count"] = sum(1 for t in state["teams"] if not t.get("market_finished"))
    state["auction_progress"] = db.get_auction_progress()
    if viewer_team:
        me = db.get_team(viewer_team)
        if me:
            roster = db.get_roster(viewer_team)
            state["me"] = {
                "team": viewer_team,
                "budget": int(me["budget"]),
                "ready": bool(me["ready"]),
                "is_manager": bool(me["is_admin"]),
                "market_finished": bool(me.get("market_finished")),
                "pin_must_change": bool(me.get("pin_must_change")),
                "roster_size": len(roster),
                "roster_signature": "|".join(f"{g['pid']}:{g['price']}" for g in sorted(roster, key=lambda x: x['pid'])),
                "max_roster": db.MAX_ROSA,
                "roster_rules": db.roster_summary(viewer_team),
            }
            if state["me"]["is_manager"]:
                state["spectator_count"] = manager.spectator_count()
    return state


async def broadcast_state():
    await manager.broadcast_state()


# ========= AUTH =========
class LoginBody(BaseModel):
    username: str
    pin: str


class AdminLoginBody(BaseModel):
    password: str


@app.post("/api/login")
async def login(body: LoginBody):
    team = db.get_team_by_credentials(body.username.strip(), body.pin.strip())
    if not team:
        raise HTTPException(401, "Username o PIN errati.")
    token = _new_session("team", team["name"])
    return {
        "token": token,
        "team": team["name"],
        "is_manager": bool(team.get("is_admin")),
        "pin_must_change": bool(team.get("pin_must_change")),
    }


@app.post("/api/admin/login")
def admin_login(body: AdminLoginBody):
    if not secrets.compare_digest(body.password, ADMIN_PASSWORD):
        raise HTTPException(401, "Password admin errata.")
    return {"token": _new_session("superadmin", None)}


@app.post("/api/spectator/login")
def spectator_login():
    return {"token": _new_session("spectator", None)}


@app.post("/api/logout")
async def logout(session: dict = Depends(get_session)):
    if session.get("role") == "team" and session.get("team"):
        db.set_ready(session["team"], False)
    SESSIONS.pop(session["token"], None)
    _save_sessions()
    await broadcast_state()
    if auction.mode == "idle" and auto_random_enabled():
        await _arm_auto_random()
    return {"ok": True}


# ========= SUPER ADMIN: PREPARAZIONE =========
@app.get("/api/admin/stats")
def admin_stats(_: dict = Depends(require_superadmin)):
    return db.count_rows()


@app.get("/api/admin/teams")
def admin_teams(_: dict = Depends(require_superadmin)):
    return db.get_all_teams(True)


def _slug(text: str):
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-zA-Z0-9]+", "", text).lower()
    return text[:18] or "team"


@app.get("/api/admin/teams/suggestions")
def team_suggestions(_: dict = Depends(require_superadmin)):
    teams = db.get_all_teams(True)
    used = set()
    out = []
    for t in teams:
        username = (t.get("username") or "").strip()
        if not username:
            base = _slug(t["name"])
            username = base
            n = 2
            while username.lower() in used:
                username = f"{base}{n}"
                n += 1
        used.add(username.lower())
        # Il Super Admin non puo' leggere un PIN gia' impostato. Per gli account
        # nuovi generiamo solo un PIN temporaneo da distribuire una volta.
        pin = None if t.get("pin_configured") else str(secrets.randbelow(9000) + 1000)
        out.append({
            "name": t["name"], "username": username, "pin": pin,
            "pin_configured": bool(t.get("pin_configured")),
            "budget": int(t["budget"]), "is_admin": bool(t["is_admin"]),
        })
    return out


class TeamConfigItem(BaseModel):
    name: str
    username: str
    pin: Optional[str] = None
    budget: int
    is_admin: bool = False


class TeamConfigBody(BaseModel):
    teams: list[TeamConfigItem]


@app.post("/api/admin/teams/config")
async def save_team_config(body: TeamConfigBody, _: dict = Depends(require_superadmin)):
    items = [x.model_dump() for x in body.teams]
    try:
        db.save_team_configuration(items)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    for item in items:
        if str(item.get("pin") or "").strip():
            _invalidate_team_sessions(str(item["name"]).strip())
    storage.salva("configurazione squadre")
    await broadcast_state()
    return {"ok": True}


@app.post("/api/admin/upload/catalog")
async def upload_catalog(file: UploadFile = File(...), _: dict = Depends(require_superadmin)):
    raw = await file.read()
    if len(raw) > 8 * 1024 * 1024:
        raise HTTPException(413, "File troppo grande (massimo 8 MB).")
    parsed = csv_parser.parse_catalog_csv(raw.decode("utf-8-sig", errors="replace"))
    if parsed["errors"]:
        raise HTTPException(400, {"message": "Catalogo non importato: correggi il file.", "errors": parsed["errors"][:30]})
    try:
        db.replace_catalog(parsed["players"])
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    storage.salva("catalogo")
    return {"ok": True, "players_loaded": len(parsed["players"])}


@app.post("/api/admin/upload/statistics")
async def upload_statistics(file: UploadFile = File(...), _: dict = Depends(require_superadmin)):
    raw = await file.read()
    if len(raw) > 12 * 1024 * 1024:
        raise HTTPException(413, "File troppo grande (massimo 12 MB).")
    name = (file.filename or "").lower()
    try:
        if name.endswith((".xlsx", ".xlsm")):
            from openpyxl import load_workbook
            wb = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
            ws = wb.active
            rows = [["" if v is None else str(v) for v in row] for row in ws.iter_rows(values_only=True)]
            parsed = csv_parser.parse_stats_rows(rows)
        else:
            parsed = csv_parser.parse_stats_csv(raw.decode("utf-8-sig", errors="replace"))
    except Exception as exc:
        raise HTTPException(400, f"Impossibile leggere il file statistiche: {exc}") from exc
    if parsed["errors"]:
        raise HTTPException(400, {"message": "Statistiche non importate.", "errors": parsed["errors"][:30]})
    summary = db.update_player_stats(parsed["records"], parsed.get("labels") or {})
    storage.salva("statistiche")
    return {"ok": True, **summary, "fields": list((parsed.get("labels") or {}).values())}


@app.post("/api/admin/upload/rosters")
async def upload_rosters(file: UploadFile = File(...), _: dict = Depends(require_superadmin)):
    raw = await file.read()
    if len(raw) > 8 * 1024 * 1024:
        raise HTTPException(413, "File troppo grande (massimo 8 MB).")
    parsed = csv_parser.parse_rosters_csv(raw.decode("utf-8-sig", errors="replace"))
    if parsed["errors"]:
        raise HTTPException(400, {"message": "Rose non importate: correggi il file.", "errors": parsed["errors"][:30]})
    try:
        db.replace_initial_rosters(list(parsed["teams"].keys()), parsed["assignments"])
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    auction.reset(persist=True)
    storage.salva("caricamento rose")
    await broadcast_state()
    return {
        "ok": True,
        "teams_found": len(parsed["teams"]),
        "assignments_loaded": len(parsed["assignments"]),
    }


class EnterManagerBody(BaseModel):
    team: str


@app.post("/api/admin/enter-manager")
def enter_manager(body: EnterManagerBody, _: dict = Depends(require_superadmin)):
    team = db.get_team(body.team.strip())
    if not team or not team.get("is_admin"):
        raise HTTPException(400, "Questa squadra non e' configurata come gestore dell'asta.")
    return {
        "token": _new_session("team", team["name"]),
        "team": team["name"],
        "is_manager": True,
        "pin_must_change": bool(team.get("pin_must_change")),
    }


class ResetPinBody(BaseModel):
    team: str


@app.post("/api/admin/teams/reset-pin")
async def admin_reset_pin(body: ResetPinBody, _: dict = Depends(require_superadmin)):
    team = body.team.strip()
    if not db.get_team(team):
        raise HTTPException(404, "Squadra non trovata.")
    temporary = str(secrets.randbelow(9000) + 1000)
    try:
        db.set_team_pin(team, temporary, must_change=True)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    _invalidate_team_sessions(team)
    await broadcast_state()
    return {"ok": True, "team": team, "temporary_pin": temporary}


class ResetBody(BaseModel):
    keep_catalog: bool = False


@app.post("/api/admin/reset")
async def reset_all(body: ResetBody, _: dict = Depends(require_superadmin)):
    global _timer_task
    _stop_timer()
    _cancel_auto_random()
    db.set_setting("auto_random", "0")
    db.reset_all(bool(body.keep_catalog))
    auction.reset(persist=True)
    await broadcast_state()
    return {"ok": True, **db.count_rows()}


# ========= BACKUP / EXPORT =========
@app.get("/api/admin/export/rose")
def export_rose(_: dict = Depends(require_superadmin)):
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    return Response(
        content=storage.genera_rose_csv(), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="rose_{today}.csv"'},
    )


@app.get("/api/admin/export/residui")
def export_residui(_: dict = Depends(require_superadmin)):
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    return Response(
        content=storage.genera_residui_csv(), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="accessi_residui_{today}.csv"'},
    )


@app.post("/api/admin/backup")
def save_backup(_: dict = Depends(require_superadmin)):
    if not storage.salva("manuale"):
        raise HTTPException(500, "Backup non riuscito.")
    return {"ok": True}


@app.get("/api/admin/backup")
def list_backups(_: dict = Depends(require_superadmin)):
    return storage.elenco_backup()


class RestoreBody(BaseModel):
    tag: str


@app.post("/api/admin/backup/restore")
async def restore_backup(body: RestoreBody, _: dict = Depends(require_superadmin)):
    _stop_timer()
    try:
        result = storage.ripristina(body.tag)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    auction.load_persisted()
    if auction.mode in ("bidding", "tiebreak") and not auction.paused:
        _start_timer()
    await broadcast_state()
    return {"ok": True, **result}


# ========= DATI PARTECIPANTE =========
@app.get("/api/state")
def get_state(session: dict = Depends(require_team)):
    return build_state(session["team"])


@app.get("/api/roster/me")
def my_roster(session: dict = Depends(require_team)):
    return db.get_roster(session["team"])


@app.get("/api/player/{pid}")
def player_detail(pid: int, session: dict = Depends(require_team)):
    _ensure_pin_changed(session)
    p = db.get_player(pid)
    if not p:
        raise HTTPException(404, "Calciatore non trovato.")
    return {"player": p, "stat_labels": db.get_stats_labels()}


@app.get("/api/history")
def history(_: dict = Depends(require_team)):
    return db.get_auction_history(120)


@app.get("/api/rosters")
def rosters(session: dict = Depends(require_team)):
    _ensure_pin_changed(session)
    return {"teams": db.get_public_teams(), "rosters": db.get_all_rosters(), "max_roster": db.MAX_ROSA}


@app.get("/api/free-agents")
def free_agents(session: dict = Depends(require_team)):
    _ensure_pin_changed(session)
    return db.get_free_agents(session["team"])


class FirstPinBody(BaseModel):
    new_pin: str


@app.post("/api/account/pin/first")
async def first_pin_change(body: FirstPinBody, session: dict = Depends(require_team)):
    team = db.get_team(session["team"])
    if not team or not team.get("pin_must_change"):
        raise HTTPException(400, "Il cambio PIN iniziale non e' richiesto.")
    try:
        db.set_team_pin(session["team"], body.new_pin, must_change=False)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    _invalidate_team_sessions(session["team"], keep_token=session["token"])
    await broadcast_state()
    return {"ok": True}


class ChangePinBody(BaseModel):
    current_pin: str
    new_pin: str


@app.post("/api/account/pin/change")
async def change_pin(body: ChangePinBody, session: dict = Depends(require_team)):
    if not db.verify_team_pin(session["team"], body.current_pin):
        raise HTTPException(400, "PIN attuale non corretto.")
    if body.current_pin.strip() == body.new_pin.strip():
        raise HTTPException(400, "Scegli un PIN diverso da quello attuale.")
    try:
        db.set_team_pin(session["team"], body.new_pin, must_change=False)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    _invalidate_team_sessions(session["team"], keep_token=session["token"])
    return {"ok": True}


@app.get("/api/spectator/state")
def spectator_state(_: dict = Depends(require_spectator)):
    return build_state(None)


@app.get("/api/spectator/player/{pid}")
def spectator_player_detail(pid: int, _: dict = Depends(require_spectator)):
    p = db.get_player(pid)
    if not p:
        raise HTTPException(404, "Calciatore non trovato.")
    return {"player": p, "stat_labels": db.get_stats_labels()}


@app.get("/api/spectator/history")
def spectator_history(_: dict = Depends(require_spectator)):
    return db.get_auction_history(120)


@app.get("/api/spectator/rosters")
def spectator_rosters(_: dict = Depends(require_spectator)):
    return {"teams": db.get_public_teams(), "rosters": db.get_all_rosters(), "max_roster": db.MAX_ROSA}


@app.get("/api/spectator/free-agents")
def spectator_free_agents(_: dict = Depends(require_spectator)):
    return db.get_free_agents(None)


class PushSubscriptionBody(BaseModel):
    endpoint: str
    keys: dict[str, str]


@app.get("/api/push/public-key")
def push_public_key(_: dict = Depends(require_team)):
    return {"public_key": push_service.public_key()}


@app.post("/api/push/subscribe")
def push_subscribe(body: PushSubscriptionBody, session: dict = Depends(require_team)):
    _ensure_pin_changed(session)
    try:
        db.upsert_push_subscription(session["team"], body.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True}


@app.post("/api/push/unsubscribe")
def push_unsubscribe(body: PushSubscriptionBody, session: dict = Depends(require_team)):
    db.delete_push_subscription(body.endpoint, session["team"])
    return {"ok": True}


class ReadyBody(BaseModel):
    ready: bool = True


@app.post("/api/ready")
async def set_ready(body: ReadyBody, session: dict = Depends(require_team)):
    team = _ensure_pin_changed(session)
    if body.ready and team and team.get("market_finished"):
        raise HTTPException(400, "Hai gia' concluso il mercato.")
    db.set_ready(session["team"], bool(body.ready))
    await broadcast_state()
    if auction.mode == "idle" and auto_random_enabled():
        await _arm_auto_random()
    return {"ok": True, "ready": bool(body.ready)}


@app.post("/api/market/finish")
async def finish_market(session: dict = Depends(require_team)):
    _ensure_pin_changed(session)
    if auction.mode != "idle":
        raise HTTPException(400, "Puoi concludere il mercato solo tra un'asta e la successiva.")
    summary = db.roster_summary(session["team"])
    if not summary["valid_finish"]:
        raise HTTPException(400, {
            "message": "La rosa non rispetta i requisiti per concludere il mercato.",
            "roster_rules": summary,
        })
    try:
        db.set_market_finished(session["team"], True)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    await broadcast_state()
    await _arm_auto_random()
    return {"ok": True, "market_finished": True, "roster_rules": summary}


# ========= TIMER / ASTA =========
async def _push_new_auction():
    player = db.get_player(auction.current_pid) if auction.current_pid is not None else None
    if not player or not auction.eligible_teams:
        return
    await push_service.send_to_teams(
        auction.eligible_teams,
        title="🎲 Nuova asta Rannatoni",
        body=f"{player['name']} — entra per offrire",
        url="/auction",
        tag=f"asta-{player['pid']}",
    )


async def _push_tiebreak(result: dict):
    player = db.get_player(result.get("pid")) if result.get("pid") is not None else None
    if not player:
        return
    await push_service.send_to_teams(
        result.get("teams") or [],
        title="⚔️ Spareggio Rannatoni",
        body=f"{player['name']} — devi rilanciare",
        url="/auction",
        tag=f"spareggio-{player['pid']}",
    )


def _cancel_auto_random():
    global _auto_random_task, _auto_random_deadline
    task = _auto_random_task
    _auto_random_task = None
    _auto_random_deadline = None
    if task and not task.done() and task is not asyncio.current_task():
        task.cancel()


async def _arm_auto_random():
    """Avvia il conto alla rovescia solo se l'asta e' davvero pronta a proseguire."""
    global _auto_random_task, _auto_random_deadline
    _cancel_auto_random()
    if not auto_random_enabled() or auction.mode != "idle":
        return False
    ready = db.ready_market_teams()
    if not ready:
        await broadcast_state()
        return False
    _auto_random_deadline = time.time() + AUTO_RANDOM_DELAY

    async def runner():
        global _auto_random_task, _auto_random_deadline
        try:
            while _auto_random_deadline and time.time() < _auto_random_deadline:
                await asyncio.sleep(0.25)
            async with AUCTION_LOCK:
                # Ricontrolla tutto allo zero: qualcuno potrebbe essersi disconnesso,
                # aver concluso il mercato o l'admin potrebbe aver aperto manualmente.
                if not auto_random_enabled() or auction.mode != "idle":
                    return
                ready_now = db.ready_market_teams()
                if not ready_now:
                    return
                auction.open_random()
                _start_timer()
            await broadcast_state()
            asyncio.create_task(_push_new_auction())
        except asyncio.CancelledError:
            return
        except auction_module.AuctionError as exc:
            print(f"[auto-random] {exc}")
        finally:
            _auto_random_task = None
            _auto_random_deadline = None
            await broadcast_state()

    _auto_random_task = asyncio.create_task(runner())
    await broadcast_state()
    return True


def _stop_timer():
    global _timer_task
    task = _timer_task
    _timer_task = None
    if task and not task.done() and task is not asyncio.current_task():
        task.cancel()


def _start_timer():
    global _timer_task
    _stop_timer()
    if auction.mode not in ("bidding", "tiebreak") or auction.paused:
        return

    async def waiter():
        try:
            while auction.mode in ("bidding", "tiebreak") and not auction.paused:
                if auction.expired():
                    async with AUCTION_LOCK:
                        if auction.mode in ("bidding", "tiebreak") and auction.expired():
                            await _close_and_broadcast(from_timer=True)
                    return
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            return

    _timer_task = asyncio.create_task(waiter())


async def _close_and_broadcast(from_timer: bool = False):
    result = auction.close()
    if result.get("type") == "tiebreak":
        _start_timer()
    else:
        _stop_timer()
        if result.get("type") == "assigned" and not result.get("needs_release"):
            backup_if_real(f"acquisto {result.get('player_name', '')}")
        elif result.get("type") == "no_offers":
            backup_if_real("giocatore passato")
    await manager.broadcast_result(result)
    await broadcast_state()
    if result.get("type") == "tiebreak":
        asyncio.create_task(_push_tiebreak(result))
    if result.get("type") in ("assigned", "no_offers") and not result.get("needs_release"):
        await _arm_auto_random()
    return result


def _readiness_status():
    active = db.active_market_teams()
    missing = [t["name"] for t in active if not t.get("ready")]
    return len(active), missing


class RandomBody(BaseModel):
    force: bool = False


@app.post("/api/auction/admin/random")
async def open_random(body: RandomBody, _: dict = Depends(require_auction_manager)):
    _cancel_auto_random()
    async with AUCTION_LOCK:
        try:
            pid = auction.open_random()
        except auction_module.AuctionError as exc:
            raise HTTPException(400, str(exc)) from exc
        _start_timer()
    await broadcast_state()
    asyncio.create_task(_push_new_auction())
    return {"ok": True, "pid": pid, "player": db.get_player(pid), "participants": len(auction.eligible_teams)}


class OpenPlayerBody(BaseModel):
    pid: int
    force: bool = False


@app.post("/api/auction/admin/open")
async def open_manual(body: OpenPlayerBody, _: dict = Depends(require_auction_manager)):
    _cancel_auto_random()
    async with AUCTION_LOCK:
        try:
            auction.open_player(body.pid)
        except auction_module.AuctionError as exc:
            raise HTTPException(400, str(exc)) from exc
        _start_timer()
    await broadcast_state()
    asyncio.create_task(_push_new_auction())
    return {"ok": True}


@app.get("/api/auction/admin/search")
def manager_search(q: str = "", _: dict = Depends(require_auction_manager)):
    players = db.search_players(q, limit=30, exclude_assigned=True)
    passed_ids = {p["pid"] for p in db.get_passed_players()}
    for p in players:
        p["passed"] = p["pid"] in passed_ids
    return players


@app.post("/api/auction/admin/close")
async def close_bids(session: dict = Depends(require_auction_manager)):
    async with AUCTION_LOCK:
        if auction.mode not in ("bidding", "tiebreak"):
            raise HTTPException(400, "Nessuna asta aperta.")
        if session["team"] in auction.eligible_teams and session["team"] not in auction.offers:
            raise HTTPException(400, "Prima di aprire le buste devi inviare la tua risposta.")
        # Il gestore puo' forzare l'apertura in qualunque momento: chi manca
        # viene considerato PASSO (o mantiene la base nello spareggio).
        result = await _close_and_broadcast()
    return result


class BidBody(BaseModel):
    amount: int = Field(gt=0)


@app.post("/api/auction/bid")
async def bid(body: BidBody, session: dict = Depends(require_team)):
    _ensure_pin_changed(session)
    result = None
    async with AUCTION_LOCK:
        try:
            auction.bid(session["team"], body.amount)
        except auction_module.AuctionError as exc:
            raise HTTPException(400, str(exc)) from exc
        if auction.all_submitted():
            result = await _close_and_broadcast()
    if result is None:
        await broadcast_state()
    return {"ok": True, "amount": body.amount, "auto_opened": result is not None}


@app.post("/api/auction/pass")
async def pass_bid(session: dict = Depends(require_team)):
    _ensure_pin_changed(session)
    result = None
    async with AUCTION_LOCK:
        try:
            auction.pass_(session["team"])
        except auction_module.AuctionError as exc:
            raise HTTPException(400, str(exc)) from exc
        if auction.all_submitted():
            result = await _close_and_broadcast()
    if result is None:
        await broadcast_state()
    return {"ok": True, "auto_opened": result is not None}


class ReleaseBody(BaseModel):
    pids: list[int]


@app.post("/api/auction/release")
async def release_for_purchase(body: ReleaseBody, session: dict = Depends(require_team)):
    info = auction.last_result.get("needs_release", {}) if auction.last_result else {}
    if auction.mode != "release" or info.get("team") != session["team"]:
        raise HTTPException(403, "Solo il vincitore puo' completare gli svincoli.")
    async with AUCTION_LOCK:
        try:
            result = auction.complete_with_releases(body.pids)
        except auction_module.AuctionError as exc:
            raise HTTPException(400, str(exc)) from exc
        backup_if_real("acquisto con svincoli")
    await manager.broadcast_result(result)
    await broadcast_state()
    await _arm_auto_random()
    return result


class AddTimeBody(BaseModel):
    seconds: int = Field(gt=0, le=300)


@app.post("/api/auction/admin/pause")
async def pause_timer(_: dict = Depends(require_auction_manager)):
    async with AUCTION_LOCK:
        try:
            auction.pause()
        except auction_module.AuctionError as exc:
            raise HTTPException(400, str(exc)) from exc
        _stop_timer()
    await broadcast_state()
    return {"ok": True}


@app.post("/api/auction/admin/resume")
async def resume_timer(_: dict = Depends(require_auction_manager)):
    async with AUCTION_LOCK:
        try:
            auction.resume()
        except auction_module.AuctionError as exc:
            raise HTTPException(400, str(exc)) from exc
        _start_timer()
    await broadcast_state()
    return {"ok": True}


@app.post("/api/auction/admin/add-time")
async def add_time(body: AddTimeBody, _: dict = Depends(require_auction_manager)):
    async with AUCTION_LOCK:
        try:
            auction.add_time(body.seconds)
        except auction_module.AuctionError as exc:
            raise HTTPException(400, str(exc)) from exc
    await broadcast_state()
    return {"ok": True}


@app.post("/api/auction/admin/cancel")
async def cancel_auction(_: dict = Depends(require_auction_manager)):
    _cancel_auto_random()
    async with AUCTION_LOCK:
        try:
            auction.cancel()
        except auction_module.AuctionError as exc:
            raise HTTPException(400, str(exc)) from exc
        _stop_timer()
    await broadcast_state()
    return {"ok": True}


class AutoRandomBody(BaseModel):
    enabled: bool


@app.post("/api/auction/admin/auto-random")
async def set_auto_random(body: AutoRandomBody, _: dict = Depends(require_auction_manager)):
    db.set_setting("auto_random", "1" if body.enabled else "0")
    if body.enabled:
        await _arm_auto_random()
    else:
        _cancel_auto_random()
        await broadcast_state()
    return {"ok": True, "enabled": body.enabled}


@app.post("/api/auction/admin/auto-random/skip")
async def skip_next_auto_random(_: dict = Depends(require_auction_manager)):
    _cancel_auto_random()
    await broadcast_state()
    return {"ok": True, "enabled": auto_random_enabled()}


class ReactivateTeamBody(BaseModel):
    team: str


@app.post("/api/auction/admin/reactivate-team")
async def reactivate_team(body: ReactivateTeamBody, _: dict = Depends(require_auction_manager)):
    if auction.mode != "idle":
        raise HTTPException(400, "Riattiva una squadra tra un'asta e la successiva.")
    try:
        db.set_market_finished(body.team.strip(), False)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    await broadcast_state()
    return {"ok": True}


@app.post("/api/auction/admin/reset-ready")
async def reset_ready(_: dict = Depends(require_auction_manager)):
    _cancel_auto_random()
    db.reset_ready()
    await broadcast_state()
    return {"ok": True}


@app.get("/api/auction/admin/passed")
def passed_players(_: dict = Depends(require_auction_manager)):
    return db.get_passed_players()


class ReopenPassedBody(BaseModel):
    pid: Optional[int] = None


@app.post("/api/auction/admin/reopen-passed")
async def reopen_passed(body: ReopenPassedBody, _: dict = Depends(require_auction_manager)):
    db.clear_passed(body.pid)
    await broadcast_state()
    return {"ok": True}


@app.post("/api/auction/admin/undo-last")
async def undo_last(_: dict = Depends(require_auction_manager)):
    _cancel_auto_random()
    if auction.mode != "idle":
        raise HTTPException(400, "Puoi annullare l'ultima assegnazione solo quando non c'e' un'asta in corso.")
    async with AUCTION_LOCK:
        try:
            result = db.undo_last_assignment()
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        auction.last_result = None
        auction._persist()
        backup_if_real("annulla ultima assegnazione")
    await broadcast_state()
    return {"ok": True, **result}


# ========= MODALITA' SIMULAZIONE =========
@app.post("/api/auction/admin/simulation/start")
async def start_simulation(_: dict = Depends(require_auction_manager)):
    _cancel_auto_random()
    if simulation_active():
        raise HTTPException(400, "La simulazione e' gia' attiva.")
    if auction.mode != "idle":
        raise HTTPException(400, "Termina o annulla l'asta corrente prima di avviare la simulazione.")
    async with AUCTION_LOCK:
        db.backup_database(SIMULATION_PATH)
        db.set_setting("simulation_active", "1")
        auction.last_result = None
        auction._persist()
    await broadcast_state()
    return {"ok": True}


@app.post("/api/auction/admin/simulation/end")
async def end_simulation(_: dict = Depends(require_auction_manager)):
    _cancel_auto_random()
    if not simulation_active() or not os.path.exists(SIMULATION_PATH):
        raise HTTPException(400, "Nessuna simulazione da terminare.")
    async with AUCTION_LOCK:
        _stop_timer()
        db.restore_database(SIMULATION_PATH)
        auction.load_persisted()
        try:
            os.remove(SIMULATION_PATH)
        except FileNotFoundError:
            pass
        if auction.mode in ("bidding", "tiebreak") and not auction.paused:
            _start_timer()
    await broadcast_state()
    return {"ok": True}


# ========= WEBSOCKET =========
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket, token: str):
    session = SESSIONS.get(token)
    if not session or float(session.get("expires_at", 0)) <= time.time():
        await ws.close(code=4401)
        return
    await manager.connect(ws, session)
    try:
        await manager.send_state(ws)
        await broadcast_state()
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except Exception:
                msg = {"type": "ping", "visibility": "visible"}
            if msg.get("type") in ("presence", "ping"):
                changed = manager.touch(ws, msg.get("visibility", "visible"))
                if changed:
                    await broadcast_state()
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(ws)
        await broadcast_state()


@app.on_event("startup")
async def startup_resume_timer():
    global _presence_task
    if auction.mode in ("bidding", "tiebreak") and not auction.paused:
        _start_timer()

    async def presence_loop():
        try:
            while True:
                await asyncio.sleep(15)
                await broadcast_state()
        except asyncio.CancelledError:
            return

    _presence_task = asyncio.create_task(presence_loop())


@app.on_event("shutdown")
async def shutdown_tasks():
    global _presence_task
    _stop_timer()
    _cancel_auto_random()
    if _presence_task and not _presence_task.done():
        _presence_task.cancel()
    _presence_task = None


# ========= FRONTEND / PWA =========
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
NO_CACHE = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}


@app.get("/health")
def health():
    return {"ok": True, "service": "rannatoni"}


@app.get("/spectator")
def serve_spectator():
    return FileResponse(os.path.join(FRONTEND_DIR, "spectator.html"), headers=NO_CACHE)


@app.get("/")
def serve_index():
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"), headers=NO_CACHE)


@app.get("/admin")
def serve_admin():
    return FileResponse(os.path.join(FRONTEND_DIR, "admin.html"), headers=NO_CACHE)


@app.get("/auction")
def serve_auction():
    return FileResponse(os.path.join(FRONTEND_DIR, "auction.html"), headers=NO_CACHE)


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(os.path.join(FRONTEND_DIR, "manifest.webmanifest"), media_type="application/manifest+json")


@app.get("/sw.js")
def service_worker():
    return FileResponse(
        os.path.join(FRONTEND_DIR, "sw.js"), media_type="application/javascript",
        headers={"Cache-Control": "no-cache"},
    )
