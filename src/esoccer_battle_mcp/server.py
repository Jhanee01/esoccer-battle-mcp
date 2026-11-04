"""FastMCP server exposing Esoccer Battle match data."""

import csv
import difflib
import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Literal

from fastmcp import FastMCP
from fastmcp.server.lifespan import lifespan
from pydantic import BaseModel, Field

from . import __version__, db, snapshot, stats, updates
from .collector import Syncer, fetch_day, missing_days, reclassify
from .schedule import Schedule, to_local

FIRST_DAY = "2025-11-11"
MAX_ROWS = 500
STALE_AFTER = timedelta(minutes=10)

SCHED = Schedule.load()

# a kliensek (Codex, Claude Desktop) a csak olvasó toolokat jóváhagyás nélkül futtathatják
READ_ONLY = {"readOnlyHint": True, "openWorldHint": False}
WRITES_FILE = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False}
SYNCER: Syncer | None = None


@lifespan
async def background_sync(server):
    """A háttér-gyűjtés a szerverrel együtt indul és áll le (ESOCCER_BATTLE_NO_SYNC=1 kikapcsolja)."""
    global SYNCER
    if os.environ.get("ESOCCER_BATTLE_NO_SYNC") != "1":
        SYNCER = Syncer(SCHED, FIRST_DAY, db.connect)
        SYNCER.start()
    try:
        yield {}
    finally:
        if SYNCER:
            SYNCER.close()
            SYNCER = None


mcp = FastMCP(
    "esoccer-battle",
    lifespan=background_sync,
    instructions=(
        "Esoccer Battle (FIFA/EA FC esports) 8 and 12 minute match results, collected from the public "
        "Battle API since 2025-11-11. Use it to research and measure betting-strategy ideas. There are no "
        "odds and no tips in the data. All times are Europe/Budapest local time. A Battle day runs from "
        "~06:00 to ~06:00 next morning; 'turnus' is the block number within that day and decides the "
        "session (day/afternoon/night). Check data_notes before analysing long windows. "
        "ANSWER ONLY WHAT THE USER ASKED. Report the numbers the tools return for the question and "
        "nothing else: do not compute your own estimates, 'expected' values, models or comparisons, do "
        "not make extra tool calls the question does not need, and do not add context, interpretation, "
        "caveats or follow-up suggestions on your own."
    ),
)

# --- közös szűrő-paraméterek ------------------------------------------------

Since = Annotated[str | None, Field(description="First Battle day to include, YYYY-MM-DD")]
Until = Annotated[str | None, Field(description="Last Battle day to include, YYYY-MM-DD")]
LastDays = Annotated[int | None, Field(description="Only the last N Battle days (overrides since)")]
Venue = Annotated[str | None, Field(description="Stamford Bridge, Old Trafford, Wembley, San Siro, "
                                                "Bernabeu (8 min) or Anfield, Etihad (12 min)")]
Format = Annotated[Literal["8min", "12min"] | None, Field(description="Match length")]
Session = Annotated[Literal["day", "afternoon", "night"] | None, Field(description="Session, derived from the turnus")]
Turnus = Annotated[int | None, Field(description="Block number within the Battle day (1-11 for 8 min, 1-8 for Latest/12 min)")]
TimeFrom = Annotated[str | None, Field(description="Earliest local kick-off time HH:MM (window may wrap midnight)")]
TimeTo = Annotated[str | None, Field(description="Latest local kick-off time HH:MM")]
Era = Annotated[str, Field(description="'current' (default), 'all', or an era id such as 'FC26'")]


def _filters(since, until, last_days, venue, format, session, turnus, time_from, time_to, era):
    return db.build_filters(SCHED, since=since, until=until, last_days=last_days, venue=venue,
                            format=format, session=session, turnus=turnus, time_from=time_from,
                            time_to=time_to, era=era)


def _coverage(conn) -> dict:
    row = conn.execute("SELECT MIN(battle_day) lo, MAX(battle_day) hi FROM matches").fetchone()
    return {"from": row["lo"], "to": row["hi"], "complete": bool(row["lo"] and row["lo"] <= FIRST_DAY)}


def _span(conn, where: str, params: list) -> tuple[int, str | None, str | None]:
    """A szűrt minta mérete és tényleges Battle-nap tartománya."""
    r = conn.execute(f"SELECT COUNT(*) n, MIN(battle_day) lo, MAX(battle_day) hi FROM matches WHERE {where}",
                     params).fetchone()
    return r["n"], r["lo"], r["hi"]


def _frame(conn, applied: dict, sample_size: int, lo: str | None, hi: str | None,
           extra_notes: list[str] | None = None) -> dict:
    # csak azok az események, amelyeken a TÉNYLEGES minta átnyúlik
    notes = SCHED.notes_for(lo, hi, applied.get("venue")) if lo else []
    if 0 < sample_size < 20:
        notes.append(f"Small sample ({sample_size}); treat percentages as unreliable.")
    cov = _coverage(conn)
    if not cov["complete"]:
        notes.append(f"Data currently only goes back to {cov['from']}; the backfill is still running.")
    stale = _is_stale(conn)
    if stale:
        notes.append("Latest matches may be missing (no successful refresh in the last 10 minutes); "
                     "see sync_status.")
    upd = updates.status(conn)
    if upd["update_available"]:
        notes.append(f"A newer esoccer-battle-mcp version ({upd['latest_release']}) is available; "
                     "see sync_status for the release notes and how to update.")
    return {"sample_size": sample_size, "filters_applied": applied, "coverage": cov,
            "stale": stale, "notes": notes + (extra_notes or [])}


def _is_stale(conn) -> bool:
    last = db.get_meta(conn, "last_sync")
    if not last:
        return True
    return datetime.now(timezone.utc) - datetime.fromisoformat(last) > STALE_AFTER


def _backfill(conn) -> dict:
    today = db.today_battle_day()
    total = (date.fromisoformat(today) - date.fromisoformat(FIRST_DAY)).days + 1
    missing = missing_days(conn, FIRST_DAY, today)
    return {"first_day": FIRST_DAY, "days_total": total, "days_missing": len(missing),
            "progress_pct": round(100 * (total - len(missing)) / total, 1),
            "running": bool(SYNCER and SYNCER.backfill_running),
            "sync_thread_alive": bool(SYNCER and SYNCER.thread.is_alive()),
            # több kliens esetén csak egy folyamat gyűjt; a többi ugyanazt az adatbázist olvassa
            "this_process_collects": bool(SYNCER and SYNCER.is_collector)}


TEAM_KEYS = {"team", "team_a", "team_b", "opponent_team"}


def _did_you_mean(conn, **names: str | None) -> dict:
    """Az adatbázisban nem létező játékos-/csapatnevekhez hasonló nevek (elírás, kis/nagybetű)."""
    out = {}
    for key, value in names.items():
        if not value:
            continue
        cols = ("team1", "team2") if key in TEAM_KEYS else ("player1", "player2")
        exists = conn.execute(f"SELECT 1 FROM matches WHERE {cols[0]} = ? COLLATE NOCASE "
                              f"OR {cols[1]} = ? COLLATE NOCASE LIMIT 1", (value, value)).fetchone()
        if exists:
            continue
        pool = {r[0] for r in conn.execute(f"SELECT {cols[0]} FROM matches UNION SELECT {cols[1]} FROM matches")}
        lower = {n.lower(): n for n in pool if n}
        v = value.lower()
        close = [lower[n] for n in difflib.get_close_matches(v, lower, n=5, cutoff=0.6)]
        contains = sorted(n for k, n in lower.items() if v in k and n not in close)[:5]
        out[key] = {"not_found": value, "suggestions": close + contains}
    return out


def _side_filter(column_pair: tuple[str, str], value: str) -> tuple[str, list]:
    a, b = column_pair
    return f"({a} = ? COLLATE NOCASE OR {b} = ? COLLATE NOCASE)", [value, value]


MATCH_COLUMNS = ("battle_day, local_date, local_time, venue, format, turnus, session, "
                 "player1, team1, player2, team2, score1, score2")


@mcp.tool(annotations=READ_ONLY)
def query_matches(
    player: Annotated[str | None, Field(description="Player nickname (either side)")] = None,
    team: Annotated[str | None, Field(description="Team name (either side)")] = None,
    opponent: Annotated[str | None, Field(description="Opponent player (with player) or team (with team)")] = None,
    limit: Annotated[int, Field(description="Max rows, up to 500")] = 100,
    order: Literal["newest", "oldest"] = "newest",
    since: Since = None, until: Until = None, last_days: LastDays = None, venue: Venue = None,
    format: Format = None, session: Session = None, turnus: Turnus = None,
    time_from: TimeFrom = None, time_to: TimeTo = None, era: Era = "current",
) -> dict:
    """Raw match rows. Use when the other tools do not answer the question directly;
    for aggregates prefer player_h2h, team_h2h, market_stats or player_form."""
    where, params, applied = _match_where(player, team, opponent, (
        since, until, last_days, venue, format, session, turnus, time_from, time_to, era))
    limit = max(1, min(limit, MAX_ROWS))
    direction = "DESC" if order == "newest" else "ASC"
    conn = db.connect()
    try:
        total, lo, hi = _span(conn, where, params)
        rows = conn.execute(f"SELECT {MATCH_COLUMNS} FROM matches WHERE {where} "
                            f"ORDER BY start_utc {direction} LIMIT ?", params + [limit]).fetchall()
        out = _frame(conn, applied, total, lo, hi)
        if total == 0:
            out["did_you_mean"] = _did_you_mean(
                conn, player=player, team=team,
                **({"opponent": opponent} if player else {"opponent_team": opponent}))
        out["matches"] = [dict(r) for r in rows]
        out["truncated"] = total > len(rows)
        return out
    finally:
        conn.close()


def _export_path(path: str | None) -> Path:
    if path:
        target = Path(path).expanduser()
        if target.suffix.lower() != ".csv":
            target = target / f"battle_matches_{datetime.now():%Y%m%d_%H%M%S}.csv"
    else:
        target = (Path.home() / "Downloads" / "esoccer-battle-exports"
                  / f"battle_matches_{datetime.now():%Y%m%d_%H%M%S}.csv")
    stem, i = target.stem, 1
    while target.exists():                     # létező fájlt soha nem írunk felül
        target = target.with_name(f"{stem}_{i}.csv")
        i += 1
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


@mcp.tool(annotations=WRITES_FILE)
def export_matches(
    player: Annotated[str | None, Field(description="Player nickname (either side)")] = None,
    team: Annotated[str | None, Field(description="Team name (either side)")] = None,
    opponent: Annotated[str | None, Field(description="Opponent player (with player) or team (with team)")] = None,
    path: Annotated[str | None, Field(description="Target .csv file or folder; default "
                                                  "~/Downloads/esoccer-battle-exports/")] = None,
    since: Since = None, until: Until = None, last_days: LastDays = None, venue: Venue = None,
    format: Format = None, session: Session = None, turnus: Turnus = None,
    time_from: TimeFrom = None, time_to: TimeTo = None, era: Era = "current",
) -> dict:
    """Write every match matching the filters to a CSV file on the user's computer (no row limit),
    oldest first, for analysis with the user's own code or a spreadsheet. Never overwrites a file."""
    where, params, applied = _match_where(player, team, opponent, (
        since, until, last_days, venue, format, session, turnus, time_from, time_to, era))
    columns = [c.strip() for c in MATCH_COLUMNS.split(",")]
    conn = db.connect()
    try:
        rows = conn.execute(f"SELECT {MATCH_COLUMNS} FROM matches WHERE {where} ORDER BY start_utc",
                            params).fetchall()
        if not rows:
            out = _frame(conn, applied, 0, None, None)
            out["did_you_mean"] = _did_you_mean(conn, player=player, team=team)
            out["path"] = None
            return out
        target = _export_path(path)
        with target.open("w", newline="", encoding="utf-8-sig") as f:   # BOM: Excel így jól nyitja
            w = csv.writer(f)
            w.writerow(columns)
            w.writerows(tuple(r) for r in rows)
        out = _frame(conn, applied, len(rows), rows[0]["battle_day"], rows[-1]["battle_day"])
        out.update({"path": str(target), "rows": len(rows), "columns": columns})
        return out
    finally:
        conn.close()


def _match_where(player, team, opponent, filters: tuple) -> tuple[str, list, dict]:
    where, params, applied = _filters(*filters)
    for value, cols, key in ((player, ("player1", "player2"), "player"), (team, ("team1", "team2"), "team")):
        if value:
            clause, p = _side_filter(cols, value)
            where += " AND " + clause
            params += p
            applied[key] = value
    if opponent:
        if not (player or team):
            raise ValueError("opponent needs player or team")
        if player:
            where += (" AND ((player1 = ? COLLATE NOCASE AND player2 = ? COLLATE NOCASE)"
                      " OR (player2 = ? COLLATE NOCASE AND player1 = ? COLLATE NOCASE))")
            params += [player, opponent, player, opponent]
        else:
            where += (" AND ((team1 = ? COLLATE NOCASE AND team2 = ? COLLATE NOCASE)"
                      " OR (team2 = ? COLLATE NOCASE AND team1 = ? COLLATE NOCASE))")
            params += [team, opponent, team, opponent]
        applied["opponent"] = opponent
    return where, params, applied


ROW_COLUMNS = ("battle_day, local_date, local_time, venue, format, turnus, session, "
               "player1, team1, player2, team2, score1, score2, start_utc")


def _pair_query(cols: tuple[str, str], a: str, b: str, last_n: int, filters: tuple) -> tuple:
    where, params, applied = _filters(*filters)
    c1, c2 = cols
    where += (f" AND (({c1} = ? COLLATE NOCASE AND {c2} = ? COLLATE NOCASE)"
              f" OR ({c1} = ? COLLATE NOCASE AND {c2} = ? COLLATE NOCASE))")
    params += [a, b, b, a]
    sql = f"SELECT {ROW_COLUMNS} FROM matches WHERE {where} ORDER BY start_utc DESC LIMIT ?"
    return sql, params + [max(1, last_n)], applied


def _h2h(cols, a, b, last_n, filters, keys) -> dict:
    sql, params, applied = _pair_query(cols, a, b, last_n, filters)
    applied.update({keys[0]: a, keys[1]: b, "last_n": last_n})
    conn = db.connect()
    try:
        rows = [dict(r) for r in conn.execute(sql, params)]
        lo = min((r["battle_day"] for r in rows), default=None)
        hi = max((r["battle_day"] for r in rows), default=None)
        out = _frame(conn, applied, len(rows), lo, hi)
        if not rows:
            out["did_you_mean"] = _did_you_mean(conn, **{keys[0]: a, keys[1]: b})
            return out
        out["perspective"] = f"'a' = {a}, 'b' = {b}"
        out.update(stats.head_to_head(stats.oriented(rows, a, cols)))
        return out
    finally:
        conn.close()


@mcp.tool(annotations=READ_ONLY)
def player_h2h(
    player_a: Annotated[str, Field(description="First player nickname")],
    player_b: Annotated[str, Field(description="Second player nickname")],
    last_n: Annotated[int, Field(description="Use the most recent N meetings")] = 50,
    since: Since = None, until: Until = None, last_days: LastDays = None, venue: Venue = None,
    format: Format = None, session: Session = None, turnus: Turnus = None,
    time_from: TimeFrom = None, time_to: TimeTo = None, era: Era = "current",
) -> dict:
    """Head-to-head record of two players against each other: wins/draws/losses, goal averages,
    over/under rate per line, both-teams-to-score, split by venue and session, last 10 meetings."""
    return _h2h(("player1", "player2"), player_a, player_b, last_n,
                (since, until, last_days, venue, format, session, turnus, time_from, time_to, era),
                ("player_a", "player_b"))


@mcp.tool(annotations=READ_ONLY)
def team_h2h(
    team_a: Annotated[str, Field(description="First team name")],
    team_b: Annotated[str, Field(description="Second team name")],
    last_n: Annotated[int, Field(description="Use the most recent N meetings")] = 50,
    since: Since = None, until: Until = None, last_days: LastDays = None, venue: Venue = None,
    format: Format = None, session: Session = None, turnus: Turnus = None,
    time_from: TimeFrom = None, time_to: TimeTo = None, era: Era = "current",
) -> dict:
    """Head-to-head record of two teams regardless of who controlled them (player names are shown
    in last_matches). Same statistics as player_h2h. Note: in some leagues players are bound to
    teams for a period (see data_notes), then team stats effectively measure the player."""
    return _h2h(("team1", "team2"), team_a, team_b, last_n,
                (since, until, last_days, venue, format, session, turnus, time_from, time_to, era),
                ("team_a", "team_b"))


def _subject_rows(conn, filters: tuple, player: str | None, team: str | None,
                  opponent: str | None, limit: int | None = None) -> tuple[list[dict], dict, tuple]:
    """Szűrt sorok az alany (játékos / csapat / nincs) szemszögéből, legújabb elöl."""
    where, params, applied = _filters(*filters)
    if player and team:
        raise ValueError("Give either player or team, not both")
    cols = ("team1", "team2") if team else ("player1", "player2")
    subject = player or team
    if subject:
        c1, c2 = cols
        if opponent:
            where += (f" AND (({c1} = ? COLLATE NOCASE AND {c2} = ? COLLATE NOCASE)"
                      f" OR ({c1} = ? COLLATE NOCASE AND {c2} = ? COLLATE NOCASE))")
            params += [subject, opponent, opponent, subject]
            applied["opponent"] = opponent
        else:
            where += f" AND ({c1} = ? COLLATE NOCASE OR {c2} = ? COLLATE NOCASE)"
            params += [subject, subject]
        applied["player" if player else "team"] = subject
    elif opponent:
        raise ValueError("opponent needs player or team")
    sql = f"SELECT {ROW_COLUMNS} FROM matches WHERE {where} ORDER BY start_utc DESC"
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = stats.oriented([dict(r) for r in conn.execute(sql, params)], subject, cols)
    for r in rows:
        r["_subject"] = "player" if player else "team"
    span = (min((r["battle_day"] for r in rows), default=None),
            max((r["battle_day"] for r in rows), default=None))
    return rows, applied, span


@mcp.tool(annotations=READ_ONLY)
def market_stats(
    market: Annotated[Literal["over_under", "btts", "1x2"], Field(description="Market to measure")],
    line: Annotated[float | None, Field(description="Total-goals line for over_under, e.g. 6.5. "
                                                    "Omit to get every line (only without group_by)")] = None,
    player: Annotated[str | None, Field(description="Subject player: only their matches, 'home' = their side")] = None,
    team: Annotated[str | None, Field(description="Subject team: only its matches, 'home' = its side")] = None,
    opponent: Annotated[str | None, Field(description="Restrict to matches against this player/team")] = None,
    group_by: Literal["none", "venue", "session", "turnus", "month", "week", "opponent"] = "none",
    min_sample: Annotated[int, Field(description="Drop groups smaller than this")] = 1,
    since: Since = None, until: Until = None, last_days: LastDays = None, venue: Venue = None,
    format: Format = None, session: Session = None, turnus: Turnus = None,
    time_from: TimeFrom = None, time_to: TimeTo = None, era: Era = "current",
) -> dict:
    """Hit rates of a market (over/under a line, both teams to score, 1X2) for a player, a pair, a
    team, or simply everything matching the filters (e.g. a whole venue or turnus), optionally split
    by venue, session, turnus, month, week or opponent. Without a subject 'home' means player1."""
    if market == "over_under" and line is None and group_by != "none":
        raise ValueError("line is required for over_under when group_by is used")
    if line is not None and (line * 2) % 2 != 1:
        raise ValueError("line must end in .5, e.g. 6.5")
    if group_by == "opponent" and not (player or team):
        raise ValueError("group_by='opponent' needs player or team")
    conn = db.connect()
    try:
        rows, applied, (lo, hi) = _subject_rows(
            conn, (since, until, last_days, venue, format, session, turnus, time_from, time_to, era),
            player, team, opponent)
        applied.update({"market": market, "group_by": group_by})
        if line is not None:
            applied["line"] = line
        out = _frame(conn, applied, len(rows), lo, hi)
        if not rows:
            out["did_you_mean"] = _did_you_mean(conn, player=player, team=team)
            return out
        out["perspective"] = (f"home = {player or team}" if (player or team) else "home = player1")
        out.update(stats.market_stats(rows, market, line, group_by, max(1, min_sample)))
        return out
    finally:
        conn.close()


@mcp.tool(annotations=READ_ONLY)
def player_form(
    player: Annotated[str, Field(description="Player nickname")],
    last_n: Annotated[int, Field(description="Window size: the last N matches vs the N before them")] = 20,
    since: Since = None, until: Until = None, last_days: LastDays = None, venue: Venue = None,
    format: Format = None, session: Session = None, turnus: Turnus = None,
    time_from: TimeFrom = None, time_to: TimeTo = None, era: Era = "current",
) -> dict:
    """Recent form of a player: the last N matches (W/D/L, goals scored/conceded, total goals, BTTS)
    compared with the N matches before them, plus the last N match rows."""
    last_n = max(1, min(last_n, MAX_ROWS // 2))
    conn = db.connect()
    try:
        rows, applied, (lo, hi) = _subject_rows(
            conn, (since, until, last_days, venue, format, session, turnus, time_from, time_to, era),
            player, None, None, limit=2 * last_n)
        applied["last_n"] = last_n
        out = _frame(conn, applied, len(rows[:last_n]), lo, hi)
        if not rows:
            out["did_you_mean"] = _did_you_mean(conn, player=player)
            return out
        out.update(stats.player_form(rows, last_n))
        return out
    finally:
        conn.close()


class Condition(BaseModel):
    """One pre-match condition; all conditions must hold (AND)."""
    feature: Literal[tuple(stats.FEATURES)] = Field(description=(
        "p1_/p2_ + avg_total | avg_scored | avg_conceded | over_rate | win_rate (the player's last n "
        "matches before kick-off; rates in %), or h2h_avg_total | h2h_over_rate | h2h_count "
        "(previous meetings of the two players). p1 = the subject player, or player1 without one."))
    n: int = Field(10, ge=1, le=200, description="Window: the last n matches before kick-off")
    op: Literal[">", ">=", "<", "<=", "between"]
    value: float | list[float] = Field(description="Threshold, or [low, high] for between")
    line: float | None = Field(None, description="Line for *_over_rate (defaults to the rule's line)")


@mcp.tool(annotations=READ_ONLY)
def test_rule(
    market: Literal["over_under", "btts", "1x2"],
    pick: Annotated[Literal["over", "under", "yes", "no", "home", "draw", "away"],
                    Field(description="over/under (over_under), yes/no (btts), home/draw/away (1x2; home = p1)")],
    conditions: Annotated[list[Condition], Field(description="Pre-match conditions, all must hold")],
    line: Annotated[float | None, Field(description="Total-goals line, required for over_under")] = None,
    player: Annotated[str | None, Field(description="Only this player's matches; p1 is always this player")] = None,
    history_scope: Annotated[Literal["all", "same_venue", "same_session"],
                             Field(description="Which earlier matches feed the features")] = "all",
    min_history: Annotated[int, Field(description="Skip a match if a feature has fewer earlier matches")] = 5,
    group_by: Literal["none", "month", "week", "venue", "session", "turnus", "era"] = "month",
    since: Since = None, until: Until = None, last_days: LastDays = None, venue: Venue = None,
    format: Format = None, session: Session = None, turnus: Turnus = None,
    time_from: TimeFrom = None, time_to: TimeTo = None, era: Era = "current",
) -> dict:
    """Back-test the user's own rule on all data, point-in-time: for every match that passes the
    filters, features are computed ONLY from matches played before it, then the conditions decide if
    the rule fires and the market outcome is checked. Returns hit rate, break-even odds (1 / hit rate)
    and a baseline (the same market without the conditions) plus a split for stability.
    There are no odds in the data: tell the user to compare break_even_odds with their bookmaker's
    odds, and flag samples under ~50 as unreliable."""
    valid_picks = {"over_under": {"over", "under"}, "btts": {"yes", "no"}, "1x2": {"home", "draw", "away"}}
    if pick not in valid_picks[market]:
        raise ValueError(f"pick for {market} must be one of {sorted(valid_picks[market])}")
    if not conditions:
        raise ValueError("At least one condition is required (use market_stats for unconditional rates)")
    for ln in [line] + [c.line for c in conditions]:
        if ln is not None and (ln * 2) % 2 != 1:
            raise ValueError("lines must end in .5, e.g. 6.5")
    if market == "over_under" and line is None:
        raise ValueError("line is required for over_under")
    conds = []
    for c in conditions:
        if (c.op == "between") != isinstance(c.value, list) or (isinstance(c.value, list) and len(c.value) != 2):
            raise ValueError("between needs value [low, high]; other operators need a single number")
        if c.feature.endswith("over_rate") and (c.line or line) is None:
            raise ValueError(f"{c.feature} needs a line")
        conds.append(c.model_dump())

    where, params, applied = _filters(since, until, last_days, venue, format, session, turnus,
                                      time_from, time_to, era)
    if player:
        where += " AND (player1 = ? COLLATE NOCASE OR player2 = ? COLLATE NOCASE)"
        params += [player, player]
        applied["player"] = player
    applied.update({"market": market, "pick": pick, "history_scope": history_scope,
                    "min_history": min_history, **({"line": line} if line is not None else {})})
    conn = db.connect()
    try:
        evaluate = {r[0] for r in conn.execute(f"SELECT id FROM matches WHERE {where}", params)}
        span = conn.execute(f"SELECT MIN(battle_day), MAX(battle_day) FROM matches WHERE {where}",
                            params).fetchone()
        # az előzmény a teljes korszakból jön (nem csak a szűrt meccsekből), de korszakhatáron nem lép át
        e = SCHED.era(era) if era != "all" else None
        hist_where = "battle_day >= ?" + (" AND battle_day <= ?" if e and e.get("to") else "") if e else "1=1"
        hist_params = ([e["from"]] + ([e["to"]] if e.get("to") else [])) if e else []
        rows = [dict(r) for r in conn.execute(
            "SELECT id, battle_day, local_time, venue, format, turnus, session, player1, player2,"
            f" score1, score2 FROM matches WHERE {hist_where} ORDER BY start_utc, id", hist_params)]

        def era_of(day: str) -> str:
            return next((e["id"] for e in reversed(SCHED.eras) if e["from"] <= day), "before_first_era")

        result = stats.evaluate_rule(rows, evaluate, player, conds, market, line, pick, history_scope,
                                     max(1, min_history), group_by, era_of)
        out = _frame(conn, applied, result["matched"], span[0], span[1])
        if not evaluate and player:
            out["did_you_mean"] = _did_you_mean(conn, player=player)
        out["conditions"] = conds
        out.update(result)
        return out
    finally:
        conn.close()


@mcp.tool(annotations=READ_ONLY)
def data_notes(venue: Venue = None) -> dict:
    """Context needed to interpret the data: eras (game versions), dated events (schedule changes,
    new leagues, fixed player-team periods), venues with format and current block schedule, and the
    turnus -> session map. Read this before analysing windows longer than a few days."""
    if venue and venue not in SCHED.venues:
        raise ValueError(f"Unknown venue {venue!r}. Valid values: {', '.join(SCHED.venues)}")
    venues = {}
    for name, v in SCHED.venues.items():
        if venue and name != venue:
            continue
        latest = v["menetrend"][-1]
        group = v["turnus_csoport"]
        venues[name] = {
            "format": SCHED.format_of(name),
            "turnus_group": group,
            "schedule_since": latest["tol"],
            **({"active_until": latest["ig"]} if latest.get("ig") else {}),
            "blocks": [{"turnus": int(t), "start": hhmm, "session": SCHED.session_map[group][t]}
                       for t, hhmm in sorted(latest["turnusok"].items(), key=lambda kv: int(kv[0]))],
            "schedule_history": [e["tol"] for e in v["menetrend"]],
        }
    return {
        "eras": SCHED.eras,
        "current_era": SCHED.current_era()["id"],
        "events": [e for e in SCHED.events if not venue or venue in e["venues"]],
        "venues": venues,
        "session_map": SCHED.session_map,
        "not_collected": sorted(SCHED.ignored_venues),
        "time_zone": "Europe/Budapest",
        "battle_day": "Runs from ~06:00 to ~06:00 next morning; matches after midnight belong to the previous day.",
    }


@mcp.tool(annotations=READ_ONLY)
def sync_status() -> dict:
    """State of the local database: freshness, coverage, unknown venues, unmatched blocks and
    the last error. Use when data freshness is in doubt or a response was marked stale."""
    conn = db.connect()
    try:
        latest = conn.execute("SELECT start_utc, venue FROM matches ORDER BY start_utc DESC LIMIT 1").fetchone()
        unmatched = conn.execute(
            "SELECT venue, battle_day, substr(start_local, 12) AS start FROM tournaments t "
            "WHERE EXISTS (SELECT 1 FROM matches m WHERE m.tournament_id = t.id AND m.turnus IS NULL) "
            "ORDER BY battle_day DESC LIMIT 20").fetchall()
        return {
            "version": __version__,
            **updates.status(conn),
            "db_path": str(db.db_path()),
            "matches_total": conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0],
            "matches_by_venue": {r["venue"]: r["n"] for r in conn.execute(
                "SELECT venue, COUNT(*) n FROM matches GROUP BY venue ORDER BY n DESC")},
            "latest_match": ({"kickoff_local": to_local(latest["start_utc"]).strftime("%Y-%m-%d %H:%M"),
                              "venue": latest["venue"]} if latest else None),
            "coverage": _coverage(conn),
            "backfill": _backfill(conn),
            "snapshot": {"last_complete_day": db.get_meta(conn, "snapshot_day"),
                         "loaded_at": db.get_meta(conn, "snapshot_loaded_at")},
            "stale": _is_stale(conn),
            "unknown_venues": [dict(r) for r in conn.execute("SELECT * FROM unknown_venues")
                               if r["venue"] not in SCHED.ignored_venues],
            "unmatched_blocks": [dict(r) for r in unmatched],
            "last_sync": db.get_meta(conn, "last_sync"),
            "last_error": db.get_meta(conn, "last_error"),
        }
    finally:
        conn.close()


def main() -> None:
    import sys
    if sys.argv[1:2] == ["sync"]:
        # karbantartói parancs (Actions): előző csomag + hiányzó napok az API-ból, majd kilép
        conn = db.connect()
        try:
            today = db.today_battle_day()
            snapshot.bootstrap(conn, FIRST_DAY, today)
            reclassify(conn, SCHED)
            for d in missing_days(conn, FIRST_DAY, today):
                print(d, fetch_day(conn, SCHED, d), flush=True)
        finally:
            conn.close()
        return
    if sys.argv[1:2] == ["build-snapshot"]:
        # karbantartói parancs: a data-latest kiadás fájljai (battle.db.gz + manifest.json)
        out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("snapshot")
        conn = db.connect()
        try:
            print(json.dumps(snapshot.build(conn, out), indent=2))
        finally:
            conn.close()
        return
    mcp.run(show_banner=False)


if __name__ == "__main__":
    main()
