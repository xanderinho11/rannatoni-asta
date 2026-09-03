import importlib, os, sys, tempfile, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / 'backend'
if str(BACKEND) not in sys.path: sys.path.insert(0, str(BACKEND))

def fresh(tmpdir):
    os.environ['FANTACALCIO_DB'] = str(Path(tmpdir)/'test.db')
    for n in ('auction','db'): sys.modules.pop(n, None)
    db = importlib.import_module('db'); db.init_db()
    auction = importlib.import_module('auction')
    return db, auction

def seed(db, players=5):
    with db.get_conn() as conn:
        for team in ('Alpha','Beta'):
            conn.execute("INSERT INTO teams(uid,name,username,pin_hash,pin_must_change,budget,is_admin,ready,market_finished) VALUES(?,?,?,?,0,500,0,1,0)", (team.lower(),team,team.lower(),'configured'))
        for pid in range(1, players+1):
            conn.execute("INSERT INTO players(pid,name,full_name,roles,club,stats_json,out_of_league) VALUES(?,?,?,?,?,'{}',0)", (pid,f'Player {pid}',f'Player {pid}','[\"A\"]','Club'))
        conn.commit()
    db.mark_auction_session_start()

def test_jolly_freezes_timer_but_bids_stay_open():
    with tempfile.TemporaryDirectory() as tmp:
        db, mod = fresh(tmp); seed(db); a = mod.Auction(); a.open_player(1)
        before = a.seconds_left(); st = a.request_extra_time('Alpha')
        assert st['remaining'] == 2
        assert st['behavior'] == 'pause'
        assert a.paused is True
        frozen = a.seconds_left()
        time.sleep(1.05)
        assert a.seconds_left() == frozen
        assert frozen <= before
        # La pausa non deve piu' impedire OFFERTA/PASSO.
        a.bid('Alpha', 10)
        a.pass_('Beta')
        assert a.all_submitted() is True
        assert a.close()['type'] == 'assigned'

def test_three_jolly_and_one_request_per_player():
    with tempfile.TemporaryDirectory() as tmp:
        db, mod = fresh(tmp); seed(db); a = mod.Auction(); a.open_player(1)
        assert a.request_extra_time('Alpha')['remaining'] == 2
        try: a.request_extra_time('Beta'); assert False
        except mod.AuctionError as e: assert "gia' bloccato" in str(e) or "gia' utilizzato" in str(e)
        a.cancel()
        for pid, rem in ((2,1),(3,0)):
            a.open_player(pid); assert a.request_extra_time('Alpha')['remaining'] == rem; a.cancel()
        a.open_player(4)
        try: a.request_extra_time('Alpha'); assert False
        except mod.AuctionError as e: assert 'tutti e 3' in str(e)

def test_manager_pause_also_allows_responses():
    with tempfile.TemporaryDirectory() as tmp:
        db, mod = fresh(tmp); seed(db); a = mod.Auction(); a.open_player(1)
        a.pause()
        assert a.paused is True
        a.bid('Alpha', 15)
        a.pass_('Beta')
        assert a.all_submitted() is True
        result = a.close()
        assert result['type'] == 'assigned'
        assert result['team'] == 'Alpha'

def test_request_is_for_player_not_round():
    with tempfile.TemporaryDirectory() as tmp:
        db, mod = fresh(tmp); seed(db); a = mod.Auction(); a.open_player(1)
        a.request_extra_time('Alpha'); a.bid('Alpha',20); a.bid('Beta',20)
        assert a.close()['type'] == 'tiebreak'
        snap = a.snapshot('Alpha')
        assert snap['duration'] == db.bid_duration()
        assert snap['extra_time']['requested_for_player'] is True
        assert snap['extra_time']['can_request'] is False
        # Il nuovo spareggio riparte con il proprio timer, ma il jolly resta
        # consumato per quel calciatore e non puo' essere richiesto di nuovo.
        assert a.paused is False

def test_new_session_resets_jolly():
    with tempfile.TemporaryDirectory() as tmp:
        db, mod = fresh(tmp); seed(db); a = mod.Auction(); a.open_player(1)
        a.request_extra_time('Alpha'); assert db.extra_time_status('Alpha',1)['remaining'] == 2
        db.mark_auction_session_start(); st = db.extra_time_status('Alpha',1)
        assert st['remaining'] == 3 and st['requested_for_player'] is False
