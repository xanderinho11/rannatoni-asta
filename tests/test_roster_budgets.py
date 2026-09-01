"""Residui calcolati all'import, correggibili senza ricalcoli impliciti."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import csv_parser
import db


class RosterBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = patch.object(db, "DB_PATH", str(Path(self.temp.name) / "test.db"))
        self.db_path.start()
        db.init_db()
        db.replace_catalog([{"pid": pid, "name": f"Player {pid}", "roles": ["Dc"]}
                            for pid in range(1, 7)])

    def tearDown(self):
        self.db_path.stop()
        self.temp.cleanup()

    def import_rosters(self, text):
        parsed = csv_parser.parse_rosters_csv(text)
        self.assertEqual(parsed["errors"], [])
        return db.replace_initial_rosters(list(parsed["teams"]), parsed["assignments"])

    def test_import_calculates_positive_zero_and_negative_residuals(self):
        result = self.import_rosters("Alpha;1;200\nAlpha;2;278\nBeta;3;500\nGamma;4;525")
        self.assertEqual(db.get_team("Alpha")["budget"], 22)
        self.assertEqual(db.get_team("Beta")["budget"], 0)
        self.assertEqual(db.get_team("Gamma")["budget"], -25)
        self.assertEqual(result, {"initial_budget": 500, "budgets_calculated": 3,
                                  "negative_budget_teams": ["Gamma"]})
        db.init_db()
        self.assertEqual(db.get_team("Alpha")["budget"], 22)

    def test_saved_initial_budget_and_zero_prices_are_used(self):
        db.apply_league_settings({"initial_budget": 700})
        result = self.import_rosters("Alpha,1,400\nAlpha,2,278.0\nBeta,3,0")
        self.assertEqual(db.get_team("Alpha")["budget"], 22)
        self.assertEqual(db.get_team("Beta")["budget"], 700)
        self.assertEqual(result["initial_budget"], 700)

    def test_manual_correction_persists_until_next_import_and_keeps_access(self):
        text = "Alpha;1;200\nAlpha;2;278"
        self.import_rosters(text)
        db.save_team_configuration([{"name": "Alpha", "username": "alpha",
                                     "pin": "1234", "budget": 77, "is_admin": True}])
        before = db.get_team("Alpha")
        db.init_db()
        db.apply_league_settings({"random_min_fvm": 4, "initial_budget": 600})
        self.assertEqual(db.get_team("Alpha")["budget"], 77)
        self.import_rosters(text)
        after = db.get_team("Alpha")
        self.assertEqual(after["budget"], 122)
        for key in ("uid", "username", "pin_hash", "is_admin"):
            self.assertEqual(after[key], before[key])

    def test_invalid_import_keeps_previous_rosters_and_budgets(self):
        self.import_rosters("Alpha;1;478")
        db.update_team_budget("Alpha", 77)
        previous = db.get_all_rosters()
        for bad_file in ("Alpha;1;478\nAlpha;1;478", "Alpha;1;478\nBeta;1;10",
                         "Alpha;99;478"):
            with self.subTest(file=bad_file):
                parsed = csv_parser.parse_rosters_csv(bad_file)
                with self.assertRaises(ValueError):
                    db.replace_initial_rosters(list(parsed["teams"]), parsed["assignments"])
                self.assertEqual(db.get_team("Alpha")["budget"], 77)
                self.assertEqual(db.get_all_rosters(), previous)

    def test_prices_are_not_silently_truncated_or_guessed(self):
        for price in ("", "abc", "nan", "inf", "-1", "12.5"):
            with self.subTest(price=price):
                result = csv_parser.parse_rosters_csv(f"Alpha;1;{price}")
                self.assertEqual(result["assignments"], [])
                self.assertEqual(len(result["errors"]), 1)


if __name__ == "__main__":
    unittest.main()
