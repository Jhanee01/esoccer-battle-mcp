import csv
from collections import Counter
from datetime import datetime
from pathlib import Path

import pytest

from esoccer_battle_mcp.schedule import Schedule, battle_day

FIXTURE = Path(__file__).parent / "fixtures" / "tournaments_since_2025-11-11.csv"


@pytest.fixture(scope="module")
def sched():
    return Schedule.load()


@pytest.fixture(scope="module")
def tournaments():
    with FIXTURE.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _classify(sched, row):
    start = datetime.fromisoformat(row["start_local"])
    day = battle_day(row["name"], start)
    return day, sched.classify(row["venue"], day, start.strftime("%H:%M"))


def test_every_block_gets_a_turnus(sched, tournaments):
    missing = [r for r in tournaments if _classify(sched, r)[1][0] is None]
    assert missing == []


def test_one_block_per_turnus_per_day(sched, tournaments):
    seen = Counter()
    for r in tournaments:
        day, (turnus, _) = _classify(sched, r)
        seen[(r["venue"], day, turnus)] += 1
    assert [k for k, n in seen.items() if n > 1] == []


@pytest.mark.parametrize("venue,day,hhmm,turnus,session", [
    ("Stamford Bridge", "2026-10-28", "06:30", 1, "day"),
    ("Stamford Bridge", "2026-10-28", "15:10", 5, "afternoon"),
    ("Stamford Bridge", "2026-10-28", "02:00", 10, "night"),
    ("Stamford Bridge", "2025-12-01", "15:40", 5, "afternoon"),   # 4 blokkos időszak: T2–T5
    ("Old Trafford", "2026-10-28", "21:55", 8, "night"),
    ("Wembley", "2026-10-28", "04:50", 11, "night"),
    ("San Siro", "2026-10-28", "21:20", 8, "afternoon"),          # Latest T8 = afternoon
    ("Bernabeu", "2026-09-01", "17:14", 6, "afternoon"),
    ("Anfield", "2026-10-28", "06:00", 1, "day"),
    ("Anfield", "2026-10-28", "21:20", 6, "afternoon"),
    ("Etihad", "2026-10-28", "02:50", 8, "night"),
    # óraátállítás éjszakái (±70 perc tűrés)
    ("Stamford Bridge", "2026-03-28", "03:10", 10, "night"),
    ("Wembley", "2026-03-28", "03:40", 10, "night"),
    ("Stamford Bridge", "2026-10-24", "03:20", 11, "night"),
])
def test_known_blocks(sched, venue, day, hhmm, turnus, session):
    assert sched.classify(venue, day, hhmm) == (turnus, session)


def test_unknown_or_out_of_range(sched):
    assert sched.classify("Hillsborough", "2026-10-28", "10:00") == (None, None)
    assert sched.classify("Bernabeu", "2026-10-01", "14:40") == (None, None)   # ig: 2026-09-21
    assert sched.classify("Wembley", "2026-10-28", "12:35") == (None, None)    # két turnus között


def test_battle_day():
    assert battle_day("World Cup B 2026-10-27", datetime(2026, 10, 28, 3, 0)) == "2026-10-27"
    assert battle_day(None, datetime(2026, 10, 28, 3, 0)) == "2026-10-27"
    assert battle_day(None, datetime(2026, 10, 28, 7, 0)) == "2026-10-28"


def test_formats(sched):
    assert sched.format_of("Wembley") == "8min"
    assert sched.format_of("Anfield") == "12min"
    assert sched.format_of("Hillsborough") is None


def test_notes(sched):
    notes = sched.notes_for("2026-07-01", "2026-09-01", "Wembley")
    assert any("2026-08-05" in n for n in notes)
    assert any("2026-08-04" in n for n in notes)
    assert sched.notes_for("2026-10-20", "2026-10-28", "Wembley") == []
    assert sched.current_era()["id"] == "FC26"


def test_digest_ignores_line_endings(monkeypatch):
    from esoccer_battle_mcp import schedule
    real = schedule._read
    lf = Schedule.load().digest
    monkeypatch.setattr(schedule, "_read", lambda n: real(n).replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    assert Schedule.load().digest == lf
