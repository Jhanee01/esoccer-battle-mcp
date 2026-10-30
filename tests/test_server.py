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


def test_player_h2h():
    r = call("player_h2h", {"player_a": "alpha", "player_b": "Beta"})
    assert r["sample_size"] == 2
    assert r["record"] == {"a_wins": 2, "draws": 0, "b_wins": 0,
                           "a_win_pct": 100.0, "draw_pct": 0.0, "b_win_pct": 0.0}
    assert r["goals"] == {"avg_total": 5.0, "avg_a": 3.5, "avg_b": 1.5}
    ou = {x["line"]: x["over"] for x in r["over_under"]}
    assert ou[1.5] == 2 and ou[4.5] == 2 and ou[5.5] == 0 and max(ou) == 12.5   # 8p vonalak
    assert r["btts"]["yes"] == 2
    assert r["by_session"]["night"]["matches"] == 1
    assert r["last_matches"][0]["score"] == "4-1"          # Beta–Alpha 1-4, Alpha szemszögéből
    r = call("player_h2h", {"player_a": "Alpha", "player_b": "Beta", "session": "day"})
    assert r["sample_size"] == 1


def test_team_h2h_and_12min_lines():
    r = call("team_h2h", {"team_a": "Porto", "team_b": "Ajax"})
    assert r["record"]["b_wins"] == 2 and r["last_matches"][0]["a_player"] == "Beta"
    r = call("team_h2h", {"team_a": "Roma", "team_b": "Lazio"})
    assert [x["line"] for x in r["over_under"]][0] == 3.5 and r["over_under"][-1]["line"] == 16.5


def test_market_stats_player():
    r = call("market_stats", {"market": "over_under", "line": 4.5, "player": "Alpha"})
    assert r["sample_size"] == 3
    assert r["overall"]["over"] == 2 and r["overall"]["under"] == 1 and r["overall"]["avg_total"] == 4.67
    r = call("market_stats", {"market": "over_under", "line": 4.5, "player": "Alpha", "group_by": "session"})
    assert {g["group"]: g["over"] for g in r["groups"]} == {"day": 1, "night": 1}
    assert sum(g["sample_size"] for g in r["groups"]) == r["sample_size"]
    r = call("market_stats", {"market": "1x2", "player": "Alpha"})
    assert (r["overall"]["home"], r["overall"]["draw"], r["overall"]["away"]) == (2, 1, 0)
    r = call("market_stats", {"market": "btts", "player": "Alpha", "group_by": "opponent", "min_sample": 2})
    assert [g["group"] for g in r["groups"]] == ["Beta"] and r["groups_below_min_sample"] == 1


def test_market_stats_without_subject():
    r = call("market_stats", {"market": "1x2", "venue": "Anfield"})
    assert r["overall"]["home"] == 1 and r["perspective"] == "home = player1"
    r = call("market_stats", {"market": "over_under", "format": "8min"})
    assert r["overall"]["lines"][0]["line"] == 1.5 and r["overall"]["avg_total"] == 4.67


@pytest.mark.parametrize("args,msg", [
    ({"market": "over_under", "group_by": "venue"}, "line is required"),
    ({"market": "over_under", "line": 6.0}, "end in .5"),
    ({"market": "btts", "group_by": "opponent"}, "needs player or team"),
    ({"market": "btts", "player": "Alpha", "team": "Ajax"}, "either player or team"),
])
def test_market_stats_invalid(args, msg):
    with pytest.raises(ToolError, match=msg):
        call("market_stats", args)


def test_player_form():
    r = call("player_form", {"player": "Alpha", "last_n": 1})
    assert r["recent"]["draws"] == 1 and r["previous"]["wins"] == 1
    assert r["change"]["win_pct"] == -100.0
    assert r["matches"][0]["score"] == "2-2" and r["matches"][0]["opponent"] == "Gamma"


def test_data_notes():
    r = call("data_notes")
    assert r["current_era"] == "FC26" and len(r["events"]) == 8
    assert r["venues"]["Anfield"]["format"] == "12min"
    sb = r["venues"]["Stamford Bridge"]
    assert sb["schedule_history"][0] == "2025-11-11" and sb["blocks"][7] == {"turnus": 8, "start": "21:40", "session": "night"}
    assert r["venues"]["Bernabeu"]["active_until"] == "2026-09-21"
    assert "Hillsborough" in r["not_collected"]
    r = call("data_notes", {"venue": "San Siro"})
    assert list(r["venues"]) == ["San Siro"] and all("San Siro" in e["venues"] for e in r["events"])


def test_test_rule_tool():
    r = call("test_rule", {"market": "over_under", "line": 3.5, "pick": "over", "player": "Alpha",
                           "min_history": 1, "group_by": "none",
                           "conditions": [{"feature": "p1_avg_total", "n": 5, "op": ">=", "value": 5}]})
    # Alpha: 5 gól, 5 gól, majd 4 gól; a 2. és 3. meccsnél az előzmény átlaga 5 → teljesül, mindkettő over 3.5
    assert (r["matched"], r["hits"], r["skipped_insufficient_history"]) == (2, 2, 1)
    assert r["baseline"]["evaluated"] == 3 and r["break_even_odds"] == 1.0
    assert r["sample_size"] == 2 and r["conditions"][0]["feature"] == "p1_avg_total"


@pytest.mark.parametrize("args,msg", [
    ({"market": "btts", "pick": "over"}, "pick for btts"),
    ({"market": "over_under", "pick": "over"}, "line is required"),
    ({"market": "btts", "pick": "yes", "conditions": []}, "At least one condition"),
    ({"market": "btts", "pick": "yes",
      "conditions": [{"feature": "p1_avg_total", "op": "between", "value": 5}]}, "between needs"),
    ({"market": "btts", "pick": "yes",
      "conditions": [{"feature": "p1_over_rate", "op": ">", "value": 50}]}, "needs a line"),
])
def test_test_rule_invalid(args, msg):
    args.setdefault("conditions", [{"feature": "p1_avg_total", "op": ">", "value": 1}])
    with pytest.raises(ToolError, match=msg):
        call("test_rule", args)


def test_h2h_not_found():
    r = call("player_h2h", {"player_a": "Alpah", "player_b": "Beta"})
    assert r["sample_size"] == 0 and r["did_you_mean"]["player_a"]["suggestions"] == ["Alpha"]


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
