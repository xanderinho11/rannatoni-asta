"""Contatore senza asterischi, anche aggiornando un'asta gia' in corso."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import db


class AuctionProgressTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = patch.object(db, "DB_PATH", str(Path(self.temp.name) / "test.db"))
        self.db_path.start()
        db.init_db()
        self.players = [
            {"pid": pid, "name": f"Player {pid}", "roles": ["C"],
             "out_of_league": pid in (1, 4),
             "stats": {"quotazione_mantra": 1 if pid == 5 else 6}}
            for pid in range(1, 8)
        ]
        db.replace_catalog(self.players)
        db.replace_initial_rosters(["Alpha"], [
            {"team": "Alpha", "pid": pid, "price": 10} for pid in (1, 2, 7)
        ])
        db.mark_auction_session_start()

    def tearDown(self):
        self.db_path.stop()
        self.temp.cleanup()

    def assert_progress(self, auctioned, total):
        self.assertEqual(db.get_auction_progress(), {"auctioned": auctioned, "total": total})

    def test_initial_repair_total_excludes_only_unowned_outside_players(self):
        self.assert_progress(0, 3)  # 3, 5, 6; il 4 libero ha l'asterisco.
        self.assertEqual(db.get_setting("auction_pool_total"), "3")
        db.apply_league_settings({"random_min_quotation": 4})
        self.assertEqual(db.random_player_pool()["ids"], [3, 6])
        self.assert_progress(0, 3)  # Il contatore generale non usa la soglia RANDOM.
        self.assertTrue(db.get_player(1)["out_of_league"])
        self.assertEqual(db.player_owner(1), "Alpha")

    def test_zero_mode_and_catalog_upload_exclude_outside_players(self):
        db.apply_league_settings({"auction_mode": "zero"})
        self.assertEqual(db.get_setting("auction_pool_total"), "5")
        self.assert_progress(0, 5)
        self.players.extend([
            {"pid": 8, "name": "Outside", "roles": ["C"], "out_of_league": True},
            {"pid": 9, "name": "Available", "roles": ["C"]},
        ])
        db.replace_catalog(self.players)
        self.assertEqual(db.get_setting("auction_pool_total"), "6")
        self.assert_progress(0, 6)
        db.apply_league_settings({"auction_mode": "zero", "initial_budget": 600})
        self.assert_progress(0, 6)

    def test_old_saved_or_missing_total_is_fixed_without_resetting_live_data(self):
        db.complete_purchase_with_releases("Alpha", 3, 12, [1, 2])
        db.mark_passed(5)
        db.log_event("no_offers", 5)
        db.save_auction_state({"mode": "bidding", "pid": 6, "paused": True})
        before = (db.get_all_rosters(), db.get_team("Alpha"),
                  db.get_auction_history(), db.load_auction_state())
        for saved_total in ("4", None):
            with self.subTest(saved_total=saved_total):
                with db.get_conn() as conn:
                    if saved_total is None:
                        conn.execute("DELETE FROM settings WHERE key='auction_pool_total'")
                    else:
                        conn.execute("UPDATE settings SET value=? WHERE key='auction_pool_total'", (saved_total,))
                    conn.commit()
                db.init_db()
                self.assert_progress(2, 3)
                self.assertEqual(db.get_setting("auction_pool_total"), "3")
                self.assert_progress(2, 3)
                after = (db.get_all_rosters(), db.get_team("Alpha"),
                         db.get_auction_history(), db.load_auction_state())
                self.assertEqual(before, after)

    def test_releases_rebuys_and_undo_keep_the_initial_total(self):
        db.complete_purchase_with_releases("Alpha", 3, 1, [1, 2])
        self.assert_progress(1, 3)
        self.assertNotIn(1, db.free_player_ids())
        db.complete_purchase_with_releases("Alpha", 2, 10, [7])
        self.assert_progress(2, 3)
        db.mark_auction_session_start()
        db.complete_purchase_with_releases("Alpha", 6, 1, [3])
        self.assert_progress(3, 3)
        for remaining in (2, 1, 0):
            db.undo_last_assignment()
            self.assert_progress(remaining, 3)

    def test_passed_and_repeated_auctions_count_each_player_once(self):
        db.mark_passed(5)
        db.log_event("no_offers", 5)
        self.assert_progress(1, 3)
        db.clear_passed(5)
        db.log_event("no_offers", 5)
        db.complete_purchase_with_releases("Alpha", 5, 1, [])
        self.assert_progress(1, 3)

    def test_updated_exit_flags_adjust_the_counter_without_deleting_history(self):
        db.complete_purchase_with_releases("Alpha", 3, 1, [])
        self.assert_progress(1, 3)
        self.players[2]["out_of_league"] = True
        db.replace_catalog(self.players)
        self.assert_progress(0, 2)
        self.assertEqual(db.get_auction_history()[0]["pid"], 3)
        self.assertEqual(db.player_owner(3), "Alpha")
        self.players[3]["out_of_league"] = False  # Il 4 torna in campionato.
        self.players[0]["out_of_league"] = False  # L'1 era nelle rose iniziali.
        db.replace_catalog(self.players)
        self.assert_progress(0, 3)

    def test_new_rosters_and_reset_rebuild_the_total(self):
        db.complete_purchase_with_releases("Alpha", 3, 1, [1])
        db.replace_initial_rosters(["Beta"], [
            {"team": "Beta", "pid": pid, "price": 1} for pid in (4, 5)
        ])
        self.assert_progress(0, 4)
        db.reset_all(keep_catalog=True)
        self.assert_progress(0, 5)

    def test_all_outside_or_empty_catalog_has_zero_total(self):
        for player in self.players:
            player["out_of_league"] = True
        db.replace_catalog(self.players)
        self.assert_progress(0, 0)
        db.reset_all()
        self.assert_progress(0, 0)


if __name__ == "__main__":
    unittest.main()
