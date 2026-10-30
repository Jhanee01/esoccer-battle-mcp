import pytest

from esoccer_battle_mcp import db
from esoccer_battle_mcp.schedule import Schedule

ROWS = [
    # id, battle_day, local_time, venue, format, turnus, session, p1, p2, s1, s2
    (1, "2025-11-12", "07:00", "Old Trafford", "8min", 1, "day", "Alpha", "Beta", 3, 2),
    (2, "2026-10-28", "15:30", "Wembley", "8min", 5, "afternoon", "Alpha", "Gamma", 4, 4),
    (3, "2026-10-28", "23:30", "Wembley", "8min", 9, "night", "Beta", "Gamma", 1, 6),
    (4, "2026-10-28", "01:10", "Etihad", "12min", 7, "night", "Delta", "Echo", 5, 5),
    (5, "2026-10-29", "06:10", "Anfield", "12min", 1, "day", "Delta", "Alpha", 7, 2),
]


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    c.executemany(
        "INSERT INTO matches(id, battle_day, local_time, venue, format, turnus, session,"
        " player1, player2, score1, score2) VALUES (?,?,?,?,?,?,?,?,?,?,?)", ROWS)
    c.commit()
    return c


@pytest.fixture(scope="module")
def sched():
    return Schedule.load()


def ids(conn, sched, **kw):
    where, params, _ = db.build_filters(sched, **kw)
    return sorted(r["id"] for r in conn.execute(f"SELECT id FROM matches WHERE {where}", params))


def test_schema_and_meta(conn):
    assert db.get_meta(conn, "schema_version") == str(db.SCHEMA_VERSION)
    db.set_meta(conn, "x", "1")
    db.set_meta(conn, "x", "2")
    assert db.get_meta(conn, "x") == "2"


def test_filters(conn, sched):
    assert ids(conn, sched) == [1, 2, 3, 4, 5]
    assert ids(conn, sched, since="2026-10-28", until="2026-10-28") == [2, 3, 4]
    assert ids(conn, sched, venue="Wembley") == [2, 3]
    assert ids(conn, sched, format="12min") == [4, 5]
    assert ids(conn, sched, session="night") == [3, 4]
    assert ids(conn, sched, turnus=1) == [1, 5]
    assert ids(conn, sched, time_from="15:00", time_to="16:00") == [2]
    assert ids(conn, sched, time_from="23:00", time_to="02:00") == [3, 4]   # éjfélen át


def test_applied(sched):
    _, _, applied = db.build_filters(sched, venue="Wembley", session="night")
    assert applied == {"era": "FC26", "venue": "Wembley", "session": "night"}


@pytest.mark.parametrize("kw,msg", [
    ({"venue": "Hillsborough"}, "Unknown venue"),
    ({"format": "6min"}, "Unknown format"),
    ({"session": "evening"}, "Unknown session"),
    ({"since": "2026/10/01"}, "YYYY-MM-DD"),
    ({"time_from": "7:00"}, "HH:MM"),
    ({"era": "FC25"}, "Unknown era"),
    ({"turnus": 12}, "between 1 and 11"),
])
def test_invalid(sched, kw, msg):
    with pytest.raises(ValueError, match=msg):
        db.build_filters(sched, **kw)


def test_today_battle_day():
    from datetime import datetime
    from esoccer_battle_mcp.schedule import TZ
    assert db.today_battle_day(datetime(2026, 10, 30, 3, 0, tzinfo=TZ)) == "2026-10-29"
    assert db.today_battle_day(datetime(2026, 10, 30, 9, 0, tzinfo=TZ)) == "2026-10-30"
