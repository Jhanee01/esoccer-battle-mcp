"""FastMCP server exposing Esoccer Battle match data."""

from typing import Annotated, Literal

from fastmcp import FastMCP
from pydantic import Field

from . import __version__, db
from .schedule import Schedule, to_local

FIRST_DAY = "2025-11-11"
MAX_ROWS = 500

SCHED = Schedule.load()

mcp = FastMCP(
    "esoccer-battle",
    instructions=(
        "Esoccer Battle (FIFA/EA FC esports) 8 and 12 minute match results, collected from the public "
        "Battle API since 2025-11-11. Use it to research and measure betting-strategy ideas. There are no "
        "odds and no tips in the data. All times are Europe/Budapest local time. A Battle day runs from "
        "~06:00 to ~06:00 next morning; 'turnus' is the block number within that day and decides the "
        "session (day/afternoon/night). Check data_notes before analysing long windows."
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
    return {"sample_size": sample_size, "filters_applied": applied, "coverage": cov,
            "stale": False, "notes": notes + (extra_notes or [])}


def _side_filter(column_pair: tuple[str, str], value: str) -> tuple[str, list]:
    a, b = column_pair
    return f"({a} = ? COLLATE NOCASE OR {b} = ? COLLATE NOCASE)", [value, value]


MATCH_COLUMNS = ("battle_day, local_date, local_time, venue, format, turnus, session, "
                 "player1, team1, player2, team2, score1, score2")


@mcp.tool
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
    where, params, applied = _filters(since, until, last_days, venue, format, session, turnus,
                                      time_from, time_to, era)
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
    limit = max(1, min(limit, MAX_ROWS))
    direction = "DESC" if order == "newest" else "ASC"
    conn = db.connect()
    try:
        total, lo, hi = _span(conn, where, params)
        rows = conn.execute(f"SELECT {MATCH_COLUMNS} FROM matches WHERE {where} "
                            f"ORDER BY start_utc {direction} LIMIT ?", params + [limit]).fetchall()
        out = _frame(conn, applied, total, lo, hi)
        out["matches"] = [dict(r) for r in rows]
        out["truncated"] = total > len(rows)
        return out
    finally:
        conn.close()


@mcp.tool
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
            "db_path": str(db.db_path()),
            "matches_total": conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0],
            "matches_by_venue": {r["venue"]: r["n"] for r in conn.execute(
                "SELECT venue, COUNT(*) n FROM matches GROUP BY venue ORDER BY n DESC")},
            "latest_match": ({"kickoff_local": to_local(latest["start_utc"]).strftime("%Y-%m-%d %H:%M"),
                              "venue": latest["venue"]} if latest else None),
            "coverage": _coverage(conn),
            "days_complete": conn.execute("SELECT COUNT(*) FROM days WHERE complete = 1").fetchone()[0],
            "unknown_venues": [dict(r) for r in conn.execute("SELECT * FROM unknown_venues")],
            "unmatched_blocks": [dict(r) for r in unmatched],
            "last_sync": db.get_meta(conn, "last_sync"),
            "last_error": db.get_meta(conn, "last_error"),
        }
    finally:
        conn.close()


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
