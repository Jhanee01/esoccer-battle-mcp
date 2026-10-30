import asyncio

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from esoccer_battle_mcp import db
from esoccer_battle_mcp.server import mcp

ROWS = [
    # id, battle_day, start_utc, local_time, venue, format, turnus, session, p1, t1, p2, t2, s1, s2
    (1, "2026-10-27", "2026-10-27T06:00:00Z", "07:00", "Wembley", "8min", 1, "day", "Alpha", "Ajax", "Beta", "Porto", 3, 2),
    (2, "2026-10-27", "2026-10-27T21:10:00Z", "22:10", "Wembley", "8min", 8, "night", "Beta", "Porto", "Alpha", "Ajax", 1, 4),
    (3, "2026-10-28", "2026-10-28T06:00:00Z", "07:00", "Wembley", "8min", 1, "day", "Alpha", "Ajax", "Gamma", "Inter", 2, 2),
    (4, "2026-10-28", "2026-10-28T05:00:00Z", "06:00", "Anfield", "12min", 1, "day", "Delta", "Roma", "Echo", "Lazio", 5, 4),
]


@pytest.fixture(autouse=True)
def database(tmp_path, monkeypatch):
    monkeypatch.setenv("ESOCCER_BATTLE_DB", str(tmp_path / "t.db"))
    conn = db.connect()
    conn.executemany(
        "INSERT INTO matches(id, battle_day, start_utc, local_time, venue, format, turnus, session,"
        " player1, team1, player2, team2, score1, score2) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ROWS)
    conn.commit()
    conn.close()


def call(name, args=None):
    async def run():
        async with Client(mcp) as c:
            return (await c.call_tool(name, args or {})).data
    return asyncio.run(run())


def test_tools_listed():
    async def run():
        async with Client(mcp) as c:
            return {t.name for t in await c.list_tools()}
    assert {"query_matches", "sync_status"} <= asyncio.run(run())


def test_query_matches_player_and_opponent():
    r = call("query_matches", {"player": "alpha"})          # kis/nagybetű-független
    assert r["sample_size"] == 3
    assert [m["battle_day"] for m in r["matches"]] == ["2026-10-28", "2026-10-27", "2026-10-27"]
    r = call("query_matches", {"player": "Alpha", "opponent": "Beta"})
    assert r["sample_size"] == 2
    r = call("query_matches", {"team": "Ajax", "session": "night"})
    assert r["sample_size"] == 1 and r["matches"][0]["score2"] == 4


def test_query_matches_limit_and_filters():
    r = call("query_matches", {"limit": 2, "order": "oldest"})
    assert r["truncated"] is True and len(r["matches"]) == 2
    assert r["filters_applied"]["era"] == "FC26"
    assert call("query_matches", {"format": "12min"})["sample_size"] == 1


def test_invalid_input_message():
    with pytest.raises(ToolError, match="Unknown venue"):
        call("query_matches", {"venue": "Hillsborough"})


def test_did_you_mean():
    r = call("query_matches", {"player": "Alpah"})
    assert r["sample_size"] == 0
    assert r["did_you_mean"]["player"]["suggestions"][0] == "Alpha"
    r = call("query_matches", {"team": "Aja"})
    assert "Ajax" in r["did_you_mean"]["team"]["suggestions"]
    r = call("query_matches", {"player": "Alpha", "opponent": "Delta"})   # létező nevek, csak nincs közös meccs
    assert r["sample_size"] == 0 and r["did_you_mean"] == {}
    assert "did_you_mean" not in call("query_matches", {"player": "Alpha"})


def test_small_sample_note():
    assert any("Small sample" in n for n in call("query_matches", {"player": "Alpha"})["notes"])


def test_notes_follow_actual_sample():
    notes = call("query_matches", {"venue": "Wembley"})["notes"]        # a minta csak 10-27…10-28
    assert not any(n.startswith("Window crosses") for n in notes)
    conn = db.connect()
    conn.execute("INSERT INTO matches(id, battle_day, start_utc, local_time, venue, format, turnus, session,"
                 " player1, team1, player2, team2, score1, score2)"
                 " VALUES (9, '2026-08-01', '2026-08-01T06:00:00Z', '08:00', 'Wembley', '8min', 1, 'day',"
                 " 'Alpha', 'Ajax', 'Beta', 'Porto', 1, 1)")
    conn.commit()
    conn.close()
    notes = call("query_matches", {"venue": "Wembley"})["notes"]
    assert any("2026-08-05" in n for n in notes)
    assert not any("2026-03-05" in n for n in notes)


def test_sync_status():
    r = call("sync_status")
    assert r["matches_total"] == 4
    assert r["matches_by_venue"] == {"Wembley": 3, "Anfield": 1}
    assert r["latest_match"]["kickoff_local"] == "2026-10-28 07:00"
