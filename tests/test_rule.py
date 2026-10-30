from esoccer_battle_mcp.stats import evaluate_rule

_id = iter(range(1, 10_000))


def m(p1, s1, p2, s2, day="2026-10-01", venue="Wembley", session="day", fmt="8min"):
    return {"id": next(_id), "battle_day": day, "local_time": "10:00", "venue": venue, "format": fmt,
            "turnus": 2, "session": session, "player1": p1, "player2": p2, "score1": s1, "score2": s2}


def run(rows, conditions, evaluate=None, subject=None, market="over_under", line=5.5, pick="over",
        scope="all", min_history=5, group_by="none"):
    ev = {r["id"] for r in rows} if evaluate is None else evaluate
    return evaluate_rule(rows, ev, subject, conditions, market, line, pick, scope, min_history,
                         group_by, lambda d: "FC26")


AVG_GT_5 = [{"feature": "p1_avg_total", "n": 5, "op": ">", "value": 5}]


def test_no_leakage_of_own_result():
    # A előzménye: 5 meccs 2 gólos összeggel, majd X (20 gól), majd Y
    rows = [m("A", 1, f"Z{i}", 1) for i in range(5)]
    x = m("A", 10, "Q", 10)
    y = m("A", 3, "R", 3)
    rows += [x, y]
    r = run(rows, AVG_GT_5, evaluate={x["id"], y["id"]})
    fired = {e["score"] for e in r["examples"]}
    assert "10-10" not in fired          # X-nél az átlag a korábbi 5 meccsből: 2.0 → nem teljesül
    assert fired == {"3-3"}              # Y-nál már benne van X: (2*4 + 20) / 5 = 5.6 → teljesül
    assert r["examples"][0]["features"] == {"p1_avg_total(n=5)": 5.6}


def test_min_history_skips_and_baseline():
    rows = [m("A", 3, f"Z{i}", 3) for i in range(7)]
    r = run(rows, AVG_GT_5)
    assert r["skipped_insufficient_history"] == 5      # az első 5 meccsnél nincs 5 korábbi
    assert r["matched"] == 2 and r["hits"] == 2 and r["hit_rate"] == 100.0
    assert r["break_even_odds"] == 1.0
    assert r["baseline"]["evaluated"] == 7 and r["baseline"]["hits"] == 7


def test_subject_is_always_p1():
    rows = [m(f"Z{i}", 0, "A", 4) for i in range(5)]   # A mindig player2, 4-0-ra nyer
    rows.append(m("B", 1, "A", 0))
    cond = [{"feature": "p1_win_rate", "n": 5, "op": ">=", "value": 100}]
    r = run(rows, cond, subject="A", market="1x2", line=None, pick="away")
    assert r["matched"] == 1 and r["hits"] == 1        # A (p1) vesztett → az 'away' bejött
    assert r["examples"][0]["p1"] == "A"


def test_h2h_count_and_between():
    rows = [m("A", 2, "B", 2) for _ in range(3)] + [m("B", 4, "A", 4)]
    cond = [{"feature": "h2h_count", "n": 10, "op": ">=", "value": 3},
            {"feature": "h2h_avg_total", "n": 10, "op": "between", "value": [3.5, 4.5]}]
    r = run(rows, cond, min_history=3)
    assert r["matched"] == 1 and r["hits"] == 1        # a 4. meccs: 3 korábbi, átlag 4.0; 8 gól > 5.5


def test_history_scope_same_venue():
    rows = [m("A", 5, f"Z{i}", 5, venue="Old Trafford") for i in range(5)]
    rows.append(m("A", 3, "Q", 3, venue="Wembley"))
    target = {rows[-1]["id"]}
    assert run(rows, AVG_GT_5, evaluate=target)["matched"] == 1
    r = run(rows, AVG_GT_5, evaluate=target, scope="same_venue")
    assert r["matched"] == 0 and r["skipped_insufficient_history"] == 1


def test_formats_do_not_mix():
    rows = [m("A", 5, f"Z{i}", 5, fmt="12min", venue="Anfield") for i in range(5)]
    rows.append(m("A", 3, "Q", 3))
    r = run(rows, AVG_GT_5, evaluate={rows[-1]["id"]})
    assert r["skipped_insufficient_history"] == 1      # 8p meccshez nincs 8p előzmény


def test_over_rate_and_groups():
    rows = [m("A", 4, f"Z{i}", 3, day=f"2026-09-0{i + 1}") for i in range(5)]
    rows += [m("A", 1, "Q", 1, day="2026-10-02"), m("A", 4, "R", 4, day="2026-10-03")]
    cond = [{"feature": "p1_over_rate", "n": 5, "op": ">=", "value": 60, "line": 6.5}]
    r = run(rows, cond, group_by="month")
    assert r["matched"] == 2 and r["hits"] == 1 and r["break_even_odds"] == 2.0
    assert r["groups"] == [{"group": "2026-10", "matched": 2, "hits": 1, "hit_rate": 50.0, "break_even_odds": 2.0}]
