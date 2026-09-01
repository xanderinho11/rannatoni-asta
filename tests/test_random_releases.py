"""Gli svincoli correnti ignorano il quotazione Mantra, rispettando passati e assegnati."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import auction
import db


class RandomReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = patch.object(db, "DB_PATH", str(Path(self.temp.name) / "test.db"))
        self.db_path.start()
        db.init_db()
        db.replace_catalog([{"pid": pid, "name": f"Player {pid}", "roles": ["Dc"],
                             "stats": {} if quotation is None else {"quotazione_mantra": quotation}}
                            for pid, quotation in ((1, 2), (2, None), (3, 10), (4, 20), (5, 1), (6, 8))])
        db.replace_initial_rosters(["Alpha", "Beta"], [
            {"team": "Alpha", "pid": 1, "price": 10},
            {"team": "Alpha", "pid": 2, "price": 10},
            {"team": "Beta", "pid": 6, "price": 10},
        ])
        db.apply_league_settings({"random_min_quotation": 4})
        db.save_team_configuration([
            {"name": name, "username": name.lower(), "pin": "1234",
             "budget": db.get_team(name)["budget"], "is_admin": name == "Alpha"}
            for name in ("Alpha", "Beta")
        ])
        db.set_ready("Alpha", True)
        db.mark_auction_session_start()

    def tearDown(self):
        self.db_path.stop()
        self.temp.cleanup()

    def release(self):
        db.complete_purchase_with_releases("Alpha", 3, 5, [1, 2])

    def test_confirmed_releases_with_low_or_missing_quotation_can_be_drawn(self):
        self.assertEqual(db.random_player_pool()["ids"], [3, 4])
        self.release()
        pool = db.random_player_pool()
        self.assertEqual(pool["ids"], [1, 2, 4])
        self.assertEqual(pool["released_exception_count"], 2)
        self.assertEqual(pool["missing_quotation_excluded_count"], 0)
        self.assertEqual(pool["below_min_count"], 1)
        with patch.object(auction.random, "choice", side_effect=lambda ids: ids[0]) as choose:
            self.assertEqual(auction.Auction().open_random(), 1)
            choose.assert_called_once_with([1, 2, 4])

    def test_release_resets_pass_flag_but_new_pass_or_purchase_excludes_again(self):
        db.mark_passed(1)
        self.release()
        self.assertIn(1, db.random_player_pool()["ids"])
        db.mark_passed(1)
        db.complete_purchase_with_releases("Beta", 2, 1, [])
        pool = db.random_player_pool()
        self.assertEqual(pool["ids"], [4])
        self.assertEqual(pool["released_exception_count"], 0)

    def test_restart_keeps_exception_but_next_session_does_not(self):
        self.release()
        db.init_db()
        self.assertEqual(db.random_player_pool()["ids"], [1, 2, 4])
        db.mark_auction_session_start()
        pool = db.random_player_pool()
        self.assertEqual(pool["ids"], [4])
        self.assertEqual(pool["released_exception_count"], 0)
        self.assertEqual(pool["missing_quotation_excluded_count"], 1)

    def test_undo_removes_release_exception_and_restores_ownership(self):
        self.release()
        db.undo_last_assignment()
        self.assertEqual(db.random_player_pool()["ids"], [3, 4])
        self.assertEqual(db.player_owner(1), "Alpha")
        with db.get_conn() as conn:
            self.assertEqual(db._session_released_player_ids_conn(conn), set())

    def test_new_roster_upload_clears_previous_release_exceptions(self):
        self.release()
        db.replace_initial_rosters(["Alpha"], [{"team": "Alpha", "pid": 6, "price": 10}])
        pool = db.random_player_pool()
        self.assertEqual(pool["ids"], [3, 4])
        self.assertEqual(pool["released_exception_count"], 0)


if __name__ == "__main__":
    unittest.main()
