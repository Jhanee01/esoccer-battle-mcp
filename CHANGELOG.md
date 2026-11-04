# Changelog

## v1.0.2 (2026-11-04)

- The server instructions now tell the assistant to answer only what was asked, using the numbers
  the tools return: no self-computed estimates or "expected" values, no extra tool calls, context
  or suggestions.

## v1.0.1 (2026-10-31)

- Fix: on Windows the schedule fingerprint did not match the one in the data snapshot, because git
  checks the schedule file out with different line endings there. The server then reclassified the
  whole history once after the first download. The fingerprint now ignores line endings.

## v1.0.0 (2026-10-31)

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
