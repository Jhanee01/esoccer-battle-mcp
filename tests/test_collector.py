import pytest

from esoccer_battle_mcp import api, collector, db
from esoccer_battle_mcp.schedule import Schedule


def tournament(tid, venue, start_utc, name_day, status=4):
    return {"id": tid, "token_international": f"Some League {name_day}", "status_id": status,
            "start_date": start_utc, "league": {"token_international": "Some League"},
            "location": {"token": venue}}


def match(mid, start_utc, p1, s1, p2, s2, status=3):
    side = lambda n, s: {"nickname": n, "score": s, "cornerScore": 0,
                         "team": {"token_international": n + " FC"}}
    return {"id": mid, "date": start_utc, "status_id": status,
            "participant1": side(p1, s1), "participant2": side(p2, s2)}


# 2026-10-28-i Battle-nap: Wembley T1 (07:00 helyi = 06:00Z), Wembley T9 (00:20 helyi másnap),
# egy kihagyott (Hillsborough), egy ismeretlen pálya, és egy már a következő naphoz tartozó torna
TOURNAMENTS = {
    "2026-10-28": [tournament(1, "Wembley", "2026-10-28T06:00:00Z", "2026-10-28"),
                   tournament(2, "Hillsborough", "2026-10-28T08:00:00Z", "2026-10-28"),
                   tournament(3, "New Arena", "2026-10-28T09:00:00Z", "2026-10-28")],
    "2026-10-29": [tournament(4, "Wembley", "2026-10-28T23:20:00Z", "2026-10-28"),
                   tournament(5, "Wembley", "2026-10-29T06:00:00Z", "2026-10-29")],
}
MATCHES = {
    1: [match(11, "2026-10-28T06:00:00Z", "Alpha", 3, "Beta", 2),
        match(12, "2026-10-28T06:12:00Z", "Gamma", 1, "Delta", 1, status=2)],   # nem befejezett
    4: [match(41, "2026-10-28T23:20:00Z", "Alpha", 4, "Gamma", 5)],
    5: [match(51, "2026-10-29T06:00:00Z", "Beta", 0, "Delta", 0)],
}


@pytest.fixture
def fake_api(monkeypatch):
    calls = []
    monkeypatch.setattr(api, "tournaments_on", lambda d: TOURNAMENTS.get(d, []))

    def matches_of(tid):
        calls.append(tid)
        return MATCHES[tid]
    monkeypatch.setattr(api, "matches_of", matches_of)
    return calls


@pytest.fixture
def conn(tmp_path):
    return db.connect(tmp_path / "t.db")


def test_fetch_day(conn, fake_api):
    sched = Schedule.load()
    stats = collector.fetch_day(conn, sched, "2026-10-28")
    assert stats["matches"] == 2 and stats["tournaments"] == 2
    assert sorted(fake_api) == [1, 4]                        # Hillsborough / ismeretlen / másnapi nem
    rows = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM matches")}
    assert set(rows) == {11, 41}
    assert (rows[11]["turnus"], rows[11]["session"], rows[11]["local_time"]) == (1, "day", "07:00")
    assert (rows[41]["turnus"], rows[41]["session"], rows[41]["local_date"]) == (9, "night", "2026-10-29")
    assert rows[41]["battle_day"] == "2026-10-28"
    assert conn.execute("SELECT venue FROM unknown_venues").fetchall()[0]["venue"] == "New Arena"
    assert stats["complete"] is True


def test_refetch_does_not_duplicate(conn, fake_api):
    sched = Schedule.load()
    collector.fetch_day(conn, sched, "2026-10-28")
    collector.fetch_day(conn, sched, "2026-10-28")
    assert conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 2


def test_finished_tournaments_not_refetched(conn, fake_api):
    sched = Schedule.load()
    collector.fetch_day(conn, sched, "2026-10-28")
    fake_api.clear()
    collector.fetch_day(conn, sched, "2026-10-28")
    assert fake_api == []                                     # minden torna status 4 → nincs új kérés


def test_reclassify_after_schedule_change(conn, fake_api):
    sched = Schedule.load()
    collector.fetch_day(conn, sched, "2026-10-28")
    conn.execute("UPDATE matches SET turnus = NULL, session = NULL")
    db.set_meta(conn, "menetrend_hash", "old")
    assert collector.reclassify(conn, sched) == 2
    assert conn.execute("SELECT COUNT(*) FROM matches WHERE turnus IS NULL").fetchone()[0] == 0
    assert collector.reclassify(conn, sched) == 0             # a hash már egyezik


def test_missing_days(conn):
    conn.executemany("INSERT INTO days VALUES (?, ?, '')", [("2026-10-28", 1), ("2026-10-27", 0)])
    assert collector.missing_days(conn, "2026-10-26", "2026-10-29") == ["2026-10-29", "2026-10-27", "2026-10-26"]


def test_syncer_backfills_and_stops(tmp_path, fake_api):
    import time
    path = tmp_path / "s.db"
    syncer = collector.Syncer(Schedule.load(), "2026-10-20", lambda: db.connect(path))
    syncer.start()
    deadline = time.monotonic() + 20
    conn = db.connect(path)
    while time.monotonic() < deadline:
        if db.get_meta(conn, "last_sync") and not syncer.backfill_running and \
                conn.execute("SELECT COUNT(*) FROM days").fetchone()[0] >= 9:
            break
        time.sleep(0.1)
    syncer.close()
    syncer.thread.join(5)
    assert not syncer.thread.is_alive()
    assert conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0] >= 2   # 10-28 betöltve
    assert db.get_meta(conn, "menetrend_hash") == Schedule.load().digest


def test_only_one_process_collects(tmp_path, monkeypatch):
    path = tmp_path / "l.db"
    a, b = db.connect(path), db.connect(path)
    assert collector.take_lease(a, "A") is True
    assert collector.take_lease(b, "B") is False          # A-nál van a jog
    assert collector.take_lease(a, "A") is True           # megújítás
    collector.release_lease(a, "A")
    assert collector.take_lease(b, "B") is True           # felszabadult → B átveszi
    real = collector.time.time
    monkeypatch.setattr(collector.time, "time", lambda: real() + collector.LEASE_S + 1)
    assert collector.take_lease(a, "A") is True           # B jogát lejártnak látja → átveszi


def test_bad_api_format_is_skipped(conn, fake_api, monkeypatch):
    broken = [{"id": 99, "date": "2026-10-28T06:00:00Z", "status_id": 3, "participant1": {}}]
    monkeypatch.setitem(MATCHES, 1, broken)
    stats = collector.fetch_day(conn, Schedule.load(), "2026-10-28")
    assert stats["skipped"] == 1 and stats["complete"] is False
    assert "unexpected API format" in db.get_meta(conn, "last_error")
    assert conn.execute("SELECT COUNT(*) FROM matches WHERE id = 99").fetchone()[0] == 0
