# Changelog

## v1.0.0 — unreleased

First public release.

- Collects Esoccer Battle 8 and 12 minute results from the public Battle API into a local SQLite
  database, from 2025-11-11 (FC26), and keeps the last two Battle days fresh every 3 minutes.
- Venues: Stamford Bridge, Old Trafford, Wembley, San Siro, Bernabeu (8 min); Anfield, Etihad (12 min).
- Session and turnus from the maintained block schedule; era log with 8 dated events.
- 9 tools: `player_h2h`, `team_h2h`, `market_stats`, `player_form`, `query_matches`, `test_rule`,
  `export_matches`, `data_notes`, `sync_status`.
- Data snapshot on first start (`data-latest` release, refreshed every 5 days), API backfill as fallback.
- New-version notice in `sync_status` and in tool notes.
- Works with Claude Desktop, Claude Code and Codex.
