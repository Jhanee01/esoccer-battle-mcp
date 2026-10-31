# Changelog

## v1.0.0 (unreleased)

First public release.

- Collects Esoccer Battle 8 and 12 minute results from the public Battle API into a local SQLite
  database. History starts on 2025-11-11 (FC26), and the last two Battle days are refreshed every
  3 minutes.
- Venues: Stamford Bridge, Old Trafford, Wembley, San Siro and Bernabeu (8 min), Anfield and Etihad
  (12 min).
- Session and turnus come from the maintained block schedule. The era log starts with 8 dated events.
- 9 tools: `player_h2h`, `team_h2h`, `market_stats`, `player_form`, `query_matches`, `test_rule`,
  `export_matches`, `data_notes`, `sync_status`.
- On first start the server downloads a data snapshot (the `data-latest` release, refreshed every
  5 days). If that fails, it fetches the history from the API.
- `sync_status` and the tool notes tell you when a new version is out.
- Works with Claude Desktop, Claude Code and Codex.
