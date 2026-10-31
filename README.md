# esoccer-battle-mcp

An MCP server that gives your AI assistant (Claude Desktop, Claude Code, Codex) the full match history of
**Esoccer Battle** (EA FC esports, 8 and 12 minute matches) since 2025-11-11, kept up to date automatically.
Use it to invent your own betting-strategy ideas and measure them on real data.

**What it does not do:** it has no tips, no strategies, no odds and no bookmaker data. You bring the idea;
the server tells you how it would have performed.

## Install

Requires [uv](https://docs.astral.sh/uv/getting-started/installation/). Nothing else: uv fetches Python and
the server on first start.

### Claude Desktop

Settings → Developer → Edit Config, then add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "esoccer-battle": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/Jhanee01/esoccer-battle-mcp@v1.0.0", "esoccer-battle-mcp"]
    }
  }
}
```

Restart Claude Desktop. If it cannot find `uvx`, use its full path (`where uvx` on Windows, `which uvx` on macOS).

### Claude Code

```
claude mcp add -s user esoccer-battle -- uvx --from git+https://github.com/Jhanee01/esoccer-battle-mcp@v1.0.0 esoccer-battle-mcp
```

### Codex

Add to `~/.codex/config.toml`:

```toml
[mcp_servers.esoccer-battle]
command = "uvx"
args = ["--from", "git+https://github.com/Jhanee01/esoccer-battle-mcp@v1.0.0", "esoccer-battle-mcp"]
required = true
```

`required = true` makes Codex wait for the server to start (a few seconds), so the tools are there in
`codex exec` runs too. Pick a model your account supports with `/model` if Codex reports the model is not
supported.

## First start

- The server downloads a ready-made data snapshot (about 12 MB, a few seconds), then fetches the last few
  days from the Battle API in the background (1–2 minutes).
- Without the snapshot (offline GitHub, firewall) the full history is fetched from the API instead. That
  takes 4–5 hours in the background; recent days come first and are usable right away.
- Tools never wait for downloads. Every answer says how far back the data goes and whether it is fresh.
- Data lives in `~/.esoccer-battle-mcp/battle.db`. Several clients can run at once: only one of them
  collects, the others read the same database.

## Example questions

- "Head to head of Bjela vs Maki in the last 30 days — over 5.5 rate?"
- "Which Stamford Bridge turnus has the highest average goals this month?"
- "Over 7.5 hit rate per venue and session since the October schedule change."
- "Test this rule: both players averaged over 3 goals scored in their last 10 matches and their previous
  meetings averaged over 7 goals — over 6.5, per month. What odds would I need?"
- "Is Ganger_29 in form? Compare their last 20 matches with the 20 before."
- "Export every San Siro match from last week to CSV."

## Tools

| Tool | What it answers |
|---|---|
| `player_h2h` | Two players' meetings: record, goals, O/U, BTTS, last results |
| `team_h2h` | The same for two teams |
| `market_stats` | Hit rate of an O/U, BTTS or 1X2 outcome, optionally grouped (venue, session, turnus, month, …) |
| `player_form` | A player's last N matches against the N before them |
| `query_matches` | The raw match list with filters (up to 500 rows) |
| `test_rule` | Point-in-time back-test of your own rule: hit rate, break-even odds, baseline, by group |
| `export_matches` | Writes the filtered matches to a CSV file for Excel |
| `data_notes` | Eras, schedule changes, venues, block times — read before long windows |
| `sync_status` | Data freshness, coverage, errors, and whether a newer version exists |

All query tools share the same filters: dates or `last_days`, venue, format (8min / 12min), session
(day / afternoon / night), turnus (block of the day), kick-off time window and era.

`test_rule` only uses matches played **before** each kick-off to compute its features, so the result is
what the rule would really have done, not hindsight.

## Updating

When a new version is out, `sync_status` (and a note in every answer) tells you, with the release notes.
Change the version in your client config (for example `@v1.0.0` → `@v1.1.0`) and restart the client. Your
local data is kept.

New releases also carry schedule and era updates (new leagues, block time changes, game version
changes), so update when you are told to.

## Settings

| Environment variable | Effect |
|---|---|
| `ESOCCER_BATTLE_DB` | Database path instead of `~/.esoccer-battle-mcp/battle.db` |
| `ESOCCER_BATTLE_NO_SYNC=1` | Do not collect, only answer from the existing database |
| `ESOCCER_BATTLE_NO_SNAPSHOT=1` | Never download the data snapshot, fetch everything from the API |
| `ESOCCER_BATTLE_NO_UPDATE_CHECK=1` | Do not check GitHub for new versions |

## Data source and limits

- Results come from the public JSON API behind football.esportsbattle.com. This project is not affiliated
  with Esports Battle; the API is unofficial and may change or disappear.
- Collected venues: Stamford Bridge, Old Trafford, Wembley, San Siro, Bernabeu (8 min) and Anfield,
  Etihad (12 min). Not collected: Hillsborough, and Emirates Stadium (a weekend-only "World Cup B"
  special event with 2-match tournaments, 2026-06-19 – 07-26).
- Sessions and turnus numbers come from a maintained block schedule (`data/menetrend.json`), not from clock
  hours. When the Battle changes its schedule, the next release fixes the classification.
- Past results do not guarantee future ones. Bet responsibly, where it is legal for you.

## License

MIT, see [LICENSE](LICENSE).
