"""Backup e file di scambio.

I CSV restano comodi per l'utente, ma il vero punto di ripristino e' una copia
completa del database SQLite: include rose, residui, passati, storico e stato
live dell'asta.
"""
from __future__ import annotations

import datetime
import glob
import os

import csv_parser
import db

DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data"))
BACKUP_DIR = os.path.join(DATA_DIR, "backup")
ROSE_FILE = os.path.join(DATA_DIR, "rose_attuale.csv")
RESIDUI_FILE = os.path.join(DATA_DIR, "residui_attuale.csv")
VERSIONI_DA_TENERE = 50


def _now_tag():
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")


def genera_rose_csv():
    return csv_parser.export_rosters_csv(db.get_all_rosters())


def genera_residui_csv():
    return csv_parser.export_residui_csv(db.get_all_teams(True))


def salva(motivo: str = ""):
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        rose = genera_rose_csv()
        residui = genera_residui_csv()
        with open(ROSE_FILE, "w", encoding="utf-8", newline="") as f:
            f.write(rose)
        with open(RESIDUI_FILE, "w", encoding="utf-8", newline="") as f:
            f.write(residui)

        tag = _now_tag()
        db.backup_database(os.path.join(BACKUP_DIR, f"asta_{tag}.db"))
        with open(os.path.join(BACKUP_DIR, f"rose_{tag}.csv"), "w", encoding="utf-8", newline="") as f:
            f.write(rose)
        with open(os.path.join(BACKUP_DIR, f"residui_{tag}.csv"), "w", encoding="utf-8", newline="") as f:
            f.write(residui)
        _pulisci_vecchi()
        return True
    except Exception as exc:
        print(f"[backup] non riuscito ({motivo}): {exc}")
        return False


def _pulisci_vecchi():
    dbs = sorted(glob.glob(os.path.join(BACKUP_DIR, "asta_*.db")))
    old = dbs[:-VERSIONI_DA_TENERE]
    for path in old:
        tag = os.path.basename(path)[5:-3]
        for candidate in (
            path,
            os.path.join(BACKUP_DIR, f"rose_{tag}.csv"),
            os.path.join(BACKUP_DIR, f"residui_{tag}.csv"),
        ):
            try:
                os.remove(candidate)
            except FileNotFoundError:
                pass


def elenco_backup():
    out = []
    for path in sorted(glob.glob(os.path.join(BACKUP_DIR, "asta_*.db")), reverse=True):
        tag = os.path.basename(path)[5:-3]
        try:
            when = datetime.datetime.strptime(tag, "%Y%m%d_%H%M%S_%f")
            label = when.strftime("%d/%m/%Y alle %H:%M:%S")
        except ValueError:
            label = tag
        out.append({"tag": tag, "etichetta": label})
    return out




def pulisci_salvataggi():
    """Rimuove tutti i backup e i CSV correnti senza toccare DB o chiavi dell'app."""
    for path in glob.glob(os.path.join(BACKUP_DIR, "*")):
        try:
            if os.path.isfile(path) or os.path.islink(path):
                os.remove(path)
        except FileNotFoundError:
            pass
    for path in (ROSE_FILE, RESIDUI_FILE):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
    try:
        os.rmdir(BACKUP_DIR)
    except OSError:
        pass
    return True


def ripristina(tag: str):
    path = os.path.join(BACKUP_DIR, f"asta_{tag}.db")
    if not os.path.exists(path):
        raise FileNotFoundError("Punto di ripristino non trovato.")
    db.restore_database(path)
    # rigenera anche i due CSV correnti dal DB ripristinato
    with open(ROSE_FILE, "w", encoding="utf-8", newline="") as f:
        f.write(genera_rose_csv())
    with open(RESIDUI_FILE, "w", encoding="utf-8", newline="") as f:
        f.write(genera_residui_csv())
    return db.count_rows()
