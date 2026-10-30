"""Aggregations over match rows: head-to-head, market rates, form, rule tests."""

from collections import defaultdict
from datetime import date

LINES = {"8min": [x + 0.5 for x in range(1, 13)],     # 1.5 … 12.5
         "12min": [x + 0.5 for x in range(3, 17)]}    # 3.5 … 16.5
MIXED_LINES = [x + 0.5 for x in range(1, 17)]          # vegyes formátum: 1.5 … 16.5


def pct(part: int, whole: int) -> float | None:
    return round(100 * part / whole, 1) if whole else None


def lines_for(rows: list[dict]) -> list[float]:
    formats = {r["format"] for r in rows}
    return LINES[formats.pop()] if len(formats) == 1 and None not in formats else MIXED_LINES


def oriented(rows: list[dict], side_a: str | None, cols: tuple[str, str]) -> list[dict]:
    """A sorokat az 'a' oldal nézőpontjából fordítja: a_goals / b_goals, a_name / b_name.
    side_a=None: nincs alany, az 'a' mindig a player1 (hazai) oldal."""
    c1, _ = cols
    out = []
    for r in rows:
        a_first = side_a is None or (r[c1] or "").lower() == side_a.lower()
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


MAX_GROUPS = 50


def group_key(r: dict, group_by: str):
    if group_by == "month":
        return r["battle_day"][:7]
    if group_by == "week":
        y, w, _ = date.fromisoformat(r["battle_day"]).isocalendar()
        return f"{y}-W{w:02d}"
    if group_by == "opponent":
        return r["b_player"] if r.get("_subject") == "player" else r["b_team"]
    return r[group_by]


def market_summary(rows: list[dict], market: str, line: float | None) -> dict:
    n = len(rows)
    if market == "over_under":
        totals = [r["a_goals"] + r["b_goals"] for r in rows]
        avg = round(sum(totals) / n, 2) if n else None
        if line is None:
            return {"avg_total": avg, "lines": [{"line": ln, "over": sum(t > ln for t in totals),
                                                 "over_pct": pct(sum(t > ln for t in totals), n)}
                                                for ln in lines_for(rows)]}
        over = sum(t > line for t in totals)
        return {"line": line, "over": over, "over_pct": pct(over, n),
                "under": n - over, "under_pct": pct(n - over, n), "avg_total": avg}
    if market == "btts":
        yes = sum(r["a_goals"] > 0 and r["b_goals"] > 0 for r in rows)
        return {"yes": yes, "yes_pct": pct(yes, n), "no": n - yes, "no_pct": pct(n - yes, n)}
    a = sum(r["a_goals"] > r["b_goals"] for r in rows)
    d = sum(r["a_goals"] == r["b_goals"] for r in rows)
    return {"home": a, "home_pct": pct(a, n), "draw": d, "draw_pct": pct(d, n),
            "away": n - a - d, "away_pct": pct(n - a - d, n)}


def market_stats(rows: list[dict], market: str, line: float | None, group_by: str, min_sample: int) -> dict:
    """rows: oriented(); 'home' = az alany ('a') oldala, alany nélkül player1."""
    out = {"overall": market_summary(rows, market, line)}
    if group_by == "none":
        return out
    groups = defaultdict(list)
    for r in rows:
        groups[group_key(r, group_by)].append(r)
    items = [(k, v) for k, v in groups.items() if len(v) >= min_sample]
    if group_by in ("venue", "opponent"):
        items.sort(key=lambda kv: -len(kv[1]))
    else:
        items.sort(key=lambda kv: (kv[0] is None, kv[0]))
    out["groups"] = [{"group": k, "sample_size": len(v), **market_summary(v, market, line)}
                     for k, v in items[:MAX_GROUPS]]
    out["groups_truncated"] = len(items) > MAX_GROUPS
    out["groups_below_min_sample"] = len(groups) - len(items)
    return out


def _form_block(rows: list[dict]) -> dict:
    n = len(rows)
    if not n:
        return {"matches": 0}
    w = sum(r["a_goals"] > r["b_goals"] for r in rows)
    d = sum(r["a_goals"] == r["b_goals"] for r in rows)
    return {"matches": n, "wins": w, "draws": d, "losses": n - w - d,
            "win_pct": pct(w, n),
            "avg_scored": round(sum(r["a_goals"] for r in rows) / n, 2),
            "avg_conceded": round(sum(r["b_goals"] for r in rows) / n, 2),
            "avg_total": round(sum(r["a_goals"] + r["b_goals"] for r in rows) / n, 2),
            "btts_pct": pct(sum(r["a_goals"] > 0 and r["b_goals"] > 0 for r in rows), n)}


def player_form(rows: list[dict], last_n: int) -> dict:
    """rows: oriented() a játékos szemszögéből, legújabb elöl, legfeljebb 2*last_n."""
    recent, previous = _form_block(rows[:last_n]), _form_block(rows[last_n:2 * last_n])
    change = ({k: round(recent[k] - previous[k], 2) for k in
               ("win_pct", "avg_scored", "avg_conceded", "avg_total", "btts_pct")}
              if recent["matches"] and previous["matches"] else None)
    return {"recent": recent, "previous": previous, "change": change,
            "matches": [{"battle_day": r["battle_day"], "local_time": r["local_time"], "venue": r["venue"],
                         "turnus": r["turnus"], "team": r["a_team"], "opponent": r["b_player"],
                         "opponent_team": r["b_team"], "score": f"{r['a_goals']}-{r['b_goals']}"}
                        for r in rows[:last_n]]}


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
