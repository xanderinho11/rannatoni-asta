import ast
import asyncio
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
import auction
import db


class PauseAttributionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.patcher = patch.object(db, "DB_PATH", str(Path(self.temp.name) / "test.db"))
        self.patcher.start()
        db.init_db()
        db.replace_catalog([{"pid": p, "name": f"Player {p}", "roles": ["A"]} for p in (1, 2)])
        with db.get_conn() as conn:
            for team in ("Alpha", "Beta"):
                conn.execute(
                    "INSERT INTO teams(uid,name,username,pin_hash,budget,ready) VALUES(?,?,?,'configured',500,1)",
                    (team.lower(), team, team.lower()),
                )
            conn.commit()
        db.mark_auction_session_start()
        self.game = auction.Auction()
        self.game.open_player(1)

    def tearDown(self):
        self.patcher.stop()
        self.temp.cleanup()

    def test_team_identity_is_public_but_other_bids_remain_private(self):
        self.game.bid("Alpha", 25)
        self.game.request_extra_time("Alpha")
        for viewer in ("Alpha", "Beta", None):
            snapshot = self.game.snapshot(viewer)
            self.assertEqual(snapshot["paused_by"], {"kind": "team", "team": "Alpha"})
            self.assertNotIn("offers", snapshot)
            if viewer != "Alpha":
                self.assertNotEqual((snapshot.get("own_response") or {}).get("amount"), 25)
        self.game.pass_("Beta")
        self.assertTrue(self.game.all_submitted())
        self.assertEqual(self.game.close()["team"], "Alpha")
        self.assertIsNone(self.game.snapshot(None)["paused_by"])

    def test_pause_author_survives_restart_and_noop_pause_cannot_replace_it(self):
        self.game.request_extra_time("Alpha")
        self.game.pause(team="Beta", source="manager")
        restored = auction.Auction()
        self.assertTrue(restored.load_persisted())
        self.assertEqual(restored.snapshot(None)["paused_by"], {"kind": "team", "team": "Alpha"})
        public = restored.snapshot(None)
        public["paused_by"]["team"] = "Changed"
        self.assertEqual(restored.paused_by["team"], "Alpha")

    def test_manager_endpoint_attributes_authenticated_team_and_resume_clears_author(self):
        scope = {"auction": self.game, "auction_module": auction, "asyncio": asyncio,
                 "AUCTION_LOCK": asyncio.Lock(), "Depends": lambda value: None,
                 "require_auction_manager": lambda: None, "_stop_timer": Mock(),
                 "broadcast_state": AsyncMock(), "HTTPException": Exception}
        node = next(n for n in ast.parse((ROOT / "backend/main.py").read_text()).body
                    if getattr(n, "name", None) == "pause_timer")
        node.decorator_list = []
        exec(compile(ast.Module(body=[node], type_ignores=[]), "pause-callback", "exec"), scope)
        asyncio.run(scope["pause_timer"]({"team": "Beta"}))
        self.assertEqual(self.game.snapshot("Alpha")["paused_by"], {"kind": "manager", "team": "Beta"})
        scope["broadcast_state"].assert_awaited_once()
        self.game.resume()
        self.assertIsNone(self.game.paused_by)
        self.game.cancel()
        self.game.open_player(2)
        self.assertIsNone(self.game.snapshot(None)["paused_by"])

    def test_tiebreak_clears_pause_but_keeps_jolly_used_for_player(self):
        self.game.request_extra_time("Alpha")
        self.game.bid("Alpha", 10)
        self.game.bid("Beta", 10)
        self.assertEqual(self.game.close()["type"], "tiebreak")
        snapshot = self.game.snapshot("Beta")
        self.assertFalse(snapshot["paused"])
        self.assertIsNone(snapshot["paused_by"])
        self.assertTrue(snapshot["extra_time"]["requested_for_player"])

    def test_restart_after_expiry_identifies_system_instead_of_a_team(self):
        self.game.deadline = time.time() - 10
        db.save_auction_state(self.game.serialize())
        restored = auction.Auction()
        self.assertTrue(restored.load_persisted())
        self.assertEqual(restored.snapshot(None)["paused_by"], {"kind": "system", "team": None})

    def test_existing_18_8_pause_loads_without_inventing_an_author(self):
        self.game.pause()
        legacy = self.game.serialize()
        legacy.pop("paused_by")
        db.save_auction_state(legacy)
        restored = auction.Auction()
        self.assertTrue(restored.load_persisted())
        self.assertTrue(restored.paused)
        self.assertIsNone(restored.snapshot(None)["paused_by"])


if __name__ == "__main__":
    unittest.main()
