"""
Parsing dei file CSV (rose e catalogo giocatori).
Logica ripresa e adattata dal bot Telegram originale.
"""
import csv
import io
import math

ALLOWED_MANTRA_ROLES = {"P", "Ds", "Dc", "Dd", "E", "M", "C", "W", "T", "A", "Pc", "B"}


STAT_HEADER_ALIASES = {
    "quotazione": {"qt.a", "qta", "quotazione", "quotazione attuale", "quot", "quot.", "qt", "quo"},
    "fvm": {"fvm", "fvm/1000", "fvm / 1000", "fanta valore di mercato", "fantavalore", "fanta valore"},
    "fantamedia": {"fm", "fmv", "fantamedia", "fanta media", "fanta media voto"},
    "media_voto": {"mv", "media voto", "media voti", "media"},
    "presenze": {"pv", "pgv", "pgv.", "presenze", "presenza"},
    "gol": {"gf", "gol", "goal", "reti"},
    "assist": {"ass", "assist"},
    "ammonizioni": {"amm", "ammonizioni"},
    "espulsioni": {"esp", "espulsioni"},
    "rigori_segnati": {"rf", "rigori segnati", "rigori fatti", "rig segnati", "rig. segnati", "rig segn."},
    "pma": {"pma", "prezzo medio asta", "prezzo medio", "pma 12", "pma fantalab"},
    "titolarita": {"titolarita", "titolarità", "% titolare", "percentuale titolare", "titolare"},
    "gol_subiti": {"gs", "gol subiti", "reti subite"},
    "rigori_parati": {"rp", "rigori parati"},
}

# Solo la quotazione identificata come Mantra alimenta il filtro RANDOM.
SUPPORTED_STAT_KEYS = ("media_voto", "fantamedia", "quotazione", "quotazione_mantra", "presenze", "fvm")
FVM_MANTRA_HEADERS = {"fvmm", "fvm m", "fvm mantra", "fvm ma", "fvm mantra/1000", "fvm mantra / 1000"}
QUOTATION_MANTRA_HEADERS = {
    "qt.a m", "qt.a mantra", "qt.a m.", "qta m", "qta mantra", "qt.am",
    "quotazione mantra", "quotazione attuale mantra", "quotazione mantra attuale",
}
OUT_OF_LEAGUE_HEADERS = {"fuori lista", "fuori campionato", "out of league"}

STAT_LABELS = {
    "quotazione": "Quotazione",
    "quotazione_mantra": "Quotazione attuale Mantra",
    "fvm": "FVM Mantra",
    "fantamedia": "FantaMedia",
    "media_voto": "Media voto",
    "presenze": "Presenze",
    "gol": "Gol",
    "assist": "Assist",
    "ammonizioni": "Ammonizioni",
    "espulsioni": "Espulsioni",
    "rigori_segnati": "Rigori segnati",
    "pma": "PMA",
    "titolarita": "Titolarità",
    "gol_subiti": "Gol subiti",
    "rigori_parati": "Rigori parati",
}

def _norm_header(value: str):
    return " ".join(str(value or "").strip().lower().replace("_", " ").replace("-", " ").split())

def _parse_stat_number(value):
    raw = str(value if value is not None else "").strip().replace("%", "")
    if not raw or raw in {"-", "--", "n.d.", "nd", "nan"}:
        return None
    # In molti CSV italiani la virgola e' il separatore decimale.
    raw = raw.replace(" ", "").replace(",", ".")
    try:
        n = float(raw)
    except ValueError:
        return None
    if not math.isfinite(n):
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
    # Se il file contiene entrambi i sistemi di gioco, Mantra ha precedenza
    # sul campo generico FVM. Una colonna esplicitamente Classic non e' usata.
    for idx, name in enumerate(normalized):
        if name in FVM_MANTRA_HEADERS:
            columns["fvm"] = idx
            break
    for idx, name in enumerate(normalized):
        if name in QUOTATION_MANTRA_HEADERS:
            columns.pop("quotazione", None)
            columns["quotazione_mantra"] = idx
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
            number = float(price_str)
            if not math.isfinite(number) or number < 0 or not number.is_integer():
                raise ValueError
            price = int(number)
        except (ValueError, OverflowError):
            result["errors"].append(f"Riga {i+1}: prezzo non valido '{price_str}' (serve un intero non negativo).")
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
    flag_col = next((i for i, name in enumerate(header)
                     if _norm_header(name) in OUT_OF_LEAGUE_HEADERS), None) if header else 16
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

        nome_breve = r[1].strip() or r[2].strip()
        nome_completo = r[2].strip() or nome_breve
        ruolo_raw = r[4].strip()
        club = r[9].strip() if len(r) > 9 else ""

        image_url = ""
        for cell in r:
            c = (cell or "").strip()
            if c.startswith("http://") or c.startswith("https://"):
                image_url = c
                break

        stats = {}
        # FantaAsta: 5/6 = Classic attuale/iniziale, 7/8 = Mantra
        # attuale/iniziale. Non ricavare la quotazione dal FVM o dalla Classic.
        if not header and len(raw_row) >= 9:
            quotation = _parse_stat_number(raw_row[7])
            if quotation is not None and quotation >= 0:
                stats["quotazione_mantra"] = quotation
        # Catalogo standard FantaAsta senza intestazione: dopo il club
        # (indice 9) arrivano FVM Classic (10) e FVM Mantra (11), base 1000.
        if not header and len(raw_row) >= 12:
            fvm = _parse_stat_number(raw_row[11])
            if fvm is not None and fvm >= 0:
                stats["fvm"] = fvm
        for key, idx in stat_cols.items():
            if idx < len(raw_row):
                value = _parse_stat_number(raw_row[idx])
                if value is not None and (key not in {"quotazione_mantra", "fvm"} or value >= 0):
                    stats[key] = value

        out_of_league = False
        if flag_col is not None and flag_col < len(raw_row):
            flag = str(raw_row[flag_col]).strip()
            if flag not in {"", "0", "1"}:
                result["errors"].append(f"Riga {i+1}: indicatore fuori campionato non valido '{flag}'.")
                continue
            out_of_league = flag == "1"

        roles = normalize_roles(ruolo_raw)
        result["players"].append({
            "pid": pid,
            "name": nome_breve or nome_completo or f"ID {pid}",
            "full_name": nome_completo or nome_breve or f"ID {pid}",
            "roles": roles,
            "club": club,
            "img": image_url,
            "stats": stats,
            "out_of_league": out_of_league,
        })
    return result



IDENTITY_HEADERS = {
    "id", "id giocatore", "id calciatore", "codice", "cod",
    "nome", "nome completo", "calciatore", "giocatore",
    "squadra", "team", "club", "ruolo", "ruoli",
    "prezzo", "fascia", "obiett", "obiett.", "commento",
}

_NAME_ALIASES = {"nome", "nome completo", "calciatore", "giocatore"}
_CLUB_ALIASES = {"squadra", "team", "club", "sq", "sq."}
_ID_ALIASES = {"#", "id", "id giocatore", "id calciatore", "codice", "cod"}


def _safe_stat_key(label: str):
    import re, unicodedata
    raw = unicodedata.normalize("NFKD", str(label or "")).encode("ascii", "ignore").decode("ascii").lower()
    raw = re.sub(r"[^a-z0-9]+", "_", raw).strip("_")
    return raw[:40] or "dato"


def _find_header(rows: list[list[str]]):
    """Trova una riga intestazione valida nelle prime 15 righe.

    Il file statistiche puo' usare un ID oppure, come Strategia/FantaLab,
    Nome + Team. Il file statistiche e' sempre un arricchimento opzionale:
    non influenza Catalogo, Rose o squadre.
    """
    for ri, row in enumerate(rows[:15]):
        norm = [_norm_header(c) for c in row]
        id_idx = next((norm.index(x) for x in _ID_ALIASES if x in norm), None)
        name_idx = next((norm.index(x) for x in _NAME_ALIASES if x in norm), None)
        club_idx = next((norm.index(x) for x in _CLUB_ALIASES if x in norm), None)
        if id_idx is not None or name_idx is not None:
            return ri, id_idx, name_idx, club_idx
    return None, None, None, None


def parse_stats_rows(rows: list[list[str]]):
    """Importa statistiche opzionali.

    Supporta due modalita' di collegamento:
    - ID giocatore, se il file lo contiene;
    - Nome breve + Team, come nei file Strategia/FantaLab.

    Riconosce automaticamente solo le metriche supportate e le considera
    disponibili soltanto se nel file hanno almeno un valore significativo.
    Una colonna presente ma interamente a zero (tipico dei listoni Euroleghe
    prima dell'inizio del campionato) non viene quindi mostrata come statistica.
    """
    result = {"records": [], "errors": [], "labels": {}}
    if not rows:
        result["errors"].append("File statistiche vuoto.")
        return result

    header_idx, id_idx, name_idx, club_idx = _find_header(rows)
    if header_idx is None:
        result["errors"].append("Non trovo una riga intestazione con ID oppure Nome nel file statistiche.")
        return result

    header = list(rows[header_idx])
    known = {k: v for k, v in _stat_columns(header).items() if k in SUPPORTED_STAT_KEYS}
    col_to_key = {}
    for key, idx in known.items():
        values = []
        for row in rows[header_idx + 1:]:
            if idx < len(row):
                value = _parse_stat_number(row[idx])
                if value is not None:
                    values.append(value)
        # Le metriche stagionali tutte a zero non generano popup fasulli.
        # Quotazione Mantra e FVM = 0 sono dati validi.
        if not values or (key not in {"fvm", "quotazione_mantra"} and not any(abs(float(v)) > 1e-12 for v in values)):
            continue
        col_to_key[idx] = key
        result["labels"][key] = STAT_LABELS.get(key, str(header[idx]).strip() or key)

    # I multiruolo possono comparire su piu' fogli FantaLab. Qui deduplichiamo
    # l'identita' mantenendo/accorpando tutte le metriche disponibili.
    records_by_identity = {}
    for row in rows[header_idx + 1:]:
        pid = None
        if id_idx is not None and id_idx < len(row) and str(row[id_idx]).strip():
            try:
                pid = int(float(str(row[id_idx]).strip().replace(',', '.')))
            except ValueError:
                pid = None

        name = str(row[name_idx]).strip() if name_idx is not None and name_idx < len(row) else ""
        club = str(row[club_idx]).strip() if club_idx is not None and club_idx < len(row) else ""
        if pid is None and not name:
            continue

        stats = {}
        for idx, key in col_to_key.items():
            if idx < len(row):
                v = _parse_stat_number(row[idx])
                if v is not None:
                    stats[key] = v
        if not stats:
            # La riga puo' essere valida anche senza metriche, ma non porta
            # alcun arricchimento e quindi non serve salvarla.
            continue

        identity = ("pid", pid) if pid is not None else ("name", _norm_header(name), _norm_header(club))
        rec = records_by_identity.setdefault(identity, {"stats": {}})
        if pid is not None:
            rec["pid"] = pid
        if name:
            rec["name"] = name
        if club:
            rec["club"] = club
        rec["stats"].update(stats)

    result["records"] = list(records_by_identity.values())
    if not result["records"]:
        result["errors"].append("Nessuna riga statistica valida trovata.")
    return result


def merge_stats_results(parts: list[dict]):
    """Unisce i risultati di piu' fogli XLSX (es. Por, Dc, B, ...)."""
    out = {"records": [], "errors": [], "labels": {}}
    merged = {}
    for part in parts:
        out["labels"].update(part.get("labels") or {})
        for rec in part.get("records") or []:
            if rec.get("pid") is not None:
                identity = ("pid", int(rec["pid"]))
            else:
                identity = ("name", _norm_header(rec.get("name", "")), _norm_header(rec.get("club", "")))
            target = merged.setdefault(identity, {"stats": {}})
            for k in ("pid", "name", "club"):
                if rec.get(k) not in (None, ""):
                    target[k] = rec[k]
            target["stats"].update(rec.get("stats") or {})
    out["records"] = list(merged.values())
    if not out["records"]:
        errs = [e for p in parts for e in (p.get("errors") or [])]
        out["errors"] = errs[:30] or ["Nessuna riga statistica valida trovata."]
    return out


def parse_stats_csv(text: str):
    return parse_stats_rows(_read_csv_rows(text))
