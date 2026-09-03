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
import math
import os
import re
import secrets
import unicodedata
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
EXTRA_TIME_REQUESTS_PER_TEAM = 3

# Valori predefiniti della lega. Restano identici alla configurazione storica
# di Rannatoni, ma da v17 le regole effettive vengono lette dalla tabella
# settings invece di essere hardcoded nella logica d'asta.
DEFAULT_LEAGUE_SETTINGS = {
    "auction_mode": "repair",
    "team_count": 12,
    "initial_budget": BUDGET_INITIAL,
    "max_roster": MAX_ROSA,
    "min_goalkeepers": MIN_GOALKEEPERS,
    "max_goalkeepers": MAX_GOALKEEPERS,
    "min_outfield": MIN_OUTFIELD,
    "bid_duration": 60,
    "auto_random_delay": 10,
    "random_min_quotation": 0,
}
_LEAGUE_SETTING_KEYS = {key: f"league_{key}" for key in DEFAULT_LEAGUE_SETTINGS}


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
                uid TEXT NOT NULL UNIQUE,
                name TEXT PRIMARY KEY,
                name_pending INTEGER NOT NULL DEFAULT 0,
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
                full_name TEXT NOT NULL DEFAULT '',
                roles TEXT NOT NULL DEFAULT '[]',
                club TEXT DEFAULT '',
                img TEXT DEFAULT '',
                stats_json TEXT NOT NULL DEFAULT '{}',
                out_of_league INTEGER NOT NULL DEFAULT 0
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

            CREATE TABLE IF NOT EXISTS auction_extra_time_requests (
                pid INTEGER PRIMARY KEY,
                team TEXT NOT NULL,
                requested_at TEXT NOT NULL,
                FOREIGN KEY (pid) REFERENCES players(pid) ON DELETE CASCADE,
                FOREIGN KEY (team) REFERENCES teams(name) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_extra_time_team ON auction_extra_time_requests(team);

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

            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                team TEXT NOT NULL,
                body TEXT NOT NULL,
                system INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (team) REFERENCES teams(name) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_chat_messages_id ON chat_messages(id);
            """
        )

        # Migrazioni leggere da versioni MVP precedenti.
        team_cols = _table_columns(conn, "teams")
        if "uid" not in team_cols:
            conn.execute("ALTER TABLE teams ADD COLUMN uid TEXT NOT NULL DEFAULT ''")
        if "name_pending" not in team_cols:
            conn.execute("ALTER TABLE teams ADD COLUMN name_pending INTEGER NOT NULL DEFAULT 0")
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

        # Dalla v18 ogni squadra ha anche un identificatore interno stabile. Il
        # nome puo' essere scelto al primo accesso nell'asta da zero senza
        # cambiare l'identita' amministrativa dell'account.
        for row in conn.execute("SELECT rowid,uid FROM teams").fetchall():
            if not str(row["uid"] or "").strip():
                conn.execute("UPDATE teams SET uid=? WHERE rowid=?", (secrets.token_hex(10), row["rowid"]))
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_teams_uid ON teams(uid)")

        player_cols = _table_columns(conn, "players")
        if "stats_json" not in player_cols:
            conn.execute("ALTER TABLE players ADD COLUMN stats_json TEXT NOT NULL DEFAULT '{}'")
        if "out_of_league" not in player_cols:
            conn.execute("ALTER TABLE players ADD COLUMN out_of_league INTEGER NOT NULL DEFAULT 0")
        if "full_name" not in player_cols:
            conn.execute("ALTER TABLE players ADD COLUMN full_name TEXT NOT NULL DEFAULT ''")
            conn.execute("UPDATE players SET full_name=name WHERE full_name='' OR full_name IS NULL")

        chat_cols = _table_columns(conn, "chat_messages")
        if "system" not in chat_cols:
            conn.execute("ALTER TABLE chat_messages ADD COLUMN system INTEGER NOT NULL DEFAULT 0")

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


# ---------- CONFIGURAZIONE LEGA ----------
def _coerce_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def get_league_settings() -> dict:
    """Restituisce sempre una configurazione completa e retrocompatibile."""
    defaults = dict(DEFAULT_LEAGUE_SETTINGS)
    with get_conn() as conn:
        keys = tuple(_LEAGUE_SETTING_KEYS.values())
        placeholders = ",".join("?" for _ in keys)
        rows = conn.execute(
            f"SELECT key,value FROM settings WHERE key IN ({placeholders})", keys
        ).fetchall()
    raw = {r["key"]: r["value"] for r in rows}
    mode = str(raw.get(_LEAGUE_SETTING_KEYS["auction_mode"], defaults["auction_mode"]) or "repair").strip().lower()
    defaults["auction_mode"] = mode if mode in ("repair", "zero") else "repair"
    for key in ("team_count", "initial_budget", "max_roster", "min_goalkeepers",
                "max_goalkeepers", "min_outfield", "bid_duration", "auto_random_delay", "random_min_quotation"):
        defaults[key] = _coerce_int(raw.get(_LEAGUE_SETTING_KEYS[key]), defaults[key])
    return defaults


def validate_league_settings(values: dict) -> dict:
    current = get_league_settings()
    merged = {**current, **(values or {})}
    mode = str(merged.get("auction_mode") or "repair").strip().lower()
    if mode not in ("repair", "zero"):
        raise ValueError("Modalita' asta non valida.")

    cfg = {
        "auction_mode": mode,
        "team_count": _coerce_int(merged.get("team_count"), current["team_count"]),
        "initial_budget": _coerce_int(merged.get("initial_budget"), current["initial_budget"]),
        "max_roster": _coerce_int(merged.get("max_roster"), current["max_roster"]),
        "min_goalkeepers": _coerce_int(merged.get("min_goalkeepers"), current["min_goalkeepers"]),
        "max_goalkeepers": _coerce_int(merged.get("max_goalkeepers"), current["max_goalkeepers"]),
        "min_outfield": _coerce_int(merged.get("min_outfield"), current["min_outfield"]),
        "bid_duration": _coerce_int(merged.get("bid_duration"), current["bid_duration"]),
        "auto_random_delay": _coerce_int(merged.get("auto_random_delay"), current["auto_random_delay"]),
        "random_min_quotation": _coerce_int(merged.get("random_min_quotation"), current["random_min_quotation"]),
    }
    if not 2 <= cfg["team_count"] <= 50:
        raise ValueError("Il numero di squadre deve essere compreso tra 2 e 50.")
    if not 1 <= cfg["initial_budget"] <= 1_000_000:
        raise ValueError("I crediti iniziali devono essere compresi tra 1 e 1.000.000.")
    if not 1 <= cfg["max_roster"] <= 100:
        raise ValueError("I posti massimi in rosa devono essere compresi tra 1 e 100.")
    if not 0 <= cfg["min_goalkeepers"] <= cfg["max_goalkeepers"]:
        raise ValueError("I portieri minimi non possono superare i portieri massimi.")
    if cfg["max_goalkeepers"] > cfg["max_roster"]:
        raise ValueError("I portieri massimi non possono superare i posti massimi in rosa.")
    if not 0 <= cfg["min_outfield"] <= cfg["max_roster"]:
        raise ValueError("Il minimo di giocatori di movimento non e' valido.")
    if cfg["min_goalkeepers"] + cfg["min_outfield"] > cfg["max_roster"]:
        raise ValueError("Portieri minimi + giocatori di movimento minimi superano la capienza della rosa.")
    if not 10 <= cfg["bid_duration"] <= 300:
        raise ValueError("La durata della busta deve essere compresa tra 10 e 300 secondi.")
    if not 0 <= cfg["auto_random_delay"] <= 120:
        raise ValueError("La pausa RANDOM deve essere compresa tra 0 e 120 secondi.")
    if cfg["random_min_quotation"] < 0:
        raise ValueError("La quotazione minima Mantra per RANDOM non puo' essere negativa.")
    return cfg


def _clear_setup_conn(conn, clear_teams: bool):
    conn.execute("DELETE FROM auction_state")
    conn.execute("DELETE FROM auction_events")
    conn.execute("DELETE FROM passed_players")
    conn.execute("DELETE FROM auction_extra_time_requests")
    conn.execute("DELETE FROM chat_messages")
    conn.execute("DELETE FROM push_subscriptions")
    conn.execute("DELETE FROM roster")
    if clear_teams:
        conn.execute("DELETE FROM teams")
    conn.execute(
        "DELETE FROM settings WHERE key IN ('auction_started','auto_random','auction_session_start_event_id','auction_pool_total','simulation_active')"
    )


def apply_league_settings(values: dict) -> dict:
    """Salva le regole e prepara il setup coerente con la modalita' scelta.

    Il catalogo/statistiche restano intatti. Nell'asta da zero il numero di
    squadre e' un limite/obiettivo: gli account vengono creati uno alla volta dal
    Super Admin e non vengono piu' generati automaticamente.
    """
    cfg = validate_league_settings(values)
    old = get_league_settings()
    mode_changed = cfg["auction_mode"] != old["auction_mode"]
    rebuilt = False

    with get_conn() as conn:
        current_team_count = int(conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0] or 0)
        roster_count = int(conn.execute("SELECT COUNT(*) FROM roster").fetchone()[0] or 0)

        if cfg["auction_mode"] == "zero":
            if mode_changed or roster_count > 0:
                _clear_setup_conn(conn, clear_teams=True)
                current_team_count = 0
                rebuilt = True
            elif cfg["team_count"] < current_team_count:
                raise ValueError(
                    f"Hai gia' creato {current_team_count} squadre. Elimina prima "
                    f"{current_team_count - cfg['team_count']} squadra/e per impostare il limite a {cfg['team_count']}."
                )

            # Prima dell'avvio tutte le squadre gia' create in un'asta da zero
            # partono dallo stesso monte crediti configurato.
            conn.execute(
                "UPDATE teams SET budget=?, ready=0, market_finished=0",
                (cfg["initial_budget"],),
            )
            total_players = _initial_auction_pool_total_conn(conn)
            conn.execute(
                "INSERT INTO settings(key,value) VALUES('auction_pool_total',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(total_players),),
            )
        elif mode_changed:
            # Tornando alla riparazione si riparte dal caricamento Rose.
            _clear_setup_conn(conn, clear_teams=True)
            rebuilt = True

        for key, value in cfg.items():
            conn.execute(
                "INSERT INTO settings(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (_LEAGUE_SETTING_KEYS[key], str(value)),
            )
        conn.commit()
    return {"settings": cfg, "setup_rebuilt": rebuilt, "teams_created": current_team_count}


def auction_mode() -> str:
    return get_league_settings()["auction_mode"]


def max_roster() -> int:
    return int(get_league_settings()["max_roster"])


def max_goalkeepers() -> int:
    return int(get_league_settings()["max_goalkeepers"])


def bid_duration() -> int:
    return int(get_league_settings()["bid_duration"])


def auto_random_delay() -> int:
    return int(get_league_settings()["auto_random_delay"])


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
def _new_team_uid() -> str:
    return secrets.token_hex(10)


def ensure_team(name: str, budget: int = 0) -> bool:
    with get_conn() as conn:
        row = conn.execute("SELECT 1 FROM teams WHERE name = ?", (name,)).fetchone()
        if row:
            return False
        conn.execute(
            "INSERT INTO teams (uid,name,name_pending,username,pin,pin_hash,pin_must_change,budget,is_admin,ready,market_finished) VALUES (?,?,0,NULL,'','',1,?,0,0,0)",
            (_new_team_uid(), name, budget),
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
            SELECT uid, name, name_pending, username, budget, is_admin, ready, market_finished,
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
            SELECT t.uid, t.name, t.name_pending, t.username, t.budget, t.is_admin, t.ready, t.market_finished, COUNT(r.pid) AS roster_size
            FROM teams t LEFT JOIN roster r ON r.team = t.name
            GROUP BY t.uid, t.name, t.name_pending, t.username, t.budget, t.is_admin, t.ready, t.market_finished
            ORDER BY t.name COLLATE NOCASE
            """
        ).fetchall()
        return [dict(r) for r in rows]


def create_zero_team(username: str, temporary_pin: str, name: str = "") -> dict:
    """Crea un singolo account nell'asta da zero rispettando il limite lega."""
    rules = get_league_settings()
    if rules["auction_mode"] != "zero":
        raise ValueError("La creazione manuale delle squadre e' disponibile solo nell'asta da zero.")
    username = str(username or "").strip()
    if not username:
        raise ValueError("Inserisci uno username.")
    temporary_pin = _validate_pin_format(temporary_pin)
    requested_name = str(name or "").strip()

    with get_conn() as conn:
        count = int(conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0] or 0)
        if count >= int(rules["team_count"]):
            raise ValueError(f"Hai gia' raggiunto il limite di {rules['team_count']} squadre.")
        if conn.execute("SELECT 1 FROM teams WHERE lower(username)=lower(?)", (username,)).fetchone():
            raise ValueError("Questo username e' gia' utilizzato.")
        if requested_name and conn.execute("SELECT 1 FROM teams WHERE lower(name)=lower(?)", (requested_name,)).fetchone():
            raise ValueError("Questo nome squadra e' gia' utilizzato.")

        uid = _new_team_uid()
        # Il nome e' ancora una chiave legacy usata dalle tabelle storiche. Se
        # l'utente lo deve scegliere al primo accesso usiamo un placeholder
        # interno riconoscibile, mai presentato come nome definitivo nella UI.
        final_name = requested_name or f"Squadra da scegliere {count + 1}"
        suffix = 2
        base_name = final_name
        while conn.execute("SELECT 1 FROM teams WHERE lower(name)=lower(?)", (final_name,)).fetchone():
            final_name = f"{base_name} {suffix}"
            suffix += 1
        conn.execute(
            """INSERT INTO teams(uid,name,name_pending,username,pin,pin_hash,pin_must_change,budget,is_admin,ready,market_finished)
               VALUES (?,?,?,?,?,?,1,?,0,0,0)""",
            (
                uid, final_name, 0 if requested_name else 1, username, "",
                hash_pin(temporary_pin), int(rules["initial_budget"]),
            ),
        )
        conn.commit()
    return get_team_by_uid(uid)


def get_team_by_uid(uid: str):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM teams WHERE uid=?", (str(uid or "").strip(),)).fetchone()
        return dict(row) if row else None


def update_zero_team(uid: str, username: str, name: str | None = None) -> dict:
    rules = get_league_settings()
    if rules["auction_mode"] != "zero":
        raise ValueError("Questa modifica e' disponibile solo nell'asta da zero.")
    username = str(username or "").strip()
    if not username:
        raise ValueError("Inserisci uno username.")
    requested_name = None if name is None else str(name).strip()

    with get_conn() as conn:
        row = conn.execute("SELECT * FROM teams WHERE uid=?", (uid,)).fetchone()
        if not row:
            raise ValueError("Squadra non trovata.")
        if conn.execute("SELECT 1 FROM teams WHERE lower(username)=lower(?) AND uid<>?", (username, uid)).fetchone():
            raise ValueError("Questo username e' gia' utilizzato.")

        old_name = row["name"]
        new_name = old_name
        pending = int(row["name_pending"] or 0)
        if requested_name:
            if conn.execute("SELECT 1 FROM teams WHERE lower(name)=lower(?) AND uid<>?", (requested_name, uid)).fetchone():
                raise ValueError("Questo nome squadra e' gia' utilizzato.")
            new_name = requested_name
            pending = 0
        elif requested_name == "" and not pending:
            # Un nome gia' definitivo non viene cancellato per errore dal form.
            new_name = old_name

        if new_name != old_name:
            dependent = sum(
                int(conn.execute(f"SELECT COUNT(*) FROM {table} WHERE team=?", (old_name,)).fetchone()[0] or 0)
                for table in ("roster", "auction_events", "chat_messages", "push_subscriptions", "auction_extra_time_requests")
            )
            if dependent:
                raise ValueError("Il nome squadra non puo' essere modificato dopo l'inizio delle attivita'.")
            conn.execute("UPDATE teams SET name=?,name_pending=?,username=? WHERE uid=?", (new_name, pending, username, uid))
        else:
            conn.execute("UPDATE teams SET name_pending=?,username=? WHERE uid=?", (pending, username, uid))
        conn.commit()
    return get_team_by_uid(uid)


def delete_zero_team(uid: str):
    rules = get_league_settings()
    if rules["auction_mode"] != "zero":
        raise ValueError("Puoi eliminare manualmente le squadre solo nell'asta da zero.")
    with get_conn() as conn:
        row = conn.execute("SELECT name FROM teams WHERE uid=?", (uid,)).fetchone()
        if not row:
            raise ValueError("Squadra non trovata.")
        team = row["name"]
        dependent = sum(
            int(conn.execute(f"SELECT COUNT(*) FROM {table} WHERE team=?", (team,)).fetchone()[0] or 0)
            for table in ("roster", "auction_events", "chat_messages", "push_subscriptions", "auction_extra_time_requests")
        )
        if dependent:
            raise ValueError("Non puoi eliminare una squadra che ha gia' dati di mercato.")
        conn.execute("DELETE FROM teams WHERE uid=?", (uid,))
        conn.commit()


def set_manager_by_uid(uid: str):
    with get_conn() as conn:
        row = conn.execute("SELECT name FROM teams WHERE uid=?", (uid,)).fetchone()
        if not row:
            raise ValueError("Squadra non trovata.")
        conn.execute("UPDATE teams SET is_admin=0")
        conn.execute("UPDATE teams SET is_admin=1 WHERE uid=?", (uid,))
        conn.commit()


def complete_first_setup(team: str, new_pin: str, requested_name: str | None = None) -> dict:
    """Completa il primo accesso e, se necessario, assegna il nome squadra."""
    encoded = hash_pin(new_pin)
    rules = get_league_settings()
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM teams WHERE name=?", (team,)).fetchone()
        if not row or not row["pin_must_change"]:
            raise ValueError("Il setup iniziale non e' richiesto.")

        new_name = row["name"]
        pending = bool(row["name_pending"])
        if pending:
            if rules["auction_mode"] != "zero":
                raise ValueError("Il nome squadra non puo' essere modificato in questa modalita'.")
            new_name = str(requested_name or "").strip()
            if not new_name:
                raise ValueError("Scegli il nome della tua squadra.")
            if conn.execute("SELECT 1 FROM teams WHERE lower(name)=lower(?) AND uid<>?", (new_name, row["uid"])).fetchone():
                raise ValueError("Questo nome squadra e' gia' utilizzato.")
            dependent = sum(
                int(conn.execute(f"SELECT COUNT(*) FROM {table} WHERE team=?", (team,)).fetchone()[0] or 0)
                for table in ("roster", "auction_events", "chat_messages", "push_subscriptions", "auction_extra_time_requests")
            )
            if dependent:
                raise ValueError("Non puoi cambiare il nome squadra dopo l'inizio delle attivita'.")

        if new_name != team:
            conn.execute(
                "UPDATE teams SET name=?,name_pending=0,pin='',pin_hash=?,pin_must_change=0 WHERE uid=?",
                (new_name, encoded, row["uid"]),
            )
        else:
            conn.execute(
                "UPDATE teams SET name_pending=0,pin='',pin_hash=?,pin_must_change=0 WHERE uid=?",
                (encoded, row["uid"]),
            )
        conn.commit()
    return get_team(new_name)


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
    """Salva nomi/accessi/crediti e il Gestore.

    In modalita' asta da zero il nome delle squadre puo' essere cambiato finche'
    il setup e' ancora vuoto; in riparazione i nomi arrivano dal file Rose.
    """
    if not items:
        raise ValueError("Nessuna squadra da salvare.")
    managers = [x for x in items if x.get("is_admin")]
    if len(managers) != 1:
        raise ValueError("Devi scegliere esattamente una squadra che gestisce l'asta.")

    usernames = [str(x.get("username", "")).strip().lower() for x in items]
    if any(not u for u in usernames) or len(usernames) != len(set(usernames)):
        raise ValueError("Ogni squadra deve avere uno username univoco.")
    names = [str(x.get("name", "")).strip() for x in items]
    if any(not n for n in names) or len(names) != len(set(names)):
        raise ValueError("Ogni squadra deve avere un nome univoco.")

    rules = get_league_settings()
    zero_mode = rules["auction_mode"] == "zero"

    with get_conn() as conn:
        existing_rows = conn.execute("SELECT name, pin_hash FROM teams").fetchall()
        existing_names = {r["name"] for r in existing_rows}
        originals = [str(x.get("original_name") or x.get("name") or "").strip() for x in items]
        if set(originals) != existing_names or len(originals) != len(existing_names):
            raise ValueError("La configurazione deve includere tutte le squadre presenti.")

        renames = [(old, new) for old, new in zip(originals, names) if old != new]
        if renames:
            if not zero_mode:
                raise ValueError("Nell'asta di riparazione i nomi squadra arrivano dal file Rose e non possono essere rinominati qui.")
            dependent = sum(int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] or 0) for table in ("roster", "auction_events", "chat_messages", "push_subscriptions", "auction_extra_time_requests"))
            if dependent:
                raise ValueError("Non puoi rinominare le squadre dopo che il mercato ha iniziato a produrre dati.")
            temp_pairs = []
            for idx, (old, new) in enumerate(renames):
                tmp = f"__rename_{idx}_{secrets.token_hex(6)}"
                conn.execute("UPDATE teams SET name=? WHERE name=?", (tmp, old))
                temp_pairs.append((tmp, new))
            for tmp, new in temp_pairs:
                conn.execute("UPDATE teams SET name=? WHERE name=?", (new, tmp))

        existing_rows = conn.execute("SELECT name, pin_hash FROM teams").fetchall()
        existing = {r["name"]: r["pin_hash"] for r in existing_rows}
        for x in items:
            name = str(x["name"]).strip()
            pin = str(x.get("pin") or "").strip()
            if not pin and not existing.get(name):
                raise ValueError(f"Devi impostare un PIN temporaneo per {name}.")
            budget = int(rules["initial_budget"]) if zero_mode else int(x["budget"])
            if pin:
                encoded = hash_pin(pin)
                conn.execute(
                    """UPDATE teams SET username=?, pin='', pin_hash=?, pin_must_change=1,
                       budget=?, is_admin=?, ready=0, market_finished=0 WHERE name=?""",
                    (str(x["username"]).strip(), encoded, budget, 1 if x.get("is_admin") else 0, name),
                )
            else:
                conn.execute(
                    "UPDATE teams SET username=?, budget=?, is_admin=? WHERE name=?",
                    (str(x["username"]).strip(), budget, 1 if x.get("is_admin") else 0, name),
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
    """Rannatoni entrati nel mercato almeno una volta tramite login (v10+)."""
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
        conn.execute("DELETE FROM auction_extra_time_requests")
        conn.execute("DELETE FROM chat_messages")
        conn.execute("DELETE FROM push_subscriptions")
        conn.execute("DELETE FROM roster")
        conn.execute("DELETE FROM teams")
        # Le impostazioni sono tutte legate alla configurazione/sessione dell'asta:
        # avvio, auto-random, statistiche, simulazione e riferimenti di sessione.
        conn.execute("DELETE FROM settings")
        if not keep_catalog:
            conn.execute("DELETE FROM players")
        conn.commit()


def count_rows():
    with get_conn() as conn:
        return {
            "teams": conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0],
            "players": conn.execute("SELECT COUNT(*) FROM players").fetchone()[0],
            "out_of_league": conn.execute("SELECT COUNT(*) FROM players WHERE out_of_league=1").fetchone()[0],
            "roster": conn.execute("SELECT COUNT(*) FROM roster").fetchone()[0],
            "events": conn.execute("SELECT COUNT(*) FROM auction_events WHERE undone=0").fetchone()[0],
        }


# ---------- PLAYERS / IMPORT ----------
def _display_player_stats(raw):
    try:
        stats = json.loads(raw or "{}")
    except (ValueError, TypeError):
        return {}
    if not isinstance(stats, dict):
        return {}
    # Compatibilita' con popup e ordinamento gia' esistenti. Il filtro legge
    # solo quotazione_mantra, mai il dato generico di un altro file.
    if "quotazione_mantra" in stats:
        stats["quotazione"] = stats["quotazione_mantra"]
    return stats


def _player_row_to_dict(r):
    d = dict(r)
    d["roles"] = json.loads(d.get("roles") or "[]")
    d["stats"] = _display_player_stats(d.pop("stats_json", "{}"))
    d["out_of_league"] = bool(d.get("out_of_league"))
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
            """INSERT INTO players(pid,name,full_name,roles,club,img,stats_json,out_of_league) VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(pid) DO UPDATE SET
                 name=excluded.name, full_name=excluded.full_name, roles=excluded.roles, club=excluded.club, img=excluded.img,
                 stats_json=excluded.stats_json, out_of_league=excluded.out_of_league""",
            [
                (
                    int(p["pid"]), p["name"], p.get("full_name") or p["name"], json.dumps(p.get("roles", [])),
                    p.get("club", ""), p.get("img", ""),
                    json.dumps(p.get("stats", {}), ensure_ascii=False),
                    int(bool(p.get("out_of_league"))),
                )
                for p in players
            ],
        )
        placeholders = ",".join("?" for _ in ids)
        conn.execute(f"DELETE FROM players WHERE pid NOT IN ({placeholders})", ids)
        conn.execute(
            "INSERT INTO settings(key,value) VALUES('catalog_revision','1') "
            "ON CONFLICT(key) DO UPDATE SET value=CAST(value AS INTEGER)+1"
        )
        conn.execute(
            "INSERT INTO settings(key,value) VALUES('auction_pool_total',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(_initial_auction_pool_total_conn(conn)),),
        )
        conn.commit()



_CLUB_CODE_TO_NAMES = {
    "ATA": {"atalanta"}, "BOL": {"bologna"}, "CAG": {"cagliari"},
    "COM": {"como"}, "CRE": {"cremonese"}, "EMP": {"empoli"},
    "FIO": {"fiorentina"}, "FRO": {"frosinone"}, "GEN": {"genoa"},
    "INT": {"inter", "internazionale"}, "JUV": {"juventus"},
    "LAZ": {"lazio"}, "LEC": {"lecce"}, "MIL": {"milan"},
    "MON": {"monza"}, "NAP": {"napoli"}, "PAR": {"parma"},
    "PIS": {"pisa"}, "ROM": {"roma"}, "SAS": {"sassuolo"},
    "TOR": {"torino"}, "UDI": {"udinese"},
    "VER": {"verona", "hellas verona"}, "VEN": {"venezia"},
}


def _norm_identity_text(value: str) -> str:
    raw = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii").lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", raw).split())


def _club_keys(value: str) -> set[str]:
    """Chiavi equivalenti per club: nome esteso e codice FantaLab."""
    raw = str(value or "").strip()
    if not raw:
        return set()
    norm = _norm_identity_text(raw)
    keys = {norm}
    upper = raw.upper().strip()
    if upper in _CLUB_CODE_TO_NAMES:
        keys.update(_CLUB_CODE_TO_NAMES[upper])
        keys.add(_norm_identity_text(upper))
    for code, names in _CLUB_CODE_TO_NAMES.items():
        if norm in names:
            keys.add(_norm_identity_text(code))
            keys.update(names)
    return {x for x in keys if x}


def update_player_stats(records: list[dict], labels: dict | None = None):
    """Sostituisce le statistiche opzionali del catalogo.

    Collegamento preferito per ID; se l'ID non e' presente (es. Strategia
    FantaLab) usa Nome breve + Squadra. Rose e squadre restano intatte;
    la sola quotazione esplicitamente Mantra puo' aggiornare il filtro RANDOM.
    """
    labels = labels or {}
    with get_conn() as conn:
        rows = conn.execute("SELECT pid,name,full_name,club,stats_json FROM players").fetchall()
        catalog = [dict(r) for r in rows]
        catalog_ids = {int(r["pid"]) for r in catalog}

        by_name = {}
        for p in catalog:
            for raw_name in (p.get("name"), p.get("full_name")):
                nk = _norm_identity_text(raw_name)
                if nk:
                    by_name.setdefault(nk, set()).add(int(p["pid"]))

        player_by_id = {int(p["pid"]): p for p in catalog}
        matched_stats = {}
        unmatched_source = 0
        source_total = 0
        match_by_id = 0
        match_by_name_team = 0

        for rec in records:
            source_total += 1
            pid = None
            rec_pid = rec.get("pid")
            if rec_pid is not None:
                try:
                    candidate = int(rec_pid)
                except (TypeError, ValueError):
                    candidate = None
                if candidate in catalog_ids:
                    pid = candidate
                    match_by_id += 1

            if pid is None and rec.get("name"):
                nk = _norm_identity_text(rec.get("name"))
                candidates = set(by_name.get(nk, set()))
                rec_club_keys = _club_keys(rec.get("club", ""))
                if rec_club_keys:
                    candidates = {
                        c for c in candidates
                        if rec_club_keys & _club_keys(player_by_id[c].get("club", ""))
                    }
                # Se il file non indica il club, accettiamo solo un nome univoco.
                if len(candidates) == 1:
                    pid = next(iter(candidates))
                    match_by_name_team += 1

            if pid is None:
                unmatched_source += 1
                continue
            matched_stats.setdefault(pid, {}).update(rec.get("stats") or {})

        # Conservare i dati di mercato quando il nuovo file contiene solo
        # statistiche stagionali. Una quotazione generica non cambia la Mantra.
        for player in catalog:
            pid = int(player["pid"])
            try:
                previous = json.loads(player["stats_json"] or "{}")
            except (ValueError, TypeError):
                previous = {}
            stats = {k: previous[k] for k in ("fvm", "quotazione_mantra") if isinstance(previous, dict) and k in previous}
            stats.update(matched_stats.get(pid, {}))
            conn.execute(
                "UPDATE players SET stats_json=? WHERE pid=?",
                (json.dumps(stats, ensure_ascii=False), int(pid)),
            )
        conn.execute(
            "INSERT INTO settings(key,value) VALUES('stats_labels',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (json.dumps(labels, ensure_ascii=False),),
        )
        conn.commit()

    matched_ids = set(matched_stats)
    return {
        "matched": len(matched_ids),
        "stats_without_catalog": unmatched_source,
        "catalog_without_stats": len(catalog_ids - matched_ids),
        "catalog_total": len(catalog_ids),
        "stats_total": source_total,
        "matched_by_id": match_by_id,
        "matched_by_name_team": match_by_name_team,
    }


def get_stats_labels():
    raw = get_setting("stats_labels", "{}")
    try: return json.loads(raw or "{}")
    except Exception: return {}

def replace_initial_rosters(team_names: list[str], assignments: list[dict]):
    """Importa le rose e ricalcola i residui: crediti iniziali meno costo rosa.

    Conserva accessi e gestore delle squadre con lo stesso nome. Il ricalcolo
    avviene solo durante l'import, senza sovrascrivere successive correzioni.
    """
    teams = [t.strip() for t in team_names if t and t.strip()]
    if not teams:
        raise ValueError("Nel file rose non e' stata trovata nessuna squadra.")
    if len(teams) != len(set(teams)):
        raise ValueError("Il file rose contiene nomi squadra duplicati.")

    seen = {}
    spent = {team: 0 for team in teams}
    for a in assignments:
        pid = int(a["pid"])
        team = str(a["team"]).strip()
        if pid in seen:
            if seen[pid] == team:
                raise ValueError(f"Il giocatore ID {pid} compare piu' volte nella rosa di '{team}'.")
            raise ValueError(f"Il giocatore ID {pid} compare sia in '{seen[pid]}' sia in '{team}'.")
        seen[pid] = team
        if team not in teams:
            raise ValueError(f"Assegnazione riferita a squadra sconosciuta: {team}")
        price = int(a["price"])
        if price < 0:
            raise ValueError(f"Il prezzo del giocatore ID {pid} non puo' essere negativo.")
        spent[team] += price

    initial_budget = int(get_league_settings()["initial_budget"])
    budgets = {team: initial_budget - cost for team, cost in spent.items()}
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
        conn.execute("DELETE FROM auction_extra_time_requests")
        conn.execute("DELETE FROM chat_messages")

        placeholders = ",".join("?" for _ in teams)
        conn.execute(f"DELETE FROM teams WHERE name NOT IN ({placeholders})", teams)
        for team in teams:
            conn.execute(
                "INSERT OR IGNORE INTO teams(uid,name,name_pending,username,pin,pin_hash,pin_must_change,budget,is_admin,ready,market_finished) VALUES (?,?,0,NULL,'','',1,0,0,0,0)",
                (_new_team_uid(), team),
            )
            conn.execute(
                "UPDATE teams SET budget=?, name_pending=0, ready=0, market_finished=0 WHERE name=?",
                (budgets[team], team),
            )
        conn.executemany(
            "INSERT INTO roster(team,pid,price) VALUES (?,?,?)",
            [(a["team"].strip(), int(a["pid"]), int(a["price"])) for a in assignments],
        )
        # Escludiamo i fuori campionato e i giocatori nelle rose iniziali.
        # Acquisti e svincoli successivi non devono far oscillare questo bacino.
        conn.execute(
            "INSERT INTO settings(key,value) VALUES('auction_pool_total',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(_initial_auction_pool_total_conn(conn)),),
        )
        conn.commit()
    return {"initial_budget": initial_budget, "budgets_calculated": len(budgets),
            "negative_budget_teams": [team for team, budget in budgets.items() if budget < 0]}


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
            WHERE (lower(p.name) LIKE ? OR lower(p.full_name) LIKE ?)
              AND p.out_of_league=0
              AND (?=0 OR p.pid NOT IN (SELECT pid FROM roster))
            ORDER BY p.name COLLATE NOCASE LIMIT ?
            """,
            (q, q, 1 if exclude_assigned else 0, int(limit)),
        ).fetchall()
        return [_player_row_to_dict(r) for r in rows]


# ---------- ROSTER ----------
def _session_start_event_id_conn(conn) -> int:
    row = conn.execute("SELECT value FROM settings WHERE key='auction_session_start_event_id'").fetchone()
    try:
        return int(row["value"] or 0) if row else 0
    except Exception:
        return 0


def mark_auction_session_start():
    """Fissa il confine della sessione e azzera i 3 jolly tempo per squadra."""
    with get_conn() as conn:
        last_id = int(conn.execute("SELECT COALESCE(MAX(id),0) FROM auction_events").fetchone()[0] or 0)
        conn.execute("DELETE FROM auction_extra_time_requests")
        conn.execute(
            "INSERT INTO settings(key,value) VALUES('auction_session_start_event_id',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(last_id),),
        )
        conn.commit()
        return last_id


def extra_time_status(team: str | None = None, pid: int | None = None) -> dict:
    """Stato dei jolly tempo della sessione corrente, senza esporre chi li usa."""
    with get_conn() as conn:
        used = 0
        if team:
            used = int(conn.execute(
                "SELECT COUNT(*) FROM auction_extra_time_requests WHERE team=?", (team,)
            ).fetchone()[0] or 0)
        requested = False
        if pid is not None:
            requested = conn.execute(
                "SELECT 1 FROM auction_extra_time_requests WHERE pid=?", (int(pid),)
            ).fetchone() is not None
    return {
        "limit": EXTRA_TIME_REQUESTS_PER_TEAM,
        "behavior": "pause",
        "used": used,
        "remaining": max(0, EXTRA_TIME_REQUESTS_PER_TEAM - used) if team else None,
        "requested_for_player": requested,
    }


def request_extra_time(team: str, pid: int) -> dict:
    """Consuma un jolly: max 3 per squadra e uno solo per calciatore."""
    team = str(team or "").strip()
    pid = int(pid)
    with get_conn() as conn:
        if not conn.execute("SELECT 1 FROM teams WHERE name=?", (team,)).fetchone():
            raise ValueError("Squadra non riconosciuta.")
        if not conn.execute("SELECT 1 FROM players WHERE pid=?", (pid,)).fetchone():
            raise ValueError("Giocatore non trovato.")
        if conn.execute("SELECT 1 FROM auction_extra_time_requests WHERE pid=?", (pid,)).fetchone():
            raise ValueError("Tempo extra gia' utilizzato per questo calciatore.")
        used = int(conn.execute(
            "SELECT COUNT(*) FROM auction_extra_time_requests WHERE team=?", (team,)
        ).fetchone()[0] or 0)
        if used >= EXTRA_TIME_REQUESTS_PER_TEAM:
            raise ValueError("Hai gia' utilizzato tutti e 3 i tempi extra disponibili.")
        conn.execute(
            "INSERT INTO auction_extra_time_requests(pid,team,requested_at) VALUES (?,?,?)",
            (pid, team, _dt.datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()
    return extra_time_status(team, pid)


def session_acquired_player_ids(team: str | None = None) -> set[int]:
    with get_conn() as conn:
        start_id = _session_start_event_id_conn(conn)
        if team:
            rows = conn.execute(
                "SELECT DISTINCT pid FROM auction_events WHERE id>? AND undone=0 AND event_type='assigned' AND team=? AND pid IS NOT NULL",
                (start_id, team),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT DISTINCT pid FROM auction_events WHERE id>? AND undone=0 AND event_type='assigned' AND pid IS NOT NULL",
                (start_id,),
            ).fetchall()
        return {int(r["pid"]) for r in rows}


def _session_released_player_ids_conn(conn) -> set[int]:
    """Svincoli confermati nella sessione corrente, esclusi quelli annullati."""
    rows = conn.execute(
        """SELECT released_json FROM auction_events
           WHERE id>? AND undone=0 AND event_type='assigned' AND released_json<>'[]'""",
        (_session_start_event_id_conn(conn),),
    ).fetchall()
    released_ids = set()
    for row in rows:
        try:
            released = json.loads(row["released_json"] or "[]")
        except (ValueError, TypeError):
            continue
        if not isinstance(released, list):
            continue
        for item in released:
            if not isinstance(item, dict) or isinstance(item.get("pid"), bool):
                continue
            try:
                released_ids.add(int(item["pid"]))
            except (KeyError, ValueError, TypeError, OverflowError):
                continue
    return released_ids


def get_roster(team: str):
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT r.pid, r.price, p.name, p.full_name, p.roles, p.club, p.img, p.stats_json, p.out_of_league
            FROM roster r JOIN players p ON p.pid=r.pid
            WHERE r.team=?
            """,
            (team,),
        ).fetchall()
        start_id = _session_start_event_id_conn(conn)
        acquired_rows = conn.execute(
            "SELECT DISTINCT pid FROM auction_events WHERE id>? AND undone=0 AND event_type='assigned' AND team=? AND pid IS NOT NULL",
            (start_id, team),
        ).fetchall()
        acquired = {int(r["pid"]) for r in acquired_rows}
        out = [_player_row_to_dict(r) for r in rows]
        for item in out:
            item["acquired_this_session"] = int(item["pid"]) in acquired
        return out


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
                """SELECT pid FROM players WHERE out_of_league=0 AND pid NOT IN (SELECT pid FROM roster)
                   AND pid NOT IN (SELECT pid FROM passed_players)"""
            ).fetchall()
        else:
            rows = conn.execute("SELECT pid FROM players WHERE out_of_league=0 AND pid NOT IN (SELECT pid FROM roster)").fetchall()
        return [r["pid"] for r in rows]


def random_player_pool(min_quotation: int | None = None):
    """Candidati RANDOM nel campionato, liberi e non passati.

    Gli svincoli confermati nella sessione corrente ignorano la soglia, anche
    senza quotazione Mantra. I fuori campionato sono sempre esclusi, anche
    dagli svincolati. La soglia non si applica alle chiamate manuali.
    """
    if min_quotation is None:
        min_quotation = get_league_settings()["random_min_quotation"]
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT pid,stats_json FROM players
               WHERE out_of_league=0 AND pid NOT IN (SELECT pid FROM roster)
                 AND pid NOT IN (SELECT pid FROM passed_players)"""
        ).fetchall()
        released_ids = _session_released_player_ids_conn(conn) if min_quotation > 0 else set()
    ids = []
    missing = 0
    below = 0
    missing_excluded = 0
    release_exceptions = 0
    for row in rows:
        try:
            stats = json.loads(row["stats_json"] or "{}")
            raw = stats.get("quotazione_mantra") if isinstance(stats, dict) else None
            quotation = float(raw) if raw is not None and not isinstance(raw, bool) else None
            if quotation is not None and (not math.isfinite(quotation) or quotation < 0):
                quotation = None
        except (ValueError, TypeError, OverflowError):
            quotation = None
        if quotation is None:
            missing += 1
        if min_quotation > 0 and (quotation is None or quotation < min_quotation):
            if row["pid"] in released_ids:
                release_exceptions += 1
            else:
                if quotation is None:
                    missing_excluded += 1
                else:
                    below += 1
                continue
        ids.append(row["pid"])
    empty_message = ""
    if not ids:
        empty_message = (
            f"Nessun giocatore estraibile con quotazione attuale Mantra almeno {min_quotation}. "
            "Abbassa la soglia nelle impostazioni o ricarica il listone aggiornato."
            if min_quotation > 0 and rows else "Nessun giocatore libero estraibile a caso."
        )
    return {"ids": ids, "min_quotation": min_quotation, "eligible_count": len(ids),
            "total_count": len(rows), "missing_quotation_count": missing,
            "missing_quotation_excluded_count": missing_excluded,
            "released_exception_count": release_exceptions,
            "below_min_count": below, "empty_message": empty_message}


def _is_goalkeeper_roles(roles) -> bool:
    return "P" in (roles or [])


def roster_summary(team: str):
    roster = get_roster(team)
    keepers = sum(1 for g in roster if _is_goalkeeper_roles(g.get("roles")))
    total = len(roster)
    outfield = total - keepers
    rules = get_league_settings()
    min_gk = int(rules["min_goalkeepers"])
    max_gk = int(rules["max_goalkeepers"])
    min_out = int(rules["min_outfield"])
    max_size = int(rules["max_roster"])
    issues = []
    if keepers < min_gk:
        issues.append(f"Servono almeno {min_gk} portieri (ora {keepers}).")
    if keepers > max_gk:
        issues.append(f"Puoi avere al massimo {max_gk} portieri (ora {keepers}).")
    if outfield < min_out:
        issues.append(f"Servono almeno {min_out} giocatori di movimento (ora {outfield}).")
    if total > max_size:
        issues.append(f"La rosa puo' avere al massimo {max_size} giocatori (ora {total}).")
    return {
        "goalkeepers": keepers,
        "outfield": outfield,
        "total": total,
        "free_slots": max(0, max_size-total),
        "min_goalkeepers": min_gk,
        "max_goalkeepers": max_gk,
        "min_outfield": min_out,
        "max_roster": max_size,
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
            "SELECT p.* FROM players p WHERE p.out_of_league=0 AND p.pid NOT IN (SELECT pid FROM roster) ORDER BY p.name COLLATE NOCASE"
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
    # v13: nella schermata Svincolati manteniamo solo i quattro criteri
    # concordati: Nome, Media voto, Fantamedia e Quotazione.
    stat_keys = [{"key": "name", "label": "Nome", "numeric": False}]
    preferred = ["media_voto", "fantamedia", "quotazione"]
    labels = {
        "media_voto": "Media voto",
        "fantamedia": "Fantamedia",
        "quotazione": "Quotazione",
    }
    present = {k for p in players for k in (p.get("stats") or {})}
    for key in preferred:
        if key in present:
            stat_keys.append({"key": key, "label": labels[key], "numeric": True})
    return {"players": players, "sort_fields": stat_keys}


def _require_available_player(conn, pid: int):
    player = conn.execute("SELECT out_of_league FROM players WHERE pid=?", (int(pid),)).fetchone()
    if not player:
        raise ValueError("Giocatore non trovato nel catalogo.")
    if player["out_of_league"]:
        raise ValueError("Giocatore fuori campionato: non disponibile per l'asta.")


def assign_player(team: str, pid: int, price: int, charge_budget: bool = True):
    with get_conn() as conn:
        _require_available_player(conn, pid)
        if conn.execute("SELECT 1 FROM roster WHERE pid=?", (pid,)).fetchone():
            raise ValueError("Giocatore gia' assegnato.")
        if not conn.execute("SELECT 1 FROM teams WHERE name=?", (team,)).fetchone():
            raise ValueError("Squadra non trovata.")
        roster_rows = conn.execute(
            "SELECT p.roles FROM roster r JOIN players p ON p.pid=r.pid WHERE r.team=?", (team,)
        ).fetchall()
        rules = get_league_settings()
        max_size = int(rules["max_roster"])
        max_gk = int(rules["max_goalkeepers"])
        total = len(roster_rows) + 1
        if total > max_size:
            raise ValueError(f"Rosa oltre il limite massimo di {max_size} giocatori.")
        newp = conn.execute("SELECT roles FROM players WHERE pid=?", (int(pid),)).fetchone()
        keepers = sum(1 for r in roster_rows if "P" in json.loads(r["roles"] or "[]"))
        if newp and "P" in json.loads(newp["roles"] or "[]"):
            keepers += 1
        if keepers > max_gk:
            raise ValueError(f"Puoi avere al massimo {max_gk} portieri: devi svincolarne uno.")
        conn.execute("INSERT INTO roster(team,pid,price) VALUES (?,?,?)", (team, pid, int(price)))
        conn.execute("DELETE FROM passed_players WHERE pid=?", (int(pid),))
        if charge_budget:
            conn.execute("UPDATE teams SET budget=budget-? WHERE name=?", (int(price), team))
        conn.commit()


def complete_purchase_with_releases(team: str, pid: int, price: int, released_pids: list[int], reveal: dict | None = None, rounds: list | None = None, tocca: bool = False):
    """Svincoli + acquisto in una singola transazione."""
    released_pids = list(dict.fromkeys(int(p) for p in released_pids))
    with get_conn() as conn:
        _require_available_player(conn, pid)
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
        rules = get_league_settings()
        max_size = int(rules["max_roster"])
        max_gk = int(rules["max_goalkeepers"])
        if any(p not in rosa for p in released_pids):
            raise ValueError("Uno o piu' giocatori selezionati non appartengono alla squadra.")

        # Regola asta di riparazione: un acquisto effettuato nella sessione
        # corrente non puo' essere svincolato nella stessa sessione. Il controllo
        # e' server-side, quindi non e' aggirabile modificando il client.
        session_start_id = _session_start_event_id_conn(conn)
        bought_now = {
            int(r["pid"]) for r in conn.execute(
                "SELECT DISTINCT pid FROM auction_events WHERE id>? AND undone=0 AND event_type='assigned' AND team=? AND pid IS NOT NULL",
                (session_start_id, team),
            ).fetchall()
        }
        forbidden = sorted(set(released_pids) & bought_now)
        if forbidden:
            names = []
            for forbidden_pid in forbidden[:6]:
                prow = conn.execute("SELECT name FROM players WHERE pid=?", (forbidden_pid,)).fetchone()
                names.append(prow["name"] if prow else f"ID {forbidden_pid}")
            raise ValueError("Non puoi svincolare giocatori acquistati in questa sessione d'asta: " + ", ".join(names))

        refund = sum(int(REIMBURSE_RATE * rosa[p]["price"]) for p in released_pids)
        if squadra["budget"] + refund < int(price):
            raise ValueError("Crediti insufficienti anche dopo gli svincoli selezionati.")

        final_total = len(rosa) - len(released_pids) + 1
        if final_total > max_size:
            raise ValueError("Devi liberare altri posti in rosa.")
        newp = conn.execute("SELECT roles FROM players WHERE pid=?", (int(pid),)).fetchone()
        new_roles = json.loads(newp["roles"] or "[]") if newp else []
        keepers = sum(1 for p, info in rosa.items() if p not in released_pids and "P" in info["roles"])
        if "P" in new_roles:
            keepers += 1
        if keepers > max_gk:
            raise ValueError(f"Puoi avere al massimo {max_gk} portieri: svincola almeno un portiere.")

        released = []
        for p in released_pids:
            prow = conn.execute("SELECT name, full_name, roles, club, img, stats_json, out_of_league FROM players WHERE pid=?", (p,)).fetchone()
            released.append({
                "pid": p,
                "price": int(rosa[p]["price"]),
                "name": prow["name"] if prow else f"ID {p}",
                "full_name": (prow["full_name"] or prow["name"]) if prow else f"ID {p}",
                "roles": json.loads(prow["roles"] or "[]") if prow else [],
                "stats": _display_player_stats(prow["stats_json"]) if prow else {},
                "out_of_league": bool(prow["out_of_league"]) if prow else False,
                "club": prow["club"] if prow else "",
                "img": prow["img"] if prow else "",
                "released_by": team,
            })
            conn.execute("DELETE FROM roster WHERE team=? AND pid=?", (team, p))
            # Riabilita i passati; i fuori campionato restano bloccati dai
            # controlli di disponibilita', indipendentemente da questo flag.
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
def _initial_auction_pool_total_conn(conn):
    """Ricostruisce il bacino iniziale usando solo calciatori in campionato.

    Le versioni fino alla 18.9 salvavano solo un totale che includeva gli
    asterischi. Ripercorrere gli acquisti al contrario recupera le rose iniziali
    anche dopo svincoli, riacquisti e annullamenti, senza modificare i dati.
    Il filtro di quotazione RANDOM non si applica al contatore generale.
    """
    initial_owned = {r["pid"] for r in conn.execute("SELECT pid FROM roster")}
    events = conn.execute(
        "SELECT pid,released_json FROM auction_events "
        "WHERE undone=0 AND event_type='assigned' ORDER BY id DESC"
    ).fetchall()
    for event in events:
        initial_owned.discard(event["pid"])
        for released in json.loads(event["released_json"] or "[]"):
            initial_owned.add(int(released["pid"]))
    eligible_ids = {
        r["pid"] for r in conn.execute("SELECT pid FROM players WHERE out_of_league=0")
    }
    return len(eligible_ids - initial_owned)


def get_auction_progress():
    """Astati e bacino iniziale, escludendo i calciatori fuori campionato."""
    with get_conn() as conn:
        auctioned = conn.execute(
            """SELECT COUNT(DISTINCT e.pid) FROM auction_events e
               JOIN players p ON p.pid=e.pid
               WHERE e.undone=0 AND p.out_of_league=0
                 AND e.event_type IN ('assigned','no_offers')"""
        ).fetchone()[0]
        total = _initial_auction_pool_total_conn(conn)
        row = conn.execute("SELECT value FROM settings WHERE key='auction_pool_total'").fetchone()
        # Corregge anche il totale gia' salvato dalle versioni precedenti,
        # senza reimportare le rose o azzerare l'asta in corso.
        if row is None or row["value"] != str(total):
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


# ---------- CHAT ----------
def _chat_row_to_dict(row):
    d = dict(row)
    d["is_manager"] = bool(d.pop("is_admin", 0))
    d["system"] = bool(d.get("system", 0))
    return d


def add_chat_message(team: str, body: str):
    clean = " ".join(str(body or "").replace("\r", " ").replace("\n", " ").split()).strip()
    if not clean:
        raise ValueError("Scrivi un messaggio.")
    if len(clean) > 500:
        raise ValueError("Il messaggio puo' contenere al massimo 500 caratteri.")
    now = _dt.datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        if not conn.execute("SELECT 1 FROM teams WHERE name=?", (team,)).fetchone():
            raise ValueError("Squadra non trovata.")
        cur = conn.execute("INSERT INTO chat_messages(ts,team,body,system) VALUES (?,?,?,0)", (now, team, clean))
        mid = int(cur.lastrowid)
        row = conn.execute(
            "SELECT c.id,c.ts,c.team,c.body,c.system,t.is_admin FROM chat_messages c JOIN teams t ON t.name=c.team WHERE c.id=?",
            (mid,),
        ).fetchone()
        conn.commit()
        return _chat_row_to_dict(row)


def add_system_chat_message(body: str):
    """Salva un evento automatico nella timeline della chat."""
    clean = " ".join(str(body or "").replace("\r", " ").replace("\n", " ").split()).strip()
    if not clean:
        raise ValueError("Messaggio di sistema vuoto.")
    now = _dt.datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        manager = conn.execute("SELECT name FROM teams WHERE is_admin=1 ORDER BY name LIMIT 1").fetchone()
        if not manager:
            return None
        team = manager["name"]
        cur = conn.execute(
            "INSERT INTO chat_messages(ts,team,body,system) VALUES (?,?,?,1)",
            (now, team, clean),
        )
        mid = int(cur.lastrowid)
        row = conn.execute(
            "SELECT c.id,c.ts,c.team,c.body,c.system,t.is_admin FROM chat_messages c JOIN teams t ON t.name=c.team WHERE c.id=?",
            (mid,),
        ).fetchone()
        conn.commit()
        return _chat_row_to_dict(row)


def get_chat_messages(limit: int = 120, after_id: int = 0):
    limit = max(1, min(int(limit), 250))
    after_id = max(0, int(after_id or 0))
    with get_conn() as conn:
        if after_id:
            rows = conn.execute(
                "SELECT c.id,c.ts,c.team,c.body,c.system,t.is_admin FROM chat_messages c JOIN teams t ON t.name=c.team WHERE c.id>? ORDER BY c.id ASC LIMIT ?",
                (after_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM (SELECT c.id,c.ts,c.team,c.body,c.system,t.is_admin FROM chat_messages c JOIN teams t ON t.name=c.team ORDER BY c.id DESC LIMIT ?) ORDER BY id ASC",
                (limit,),
            ).fetchall()
        return [_chat_row_to_dict(r) for r in rows]


def delete_chat_message(message_id: int):
    with get_conn() as conn:
        row = conn.execute("SELECT id,team FROM chat_messages WHERE id=?", (int(message_id),)).fetchone()
        if not row:
            return None
        d = dict(row)
        conn.execute("DELETE FROM chat_messages WHERE id=?", (int(message_id),))
        conn.commit()
        return d


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
            SELECT e.*, p.name AS player_name, p.full_name AS player_full_name, p.roles AS player_roles,
                   p.club AS player_club, p.img AS player_img, p.stats_json AS player_stats_json,
                   p.out_of_league AS player_out_of_league,
                   t.username AS team_username
            FROM auction_events e
            LEFT JOIN players p ON p.pid=e.pid
            LEFT JOIN teams t ON t.name=e.team
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
                if rel.get("pid") is not None:
                    prow = conn.execute("SELECT name, full_name, roles, club, img, stats_json, out_of_league FROM players WHERE pid=?", (int(rel["pid"]),)).fetchone()
                    if prow:
                        rel["out_of_league"] = bool(prow["out_of_league"])
                        rel.setdefault("name", prow["name"])
                        rel.setdefault("full_name", prow["full_name"] or prow["name"])
                        rel.setdefault("roles", json.loads(prow["roles"] or "[]"))
                        rel.setdefault("stats", _display_player_stats(prow["stats_json"]))
                        rel.setdefault("club", prow["club"] or "")
                        rel.setdefault("img", prow["img"] or "")
            d["player_roles"] = json.loads(d.get("player_roles") or "[]")
            d["player_stats"] = _display_player_stats(d.pop("player_stats_json", "{}"))
            d["player_out_of_league"] = bool(d.get("player_out_of_league"))
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
