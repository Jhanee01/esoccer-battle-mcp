import json
from datetime import datetime, timedelta, timezone

from esoccer_battle_mcp import db, updates

RELEASES = [
    {"tag_name": "data-latest", "prerelease": False},
    {"tag_name": "v1.2.0", "draft": True},
    {"tag_name": "v1.1.0", "body": "Fixes", "html_url": "u"},
    {"tag_name": "v1.10.0", "prerelease": True},
    {"tag_name": "v1.0.3"},
]


def test_latest_ignores_data_and_drafts():
    assert updates.latest_of(RELEASES)["tag_name"] == "v1.1.0"
    assert updates.latest_of([{"tag_name": "data-latest"}]) is None


def test_status_reports_newer_version(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "u.db")
    assert updates.status(conn)["update_available"] is False
    db.set_meta(conn, "latest_release", json.dumps({"tag": "v99.0.0", "notes": "New", "url": "u"}))
    s = updates.status(conn)
    assert s["update_available"] and s["release_notes"] == "New" and "@v99.0.0" in s["how_to_update"]
    db.set_meta(conn, "latest_release", json.dumps({"tag": "v0.0.1"}))
    assert updates.status(conn)["update_available"] is False


def test_check_at_most_every_6_hours(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "u.db")
    monkeypatch.delenv("ESOCCER_BATTLE_NO_UPDATE_CHECK")
    calls = []

    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self): return json.dumps(RELEASES).encode()

    monkeypatch.setattr(updates.urllib.request, "urlopen", lambda req, timeout: calls.append(1) or Resp())
    now = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
    updates.check(conn, now)
    updates.check(conn, now + timedelta(hours=1))
    assert len(calls) == 1 and json.loads(db.get_meta(conn, "latest_release"))["tag"] == "v1.1.0"
    updates.check(conn, now + timedelta(hours=7))
    assert len(calls) == 2


def test_check_network_error_is_silent(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "u.db")
    monkeypatch.delenv("ESOCCER_BATTLE_NO_UPDATE_CHECK")

    def boom(req, timeout):
        raise OSError("offline")
    monkeypatch.setattr(updates.urllib.request, "urlopen", boom)
    updates.check(conn)
    assert db.get_meta(conn, "latest_release") is None
