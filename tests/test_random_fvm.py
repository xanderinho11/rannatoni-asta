"""Run with: python -m unittest discover -s tests -v"""
import csv
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import auction
import csv_parser
import db


def player(pid, fvm=None):
    return {"pid": pid, "name": f"Player {pid}", "roles": ["Dc"],
            "club": "Club", "stats": {} if fvm is None else {"fvm": fvm}}


class FvmImportTests(unittest.TestCase):
    def test_standard_catalog_uses_mantra_not_classic_or_quotation(self):
        line = [22, "De Roon", "Marten De Roon", "C", "M;C", 12, 12, 13, 13,
                "Atalanta", 33, 38, "destro", "Olanda", "29/03/1991", "", 0, 5.5, 5.5]
        stream = io.StringIO()
        csv.writer(stream).writerow(line)
        result = csv_parser.parse_catalog_csv(stream.getvalue())
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["players"][0]["stats"]["fvm"], 38)

    def test_explicit_mantra_header_wins_over_generic(self):
        result = csv_parser.parse_stats_rows([
            ["Id", "Nome", "FVM", "FVM Mantra"], [1, "One", 100, 4]])
        self.assertEqual(result["records"][0]["stats"], {"fvm": 4})
        classic = csv_parser.parse_stats_rows([
            ["Id", "Nome", "FVM Classic", "MV"], [1, "One", 100, 6]])
        self.assertNotIn("fvm", classic["records"][0]["stats"])

    def test_numeric_zero_is_imported_and_nonfinite_values_are_not(self):
        result = csv_parser.parse_stats_rows([
            ["Id", "Nome", "FVM/1000"], [1, "One", 0]])
        self.assertEqual(result["records"][0]["stats"]["fvm"], 0)
        for value in ("NaN", "inf", "-inf", "not a number"):
            self.assertIsNone(csv_parser._parse_stat_number(value))


class RandomFvmTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = patch.object(db, "DB_PATH", str(Path(self.temp.name) / "test.db"))
        self.db_path.start()
        db.init_db()
        db.replace_catalog([player(1, 3.999), player(2, 4), player(3, 20),
                            player(4), player(5, 50), player(6, 60), player(7, 0)])
        with db.get_conn() as conn:
            conn.execute("INSERT INTO teams(uid,name,username,pin_hash,ready,budget) VALUES('test-team','Team','test',?,1,500)", (db.hash_pin('1234'),))
            conn.execute("INSERT INTO roster(team,pid,price) VALUES('Team',5,1)")
            conn.execute("INSERT INTO passed_players(pid) VALUES(6)")
            conn.commit()

    def tearDown(self):
        self.db_path.stop()
        self.temp.cleanup()

    def threshold(self, value):
        return db.apply_league_settings({"random_min_fvm": value})

    def test_threshold_is_inclusive_and_excludes_owned_passed_and_missing(self):
        self.threshold(4)
        pool = db.random_player_pool()
        self.assertEqual(pool["ids"], [2, 3])
        self.assertEqual(pool["missing_fvm_count"], 1)
        self.assertEqual(pool["below_min_count"], 2)
        self.assertEqual(pool["eligible_count"], 2)

    def test_disabled_filter_restores_all_remaining_free_players(self):
        self.threshold(4)
        self.threshold(0)
        self.assertEqual(db.random_player_pool()["ids"], [1, 2, 3, 4, 7])

    def test_setting_survives_reinitialization_and_rejects_negative(self):
        self.threshold(4)
        db.init_db()
        self.assertEqual(db.get_league_settings()["random_min_fvm"], 4)
        with self.assertRaises(ValueError):
            self.threshold(-1)
        self.assertEqual(db.get_league_settings()["random_min_fvm"], 4)

    def test_stats_refresh_preserves_fvm_even_for_unmatched_players(self):
        db.update_player_stats([{"pid": 2, "stats": {"media_voto": 6}}])
        self.assertEqual(db.get_player(2)["stats"], {"fvm": 4, "media_voto": 6})
        self.assertEqual(db.get_player(3)["stats"], {"fvm": 20})
        db.update_player_stats([{"pid": 2, "stats": {"fvm": 0}}])
        self.assertEqual(db.get_player(2)["stats"], {"fvm": 0})

    def test_random_draw_uses_filtered_pool_but_manual_draw_still_works(self):
        self.threshold(4)
        game = auction.Auction()
        with patch.object(auction.random, "choice", side_effect=lambda ids: ids[0]) as choose:
            self.assertEqual(game.open_random(), 2)
            choose.assert_called_once_with([2, 3])
        game.reset()
        game.open_player(1)
        self.assertEqual(game.current_pid, 1)
        self.assertIn(1, db.free_player_ids())

    def test_empty_pool_does_not_start_an_auction(self):
        self.threshold(100)
        game = auction.Auction()
        with self.assertRaisesRegex(auction.AuctionError, "FVM Mantra almeno 100"):
            game.open_random()
        self.assertEqual(game.mode, "idle")
        self.assertIsNone(game.current_pid)

    def test_invalid_stored_fvm_cannot_bypass_an_active_filter(self):
        self.threshold(4)
        for value in (float("nan"), float("inf"), "bad", -5, True):
            with self.subTest(value=value), db.get_conn() as conn:
                conn.execute("UPDATE players SET stats_json=? WHERE pid=2", (json.dumps({"fvm": value}),))
                conn.commit()
                self.assertNotIn(2, db.random_player_pool()["ids"])


if __name__ == "__main__":
    unittest.main()
