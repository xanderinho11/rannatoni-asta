"""Logica dell'asta live a busta chiusa.

Lo stato viene serializzato nel DB dopo ogni azione: un riavvio del server non
cancella offerte, spareggi o svincoli pendenti.
"""
from __future__ import annotations

import random
import time

import db

DURATA_ASTA = 60


class AuctionError(Exception):
    pass


class Auction:
    def __init__(self):
        self.reset(persist=False)

    def reset(self, persist: bool = True, keep_last_result: bool = False):
        last = getattr(self, "last_result", None) if keep_last_result else None
        self.mode = "idle"               # idle | bidding | tiebreak | release
        self.current_pid = None
        self.offers: dict[str, int] = {}
        self.eligible_teams: set[str] = set()
        self.tiebreak_value = None
        self.deadline = None
        self.paused = False
        self.paused_remaining = None
        self.round_history: list[dict] = []
        self.last_result = last
        if persist:
            self._persist()

    def _persist(self):
        try:
            db.save_auction_state(self.serialize())
        except Exception as exc:
            print(f"[auction] salvataggio stato non riuscito: {exc}")

    def serialize(self) -> dict:
        return {
            "mode": self.mode,
            "current_pid": self.current_pid,
            "offers": self.offers,
            "eligible_teams": sorted(self.eligible_teams),
            "tiebreak_value": self.tiebreak_value,
            "deadline": self.deadline,
            "paused": self.paused,
            "paused_remaining": self.paused_remaining,
            "round_history": self.round_history,
            "last_result": self.last_result,
        }

    def load_persisted(self):
        state = db.load_auction_state()
        if not state:
            self.reset(persist=True)
            return False
        try:
            self.mode = state.get("mode", "idle")
            self.current_pid = state.get("current_pid")
            self.offers = {str(k): int(v) for k, v in (state.get("offers") or {}).items()}
            self.eligible_teams = set(state.get("eligible_teams") or [])
            self.tiebreak_value = state.get("tiebreak_value")
            self.deadline = state.get("deadline")
            self.paused = bool(state.get("paused", False))
            self.paused_remaining = state.get("paused_remaining")
            self.round_history = state.get("round_history") or []
            self.last_result = state.get("last_result")

            # Se il server e' rimasto spento oltre la scadenza, non assegniamo
            # automaticamente al riavvio: il giro viene messo in pausa con 30 s.
            if self.mode in ("bidding", "tiebreak") and not self.paused:
                if self.deadline is None or self.deadline <= time.time():
                    self.paused = True
                    self.paused_remaining = 30
                    self.deadline = None
                    self._persist()
            return True
        except Exception:
            self.reset(persist=True)
            return False

    # ---------- apertura ----------
    def active_teams(self) -> set[str]:
        # v10: "ready" e' uno stato interno che significa "entrato nel mercato".
        # Viene attivato automaticamente al login e resta tale anche se il telefono
        # va offline/background. Si spegne solo con "Ho finito gli acquisti".
        return {t["name"] for t in db.ready_market_teams()}

    def open_player(self, pid: int):
        if self.mode != "idle":
            raise AuctionError("C'e' gia' un'asta in corso.")
        if db.get_player(pid) is None:
            raise AuctionError("Giocatore non trovato nel catalogo.")
        owner = db.player_owner(pid)
        if owner:
            raise AuctionError(f"Giocatore gia' assegnato a {owner}.")
        attive = self.active_teams()
        if len(attive) < 1:
            raise AuctionError("Nessun Rannatone e' ancora entrato nel mercato: serve almeno un partecipante per avviare l'asta.")

        self.mode = "bidding"
        self.current_pid = int(pid)
        self.offers = {}
        self.eligible_teams = attive
        self.tiebreak_value = None
        self.deadline = time.time() + DURATA_ASTA
        self.paused = False
        self.paused_remaining = None
        self.round_history = []
        self.last_result = None
        self._persist()

    def open_random(self):
        liberi = db.free_player_ids(exclude_passed=True)
        if not liberi:
            raise AuctionError("Nessun giocatore libero estraibile a caso.")
        pid = random.choice(liberi)
        self.open_player(pid)
        return pid

    # ---------- offerte ----------
    def _validate_turn(self, team: str):
        if self.mode not in ("bidding", "tiebreak"):
            raise AuctionError("Nessuna asta aperta al momento.")
        if self.paused:
            raise AuctionError("Il timer e' in pausa.")
        if self.expired():
            raise AuctionError("Tempo scaduto: la busta non puo' piu' essere modificata.")
        if team not in self.eligible_teams:
            raise AuctionError("Non sei ammesso a questo giro.")
        # Se la squadra ha gia' risposto puo' sovrascrivere la propria busta
        # finche' il giro non viene chiuso. L'ultima risposta e' quella valida.

    def bid(self, team: str, amount: int):
        self._validate_turn(team)
        amount = int(amount)
        if amount <= 0:
            raise AuctionError("L'offerta deve essere positiva.")
        squadra = db.get_team(team)
        if not squadra:
            raise AuctionError("Squadra non riconosciuta.")
        massimo = int(squadra["budget"]) + sum(int(g["price"]) for g in db.get_roster(team))
        if amount > massimo:
            raise AuctionError(
                f"Offerta troppo alta: massimo {massimo} "
                f"(residuo {squadra['budget']} + svincoli possibili)."
            )
        release_floor = db.get_release_floor(team, self.current_pid)
        if release_floor and amount < int(release_floor):
            raise AuctionError(
                f"Hai svincolato questo giocatore a {release_floor}: la tua offerta minima e' {release_floor}."
            )
        if self.mode == "tiebreak" and amount <= int(self.tiebreak_value):
            raise AuctionError(f"Nello spareggio devi superare {self.tiebreak_value}.")
        self.offers[team] = amount
        self._persist()

    def pass_(self, team: str):
        self._validate_turn(team)
        self.offers[team] = int(self.tiebreak_value) if self.mode == "tiebreak" else 0
        self._persist()

    def submitted_teams(self):
        return sorted(t for t in self.eligible_teams if t in self.offers)

    def waiting_teams(self):
        return sorted(t for t in self.eligible_teams if t not in self.offers)

    def all_submitted(self):
        return not self.waiting_teams()

    def seconds_left(self):
        if self.mode not in ("bidding", "tiebreak"):
            return None
        if self.paused:
            return max(0, int(self.paused_remaining or 0))
        if self.deadline is None:
            return 0
        return max(0, int(self.deadline - time.time()))

    def expired(self):
        return self.mode in ("bidding", "tiebreak") and not self.paused and self.seconds_left() <= 0

    # ---------- timer ----------
    def pause(self):
        if self.mode not in ("bidding", "tiebreak"):
            raise AuctionError("Nessun timer da mettere in pausa.")
        if self.paused:
            return
        self.paused_remaining = max(0, (self.deadline or time.time()) - time.time())
        self.deadline = None
        self.paused = True
        self._persist()

    def resume(self):
        if self.mode not in ("bidding", "tiebreak"):
            raise AuctionError("Nessun timer da riprendere.")
        if not self.paused:
            return
        remaining = max(1, float(self.paused_remaining or 0))
        self.deadline = time.time() + remaining
        self.paused_remaining = None
        self.paused = False
        self._persist()

    def add_time(self, seconds: int):
        if self.mode not in ("bidding", "tiebreak"):
            raise AuctionError("Nessun timer attivo.")
        seconds = int(seconds)
        if seconds <= 0 or seconds > 300:
            raise AuctionError("Tempo extra non valido.")
        if self.paused:
            self.paused_remaining = float(self.paused_remaining or 0) + seconds
        else:
            self.deadline = float(self.deadline or time.time()) + seconds
        self._persist()

    # ---------- chiusura ----------
    def close(self):
        if self.mode not in ("bidding", "tiebreak"):
            raise AuctionError("Nessuna asta aperta al momento.")

        base = int(self.tiebreak_value) if self.mode == "tiebreak" else 0
        for t in self.eligible_teams:
            self.offers.setdefault(t, base)

        reveal = dict(self.offers)
        self.round_history.append(dict(reveal))

        if self.mode == "tiebreak" and set(reveal.values()) == {base}:
            winner = random.choice(sorted(self.eligible_teams))
            return self._assign(winner, base, reveal, tocca=True)

        positive = {t: a for t, a in reveal.items() if a > 0}
        if not positive:
            pid = self.current_pid
            player = db.get_player(pid)
            db.mark_passed(pid)
            db.log_event("no_offers", pid, reveal=reveal, rounds=self.round_history)
            result = {
                "type": "no_offers", "pid": pid,
                "player_name": player["name"] if player else f"ID {pid}",
                "player_full_name": (player.get("full_name") or player.get("name")) if player else f"ID {pid}",
                "player_roles": list(player.get("roles") or []) if player else [],
                "player_club": player.get("club", "") if player else "",
                "player_img": player.get("img", "") if player else "",
                "player_stats": dict(player.get("stats") or {}) if player else {},
                "reveal": reveal, "rounds": list(self.round_history),
            }
            self.reset(persist=False)
            self.last_result = result
            self._persist()
            return result

        maximum = max(positive.values())
        winners = [t for t, a in positive.items() if a == maximum]
        if len(winners) > 1:
            self.mode = "tiebreak"
            self.eligible_teams = set(winners)
            self.tiebreak_value = maximum
            self.offers = {}
            self.deadline = time.time() + DURATA_ASTA
            self.paused = False
            self.paused_remaining = None
            result = {
                "type": "tiebreak", "teams": sorted(winners), "value": maximum,
                "pid": self.current_pid, "reveal": reveal,
            }
            self.last_result = result
            self._persist()
            return result

        winner = winners[0]
        others = sorted((a for t, a in reveal.items() if t != winner), reverse=True)
        second = others[0] if others else 0
        return self._assign(winner, second + 1, reveal)

    def _assign(self, team: str, price: int, reveal: dict, tocca: bool = False):
        pid = self.current_pid
        player = db.get_player(pid)
        squadra = db.get_team(team)
        roster = db.get_roster(team)
        release_floor = db.get_release_floor(team, pid)
        # Se il vincitore aveva precedentemente svincolato il giocatore, non puo'
        # riacquistarlo a meno del prezzo di quello svincolo.
        price = max(int(price), int(release_floor or 0))
        result = {
            "type": "assigned", "team": team, "pid": pid,
            "player_name": player["name"] if player else f"ID {pid}",
            "player_full_name": (player.get("full_name") or player.get("name")) if player else f"ID {pid}",
            "player_roles": list(player.get("roles") or []) if player else [],
            "player_club": player.get("club", "") if player else "",
            "player_img": player.get("img", "") if player else "",
            "player_stats": dict(player.get("stats") or {}) if player else {},
            "price": int(price), "reveal": reveal,
            "rounds": list(self.round_history), "tocca": bool(tocca),
            "release_floor_applied": int(release_floor or 0),
        }

        deficit = max(0, int(price) - int(squadra["budget"]))
        slots_needed = max(0, len(roster) + 1 - db.MAX_ROSA)
        keeper_count = sum(1 for g in roster if "P" in (g.get("roles") or []))
        buying_keeper = bool(player and "P" in (player.get("roles") or []))
        keeper_release_needed = max(0, keeper_count + (1 if buying_keeper else 0) - db.MAX_GOALKEEPERS)
        if deficit > 0 or slots_needed > 0 or keeper_release_needed > 0:
            result["needs_release"] = {
                "team": team, "pid": pid, "price": int(price), "deficit": deficit,
                "slots_needed": slots_needed, "keeper_release_needed": keeper_release_needed,
                "budget": int(squadra["budget"]), "roster_size": len(roster),
                "max_roster": db.MAX_ROSA, "max_goalkeepers": db.MAX_GOALKEEPERS,
            }
            self.mode = "release"
            self.deadline = None
            self.paused = False
            self.paused_remaining = None
            self.last_result = result
            self._persist()
            return result

        db.assign_player(team, pid, int(price), charge_budget=True)
        db.log_event(
            "assigned", pid, team, int(price), reveal=reveal,
            rounds=self.round_history, released=[], tocca=tocca,
        )
        self.reset(persist=False)
        self.last_result = result
        self._persist()
        return result

    def complete_with_releases(self, released_pids: list[int]):
        if self.mode != "release" or not self.last_result:
            raise AuctionError("Nessun acquisto in attesa di svincoli.")
        info = self.last_result.get("needs_release") or {}
        team, pid, price = info.get("team"), info.get("pid"), info.get("price")
        try:
            released = db.complete_purchase_with_releases(
                team, pid, price, released_pids,
                reveal=self.last_result.get("reveal", {}),
                rounds=self.last_result.get("rounds", []),
                tocca=bool(self.last_result.get("tocca")),
            )
        except ValueError as exc:
            raise AuctionError(str(exc)) from exc

        result = dict(self.last_result)
        result["released"] = released
        result.pop("needs_release", None)
        self.reset(persist=False)
        self.last_result = result
        self._persist()
        return result

    def cancel(self):
        if self.mode == "idle":
            raise AuctionError("Nessuna asta da annullare.")
        self.reset(persist=True, keep_last_result=True)

    # ---------- stato per client ----------
    def snapshot(self, viewer_team: str | None = None):
        player = db.get_player(self.current_pid) if self.current_pid else None
        active = self.mode in ("bidding", "tiebreak")
        own = None
        if viewer_team and active and viewer_team in self.eligible_teams:
            if viewer_team in self.offers:
                amount = self.offers[viewer_team]
                own = {
                    "submitted": True,
                    "amount": amount,
                    "action": (
                        "pass" if self.mode == "bidding" and amount == 0
                        else "hold" if self.mode == "tiebreak" and amount == self.tiebreak_value
                        else "bid"
                    ),
                }
            else:
                own = {"submitted": False, "amount": None, "action": None}

        own_min_bid = None
        own_release_floor = 0
        if viewer_team and active and viewer_team in self.eligible_teams:
            own_release_floor = db.get_release_floor(viewer_team, self.current_pid)
            own_min_bid = max(1, int(own_release_floor or 0))
            if self.mode == "tiebreak":
                own_min_bid = max(own_min_bid, int(self.tiebreak_value or 0) + 1)

        release_public = None
        release_private = None
        if self.mode == "release" and self.last_result:
            info = self.last_result.get("needs_release") or {}
            release_public = {
                "team": info.get("team"), "pid": info.get("pid"), "price": info.get("price")
            }
            if viewer_team == info.get("team"):
                release_private = dict(info)

        return {
            "mode": self.mode,
            "current_player": player,
            "submitted": self.submitted_teams() if active else [],
            "waiting": self.waiting_teams() if active else [],
            "eligible_teams": sorted(self.eligible_teams) if active else [],
            "all_submitted": self.all_submitted() if active else False,
            "tiebreak_value": self.tiebreak_value,
            "seconds_left": self.seconds_left(),
            "duration": DURATA_ASTA,
            "paused": self.paused,
            "own_response": own,
            "own_min_bid": own_min_bid,
            "own_release_floor": int(own_release_floor or 0),
            "release_public": release_public,
            "release_info": release_private,
            "last_result": self.last_result,
        }


auction = Auction()
