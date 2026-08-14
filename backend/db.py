"""Database SQLite per Asta Fantacalcio.

Il DB e' la fonte di verita' per configurazione, rose, storico e stato live.
Le connessioni sono brevi e hanno foreign key + busy timeout attivi.
"""
from __future__ import annotations

import base64
import datetime as _dt
import hashlib
import hmac
import json
import os
import secrets
import shutil
import sqlite3
from contextlib import contextmanager

_DEFAULT_DB = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "fantacalcio.db"))
DB_PATH = os.environ.get("FANTACALCIO_DB", _DEFAULT_DB)

BUDGET_INITIAL = 500
MAX_ROSA = 35
MIN_GOALKEEPERS = 2
MAX_GOALKEEPERS = 5
MIN_OUTFIELD = 21
REIMBURSE_RATE = 1.0


@contextmanager
def get_conn():
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    try:
        yield conn
    finally:
        conn.close()


def _table_columns(conn, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def init_db():
    with get_conn() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS teams (
                name TEXT PRIMARY KEY,
                username TEXT UNIQUE,
                pin TEXT NOT NULL DEFAULT '',
                pin_hash TEXT NOT NULL DEFAULT '',
                pin_must_change INTEGER NOT NULL DEFAULT 1,
                budget INTEGER NOT NULL DEFAULT 0,
                is_admin INTEGER NOT NULL DEFAULT 0,
                ready INTEGER NOT NULL DEFAULT 0,
                market_finished INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS players (
                pid INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                roles TEXT NOT NULL DEFAULT '[]',
                club TEXT DEFAULT '',
                img TEXT DEFAULT '',
                stats_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS roster (
                team TEXT NOT NULL,
                pid INTEGER NOT NULL UNIQUE,
                price INTEGER NOT NULL,
                PRIMARY KEY (team, pid),
                FOREIGN KEY (team) REFERENCES teams(name) ON DELETE CASCADE,
                FOREIGN KEY (pid) REFERENCES players(pid) ON DELETE RESTRICT
            );

            CREATE TABLE IF NOT EXISTS auction_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                event_type TEXT NOT NULL,
                pid INTEGER,
                team TEXT,
                price INTEGER,
                reveal_json TEXT NOT NULL DEFAULT '{}',
                rounds_json TEXT NOT NULL DEFAULT '[]',
                released_json TEXT NOT NULL DEFAULT '[]',
                tocca INTEGER NOT NULL DEFAULT 0,
                undone INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS passed_players (
                pid INTEGER PRIMARY KEY,
                FOREIGN KEY (pid) REFERENCES players(pid) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE IF NOT EXISTS auction_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                state_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS push_subscriptions (
                endpoint TEXT PRIMARY KEY,
                team TEXT NOT NULL,
                p256dh TEXT NOT NULL,
                auth TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (team) REFERENCES teams(name) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_push_team ON push_subscriptions(team);
            """
        )

        # Migrazioni leggere da versioni MVP precedenti.
        team_cols = _table_columns(conn, "teams")
        if "username" not in team_cols:
            conn.execute("ALTER TABLE teams ADD COLUMN username TEXT")
        if "pin" not in team_cols:
            conn.execute("ALTER TABLE teams ADD COLUMN pin TEXT NOT NULL DEFAULT ''")
        if "pin_hash" not in team_cols:
            conn.execute("ALTER TABLE teams ADD COLUMN pin_hash TEXT NOT NULL DEFAULT ''")
        if "pin_must_change" not in team_cols:
            conn.execute("ALTER TABLE teams ADD COLUMN pin_must_change INTEGER NOT NULL DEFAULT 1")
        if "budget" not in team_cols:
            conn.execute("ALTER TABLE teams ADD COLUMN budget INTEGER NOT NULL DEFAULT 0")
        if "is_admin" not in team_cols:
            conn.execute("ALTER TABLE teams ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")
        if "ready" not in team_cols:
            conn.execute("ALTER TABLE teams ADD COLUMN ready INTEGER NOT NULL DEFAULT 0")
        if "market_finished" not in team_cols:
            conn.execute("ALTER TABLE teams ADD COLUMN market_finished INTEGER NOT NULL DEFAULT 0")

        player_cols = _table_columns(conn, "players")
        if "stats_json" not in player_cols:
            conn.execute("ALTER TABLE players ADD COLUMN stats_json TEXT NOT NULL DEFAULT '{}'")

        # Il vecchio schema non aveva UNIQUE(pid). Prima di creare l'indice segnaliamo
        # eventuali dati corrotti invece di scegliere arbitrariamente un proprietario.
        duplicates = conn.execute(
            "SELECT pid, COUNT(*) n FROM roster GROUP BY pid HAVING COUNT(*) > 1"
        ).fetchall()
        if duplicates:
            ids = ", ".join(str(r["pid"]) for r in duplicates[:10])
            raise RuntimeError(
                "Database non valido: alcuni giocatori appartengono a piu' squadre "
                f"(ID: {ids}). Correggi/reimporta le rose prima di continuare."
            )
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_roster_pid ON roster(pid)")
        _migrate_plaintext_pins(conn)
        conn.commit()


# ---------- PIN / CREDENZIALI ----------
_PIN_ITERATIONS = 220_000

def _validate_pin_format(pin: str):
    pin = str(pin or "").strip()
    if not (pin.isdigit() and len(pin) in (4, 6)):
        raise ValueError("Il PIN deve contenere esattamente 4 oppure 6 cifre.")
    return pin

def hash_pin(pin: str) -> str:
    pin = _validate_pin_format(pin)
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, _PIN_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        _PIN_ITERATIONS,
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    )

def verify_pin(pin: str, encoded: str) -> bool:
    try:
        scheme, iterations, salt_b64, digest_b64 = str(encoded or "").split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        salt = base64.urlsafe_b64decode(salt_b64.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_b64.encode("ascii"))
        actual = hashlib.pbkdf2_hmac("sha256", str(pin).encode("utf-8"), salt, int(iterations))
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False

def _migrate_plaintext_pins(conn):
    """Migra automaticamente i PIN delle vecchie versioni senza lasciarli in chiaro."""
    rows = conn.execute("SELECT name, pin, pin_hash FROM teams").fetchall()
    for row in rows:
        plain = str(row["pin"] or "").strip()
        encoded = str(row["pin_hash"] or "").strip()
        if plain and not encoded:
            try:
                encoded = hash_pin(plain)
            except ValueError:
                continue
            conn.execute(
                "UPDATE teams SET pin_hash=?, pin='', pin_must_change=1 WHERE name=?",
                (encoded, row["name"]),
            )
        elif plain:
            conn.execute("UPDATE teams SET pin='' WHERE name=?", (row["name"],))



# ---------- TEAMS ----------
def ensure_team(name: str, budget: int = 0) -> bool:
    with get_conn() as conn:
        row = conn.execute("SELECT 1 FROM teams WHERE name = ?", (name,)).fetchone()
        if row:
            return False
        conn.execute(
            "INSERT INTO teams (name, username, pin, pin_hash, pin_must_change, budget, is_admin, ready, market_finished) VALUES (?, NULL, '', '', 1, ?, 0, 0, 0)",
            (name, budget),
        )
        conn.commit()
        return True


def get_team(name: str):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM teams WHERE name = ?", (name,)).fetchone()
        return dict(row) if row else None


def get_all_teams(include_secrets: bool = True):
    """Non restituisce mai hash o PIN attuali al frontend Super Admin."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT name, username, budget, is_admin, ready, market_finished,
                   CASE WHEN pin_hash<>'' THEN 1 ELSE 0 END AS pin_configured,
                   pin_must_change
            FROM teams ORDER BY name COLLATE NOCASE
            """
        ).fetchall()
        return [dict(r) for r in rows]


def get_public_teams():
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT t.name, t.budget, t.is_admin, t.ready, t.market_finished, COUNT(r.pid) AS roster_size
            FROM teams t LEFT JOIN roster r ON r.team = t.name
            GROUP BY t.name, t.budget, t.is_admin, t.ready, t.market_finished
            ORDER BY t.name COLLATE NOCASE
            """
        ).fetchall()
        return [dict(r) for r in rows]


def get_team_by_credentials(username: str, pin: str):
    if not username or not pin:
        return None
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM teams WHERE lower(username) = lower(?) AND pin_hash <> ''",
            (username,),
        ).fetchone()
        if not row or not verify_pin(pin, row["pin_hash"]):
            return None
        return dict(row)


def verify_team_pin(team: str, pin: str) -> bool:
    with get_conn() as conn:
        row = conn.execute("SELECT pin_hash FROM teams WHERE name=?", (team,)).fetchone()
        return bool(row and verify_pin(pin, row["pin_hash"]))


def username_taken_by_other(username: str, team_name: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT name FROM teams WHERE lower(username) = lower(?) AND name <> ?",
            (username, team_name),
        ).fetchone()
        return row is not None


def save_team_configuration(items: list[dict]):
    """Salva configurazione e, solo se fornito, imposta un nuovo PIN temporaneo.

    Il PIN esistente non viene mai restituito ne' richiesto per risalvare residui/username.
    """
    if not items:
        raise ValueError("Nessuna squadra da salvare.")
    managers = [x for x in items if x.get("is_admin")]
    if len(managers) != 1:
        raise ValueError("Devi scegliere esattamente una squadra che gestisce l'asta.")

    usernames = [str(x.get("username", "")).strip().lower() for x in items]
    if any(not u for u in usernames) or len(usernames) != len(set(usernames)):
        raise ValueError("Ogni squadra deve avere uno username univoco.")

    with get_conn() as conn:
        existing_rows = conn.execute("SELECT name, pin_hash FROM teams").fetchall()
        existing = {r["name"]: r["pin_hash"] for r in existing_rows}
        requested = {str(x["name"]).strip() for x in items}
        missing = requested - set(existing)
        if missing:
            raise ValueError("Squadre non trovate: " + ", ".join(sorted(missing)))
        for x in items:
            name = str(x["name"]).strip()
            pin = str(x.get("pin") or "").strip()
            if not pin and not existing.get(name):
                raise ValueError(f"Devi impostare un PIN temporaneo per {name}.")
            if pin:
                encoded = hash_pin(pin)
                conn.execute(
                    """UPDATE teams SET username=?, pin='', pin_hash=?, pin_must_change=1,
                       budget=?, is_admin=?, ready=0, market_finished=0 WHERE name=?""",
                    (str(x["username"]).strip(), encoded, int(x["budget"]), 1 if x.get("is_admin") else 0, name),
                )
            else:
                conn.execute(
                    "UPDATE teams SET username=?, budget=?, is_admin=? WHERE name=?",
                    (str(x["username"]).strip(), int(x["budget"]), 1 if x.get("is_admin") else 0, name),
                )
        conn.commit()


def set_team_pin(team: str, new_pin: str, must_change: bool):
    encoded = hash_pin(new_pin)
    with get_conn() as conn:
        if not conn.execute("SELECT 1 FROM teams WHERE name=?", (team,)).fetchone():
            raise ValueError("Squadra non trovata.")
        conn.execute(
            "UPDATE teams SET pin='', pin_hash=?, pin_must_change=? WHERE name=?",
            (encoded, 1 if must_change else 0, team),
        )
        conn.commit()


def set_pin_must_change(team: str, must_change: bool):
    with get_conn() as conn:
        conn.execute("UPDATE teams SET pin_must_change=? WHERE name=?", (1 if must_change else 0, team))
        conn.commit()


def set_ready(team: str, ready: bool):
    with get_conn() as conn:
        conn.execute("UPDATE teams SET ready=? WHERE name=?", (1 if ready else 0, team))
        conn.commit()


def reset_ready():
    with get_conn() as conn:
        conn.execute("UPDATE teams SET ready=0 WHERE market_finished=0")
        conn.commit()


def set_market_finished(team: str, finished: bool):
    """Segna una squadra come fuori dal mercato senza cancellarne account o rosa."""
    with get_conn() as conn:
        row = conn.execute("SELECT 1 FROM teams WHERE name=?", (team,)).fetchone()
        if not row:
            raise ValueError("Squadra non trovata.")
        conn.execute(
            "UPDATE teams SET market_finished=?, ready=? WHERE name=?",
            (1 if finished else 0, 0, team),
        )
        conn.commit()


def active_market_teams():
    """Squadre configurate che devono ancora partecipare alle aste."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM teams
               WHERE username IS NOT NULL AND username<>'' AND pin_hash<>'' AND market_finished=0
               ORDER BY name COLLATE NOCASE"""
        ).fetchall()
        return [dict(r) for r in rows]


def ready_market_teams():
    return [t for t in active_market_teams() if t.get("ready")]


def update_team_budget(team: str, new_budget: int):
    with get_conn() as conn:
        conn.execute("UPDATE teams SET budget=? WHERE name=?", (int(new_budget), team))
        conn.commit()


def reset_all(keep_catalog: bool = False):
    with get_conn() as conn:
        conn.execute("DELETE FROM auction_state")
        conn.execute("DELETE FROM auction_events")
        conn.execute("DELETE FROM passed_players")
        conn.execute("DELETE FROM roster")
        conn.execute("DELETE FROM teams")
        if not keep_catalog:
            conn.execute("DELETE FROM players")
        conn.commit()


def count_rows():
    with get_conn() as conn:
        return {
            "teams": conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0],
            "players": conn.execute("SELECT COUNT(*) FROM players").fetchone()[0],
            "roster": conn.execute("SELECT COUNT(*) FROM roster").fetchone()[0],
            "events": conn.execute("SELECT COUNT(*) FROM auction_events WHERE undone=0").fetchone()[0],
        }


# ---------- PLAYERS / IMPORT ----------
def _player_row_to_dict(r):
    d = dict(r)
    d["roles"] = json.loads(d.get("roles") or "[]")
    try:
        d["stats"] = json.loads(d.pop("stats_json", "{}") or "{}")
    except Exception:
        d["stats"] = {}
    return d


def replace_catalog(players: list[dict]):
    if not players:
        raise ValueError("Il catalogo non contiene giocatori validi.")
    ids = [int(p["pid"]) for p in players]
    if len(ids) != len(set(ids)):
        raise ValueError("Il catalogo contiene ID duplicati.")
    with get_conn() as conn:
        roster_ids = {r["pid"] for r in conn.execute("SELECT pid FROM roster").fetchall()}
        missing = roster_ids - set(ids)
        if missing:
            raise ValueError(
                "Il nuovo catalogo non contiene alcuni giocatori gia' presenti nelle rose: "
                + ", ".join(map(str, sorted(missing)[:20]))
            )
        # Upsert prima, poi elimina solo i giocatori non piu' presenti. In questo
        # modo le FK delle rose esistenti restano valide durante l'aggiornamento.
        conn.executemany(
            """INSERT INTO players(pid,name,roles,club,img,stats_json) VALUES (?,?,?,?,?,?)
               ON CONFLICT(pid) DO UPDATE SET
                 name=excluded.name, roles=excluded.roles, club=excluded.club, img=excluded.img,
                 stats_json=excluded.stats_json""",
            [
                (
                    int(p["pid"]), p["name"], json.dumps(p.get("roles", [])),
                    p.get("club", ""), p.get("img", ""),
                    json.dumps(p.get("stats", {}), ensure_ascii=False),
                )
                for p in players
            ],
        )
        placeholders = ",".join("?" for _ in ids)
        conn.execute(f"DELETE FROM players WHERE pid NOT IN ({placeholders})", ids)
        conn.commit()


def replace_initial_rosters(team_names: list[str], assignments: list[dict]):
    """Sostituisce atomicamente le rose iniziali preservando la configurazione
    delle squadre che hanno lo stesso nome. Blocca duplicati e ID sconosciuti.
    """
    teams = [t.strip() for t in team_names if t and t.strip()]
    if not teams:
        raise ValueError("Nel file rose non e' stata trovata nessuna squadra.")
    if len(teams) != len(set(teams)):
        raise ValueError("Il file rose contiene nomi squadra duplicati.")

    seen = {}
    for a in assignments:
        pid = int(a["pid"])
        team = str(a["team"]).strip()
        if pid in seen and seen[pid] != team:
            raise ValueError(f"Il giocatore ID {pid} compare sia in '{seen[pid]}' sia in '{team}'.")
        seen[pid] = team
        if team not in teams:
            raise ValueError(f"Assegnazione riferita a squadra sconosciuta: {team}")

    with get_conn() as conn:
        existing_players = {r["pid"] for r in conn.execute("SELECT pid FROM players").fetchall()}
        unknown = set(seen) - existing_players
        if unknown:
            raise ValueError(
                "Alcuni ID presenti nelle rose non esistono nel catalogo: "
                + ", ".join(map(str, sorted(unknown)[:20]))
            )

        conn.execute("DELETE FROM roster")
        conn.execute("DELETE FROM auction_events")
        conn.execute("DELETE FROM auction_state")
        conn.execute("DELETE FROM passed_players")

        placeholders = ",".join("?" for _ in teams)
        conn.execute(f"DELETE FROM teams WHERE name NOT IN ({placeholders})", teams)
        for team in teams:
            conn.execute(
                "INSERT OR IGNORE INTO teams(name, username, pin, pin_hash, pin_must_change, budget, is_admin, ready, market_finished) VALUES (?, NULL, '', '', 1, 0, 0, 0, 0)",
                (team,),
            )
            conn.execute("UPDATE teams SET ready=0, market_finished=0 WHERE name=?", (team,))
        conn.executemany(
            "INSERT INTO roster(team,pid,price) VALUES (?,?,?)",
            [(a["team"].strip(), int(a["pid"]), int(a["price"])) for a in assignments],
        )
        # Salviamo la dimensione iniziale del bacino d'asta. In seguito le rose
        # cambiano, quindi calcolarlo dal roster corrente darebbe un totale mobile.
        total_players = conn.execute("SELECT COUNT(*) FROM players").fetchone()[0]
        initial_owned = len({int(a["pid"]) for a in assignments})
        conn.execute(
            "INSERT INTO settings(key,value) VALUES('auction_pool_total',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(max(0, int(total_players) - initial_owned)),),
        )
        conn.commit()


def get_player(pid: int):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM players WHERE pid=?", (int(pid),)).fetchone()
        return _player_row_to_dict(row) if row else None


def get_all_players():
    with get_conn() as conn:
        return [_player_row_to_dict(r) for r in conn.execute("SELECT * FROM players ORDER BY name COLLATE NOCASE").fetchall()]


def search_players(query: str, limit: int = 30, exclude_assigned: bool = True):
    with get_conn() as conn:
        q = f"%{query.strip().lower()}%"
        rows = conn.execute(
            """
            SELECT p.* FROM players p
            WHERE lower(p.name) LIKE ?
              AND (?=0 OR p.pid NOT IN (SELECT pid FROM roster))
            ORDER BY p.name COLLATE NOCASE LIMIT ?
            """,
            (q, 1 if exclude_assigned else 0, int(limit)),
        ).fetchall()
        return [_player_row_to_dict(r) for r in rows]


# ---------- ROSTER ----------
def get_roster(team: str):
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT r.pid, r.price, p.name, p.roles, p.club, p.img, p.stats_json
            FROM roster r JOIN players p ON p.pid=r.pid
            WHERE r.team=?
            """,
            (team,),
        ).fetchall()
        return [_player_row_to_dict(r) for r in rows]


def get_all_rosters():
    return {t["name"]: get_roster(t["name"]) for t in get_all_teams(False)}


def player_owner(pid: int):
    with get_conn() as conn:
        row = conn.execute("SELECT team FROM roster WHERE pid=?", (int(pid),)).fetchone()
        return row["team"] if row else None


def free_player_ids(exclude_passed: bool = True):
    with get_conn() as conn:
        if exclude_passed:
            rows = conn.execute(
                """SELECT pid FROM players WHERE pid NOT IN (SELECT pid FROM roster)
                   AND pid NOT IN (SELECT pid FROM passed_players)"""
            ).fetchall()
        else:
            rows = conn.execute("SELECT pid FROM players WHERE pid NOT IN (SELECT pid FROM roster)").fetchall()
        return [r["pid"] for r in rows]


def _is_goalkeeper_roles(roles) -> bool:
    return "P" in (roles or [])


def roster_summary(team: str):
    roster = get_roster(team)
    keepers = sum(1 for g in roster if _is_goalkeeper_roles(g.get("roles")))
    total = len(roster)
    outfield = total - keepers
    issues = []
    if keepers < MIN_GOALKEEPERS:
        issues.append(f"Servono almeno {MIN_GOALKEEPERS} portieri (ora {keepers}).")
    if keepers > MAX_GOALKEEPERS:
        issues.append(f"Puoi avere al massimo {MAX_GOALKEEPERS} portieri (ora {keepers}).")
    if outfield < MIN_OUTFIELD:
        issues.append(f"Servono almeno {MIN_OUTFIELD} giocatori di movimento (ora {outfield}).")
    if total > MAX_ROSA:
        issues.append(f"La rosa puo' avere al massimo {MAX_ROSA} giocatori (ora {total}).")
    return {
        "goalkeepers": keepers,
        "outfield": outfield,
        "total": total,
        "free_slots": max(0, MAX_ROSA-total),
        "min_goalkeepers": MIN_GOALKEEPERS,
        "max_goalkeepers": MAX_GOALKEEPERS,
        "min_outfield": MIN_OUTFIELD,
        "max_roster": MAX_ROSA,
        "valid_finish": not issues,
        "issues": issues,
    }


def _release_events_desc(conn):
    return conn.execute(
        "SELECT id,released_json FROM auction_events "
        "WHERE undone=0 AND event_type='assigned' AND released_json<>'[]' ORDER BY id DESC"
    ).fetchall()


def get_release_floor(team: str, pid: int) -> int:
    """Ultimo prezzo al quale questa squadra ha svincolato il giocatore."""
    with get_conn() as conn:
        for row in _release_events_desc(conn):
            try:
                released = json.loads(row["released_json"] or "[]")
            except Exception:
                continue
            for item in released:
                if int(item.get("pid", -1)) == int(pid) and str(item.get("released_by") or item.get("team") or "") == str(team):
                    return int(item.get("price") or 0)
                # Gli eventi v5 non salvavano released_by: appartengono sempre al vincitore/event.team.
            event_team = conn.execute("SELECT team FROM auction_events WHERE id=?", (row["id"],)).fetchone()
            if event_team and event_team["team"] == team:
                for item in released:
                    if int(item.get("pid", -1)) == int(pid):
                        return int(item.get("price") or 0)
    return 0


def get_release_floors(team: str) -> dict[int, int]:
    floors = {}
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT team,released_json FROM auction_events "
            "WHERE undone=0 AND event_type='assigned' AND released_json<>'[]' ORDER BY id DESC"
        ).fetchall()
        for row in rows:
            if row["team"] != team:
                continue
            try:
                released = json.loads(row["released_json"] or "[]")
            except Exception:
                continue
            for item in released:
                pid = int(item.get("pid", -1))
                if pid >= 0 and pid not in floors:
                    floors[pid] = int(item.get("price") or 0)
    return floors


def get_free_agents(viewer_team: str | None = None):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT p.* FROM players p WHERE p.pid NOT IN (SELECT pid FROM roster) ORDER BY p.name COLLATE NOCASE"
        ).fetchall()
        passed = {r["pid"] for r in conn.execute("SELECT pid FROM passed_players").fetchall()}
    floors = get_release_floors(viewer_team) if viewer_team else {}
    players = []
    for row in rows:
        p = _player_row_to_dict(row)
        p["passed"] = p["pid"] in passed
        if viewer_team:
            p["my_release_floor"] = int(floors.get(int(p["pid"]), 0))
        players.append(p)
    stat_keys = []
    preferred = ["quotazione", "fvm", "fantamedia", "media_voto", "presenze", "gol", "assist", "ammonizioni", "espulsioni", "rigori_segnati"]
    labels = {
        "quotazione":"Quotazione", "fvm":"FVM", "fantamedia":"FantaMedia",
        "media_voto":"Media voto", "presenze":"Presenze", "gol":"Gol",
        "assist":"Assist", "ammonizioni":"Ammonizioni", "espulsioni":"Espulsioni",
        "rigori_segnati":"Rigori segnati",
    }
    present = {k for p in players for k in (p.get("stats") or {})}
    for key in preferred:
        if key in present:
            stat_keys.append({"key": key, "label": labels[key], "numeric": True})
    stat_keys.append({"key":"name", "label":"Nome", "numeric":False})
    return {"players": players, "sort_fields": stat_keys}


def assign_player(team: str, pid: int, price: int, charge_budget: bool = True):
    with get_conn() as conn:
        if conn.execute("SELECT 1 FROM roster WHERE pid=?", (pid,)).fetchone():
            raise ValueError("Giocatore gia' assegnato.")
        if not conn.execute("SELECT 1 FROM teams WHERE name=?", (team,)).fetchone():
            raise ValueError("Squadra non trovata.")
        roster_rows = conn.execute(
            "SELECT p.roles FROM roster r JOIN players p ON p.pid=r.pid WHERE r.team=?", (team,)
        ).fetchall()
        total = len(roster_rows) + 1
        if total > MAX_ROSA:
            raise ValueError("Rosa oltre il limite massimo di 35 giocatori.")
        newp = conn.execute("SELECT roles FROM players WHERE pid=?", (int(pid),)).fetchone()
        keepers = sum(1 for r in roster_rows if "P" in json.loads(r["roles"] or "[]"))
        if newp and "P" in json.loads(newp["roles"] or "[]"):
            keepers += 1
        if keepers > MAX_GOALKEEPERS:
            raise ValueError("Puoi avere al massimo 5 portieri: devi svincolarne uno.")
        conn.execute("INSERT INTO roster(team,pid,price) VALUES (?,?,?)", (team, pid, int(price)))
        conn.execute("DELETE FROM passed_players WHERE pid=?", (int(pid),))
        if charge_budget:
            conn.execute("UPDATE teams SET budget=budget-? WHERE name=?", (int(price), team))
        conn.commit()


def complete_purchase_with_releases(team: str, pid: int, price: int, released_pids: list[int], reveal: dict | None = None, rounds: list | None = None, tocca: bool = False):
    """Svincoli + acquisto in una singola transazione."""
    released_pids = list(dict.fromkeys(int(p) for p in released_pids))
    with get_conn() as conn:
        squadra = conn.execute("SELECT budget FROM teams WHERE name=?", (team,)).fetchone()
        if not squadra:
            raise ValueError("Squadra non trovata.")
        if conn.execute("SELECT 1 FROM roster WHERE pid=?", (pid,)).fetchone():
            raise ValueError("Giocatore gia' assegnato.")
        rows = conn.execute(
            "SELECT r.pid,r.price,p.roles FROM roster r JOIN players p ON p.pid=r.pid WHERE r.team=?",
            (team,),
        ).fetchall()
        rosa = {r["pid"]: {"price": r["price"], "roles": json.loads(r["roles"] or "[]")} for r in rows}
        if any(p not in rosa for p in released_pids):
            raise ValueError("Uno o piu' giocatori selezionati non appartengono alla squadra.")
        refund = sum(int(REIMBURSE_RATE * rosa[p]["price"]) for p in released_pids)
        if squadra["budget"] + refund < int(price):
            raise ValueError("Crediti insufficienti anche dopo gli svincoli selezionati.")

        final_total = len(rosa) - len(released_pids) + 1
        if final_total > MAX_ROSA:
            raise ValueError("Devi liberare altri posti in rosa.")
        newp = conn.execute("SELECT roles FROM players WHERE pid=?", (int(pid),)).fetchone()
        new_roles = json.loads(newp["roles"] or "[]") if newp else []
        keepers = sum(1 for p, info in rosa.items() if p not in released_pids and "P" in info["roles"])
        if "P" in new_roles:
            keepers += 1
        if keepers > MAX_GOALKEEPERS:
            raise ValueError("Puoi avere al massimo 5 portieri: svincola almeno un portiere.")

        released = []
        for p in released_pids:
            prow = conn.execute("SELECT name, roles, club, img FROM players WHERE pid=?", (p,)).fetchone()
            released.append({
                "pid": p,
                "price": int(rosa[p]["price"]),
                "name": prow["name"] if prow else f"ID {p}",
                "roles": json.loads(prow["roles"] or "[]") if prow else [],
                "club": prow["club"] if prow else "",
                "img": prow["img"] if prow else "",
                "released_by": team,
            })
            conn.execute("DELETE FROM roster WHERE team=? AND pid=?", (team, p))
            # Uno svincolato deve tornare immediatamente astabile anche se in
            # passato era stato segnato come PASSATO.
            conn.execute("DELETE FROM passed_players WHERE pid=?", (p,))
        conn.execute("UPDATE teams SET budget=budget+?-? WHERE name=?", (refund, int(price), team))
        conn.execute("INSERT INTO roster(team,pid,price) VALUES (?,?,?)", (team, int(pid), int(price)))
        conn.execute("DELETE FROM passed_players WHERE pid=?", (int(pid),))
        # Storico e svincoli vengono committati insieme: se il processo cade,
        # non puo' esistere uno svincolo senza la traccia necessaria alla regola
        # del prezzo minimo di riacquisto.
        conn.execute(
            """INSERT INTO auction_events(ts,event_type,pid,team,price,reveal_json,rounds_json,released_json,tocca)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                _dt.datetime.now().isoformat(timespec="seconds"), "assigned", int(pid), team, int(price),
                json.dumps(reveal or {}, ensure_ascii=False),
                json.dumps(rounds or [], ensure_ascii=False),
                json.dumps(released, ensure_ascii=False),
                1 if tocca else 0,
            ),
        )
        conn.commit()
        return released


# ---------- PASSATI ----------
def mark_passed(pid: int):
    if pid is None:
        return
    with get_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO passed_players(pid) VALUES (?)", (int(pid),))
        conn.commit()


def clear_passed(pid: int | None = None):
    with get_conn() as conn:
        if pid is None:
            conn.execute("DELETE FROM passed_players")
        else:
            conn.execute("DELETE FROM passed_players WHERE pid=?", (int(pid),))
        conn.commit()


def get_passed_players():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT p.* FROM players p JOIN passed_players pp ON pp.pid=p.pid ORDER BY p.name COLLATE NOCASE"
        ).fetchall()
        return [_player_row_to_dict(r) for r in rows]


# ---------- PROGRESSO ASTA ----------
def get_auction_progress():
    """Numero di calciatori diversi gia' chiusi e totale del bacino iniziale."""
    with get_conn() as conn:
        auctioned = conn.execute(
            """SELECT COUNT(DISTINCT pid) FROM auction_events
               WHERE undone=0 AND pid IS NOT NULL AND event_type IN ('assigned','no_offers')"""
        ).fetchone()[0]
        row = conn.execute("SELECT value FROM settings WHERE key='auction_pool_total'").fetchone()
        if row is not None:
            total = int(row["value"] or 0)
        else:
            # Fallback per database creati con una versione precedente alla v5.
            free_now = conn.execute(
                """SELECT COUNT(*) FROM players
                   WHERE pid NOT IN (SELECT pid FROM roster)
                     AND pid NOT IN (SELECT pid FROM passed_players)"""
            ).fetchone()[0]
            total = int(auctioned) + int(free_now)
            conn.execute(
                "INSERT INTO settings(key,value) VALUES('auction_pool_total',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(total),),
            )
            conn.commit()
        return {"auctioned": int(auctioned), "total": max(int(total), int(auctioned))}


# ---------- WEB PUSH ----------
def upsert_push_subscription(team: str, subscription: dict):
    endpoint = str((subscription or {}).get("endpoint") or "").strip()
    keys = (subscription or {}).get("keys") or {}
    p256dh = str(keys.get("p256dh") or "").strip()
    auth = str(keys.get("auth") or "").strip()
    if not endpoint or not p256dh or not auth:
        raise ValueError("Sottoscrizione push non valida.")
    now = _dt.datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO push_subscriptions(endpoint,team,p256dh,auth,created_at,updated_at)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(endpoint) DO UPDATE SET
                 team=excluded.team,p256dh=excluded.p256dh,auth=excluded.auth,updated_at=excluded.updated_at""",
            (endpoint, team, p256dh, auth, now, now),
        )
        conn.commit()


def delete_push_subscription(endpoint: str, team: str | None = None):
    with get_conn() as conn:
        if team:
            conn.execute("DELETE FROM push_subscriptions WHERE endpoint=? AND team=?", (endpoint, team))
        else:
            conn.execute("DELETE FROM push_subscriptions WHERE endpoint=?", (endpoint,))
        conn.commit()


def push_subscriptions_for_teams(teams: list[str] | set[str]):
    names = sorted({str(t) for t in teams if t})
    if not names:
        return []
    placeholders = ",".join("?" for _ in names)
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT endpoint,team,p256dh,auth FROM push_subscriptions WHERE team IN ({placeholders})",
            names,
        ).fetchall()
        return [
            {
                "team": r["team"],
                "subscription": {
                    "endpoint": r["endpoint"],
                    "keys": {"p256dh": r["p256dh"], "auth": r["auth"]},
                },
            }
            for r in rows
        ]


# ---------- STORICO ----------
def log_event(event_type: str, pid: int | None, team: str | None = None, price: int | None = None,
              reveal: dict | None = None, rounds: list | None = None,
              released: list | None = None, tocca: bool = False):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO auction_events(ts,event_type,pid,team,price,reveal_json,rounds_json,released_json,tocca)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                _dt.datetime.now().isoformat(timespec="seconds"), event_type, pid, team, price,
                json.dumps(reveal or {}, ensure_ascii=False),
                json.dumps(rounds or [], ensure_ascii=False),
                json.dumps(released or [], ensure_ascii=False),
                1 if tocca else 0,
            ),
        )
        conn.commit()


def get_auction_history(limit: int = 100):
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT e.*, p.name AS player_name, p.roles AS player_roles, p.club AS player_club, p.img AS player_img
            FROM auction_events e LEFT JOIN players p ON p.pid=e.pid
            WHERE e.undone=0 ORDER BY e.id DESC LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["reveal"] = json.loads(d.pop("reveal_json") or "{}")
            d["rounds"] = json.loads(d.pop("rounds_json") or "[]")
            d["released"] = json.loads(d.pop("released_json") or "[]")
            for rel in d["released"]:
                if rel.get("pid") is not None and (not rel.get("name") or not rel.get("img")):
                    prow = conn.execute("SELECT name, roles, club, img FROM players WHERE pid=?", (int(rel["pid"]),)).fetchone()
                    if prow:
                        rel.setdefault("name", prow["name"])
                        rel.setdefault("roles", json.loads(prow["roles"] or "[]"))
                        rel.setdefault("club", prow["club"] or "")
                        rel.setdefault("img", prow["img"] or "")
            d["player_roles"] = json.loads(d.get("player_roles") or "[]")
            d["tocca"] = bool(d["tocca"])
            out.append(d)
        return out


def undo_last_assignment():
    """Annulla l'ultima assegnazione e ripristina budget + eventuali svincoli."""
    with get_conn() as conn:
        event = conn.execute(
            "SELECT * FROM auction_events WHERE event_type='assigned' AND undone=0 ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not event:
            raise ValueError("Nessuna assegnazione da annullare.")
        team, pid, price = event["team"], event["pid"], int(event["price"])
        current = conn.execute("SELECT price FROM roster WHERE team=? AND pid=?", (team, pid)).fetchone()
        if not current:
            raise ValueError("Impossibile annullare: il giocatore non e' piu' nella rosa del vincitore.")
        released = json.loads(event["released_json"] or "[]")
        for r in released:
            if conn.execute("SELECT 1 FROM roster WHERE pid=?", (int(r["pid"]),)).fetchone():
                raise ValueError("Impossibile annullare: uno dei giocatori svincolati e' stato nel frattempo acquistato.")

        conn.execute("DELETE FROM roster WHERE team=? AND pid=?", (team, pid))
        refund_to_remove = sum(int(REIMBURSE_RATE * int(r["price"])) for r in released)
        conn.execute("UPDATE teams SET budget=budget+?-? WHERE name=?", (price, refund_to_remove, team))
        for r in released:
            conn.execute(
                "INSERT INTO roster(team,pid,price) VALUES (?,?,?)",
                (team, int(r["pid"]), int(r["price"])),
            )
        conn.execute("UPDATE auction_events SET undone=1 WHERE id=?", (event["id"],))
        conn.commit()
        return {"team": team, "pid": pid, "price": price, "released": released}


# ---------- STATO ASTA ----------
def save_auction_state(state: dict):
    payload = json.dumps(state, ensure_ascii=False)
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO auction_state(id,state_json,updated_at) VALUES(1,?,?)
               ON CONFLICT(id) DO UPDATE SET state_json=excluded.state_json, updated_at=excluded.updated_at""",
            (payload, _dt.datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()


def load_auction_state():
    with get_conn() as conn:
        row = conn.execute("SELECT state_json FROM auction_state WHERE id=1").fetchone()
        if not row:
            return None
        try:
            return json.loads(row["state_json"])
        except Exception:
            return None


def clear_auction_state():
    with get_conn() as conn:
        conn.execute("DELETE FROM auction_state")
        conn.commit()


# ---------- SETTINGS ----------
def set_setting(key: str, value: str):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO settings(key,value) VALUES (?,?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
            (key, str(value)),
        )
        conn.commit()


def get_setting(key: str, default=None):
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


# ---------- BACKUP DB ----------
def backup_database(dest_path: str):
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    with get_conn() as src:
        dest = sqlite3.connect(dest_path)
        try:
            src.backup(dest)
        finally:
            dest.close()


def restore_database(src_path: str):
    if not os.path.exists(src_path):
        raise FileNotFoundError(src_path)
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    # Copia tramite backup SQLite in un file temporaneo per evitare DB parziali.
    tmp = DB_PATH + ".restore_tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    src = sqlite3.connect(src_path)
    dest = sqlite3.connect(tmp)
    try:
        src.backup(dest)
    finally:
        src.close(); dest.close()
    # Rimuove eventuali sidecar WAL/SHM del database precedente prima di
    # rendere attiva la copia ripristinata.
    for sidecar in (DB_PATH + "-wal", DB_PATH + "-shm"):
        try:
            os.remove(sidecar)
        except FileNotFoundError:
            pass
    os.replace(tmp, DB_PATH)
    init_db()
