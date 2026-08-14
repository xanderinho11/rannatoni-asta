"""
Parsing dei file CSV (rose e catalogo giocatori).
Logica ripresa e adattata dal bot Telegram originale.
"""
import csv
import io

ALLOWED_MANTRA_ROLES = {"P", "Ds", "Dc", "Dd", "E", "M", "C", "W", "T", "A", "Pc", "B"}


STAT_HEADER_ALIASES = {
    "quotazione": {"qt.a", "qta", "quotazione", "quotazione attuale", "quot", "qt"},
    "fvm": {"fvm", "fanta valore di mercato", "fantavalore", "fanta valore"},
    "fantamedia": {"fm", "fantamedia", "fanta media"},
    "media_voto": {"mv", "media voto", "media voti", "media"},
    "presenze": {"pv", "presenze", "presenza"},
    "gol": {"gf", "gol", "goal", "reti"},
    "assist": {"ass", "assist"},
    "ammonizioni": {"amm", "ammonizioni"},
    "espulsioni": {"esp", "espulsioni"},
    "rigori_segnati": {"rf", "rigori segnati", "rigori fatti"},
}

STAT_LABELS = {
    "quotazione": "Quotazione",
    "fvm": "FVM",
    "fantamedia": "FantaMedia",
    "media_voto": "Media voto",
    "presenze": "Presenze",
    "gol": "Gol",
    "assist": "Assist",
    "ammonizioni": "Ammonizioni",
    "espulsioni": "Espulsioni",
    "rigori_segnati": "Rigori segnati",
}

def _norm_header(value: str):
    return " ".join(str(value or "").strip().lower().replace("_", " ").replace("-", " ").split())

def _parse_stat_number(value):
    raw = str(value or "").strip().replace("%", "")
    if not raw or raw in {"-", "--", "n.d.", "nd", "nan"}:
        return None
    # In molti CSV italiani la virgola e' il separatore decimale.
    raw = raw.replace(" ", "").replace(",", ".")
    try:
        n = float(raw)
    except ValueError:
        return None
    return int(n) if n.is_integer() else round(n, 3)

def _stat_columns(header):
    columns = {}
    normalized = [_norm_header(x) for x in header]
    for key, aliases in STAT_HEADER_ALIASES.items():
        for idx, name in enumerate(normalized):
            if name in aliases:
                columns[key] = idx
                break
    return columns


def export_residui_csv(teams: list) -> str:
    """Esporta username, residui e gestore senza rivelare PIN o hash."""
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";", lineterminator="\n")
    writer.writerow(["squadra", "username", "residuo", "gestisce_asta", "pin_impostato"])
    for t in teams:
        writer.writerow([
            t["name"], t.get("username") or "", int(t["budget"]),
            1 if t.get("is_admin") else 0, 1 if t.get("pin_configured") else 0,
        ])
    return out.getvalue()

def parse_residui_csv(text: str):
    """Rilegge un export residui.

    Supporta sia il formato v4 (senza PIN) sia il vecchio formato legacy.
    Il parser non puo' ricostruire un PIN personale: nel formato v4 restituisce
    solo username/residuo/gestore e l'indicazione che un PIN era configurato.
    """
    rows = _read_csv_rows(text)
    result = {"teams": [], "errors": []}
    if not rows:
        return result

    header = [c.strip().lower() for c in rows[0]]
    is_v4 = "residuo" in header and "pin_impostato" in header
    indexes = {name: i for i, name in enumerate(header)} if is_v4 else {}

    for i, r in enumerate(rows):
        if not r or not r[0].strip():
            continue
        if i == 0 and r[0].strip().lower() == "squadra":
            continue

        if is_v4:
            try:
                name = r[indexes["squadra"]].strip()
                username = r[indexes["username"]].strip()
                budget = int(float(r[indexes["residuo"]].strip() or 0))
                is_admin = r[indexes["gestisce_asta"]].strip().lower() in ("1", "true", "si", "sì")
                pin_configured = r[indexes["pin_impostato"]].strip().lower() in ("1", "true", "si", "sì")
            except (IndexError, ValueError):
                result["errors"].append(f"Riga {i+1}: formato residui non valido.")
                continue
            result["teams"].append({
                "name": name,
                "username": username,
                "budget": budget,
                "is_admin": is_admin,
                "pin_configured": pin_configured,
            })
            continue

        # Formato storico: squadra;username;pin;residuo;gestisce_asta
        while len(r) < 5:
            r.append("")
        name = r[0].strip()
        try:
            budget = int(float(r[3].strip() or 0))
        except ValueError:
            result["errors"].append(f"Riga {i+1}: residuo non valido per '{name}'.")
            continue
        result["teams"].append({
            "name": name,
            "username": r[1].strip(),
            "pin": r[2].strip(),
            "budget": budget,
            "is_admin": r[4].strip().lower() in ("1", "true", "si", "sì"),
        })
    return result

def export_rosters_csv(rosters_by_team: dict) -> str:
    """Rigenera il file rose nello stesso formato logico di quello caricato."""
    out = io.StringIO()
    writer = csv.writer(out, delimiter=",", lineterminator="\n")
    for team, rosa in rosters_by_team.items():
        writer.writerow(["$", "$", "$"])
        for g in rosa:
            writer.writerow([team, int(g["pid"]), int(g["price"])])
    return out.getvalue()

def _detect_delimiter(sample_line: str) -> str:
    return ";" if ";" in sample_line and sample_line.count(";") >= sample_line.count(",") else ","


def _read_csv_rows(text: str):
    first_line = text.splitlines()[0] if text.splitlines() else ""
    delim = _detect_delimiter(first_line)
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    rows = [row for row in reader if any(cell.strip() for cell in row)]
    return rows


def normalize_roles(raw_roles: str):
    if not raw_roles:
        return []
    parts = [p.strip() for p in str(raw_roles).replace(",", ";").split(";") if p.strip()]
    mapped = []
    for p in parts:
        if p.lower() in {"por", "gk", "pt"}:
            mapped.append("P")
        elif p in ALLOWED_MANTRA_ROLES:
            mapped.append(p)
        else:
            up = p.upper()
            if up == "ATT":
                mapped.append("A")
            elif up == "DC":
                mapped.append("Dc")
            elif up == "DD":
                mapped.append("Dd")
            elif up == "DS":
                mapped.append("Ds")
            elif up == "PC":
                mapped.append("Pc")
            else:
                mapped.append(p)
    seen, out = set(), []
    for r in mapped:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def parse_rosters_csv(text: str):
    """Formato: squadra;id_giocatore;prezzo (rose di partenza gia' assegnate, opzionale)"""
    rows = _read_csv_rows(text)
    result = {"teams": {}, "assignments": [], "errors": []}
    for i, r in enumerate(rows):
        if len(r) < 3:
            result["errors"].append(f"Riga {i+1}: formato non valido.")
            continue
        team = r[0].strip()
        id_str = r[1].strip()
        price_str = r[2].strip()
        if not team or team == "$" or id_str == "$":
            continue
        try:
            pid = int(id_str)
        except ValueError:
            result["errors"].append(f"Riga {i+1}: ID non numerico '{id_str}'.")
            continue
        try:
            price = int(float(price_str))
        except ValueError:
            result["errors"].append(f"Riga {i+1}: prezzo non numerico '{price_str}'.")
            continue
        result["teams"].setdefault(team, True)
        result["assignments"].append({"team": team, "pid": pid, "price": price})
    return result


def parse_catalog_csv(text: str):
    """Import catalogo Fantacalcio.

    Mantiene la compatibilita' con il layout usato dal bot (ID in colonna 0,
    nome in 2, ruolo Mantra in 4, squadra in 9) e, quando il CSV contiene
    intestazioni riconoscibili, conserva anche statistiche utili alla sezione
    Svincolati (quotazione, FantaMedia, media voto, gol, assist, ecc.).
    """
    rows = _read_csv_rows(text)
    result = {"players": [], "errors": [], "stat_labels": STAT_LABELS.copy()}
    if not rows:
        return result

    header = rows[0] if rows and rows[0] and rows[0][0].strip().upper() == "ID" else []
    stat_cols = _stat_columns(header) if header else {}
    id_seen = set()
    for i, raw_row in enumerate(rows):
        r = list(raw_row)
        while len(r) < 20:
            r.append("")
        raw_id = r[0].strip()
        if not raw_id or raw_id.upper() == "ID":
            continue
        try:
            pid = int(raw_id)
        except ValueError:
            result["errors"].append(f"Riga {i+1}: ID non numerico '{raw_id}'.")
            continue
        if pid in id_seen:
            result["errors"].append(f"Riga {i+1}: ID duplicato {pid}, ignorato.")
            continue
        id_seen.add(pid)

        nome_completo = r[2].strip() or r[1].strip()
        ruolo_raw = r[4].strip()
        club = r[9].strip() if len(r) > 9 else ""

        image_url = ""
        for cell in r:
            c = (cell or "").strip()
            if c.startswith("http://") or c.startswith("https://"):
                image_url = c
                break

        stats = {}
        for key, idx in stat_cols.items():
            if idx < len(raw_row):
                value = _parse_stat_number(raw_row[idx])
                if value is not None:
                    stats[key] = value

        roles = normalize_roles(ruolo_raw)
        result["players"].append({
            "pid": pid,
            "name": nome_completo or f"ID {pid}",
            "roles": roles,
            "club": club,
            "img": image_url,
            "stats": stats,
        })
    return result

