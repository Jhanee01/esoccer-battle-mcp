"""Battle day, turnus, session and format classification from the bundled schedule files."""

import hashlib
import json
import re
from datetime import date, datetime, timedelta
from importlib import resources
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Budapest")
DAY_START_MIN = 6 * 60          # 06:00 előtti idő az előző Battle-naphoz tartozik (első blokk: Anfield 06:00, utolsó hajnali: Wembley 05:10)
TOLERANCE_MIN = 10              # ennél nagyobb eltérés = menetrend-változás, jelezni kell
DST_SHIFT_MIN = 60              # óraátállítás éjszakáján a hajnali blokkok egy órával elcsúsznak
FORMATS = {"8p": "8min", "12p": "12min"}

_NAME_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})\s*$")


def _read(name: str) -> bytes:
    return resources.files("esoccer_battle_mcp").joinpath("data", name).read_bytes()


def _day_minutes(hhmm: str) -> int:
    """Perc éjféltől, a Battle-nap sorrendjében (06:00 előtti idő a nap végére kerül)."""
    h, m = map(int, hhmm.split(":"))
    total = h * 60 + m
    return total + 1440 if total < DAY_START_MIN else total


def to_local(start_utc: str) -> datetime:
    return datetime.fromisoformat(start_utc.replace("Z", "+00:00")).astimezone(TZ)


def is_dst_night(day: str) -> bool:
    """Igaz, ha a Battle-nap éjszakáján óraátállítás van (a nap és a másnap dél UTC-eltérése más)."""
    d = date.fromisoformat(day)
    nxt = d + timedelta(days=1)
    return (datetime(d.year, d.month, d.day, 12, tzinfo=TZ).utcoffset()
            != datetime(nxt.year, nxt.month, nxt.day, 12, tzinfo=TZ).utcoffset())


def battle_day(tournament_name: str | None, start_local: datetime) -> str:
    """A torna nevének végén álló dátum; ha nincs, 06:00 előtti kezdés az előző nap."""
    m = _NAME_DATE.search(tournament_name or "")
    if m:
        return m.group(1)
    d = start_local.date()
    if start_local.hour * 60 + start_local.minute < DAY_START_MIN:
        d -= timedelta(days=1)
    return d.isoformat()


class Schedule:
    def __init__(self, menetrend: dict, eras: dict, digest: str):
        self.venues = menetrend["palyak"]
        self.session_map = menetrend["napszak_turnus"]
        self.eras = eras["eras"]
        self.events = eras["events"]
        self.digest = digest

    @classmethod
    def load(cls) -> "Schedule":
        raw = _read("menetrend.json")
        return cls(json.loads(raw), json.loads(_read("eras.json")), hashlib.sha256(raw).hexdigest())

    def format_of(self, venue: str) -> str | None:
        v = self.venues.get(venue)
        return FORMATS[v["formatum"]] if v else None

    def _entry(self, venue: str, day: str) -> dict | None:
        valid = [e for e in self.venues[venue]["menetrend"]
                 if e["tol"] <= day and (not e.get("ig") or day <= e["ig"])]
        return valid[-1] if valid else None

    def classify(self, venue: str, day: str, local_hhmm: str) -> tuple[int | None, str | None]:
        """(turnus, session) a blokk kezdése alapján; (None, None), ha nem illeszkedik."""
        if venue not in self.venues:
            return None, None
        entry = self._entry(venue, day)
        if not entry:
            return None, None
        start = _day_minutes(local_hhmm)
        dst = is_dst_night(day)
        best, best_diff = None, TOLERANCE_MIN + 1
        for turnus, hhmm in entry["turnusok"].items():
            slot = _day_minutes(hhmm)
            # az eltolás csak a 02:00 utáni hajnali blokkokat érinti
            shifts = (0, DST_SHIFT_MIN, -DST_SHIFT_MIN) if dst and slot >= 1440 + 120 else (0,)
            for shift in shifts:
                diff = abs(slot + shift - start)
                if diff < best_diff:
                    best, best_diff = int(turnus), diff
        if best is None:
            return None, None
        group = self.venues[venue]["turnus_csoport"]
        return best, self.session_map[group][str(best)]

    # --- korszakok ---------------------------------------------------------

    def current_era(self) -> dict:
        return self.eras[-1]

    def era(self, era_id: str) -> dict | None:
        if era_id == "current":
            return self.current_era()
        return next((e for e in self.eras if e["id"] == era_id), None)

    def notes_for(self, since: str | None, until: str | None, venue: str | None = None) -> list[str]:
        """Figyelmeztetések az ablakon belül eső eseményekről és korszak-határokról."""
        lo = since or "0000-00-00"
        hi = until or date.max.isoformat()
        notes = []
        for e in self.eras[1:]:
            if lo < e["from"] <= hi:
                notes.append(f"Window crosses {e['from']}: new era {e['id']}. {e['description']}")
        for ev in self.events:
            if venue and venue not in ev["venues"]:
                continue
            if lo < ev["date"] <= hi:
                notes.append(f"Window crosses {ev['date']} ({', '.join(ev['venues'])}): {ev['description']}")
            if ev.get("until") and lo <= ev["until"] < hi:
                notes.append(f"Window crosses {ev['until']} (end of: {ev['description']})")
        return notes
