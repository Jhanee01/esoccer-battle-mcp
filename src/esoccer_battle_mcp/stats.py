"""Aggregations over match rows: head-to-head, market rates, form, rule tests."""

from collections import defaultdict

LINES = {"8min": [x + 0.5 for x in range(1, 13)],     # 1.5 … 12.5
         "12min": [x + 0.5 for x in range(3, 17)]}    # 3.5 … 16.5
MIXED_LINES = [x + 0.5 for x in range(1, 17)]          # vegyes formátum: 1.5 … 16.5


def pct(part: int, whole: int) -> float | None:
    return round(100 * part / whole, 1) if whole else None


def lines_for(rows: list[dict]) -> list[float]:
    formats = {r["format"] for r in rows}
    return LINES[formats.pop()] if len(formats) == 1 and None not in formats else MIXED_LINES


def oriented(rows: list[dict], side_a: str, cols: tuple[str, str]) -> list[dict]:
    """A sorokat az 'a' oldal nézőpontjából fordítja: a_goals / b_goals, a_name / b_name."""
    c1, c2 = cols
    out = []
    for r in rows:
        a_first = (r[c1] or "").lower() == side_a.lower()
        out.append({**r,
                    "a_goals": r["score1"] if a_first else r["score2"],
                    "b_goals": r["score2"] if a_first else r["score1"],
                    "a_player": r["player1"] if a_first else r["player2"],
                    "b_player": r["player2"] if a_first else r["player1"],
                    "a_team": r["team1"] if a_first else r["team2"],
                    "b_team": r["team2"] if a_first else r["team1"]})
    return out


def _summary(rows: list[dict]) -> dict:
    n = len(rows)
    a_w = sum(r["a_goals"] > r["b_goals"] for r in rows)
    d = sum(r["a_goals"] == r["b_goals"] for r in rows)
    total = sum(r["a_goals"] + r["b_goals"] for r in rows)
    return {"matches": n, "a_win_pct": pct(a_w, n), "draw_pct": pct(d, n),
            "b_win_pct": pct(n - a_w - d, n), "avg_total_goals": round(total / n, 2) if n else None}


def head_to_head(rows: list[dict]) -> dict:
    """rows: oriented() kimenete, legújabb elöl."""
    n = len(rows)
    a_w = sum(r["a_goals"] > r["b_goals"] for r in rows)
    d = sum(r["a_goals"] == r["b_goals"] for r in rows)
    totals = [r["a_goals"] + r["b_goals"] for r in rows]
    btts = sum(r["a_goals"] > 0 and r["b_goals"] > 0 for r in rows)
    by = {"venue": defaultdict(list), "session": defaultdict(list)}
    for r in rows:
        by["venue"][r["venue"]].append(r)
        by["session"][r["session"] or "unknown"].append(r)
    return {
        "record": {"a_wins": a_w, "draws": d, "b_wins": n - a_w - d,
                   "a_win_pct": pct(a_w, n), "draw_pct": pct(d, n), "b_win_pct": pct(n - a_w - d, n)},
        "goals": {"avg_total": round(sum(totals) / n, 2) if n else None,
                  "avg_a": round(sum(r["a_goals"] for r in rows) / n, 2) if n else None,
                  "avg_b": round(sum(r["b_goals"] for r in rows) / n, 2) if n else None},
        "over_under": [{"line": line, "over": sum(t > line for t in totals),
                        "over_pct": pct(sum(t > line for t in totals), n)} for line in lines_for(rows)],
        "btts": {"yes": btts, "yes_pct": pct(btts, n)},
        "by_venue": {k: _summary(v) for k, v in by["venue"].items()},
        "by_session": {k: _summary(v) for k, v in by["session"].items()},
        "last_matches": [{"battle_day": r["battle_day"], "local_date": r["local_date"],
                          "local_time": r["local_time"], "venue": r["venue"], "turnus": r["turnus"],
                          "a_player": r["a_player"], "a_team": r["a_team"],
                          "b_player": r["b_player"], "b_team": r["b_team"],
                          "score": f"{r['a_goals']}-{r['b_goals']}"} for r in rows[:10]],
    }
