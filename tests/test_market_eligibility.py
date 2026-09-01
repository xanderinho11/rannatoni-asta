"""Quotazione Mantra, fuori campionato e integrita' di rose/svincoli."""
import csv
import io
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import auction
import csv_parser
import db


def catalog_row(pid=1, flag=0):
    # Valori distinti: Classic 31/32, Mantra 29/30, FVM 240/241.
    return [pid, f"Player {pid}", f"Full {pid}", "D", "Ds;E", 31, 32, 29, 30,
            "Inter", 240, 241, "", "", "", "https://example.test/player.png",
            flag, 6.25, 6.25, 2, 28, 21]


def as_csv(rows):
    stream = io.StringIO()
    csv.writer(stream).writerows(rows)
    return stream.getvalue()


class MarketImportTests(unittest.TestCase):
    def test_standard_catalog_reads_current_mantra_and_exit_flag(self):
        result = csv_parser.parse_catalog_csv(as_csv([catalog_row(1, 1), catalog_row(2)]))
        self.assertEqual(result["errors"], [])
        first, second = result["players"]
        self.assertEqual(first["stats"], {"quotazione_mantra": 29, "fvm": 241})
        self.assertTrue(first["out_of_league"])
        self.assertFalse(second["out_of_league"])
        self.assertEqual(first["name"], "Player 1")

    def test_headers_distinguish_mantra_current_from_initial_and_generic_quote(self):
        headers = ["ID", "Nome", "Nome completo", "R", "RM", "Qt.A", "Qt.I",
                   "Qt.A Mantra", "Qt.I Mantra", "Squadra", "FVM", "FVM Mantra",
                   "", "", "", "Foto", "Fuori campionato"]
        parsed = csv_parser.parse_catalog_csv(as_csv([headers, catalog_row(1, 1)]))
        self.assertEqual(parsed["players"][0]["stats"]["quotazione_mantra"], 29)
        self.assertTrue(parsed["players"][0]["out_of_league"])
        stats = csv_parser.parse_stats_rows([
            ["ID", "Qt.A", "Qt.A Mantra", "Qt.I Mantra"], [1, 99, 4, 50]])
        self.assertEqual(stats["records"][0]["stats"], {"quotazione_mantra": 4})
        generic = csv_parser.parse_stats_rows([["ID", "Qt.A"], [1, 99]])
        self.assertNotIn("quotazione_mantra", generic["records"][0]["stats"])

    def test_invalid_exit_flag_is_reported_and_zero_quote_is_valid(self):
        parsed = csv_parser.parse_catalog_csv(as_csv([catalog_row(1, "invalid")]))
        self.assertTrue(parsed["errors"])
        parsed = csv_parser.parse_stats_rows([["ID", "Quotazione attuale Mantra"], [1, 0]])
        self.assertEqual(parsed["records"][0]["stats"]["quotazione_mantra"], 0)


class EligibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "test.db")
        self.patcher = patch.object(db, "DB_PATH", self.path)
        self.patcher.start()
        db.init_db()
        self.players = csv_parser.parse_catalog_csv(as_csv([
            catalog_row(1, 1), catalog_row(2), catalog_row(3, 1), catalog_row(4)]))["players"]
        self.players[0]["stats"]["quotazione_mantra"] = 1
        self.players[1]["stats"]["quotazione_mantra"] = 1
        db.replace_catalog(self.players)
        db.replace_initial_rosters(["Alpha"], [
            {"team": "Alpha", "pid": 1, "price": 10},
            {"team": "Alpha", "pid": 2, "price": 10},
        ])
        db.set_ready("Alpha", True)
        db.mark_auction_session_start()

    def tearDown(self):
        self.patcher.stop()
        self.temp.cleanup()

    def test_outside_players_never_available_but_remain_in_roster(self):
        self.assertEqual([p["pid"] for p in db.get_roster("Alpha")], [1, 2])
        self.assertTrue(db.get_roster("Alpha")[0]["out_of_league"])
        for threshold in (0, 4):
            self.assertEqual(db.random_player_pool(threshold)["ids"], [4])
        self.assertEqual(db.free_player_ids(), [4])
        self.assertEqual(db.free_player_ids(False), [4])
        self.assertEqual([p["pid"] for p in db.get_free_agents()["players"]], [4])
        self.assertEqual([p["pid"] for p in db.get_free_agents("Alpha")["players"]], [4])
        self.assertEqual([p["pid"] for p in db.search_players("Player")], [4])
        with self.assertRaisesRegex(auction.AuctionError, "fuori campionato"):
            auction.Auction().open_player(3)

    def test_current_release_bypasses_quote_only_for_in_league_players(self):
        released = db.complete_purchase_with_releases("Alpha", 4, 1, [1, 2])
        self.assertTrue(released[0]["out_of_league"])
        for threshold in (0, 4):
            self.assertEqual(db.random_player_pool(threshold)["ids"], [2])
        self.assertEqual(db.random_player_pool(4)["released_exception_count"], 1)
        self.assertEqual([p["pid"] for p in db.get_free_agents()["players"]], [2])
        db.init_db()
        self.assertEqual(db.random_player_pool(4)["ids"], [2])
        history = db.get_auction_history()[0]
        self.assertTrue(history["released"][0]["out_of_league"])
        db.undo_last_assignment()
        self.assertEqual(db.player_owner(1), "Alpha")
        self.assertTrue(db.get_player(1)["out_of_league"])

    def test_stats_upload_and_catalog_refresh_preserve_roster_and_manual_balance(self):
        with db.get_conn() as conn:
            conn.execute("UPDATE teams SET budget=137 WHERE name='Alpha'")
            conn.commit()
        db.update_player_stats([{"pid": 1, "stats": {"media_voto": 6}}])
        self.assertTrue(db.get_player(1)["out_of_league"])
        db.replace_catalog(self.players)
        self.assertEqual(db.get_team("Alpha")["budget"], 137)
        self.assertEqual(db.player_owner(1), "Alpha")
        self.assertEqual(db.get_roster("Alpha")[0]["price"], 10)
        self.players[2]["out_of_league"] = False
        db.replace_catalog(self.players)
        self.assertIn(3, db.random_player_pool(4)["ids"])

    def test_history_uses_current_status_even_with_complete_old_snapshot(self):
        db.complete_purchase_with_releases("Alpha", 4, 1, [2])
        self.assertFalse(db.get_auction_history()[0]["released"][0]["out_of_league"])
        self.players[1]["out_of_league"] = True
        self.players[3]["out_of_league"] = True
        db.replace_catalog(self.players)
        history = db.get_auction_history()[0]
        self.assertTrue(history["player_out_of_league"])
        self.assertTrue(history["released"][0]["out_of_league"])
        self.assertEqual(db.random_player_pool(0)["ids"], [])

    def test_purchase_guard_rejects_outside_player_without_releasing_or_charging(self):
        before = db.get_team("Alpha")["budget"]
        with self.assertRaisesRegex(ValueError, "fuori campionato"):
            db.complete_purchase_with_releases("Alpha", 3, 1, [1, 2])
        with self.assertRaisesRegex(ValueError, "fuori campionato"):
            db.assign_player("Alpha", 3, 1)
        self.assertEqual(db.get_team("Alpha")["budget"], before)
        self.assertEqual(db.player_owner(1), "Alpha")
        self.assertEqual(db.get_auction_history(), [])

    def test_old_fvm_threshold_and_generic_quotation_do_not_drive_new_filter(self):
        db.set_setting("league_random_min_fvm", "100")
        self.assertEqual(db.get_league_settings()["random_min_quotation"], 0)
        self.players[3]["stats"] = {"fvm": 1000, "quotazione": 50}
        db.replace_catalog(self.players)
        self.assertEqual(db.random_player_pool(4)["ids"], [])
        db.apply_league_settings({"random_min_quotation": 4})
        db.init_db()
        self.assertEqual(db.get_league_settings()["random_min_quotation"], 4)


class MigrationTests(unittest.TestCase):
    def test_legacy_schema_is_upgraded_without_changing_player_values(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "legacy.db")
            with sqlite3.connect(path) as conn:
                conn.execute("CREATE TABLE players(pid INTEGER PRIMARY KEY,name TEXT,full_name TEXT,roles TEXT,club TEXT,img TEXT,stats_json TEXT)")
                conn.execute("INSERT INTO players VALUES(1,'Legacy','Legacy','[]','','','{\"fvm\":12,\"quotazione\":40}')")
            with patch.object(db, "DB_PATH", path):
                db.init_db()
                db.init_db()
                player = db.get_player(1)
                self.assertFalse(player["out_of_league"])
                self.assertEqual(player["stats"], {"fvm": 12, "quotazione": 40})
                self.assertEqual(db.random_player_pool(4)["ids"], [])


if __name__ == "__main__":
    unittest.main()
