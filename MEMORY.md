# MEMORY.md — Shared Project Memory

> Central source of truth for **C-TierBot** across Antigravity, Claude Code, and OpenAI Codex on all development machines.
> Before making changes, also read [`AGENTS.md`](AGENTS.md), [`design-system/MASTER.md`](design-system/MASTER.md), and [`HOST_COMMANDS.md`](HOST_COMMANDS.md).

---

## Project Overview

**C-TierBot** is a Discord bot built with Python 3.10+ and `discord.py` (`>=2.4.0`) for a competitive [survev.de](https://survev.de) community that runs 4v4 scrim queues through [NeatQueue](https://neatqueue.com).

- **Purpose:** Link Discord server members to their `survev.de` accounts via OAuth, automatically track and render visual scorecards for NeatQueue 4v4 scrim series, maintain per-player scrim statistics and seasonal leaderboards, and provide rich visual views for `survev.de` leaderboards, player profiles, head-to-head comparisons, inventories, and shop offers directly inside Discord.
- **Intended Users:** Competitive `survev.de` Discord servers (players checking stats/scrims and server administrators configuring NeatQueue result channels or operating the host console).
- **Production Environment:** Hosted on **PebbleHost** in a Linux container (`/home/container/`, Python 3.13 virtualenv) and actively developed across two computers using Antigravity, Claude Code, and OpenAI Codex.

---

## Current State

The existing features below are implemented. The queue-mention shortcut is committed in `acb524d` and its `score?` alias in `a134178`; silent suppression of repeated requests and the queue spectator fix are local changes pending deployment. The automated unit test suite currently passes `214` tests across `10` test modules via `python -m unittest discover tests`. Nine queue-stat accuracy fixes are approved backlog items, not yet implemented. See Current Focus and Handover for deployment and working-tree status.

### Slash Commands (`16` total in `leaderboard_bot.py`)

| Category | Command | Status & Verification |
| :--- | :--- | :--- |
| **Account Linking** | `/verify` | **Implemented & Verified.** Starts a DM OAuth flow against `survev.de`, polls up to 5 minutes (`VERIFICATION_TIMEOUT_SECONDS = 300`) with `429`/`5xx` retry backoff, stores `(discord_id, access_token, slug, username)` in `users`, and runs a 30-minute background `slug_backfill_loop`. |
| **Leaderboards** | `/leaderboard_weekly` | **Implemented & Verified.** 7-day `survev.de` stats (Kills, Wins, Games, Avg Damage) for verified server members with `LeaderboardView` buttons and stat dropdown. |
| **Leaderboards** | `/leaderboard_monthly` | **Implemented & Verified.** 30-day `survev.de` stats with the same interactive `LeaderboardView`. |
| **Leaderboards** | `/leaderboard_season [month]` | **Implemented & Verified.** Server-scoped 4v4 NeatQueue average damage leaderboard for a UTC calendar month (`YYYY-MM`, defaults to current UTC month). Game-weighted (`SUM(total_damage) / SUM(games_played)`), requires `total_players > 4` and `total_damage > 0`, paginated 10 players per page via `SeasonalLeaderboardView`. |
| **Leaderboards** | `/leaderboard_fries` | **Implemented & Verified.** Ranks verified server members by Golden Fries balance (`get_user_balances`). |
| **Players** | `/profile [member]` | **Implemented & Verified.** Shows cached 4v4 NeatQueue stats (`4v4 Play Time`, `All-Time Avg Damage`, `{Month} Avg Damage`, `All-Time Avg Impact`, `{Month} Avg Impact`) and live/cached `survev.de` **Squad Tier** (`F E D C B- B A- A S` + rating, `teamMode == 4`). |
| **Players** | `/compare <member_a> [member_b]` | **Implemented & Verified.** Renders a `920px` side-by-side Pillow comparison card (`generate_compare_image`) with 30-day Kills, Wins, Games, Win Rate, Avg Damage, Golden Fries, and Squad Tier. Defaults `member_b` to caller. |
| **Players** | `/history [member]` | **Implemented & Verified.** Renders a `1200px` Pillow performance card (`generate_history_image`) showing the player's last 10 finalized 4v4 queues in the server (summary pills, trend sparkline with per-point value badges, and 10-row recent queue table) with `HistoryMetricView` toggle buttons (`Damage`, `Impact`, `Kills`). |
| **Players** | `/inventory [member]` | **Implemented & Verified.** Paged `900px` Pillow inventory grid (`generate_inventory_image`) with rarity filter dropdown and Previous/Next buttons (`InventoryPaginationView`). |
| **Players** | `/goldenfries [member]` | **Implemented & Verified.** Displays a verified member's Golden Fries balance. |
| **Players** | `/market [member]` | **Implemented & Verified.** Unified shop command rendering a `900px` Pillow grid (`generate_shop_image`) with `Daily`, `Weekly`, and `All` filter buttons and pagination (`ShopPaginationView`). |
| **Queues** | `/queue_stats <match_id>` | **Implemented & Verified.** Manually computes and renders the `1600px` (`3200px` 2x-supersampled) queue result card for any NeatQueue match number (finished or ongoing), updates Hall of Fame and `player_queue_stats` if `is_final` is true, saves a 90-day snapshot for `Game breakdown`, and writes a structured diagnostic JSON log to `log/`. |
| **Queues** | `/hall_of_fame` | **Implemented & Verified.** Displays server-scoped single-queue records (`Most Kills`, `Highest Avg Damage`, `Longest Queue`) from `hall_of_fame_records`. |
| **Admin** | `/setup <channel>` | **Implemented & Verified.** Registers a channel for automated NeatQueue tracking in `guild_settings` (supports multiple channels per guild). |
| **Admin** | `/unsetup <channel>` | **Implemented & Verified.** Removes a channel from `guild_settings`. |
| **Admin** | `/reset_hall_of_fame` | **Implemented & Verified.** Clears all Hall of Fame records for the current guild. |

### Automated Systems & Console
- **Silent duplicate requests (approved 2026-11-03):** Both queue-channel triggers silently ignore requests during the shared 30-second cooldown or an active lookup. No wait reply or DM is sent. This supersedes the earlier approval of brief wait replies; ordinary stats and lookup-error replies remain unchanged.
- **Plain-text scoreboard shortcut (approved 2026-11-03):** A human message containing the word `score?` anywhere (e.g. "what is the score?", matched by `score\?`, case-insensitive; widened from whole-message match on 2026-11-03) in `queue-<digits>` triggers the same handler as a bot mention. It requires no mention and shares the existing per-channel cooldown and in-flight guard. Bot/webhook messages and DMs remain excluded; finished queues still show final stats. Requires the existing Message Content Intent.
- **Queue-channel mentions (`reply_to_queue_mention` in `leaderboard_bot.py`):** A human explicitly mentioning the bot in a channel named exactly `queue-<digits>` receives the same current stats as `/queue_stats` in a public reply, without registering the channel with `/setup`. Uses a per-channel in-flight guard and 30-second cooldown, ignores bot/webhook messages and implicit reply pings, and never changes `processed_matches` or the guild catch-up timestamp. Final results retain Game breakdown; ongoing results remain IN PROGRESS. The user explicitly approved finished-queue support and the 30-second channel cooldown with a brief wait reply on 2026-11-02.
- **Live NeatQueue Tracking (`on_message`, `on_message_edit`):** Detects NeatQueue result embeds in tracked channels, waits `QUEUE_RESULT_FETCH_DELAY_SECONDS` (`5s`), builds and posts the queue result card with `QueueResultView` (`Verify Survev Account` + `Game breakdown` buttons), and ignores message edits older than `24` hours (`QUEUE_RESULT_MAX_MESSAGE_AGE_SECONDS`).
- **Startup Catch-Up (`backfill_missed_queue_results`):** On `on_ready`, scans the last `10` NeatQueue matches per tracked guild (`STARTUP_QUEUE_BACKFILL_LIMIT = 10`) and posts any finalized matches missing from `processed_matches`.
- **Per-Game Breakdown (`QueueGamesPagerView`):** Clicking `Game breakdown` on any finalized queue card (for up to 90 days) opens an ephemeral message rendering one `1600px` card per game (`generate_queue_game_image`) with an interactive round timeline pill strip and `Previous` / `Next` buttons.
- **Host Terminal Console (`console_service.py`):** Interactive stdin loop supporting `help`, `status`, `servers`, `channels <guild_id>`, `send <channel_id> <msg>`, `dm <user_id> <msg>`, `delete <channel_id> <msg_id>`, `winrate [guild_id]`, `sql <query>`, and `exit` / `quit`. Works in both interactive terminals and non-TTY server consoles (SSH/PebbleHost).

---

## Architecture and Technical Context

### Key Files & Module Responsibilities

| File | Responsibility |
| :--- | :--- |
| [`bot_config.py`](bot_config.py) | Loads `.env`, configures constants (API URLs, limits, timeouts, palette/layout constants), and initializes the shared `bot = commands.Bot` instance (`intents.message_content = True`, `intents.members = True`). |
| [`db.py`](db.py) | SQLite connection (`leaderboard.db`), schema initialization (`init_db`), automatic column/key migrations, and all CRUD/query helpers. |
| [`leaderboard_bot.py`](leaderboard_bot.py) | Application entry point, slash command definitions, `on_ready` startup tasks, `on_message` / `on_message_edit` NeatQueue listeners, and the central `log_interaction` (`@bot.listen('on_interaction')`) button/select router. |
| [`survev_client.py`](survev_client.py) | `survev.de` HTTP client (`aiohttp`), OAuth verification loop (`run_survev_verification`), user profile/inventory/shop/match-history fetchers, `fetch_user_stats` + `extract_squad_tier`, and remote cosine asset caching (`assets_cache/`). |
| [`neatqueue_client.py`](neatqueue_client.py) | NeatQueue API client (`fetch_neatqueue_data`), Discord queue panel scanner (`find_queue_Session_Start`), and the core cross-referencing engine (`calculate_queue_match_stats`) that maps a NeatQueue match to `survev.de` round scoreboards and emits structured `diagnostics`. |
| [`queue_stats_service.py`](queue_stats_service.py) | Orchestrates `build_queue_stats_payload`, `post_queue_result`, `backfill_missed_queue_results`, `build_queue_game_payload`, `build_hall_of_fame_payload`, `build_history_payload`, and `write_queue_stats_command_log`. |
| [`leaderboard_service.py`](leaderboard_service.py) | Orchestrates weekly/monthly/fries/seasonal leaderboards, `/inventory`, `/market`, `/profile`, `/compare`, 5-minute Squad tier caching (`_tier_cache`), and bounded payload caches (`_market_cache`, `_inventory_cache`). |
| [`image_utils.py`](image_utils.py) | All Pillow (`PIL`) image generation (`generate_queue_result_image`, `generate_queue_game_image`, `generate_history_image`, `generate_compare_image`, `generate_inventory_image`, `generate_shop_image`), bundled `QuattrocentoSans-Bold.ttf` loading, and vector icon drawing (`_build_unlinked_icon`). |
| [`discord_ui.py`](discord_ui.py) | `discord.ui.View` subclasses (`LeaderboardView`, `SeasonalLeaderboardView`, `InventoryPaginationView`, `ShopPaginationView`, `QueueResultView`, `QueueGamesPagerView`, `HistoryMetricView`) and custom ID parsers. |
| [`console_service.py`](console_service.py) | Background host terminal command loop (`console_loop`, `execute_console_command`) and console-only analytics (`winrate`). |
| [`design-system/MASTER.md`](design-system/MASTER.md) | Locked visual design specification (colors, typography, canvas dimensions, spacing, component rules, and anti-patterns). |
| [`HOST_COMMANDS.md`](HOST_COMMANDS.md) | Reference documentation for host terminal commands. |

### Critical Architectural Patterns (Must Know Before Editing)

1. **Centralized Component Routing in `log_interaction` (`leaderboard_bot.py`):**
   - `discord.ui.View` methods in `discord_ui.py` use `pass` inside their `@discord.ui.button` / `@discord.ui.select` callbacks.
   - **All** interactive component clicks (`custom_id`s) are intercepted and handled in `log_interaction(interaction: discord.Interaction)` in [`leaderboard_bot.py`](leaderboard_bot.py).
   - **Rule:** Never implement logic inside both a `discord.ui.View` callback and `log_interaction`—doing so causes Discord HTTP `40060: Interaction has already been acknowledged` in production (previously hit on `queue_result_verify` and fixed in commit `100edc8`). When adding a new button or select menu, give it a deterministic `custom_id`, leave the View method body as `pass`, and route it inside `log_interaction`.

2. **NeatQueue ↔ `survev.de` Match Cross-Referencing (`neatqueue_client.py`):**
   - **Step 1 (Lookup & Finality):** Fetches recent matches from NeatQueue (`GET /api/v2/history/{guild_id}?limit=100`).
     - A match is treated as finished (`is_finished`) only when `match is not None and winner not in (None, -2)`.
     - NeatQueue uses `winner: -2` for ongoing/undecided queues that already appear in the history endpoint. Treating `winner == -2` as pending/ongoing is critical so the match window extends through `now` instead of being cut off at `start_ms + 10 minutes`.
   - **Step 2 (Roster & Time Window):**
     - **Finished queues** (`is_finished == True`) use the NeatQueue history entry's roster and `[start_ms - 60s, end_ms + 90s]` window (`QUEUE_MATCH_START_BUFFER_MS`, `QUEUE_MATCH_END_BUFFER_MS`). If `end_ms` cannot be parsed or `end_ms <= start_ms`, it falls back to `start_ms + 10m` (`QUEUE_MATCH_FALLBACK_DURATION_MS`).
     - **Ongoing queues** (`is_finished == False`, including `winner == -2`) scan recent Discord channel history (`find_queue_Session_Start`, up to 300 messages across tracked channels) for the live NeatQueue panel embed (`Queue#<id>`) to get the roster and `[panel_created_at - 60s, now + 90s]`. If the Discord panel cannot be found or read, it falls back to the pending NeatQueue history entry's roster and `start_time` (running through `now + 90s`).
   - **Step 3 (Anchor Intersection):**
     - Verified players in the roster with a known `survev_slug` (fetched on-demand if missing) act as **anchors**.
     - Each anchor's `survev.de` match history (`GET /api/user/match_history?count=30&offset=...`, up to 10 pages) is paginated until matches are older than `window_start_ms`.
     - If multiple verified anchors return non-empty GUID sets in the time window, candidate rounds are filtered by `set.intersection(*anchor_guid_sets)` (or the single anchor's set if only one anchor has games).
   - **Step 4 (Scoreboard Validation & Unlinked Resolution):**
     - Public round scoreboards (`GET /api/match_data?guid=...`) are fetched concurrently under `API_SEM = asyncio.Semaphore(5)`.
     - A round is accepted only if `team_total >= max(len(roster_A), len(roster_B), 1)` and `total_players >= max(expected_total_players - 2, 2)`.
     - Unlinked players are matched to remaining scoreboard slots by exact normalized Discord display name, prefix/substring fuzzy match, or leftover team slot assignment.
   - **Step 5 (Finality Gate):**
     - `is_final = is_finished` (NeatQueue winner not `None`/`-2`); the code does **not** also require a computed `winning_team_index` (corrected 2026-11-03), so a finished queue with a tied or unknown survev.de score is still saved. Nothing compares the computed winner with NeatQueue's declared winner. Only `is_final` results update Hall of Fame (`hall_of_fame`), per-player stats (`player_queue_stats`), and 90-day `Game breakdown` snapshots (`queue_results`).

3. **Diagnostic Logging & Privacy Contract (`queue_stats_service.py`, `neatqueue_client.py`):**
   - Every `/queue_stats <match_id>` invocation writes `log/queue_stats_<match_id>_<UTC-timestamp>.json` via `write_queue_stats_command_log`.
   - The JSON contains `diagnostics` covering:
     - Finality decision (`is_final`, `finality_reason`, `is_finished`, `neatqueue_winner`)
     - Time window and sources (`start_source`, `end_source`, `window_start_ms`, `window_end_ms`)
     - Roster source (`discord_panel` vs `neatqueue_history`, `panel_found`, `history_players_per_team`)
     - Per-anchor `survev.de` pagination status, HTTP status codes, and candidate GUIDs/timestamps
     - Per-candidate round inclusion/rejection reasons (`accepted`, `outside_time_window`, `excluded_by_anchor_intersection`, `scoreboard_missing_or_empty`, `wrong_lobby_size`) and scoreboard availability
   - Failed lookups attach collected diagnostics onto `QueueStatsError.diagnostics` before raising so failed command logs preserve everything gathered prior to the failure.
   - **Strict Privacy Rule:** Logs and diagnostics must **never** include `access_token`s, `Authorization` headers, client secrets, or raw API response bodies.

4. **Database Schema (`db.py` — `leaderboard.db`):**
   - *(Corrected against `db.py` on 2026-11-03; earlier notes used outdated table and column names.)*
   - `users (discord_id INTEGER PRIMARY KEY, access_token TEXT NOT NULL, slug TEXT, username TEXT)` — `slug` is not unique, so one survev.de account linked by two Discord accounts resolves to an arbitrary one in `get_discord_id_by_slug`.
   - `guild_settings (guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL, last_updated TEXT NOT NULL, PRIMARY KEY (guild_id, channel_id))` — auto-migrated from single-channel primary key if legacy schema is detected.
   - `processed_matches (guild_id INTEGER NOT NULL, match_id TEXT NOT NULL, processed_at TEXT NOT NULL, PRIMARY KEY (guild_id, match_id))`
   - `hall_of_fame (record_type TEXT PRIMARY KEY, value REAL NOT NULL, discord_id INTEGER, display_name TEXT, match_id TEXT NOT NULL, guild_id INTEGER NOT NULL, achieved_at TEXT NOT NULL, duration_ms INTEGER)` — keyed by record type only, so records are global rather than per server.
   - `player_queue_stats (guild_id, match_id, player_key, discord_id, display_name, username, slug, team_index, total_damage, avg_damage, games, duration_ms, match_start_ms, match_end_ms, cached_at, kills, impact, winning_team_index, team_round_wins, PRIMARY KEY (guild_id, match_id, player_key))` — written with `INSERT OR REPLACE`, so a re-run never deletes rows for players who are no longer in the result.
     - `player_key` is `"discord:{discord_id}"` for linked players or `"guest:{slug_or_name}"` for unlinked players.
     - Legacy rows recorded before `kills`, `impact`, `winning_team_index`, and `team_round_wins` were added are enriched on read in `get_player_queue_history()` from `queue_results.payload_json` if a 90-day snapshot exists.
   - `queue_results (guild_id INTEGER NOT NULL, match_id TEXT NOT NULL, snapshot TEXT NOT NULL, saved_at TEXT NOT NULL, PRIMARY KEY (guild_id, match_id))` — replaced on each final re-run; pruned automatically after `QUEUE_RESULT_RETENTION_DAYS = 90`.

---

## Requirements and Approved Decisions

The following decisions were explicitly requested or approved by the user across past sessions. Future agents must respect these and **must not** reintroduce rejected designs.

### Visual Design & UI Decisions (Locked in `design-system/MASTER.md`)
1. **Locked Design System (`design-system/MASTER.md`):**
   - All Pillow-rendered cards must follow the dark tactical aesthetic (`#18191C` canvas, `#202225` card containers, `#2B2D31` header boxes, `#F2F3F5` primary text, `#949BA4` muted text, `#2ECC71` win green, `#E74C3C` loss red, `#F1C40F` gold accent) documented in [`design-system/MASTER.md`](design-system/MASTER.md). Do not invent new color palettes or font families.
2. **Bundled Font (`QuattrocentoSans-Bold.ttf`):**
   - `QuattrocentoSans-Bold.ttf` is committed in the repository root and resolved via `_BASE_DIR = os.path.dirname(os.path.abspath(__file__))` in `bot_config.py` so Linux (PebbleHost) and Windows render identical typography without falling back to `DejaVuSans-Bold`.
3. **Queue Result Card Layout (`generate_queue_result_image`):**
   - **Ongoing status (approved 2026-11-02):** With `is_final == False`, show `IN PROGRESS` centered above the score and `Team N leads` as the subtitle (`No team leading` for no leader). Finished queues retain winner wording. `build_queue_stats_payload` passes the actual finality flag to the renderer.
   - **Supersampling:** Rendered at `2x` (`S = 2`, `3200px` internal width) and downscaled to `1600px` with `Image.Resampling.LANCZOS`.
   - **Top-Left Subtitle:** Shows `Match Duration: Xm Ys` in **bold** (`22px` base / `44px` at 2x), replacing the old `"Data courtesy of NeatQueue & survev.de APIs :)"` subtitle.
   - **Team Cards:** Rounded team containers (`radius=18`, `QUEUE_IMG_CARD_BG`) with flat-bottom tinted header banners (`QUEUE_IMG_WIN_BANNER_BG`, `QUEUE_IMG_LOSE_BANNER_BG`, `QUEUE_IMG_NEUTRAL_BANNER_BG`) and inset alternating player row pills (`radius=10`).
   - **Typography & Column Ratios:** `body_font`/`body_font_bold` = `35px`, `header_font` = `30px`, `team_header_font` = `38px`. Column x-ratios are `[0.0, 0.40, 0.60, 0.80]` without Impact and `[0.0, 0.36, 0.47, 0.64, 0.80]` when Impact is shown.
   - **Unlinked Indicator:** Unlinked players are marked with a 4x-supersampled PIL vector broken-chain icon (`_build_unlinked_icon`, `24px` in player rows, `18px` in the bottom-left footer legend), **not** `" (unlinked)"` text.
   - **REJECTED — Centered Scoreboard Container Box:** Do **not** put a bordered/tinted container box around the center `4 - 2` series score in `generate_queue_result_image`. The user explicitly rejected the boxed scoreboard design (`"hmm i wont need the scoreboard change"`).
   - **REJECTED — Impact Subtext on Main Queue Result Message:** Do **not** include `"-# Impact is survev.de's per-game impact score (0-100)..."` in the Discord message `content` of main queue results (`post_queue_result` / `/queue_stats`). The user explicitly requested removing that text below the image (commit `51b1011`).
4. **Per-Player Queue History Card (`/history`, `generate_history_image`):**
   - Shows the last 10 finalized 4v4 queues in the server with `Damage`, `Impact`, and `Kills` toggle buttons (`HistoryMetricView`).
   - Every data point on the sparkline renders a numeric value pill above the dot, and `plot_y1` is raised `22px` above `area_base_y` so minimum value pills never collide with X-axis queue numbers (`#4518`).
   - **REJECTED / REMOVED Text on `/history`:** Do **not** render `Oldest ───► Newest` in the sparkline header, and do **not** include `"Impact is survev.de's per-game score (0–100)."` in either the `/history` embed footer or the rendered card footer (commit `33b1244`).
5. **Seasonal Leaderboard Format (`/leaderboard_season`):**
   - Uses the two-line per-player format (`**#1** <@discord_id>\n┗ 💥 **Avg Damage:** 168 (136 games)` / standard list without a summary callout banner).
   - **REJECTED — Summary Callout Banner & Podium Redesign:** In commit `ee219a9`, the user explicitly reverted an experimental top summary banner (`Players • Games • Total Dmg`) and podium layout on `/leaderboard_season` (`"nvm revert back to the format before"`). Do not reintroduce it.
6. **`/profile` Embed Layout (`leaderboard_service.py`):**
   - No embed `description` (the old `"Cached queue results that matched the current-month cache rules."` text was explicitly removed in commit `7cfeb4f`).
   - Exact field row structure (commit `ff850a9`):
     - Row 1: `⏱️ 4v4 Play Time` (`inline=False`)
     - Row 2: `💥 All-Time Avg Damage` (`inline=True`), `📅 {Month} Avg Damage` (`inline=True`), `\u200b` spacer (`inline=True`)
     - Row 3: `⚡ All-Time Avg Impact` (`inline=True`), `📅 {Month} Avg Impact` (`inline=True`), `\u200b` spacer (`inline=True`)
     - Row 4 (if verified): `🏅 survev.de Squad Tier` (`inline=False`)

### Command, Caching & Operational Decisions
7. **Removed `/queueresults` Alias:** Only `/queue_stats` exists (commit `463d79d`). Do not re-add `/queueresults`.
8. **Unified `/market [member]`:** Replaced `/market daily`, `/market weekly`, and `/market all` subcommands with a single `/market [member]` command using interactive `Daily | Weekly | All` toggle buttons on `ShopPaginationView` (commit `dc1a70a`).
9. **Centralized `player_queue_stats` Caching Across All Months:** `cache_player_queue_stats()` runs inside `build_queue_stats_payload()` whenever `is_final` is true (covering `/queue_stats`, live `post_queue_result`, and startup `backfill_missed_queue_results`) and caches **all** finalized 4v4 matches (`total_players > 4`) regardless of calendar month (commit `e59c90f`). Monthly filtering is performed at query time.
10. **Spam Prevention on Old NeatQueue Edits:** `post_queue_result()` ignores Discord messages older than `QUEUE_RESULT_MAX_MESSAGE_AGE_SECONDS` (`24` hours default, commit `6c74570`) unless `allow_old_messages=True` so NeatQueue mass-edits of old messages do not spam the channel.
11. **`/verify` Completion Text:** The DM sent upon successful verification is simply `"✅ **Account Linked!**"` (the old `" You are now entered into weekly & monthly leaderboards."` suffix was explicitly removed in commit `33b1244`).
12. **Console Analytics Over Standalone Scripts:** When the user wants operational/internal analytics (such as `winrate [guild_id]` comparing team average Squad rating vs match winner), add it as a command in `console_service.py` rather than creating standalone one-off scripts in the repository root.
13. **Spectators Never Count as Queue Players (implemented 2026-11-03):** Scoreboard rows with survev.de's spectator rank `999` (`SURVEV_SPECTATOR_RANK` in `neatqueue_client.py`) stay in the per-game log with `display_team_index: None` but never join a team, series stats, round winners, or the Game breakdown. Zero-stat players with a real rank are kept. The user limited this fix to spectators; the other accuracy items stay backlog.
14. **Queue Lobby Shape (confirmed by user 2026-11-03, not yet used in code):** 4v4 queue games are always custom lobbies containing only the two queue teams plus spectators, never public lobbies with other squads. Future round validation (backlog items on unrelated games and queue-team selection) may reject games without exactly two playing teams.
15. **Automatic Posts Wait for Final Results (chosen by user 2026-11-03, not yet implemented):** When NeatQueue's message fires before the queue is final, the automatic result post should post nothing and publish only the final card later. Apply this when implementing the P1 processed-marking backlog item. Behavior for finished queues with incomplete survev.de data is **undecided**; the user asked for an explanation first.

---

## Approved TODO List

> **Rules for this list:**
> - Only tasks explicitly requested or approved by the user belong here.
> - Discussing, exploring, or suggesting an idea does **not** count as approval.
> - Completed tasks are preserved for historical continuity.

### Active / Pending Tasks

| Task | Status | Context & Acceptance Criteria |
| :--- | :--- | :--- |
| **[P1] Reject incomplete data before publishing final queue results** | **Not started** | Approved 2026-11-03 after review. `neatqueue_client.py` skips unavailable scoreboards but still marks results final: mocked 2-1 became 0-1 with two boards missing. Require complete validated data before final publication or persistence; explicitly handle partial history/scoreboard fetches and cover retries. |
| **[P1] Preserve valid rounds when a linked player joins or logs in late** | **Not started** | Approved 2026-11-03 after review. Strict intersection of nonempty anchor histories drops earlier rounds: mocked 2-1 became 1-0. Replace this assumption with validated round selection that preserves legitimate rounds without admitting unrelated games; add late-join/login regressions. |
| **[P1] Mark automatic queue results processed only after a successful final post** | **Not started** | Approved 2026-11-03 after review. `post_queue_result` and startup backfill claim the queue before lookup/delivery succeeds. An early voting message or lookup failure blocks the final post. Preserve concurrency protection while allowing retry and later final publication; test early votes, failures, and final edits. |
| **[P1] Stop treating voting-message creation time as the match end** | **Not started** | Approved 2026-11-03 after review. `get_match_end_ms` prefers a winner-message snowflake timestamp over a valid explicit end when the message is unavailable: mocked 60-minute queue was cut to 65 seconds. Prefer reliable end evidence and avoid truncating rounds on deleted/inaccessible vote messages. |
| **[P2] Map substitutes using teammates in each round** | **Not started** | Approved 2026-11-03 after review. Aggregating each player's first team ID across games can put a late substitute on the opposing team because IDs are reused. Mocked 2-1 became 2-0. Resolve each round's teams to stable queue teams; test changing IDs and substitutions. |
| **[P2] Exclude unrelated games from queue calculations** | **Not started** | Approved 2026-11-03 after review. All-mode anchor history currently accepts rounds without roster/lobby validation. A mocked solo game turned 2-1 into 3-1 and inflated damage from 3,000 to 12,000. Validate candidate rounds against queue participation and mode/lobby evidence, including single-anchor cases. |
| **[P2] Select queue teams rather than the two best-placed squads** | **Not started** | Approved 2026-11-03 after review. Top-two placement selection can replace a real queue team with a stronger unrelated squad. Keep the queue's actual teams in series and per-game images regardless of outsider placement; test a third squad beating both queue teams. |
| **[P2] Preserve NeatQueue team numbers when only Team 2 is linked** | **Not started** | Approved 2026-11-03 after review. Sorting unknown teams last relabels linked Team 2 as Team 1, swapping the displayed score. Anchor teams to their actual NeatQueue indices and correctly place the remaining team; test either team being the only linked side. |
| **[P2] Keep same-name guest players separate** | **Not started** | Approved 2026-11-03 after review. Lowercase username identity merges unlinked players across opposing teams. Mocked one-game guests became one row with two games and combined stats. Use collision-safe identities or explicit ambiguity handling consistently in aggregation and Game breakdown; test duplicate guest names. |
| **Combine `/profile` and `/history` commands** | **Not started** | Evaluate and implement combining `/profile` (4v4 play time, all-time/monthly avg damage & impact, Squad tier) and `/history` (recent 10 queues sparkline chart and table) into a single unified player command or interactive view (e.g., toggle buttons / tabs). Acceptance criteria: Users can access both high-level profile stats and recent queue history without cluttering slash commands; preserves metric toggling (`Damage`/`Impact`/`Kills`) and Squad tier display. |
| **Combine `/leaderboard_monthly` and `/leaderboard_weekly`** | **Not started** | Consolidate `/leaderboard_weekly` and `/leaderboard_monthly` into a unified `/leaderboard` command with interactive buttons to switch timeframes (Weekly vs Monthly), matching the unification pattern used for `/market`. Acceptance criteria: Single slash command allowing seamless switching between 7-day and 30-day stats across all metrics (Kills, Wins, Games, Damage) without code duplication. |
| **Visual redesign for `/inventory` and `/market` cards** | **Not started** | Overhaul Pillow image generation for `generate_inventory_image` and `generate_shop_image` in [`image_utils.py`](image_utils.py) to bring them up to the visual fidelity of queue result and history cards under [`design-system/MASTER.md`](design-system/MASTER.md). Acceptance criteria: Upgraded layout, typography, borders/radiuses, item rarity accents, and container styling making inventory and shop offer images modern and visually cohesive. |

### Completed Tasks

| Task | Status | Context & Acceptance Criteria |
| :--- | :--- | :--- |
| **Exclude spectators from queue teams and stats** | **Completed** | Requested 2026-11-03 after Queue `#4600` showed a spectator as a fifth Team 1 player. Rank-`999` rows are logged but never aggregated or displayed; regressions cover a spectator reusing a team's earlier `team_id`, a spectator named like a player, and zero-stat real players. Offline replay of the 4600 log: 5/4 became 4/4, score 4-1 unchanged. Local change pending deployment. |
| **Silence queue-stat cooldown and in-flight wait replies** | **Completed** | Approved 2026-11-03. Repeated mentions and `score?` requests are consumed silently during the shared cooldown or active lookup, without a reply, DM, typing indicator, or duplicate lookup. Normal requests resume after cooldown expiry. |
| **Show queue stats for `score?` without a bot mention** | **Completed** | Reuses the queue-channel mention handler, stats payload, and cooldown. Matches `score?` anywhere in the message, case-insensitive (widened from whole-message match 2026-11-03). Covered by trigger, exclusions, mixed-trigger cooldown/concurrency, and finished-queue tests. Local change pending deployment. |
| **Reply with queue stats when mentioned in queue channels** | **Completed** | Explicit human mentions in `queue-<number>` reuse the existing stats payload and finality-aware view; no `/setup` needed for the mention channel. Ignore recurring vote messages, preserve automatic tracking, guard duplicate requests, and cover routing/error handling with tests. Local implementation; Discord deployment verification remains outstanding. |
| **Distinguish ongoing queue cards from final results** | **Completed** | Show `IN PROGRESS` above the scoreboard and `Team N leads` / `No team leading` for ongoing queues. Pass `is_final` into image rendering. Verified with 196 passing tests and a rendered preview; local changes await deployment. |
| **Initialise shared `MEMORY.md`, `AGENTS.md`, and clean up `README.md`** | **Completed** | Establish Git-managed shared project memory (`MEMORY.md`) and permanent agent operating instructions (`AGENTS.md`) for seamless switching between Antigravity, Claude Code, and OpenAI Codex across two computers; separate human-facing documentation in `README.md` from internal working notes. |
| **Ongoing queue fallback to NeatQueue history roster/start time** | **Completed** | When an ongoing queue's Discord panel (`Queue#<id>`) cannot be found or read, fall back to the pending NeatQueue history entry's roster and `start_time` when available, and record `roster_source` and `history_players_per_team` in diagnostics (`commit 22e6c84`). |
| **Fix live `/queue_stats` cutoff when NeatQueue returns `winner: -2`** | **Completed** | Treat NeatQueue `winner == -2` as pending/ongoing (`is_finished = False`) so live `/queue_stats` uses the current time (`now`) as the window end instead of the 10-minute finished-queue fallback duration (`commit dd2b4d1`, fixed Queue `#4600`). |
| **Structured `/queue_stats` JSON lookup diagnostics** | **Completed** | Add `diagnostics` to `calculate_queue_match_stats` and `write_queue_stats_command_log` capturing finality decision, NeatQueue winner, time window sources, anchor match history pages/GUIDs, and candidate round inclusion/rejection reasons without leaking tokens or raw payloads (`commit 5ee7af5`). |
| **Per-player `/history [member]` command & visual refinements** | **Completed** | Add `/history [member]` showing last 10 finalized 4v4 queues (`Damage`/`Impact`/`Kills` toggles), cache `player_queue_stats` across all months, show value pills on every sparkline point without X-axis overlap, and remove `Oldest ───► Newest` and Impact footer text (`commits e59c90f, 33b1244`). |
| **`/profile` Impact stats & 2x2 grid layout** | **Completed** | Add `All-Time Avg Impact` and `{Month} Avg Impact` to `/profile`, arrange Damage and Impact in aligned 2-column rows using inline `\u200b` spacers, and remove the embed description (`commits 99259003, 7cfeb4f, ff850a9`). |
| **Harden `/verify` flow, fix interaction `40060` crash, and add `winrate` console command** | **Completed** | Remove duplicate `verify_button` handler in `discord_ui.py` (`commit 100edc8`), add rate-limit backoff + duplicate verification guard + 30-min `slug_backfill_loop`, and add `winrate [guild_id]` to `console_service.py` (`commit 558f7f0`). |
| **Impact column, per-game breakdown (`QueueGamesPagerView`), and Squad Tier** | **Completed** | Render per-game `survev.de` Impact (`0–100`) on queue cards when scored, persist 90-day snapshots in `queue_results` for ephemeral per-game breakdown cards, and show cached `survev.de` Squad Tier on `/profile` and `/compare` (`commits 88f9175, 51b1011`). |
| **Codebase concurrency & `/market` unification refactor** | **Completed** | Parallelize API calls with `asyncio.Semaphore(5)`, offload Pillow rendering to `asyncio.to_thread`, cap pagination caches, add `delete <channel_id> <msg_id>` to host console, and unify `/market [member]` with `Daily \| Weekly \| All` toggle buttons (`commits 30d65b8, fe025de, dc1a70a`). |
| **Queue results card redesign, bundled font, vector unlinked icon, and `design-system/MASTER.md`** | **Completed** | Upgrade `generate_queue_result_image` with `2x` supersampling, rounded team cards, bold `Match Duration`, bundled `QuattrocentoSans-Bold.ttf`, vector broken-chain icon, bumped font sizes, and locked `design-system/MASTER.md` (`commits 4dd2a39, 661d013, a3d840f, 051e4da, d12198d, e9228ac`). |
| **Multi-channel `/setup`, `/leaderboard_season`, and old-message spam guard** | **Completed** | Support multiple tracked channels per guild (`commit cb15d02`), add `/leaderboard_season [month]` (`commits c0e84e0, 380e2bf, ee219a9`), and ignore NeatQueue edits older than 24h (`commit 6c74570`). |

---

## Known Issues and Limitations

1. **Strict Anchor GUID Intersection When a Linked Player Plays Logged Out or Subs Mid-Series (Confirmed Bug — Approved Backlog):**
   - **Location:** [`neatqueue_client.py`](neatqueue_client.py) (`set.intersection(*anchor_guid_sets)`).
   - **What happens:** If a queue has multiple verified anchor players and one of them plays part of the series logged out of `survev.de` (`slug: null` on the scoreboard) or substitutes in mid-series, that player's `survev.de` match history only contains the subset of games they played while logged in. Because `calculate_queue_match_stats` intersects all non-empty `anchor_guid_sets`, the series is truncated to only the games where **every** active anchor was logged in (observed in Queue `#4444`, where `Qualle` only logged in for Game 5, causing the bot to initially report `1 - 0` instead of `4 - 1`).
   - **Status:** The user approved adding this fix to the TODO list on 2026-11-03 following the accuracy review. It remains **Not started**; no particular replacement algorithm has been chosen. The immediate implementation request was limited to silencing wait replies.

2. **Re-Running `/queue_stats <match_id>` After Account Linking Leaves Old `"guest:<name>"` Rows in `player_queue_stats`:**
   - **Location:** [`db.py`](db.py) (`cache_player_queue_stats`).
   - **What happens:** `player_queue_stats` uses primary key `(match_id, guild_id, player_key)`. If a player is initially cached as `"guest:Name"` and later links their account (becoming `"discord:12345"`), re-running `/queue_stats <match_id>` inserts the new `"discord:12345"` row without deleting the old `"guest:Name"` row. This does not affect `/leaderboard_season`, `/profile`, or `/history` (which filter by `discord_id IS NOT NULL`), but leaves orphan guest rows in SQLite.

3. **Legacy `player_queue_stats` Rows Prior to Queue `#4520` Missing Extended Columns:**
   - **Location:** [`db.py`](db.py) (`get_player_queue_history`).
   - **What happens:** Rows cached before `kills`, `impact`, `winning_team_index`, and `team_round_wins` were added have `NULL` for those columns unless a matching snapshot exists in `queue_results` (which only began being saved with the Game breakdown feature and expires after 90 days) or `/queue_stats <match_id>` is manually re-run for those older queue IDs.

4. **`post_queue_result` Marks `processed_matches` Before Checking `is_final`:**
   - **Location:** [`queue_stats_service.py`](queue_stats_service.py) (`post_queue_result`).
   - **What happens:** When NeatQueue posts or edits a result message in a tracked channel, `mark_match_processed(match_id, guild_id)` is called before `build_queue_stats_payload()` verifies `is_final`. If a premature trigger yields a non-final result (`winning_team_index is None`), subsequent edits of that same match ID are skipped by `is_match_processed()`, requiring a manual `/queue_stats <match_id>` run to backfill Hall of Fame, `player_queue_stats`, and `queue_results`.

5. **In-Memory Pagination State for `/inventory` and `/market`:**
   - **Location:** [`leaderboard_bot.py`](leaderboard_bot.py) (`inventory_pagination_state`, `shop_pagination_state`).
   - **What happens:** Unlike `/leaderboard_season`, `Game breakdown`, and `/history` (which encode state in stateless `custom_id` strings like `season_prev:{month}:{page}`, `qgame_next:{match_id}:{idx}`, `history_metric:{metric}:{limit}:{user_id}`), `/inventory` and `/market` store page/filter state in in-memory dicts keyed by message ID (capped at 200 entries) and expire after a bot restart.

6. **Discord Mobile In-App Browser Blocks Google OAuth During `/verify`:**
   - **Constraint:** Google blocks OAuth sign-ins inside embedded WebViews (`Error 403: disallowed_useragent`). `/verify` instructs mobile users to long-press the authorization link and open it in external Safari or Chrome.

7. **Minor Documentation Drift in `design-system/MASTER.md` Section 5.6:**
   - Section 5.6 of [`design-system/MASTER.md`](design-system/MASTER.md) still mentions that `/history` sparklines show value pills only on `Max`, `Min`, and `Latest` points and include the `Oldest ───► Newest` label and Impact footer note, whereas commit `33b1244` updated [`image_utils.py`](image_utils.py) to draw value pills on **every** data point and removed both `Oldest ───► Newest` and the Impact footer text per user request. The implementation in `image_utils.py` is authoritative.

---

## Current Focus and Handover

### Broader `score?` Trigger (2026-11-03, local changes)
- `reply_to_queue_mention` now uses `SCORE_QUESTION_PATTERN` (`score\?`, case-insensitive) so `score?` anywhere in a message triggers stats (e.g. "what is the score?"); `scores?`, `scoreboard?`, `underscore?`, `score ?` and `score` still do not. Mention trigger, cooldown and exclusions unchanged.
- Diagnosed a live temp-queue channel returning Discord 403/50001 Missing Access: the bot lacked View Channel. Fixed server-side by the owners via NeatQueue `/tempchannels permissions set` for the bot role (applies to newly created channels only).
- Stats message header for unfinished queues (`is_final` falsy) in `build_queue_stats_payload` is now `NeatQueue#<id> - IN PROGRESS`; finished queues keep `Queue stats for NeatQueue#<id>`. Applies to `/queue_stats`, mention, and `score?` replies.
- All 215 tests pass via `.venv/Scripts/python.exe -m unittest discover tests`; `git diff --check` passed. Changes in `leaderboard_bot.py`, `queue_stats_service.py`, `tests/test_queue_mentions.py`, `tests/test_queue_result_logic.py`, `README.md`, `MEMORY.md`; no commit, push, or deployment.

### Queue Stats Accuracy Re-review (2026-11-03, analysis only)
- **Usage facts from the user:** mid-series substitutes happen often; remakes happen (an abandoned game may still end with a winner); automatic posts are right about 99% of the time; cancelled queues produce no NeatQueue winner/results message.
- **Backlog ranked by likelihood in real use** (P1/P2 labels unchanged): 1 late join/login intersection (seen in `#4444`; also hit by linked subs); 2 per-round substitute mapping (survev.de rotates team IDs, e.g. `#4600` Team 1 used 1,1,1,2,3; a sub's new ID can also create a third team that the top-two filter keeps over a real team); 3 same-name guests (logged-out default names such as `Player`); 4 processed-before-final; 5 unrelated games; 6 vote-message end time; 7 incomplete data (never seen in 13 logs); 8 Team-2-only linked; 9 top-two squads (only reachable through substitute mis-mapping, since lobbies are always 2 teams).
- **Candidate issues found, not approved or added to the TODO list:** remade/abandoned games counted as series rounds; a linked player who plays some games logged out appears twice (guest row plus linked row); no check of the computed winner against NeatQueue's declared winner before saving; wrong saved data persists (Hall of Fame records only increase, posted cards are never edited). Lower: display names taken from the first server containing the member; non-unique `users.slug`; global Hall of Fame records; ongoing-queue start taken from the newest `Queue#N` message.

### Queue Spectator Fix (2026-11-03, local changes)
- `calculate_queue_match_stats` now skips survev.de spectator rows (rank `999`) before aggregation and forces their per-game `display_team_index` to `None`. The survev.de server source confirms `999` is used only for spectators (`server/src/game/gameModeManager.ts`, `getPlayersSortedByRank`); spectators are saved without a user ID, so they never have a slug.
- The user chose the spectator-only scope. The nine accuracy items remain **Not started**; per-game team mapping still uses each identity's first `team_id` (substitute risk). Decisions 14-15 above record the user's answers for that later work.
- Verified: all 214 tests pass (`.venv/Scripts/python.exe -m unittest discover tests`); two new spectator tests fail without the fix. An offline replay of the user's 4600 log (mocked APIs, script kept outside the repo) went from 5/4 players to 4/4, with score and all eight players' stats unchanged. `git diff --check` passed. No live Discord check, commit, push, or deployment.
- Working tree also still holds the uncommitted silent-request changes (`leaderboard_bot.py`, `tests/test_queue_mentions.py`, `README.md`). This session added `neatqueue_client.py`, `tests/test_queue_match_stats.py`, and `MEMORY.md` edits.

### Queue 4600 Spectator Diagnosis (2026-11-03, analysis only — fixed in the section above)
- Inspected user-provided `queue_stats_20261103T123416.806663Z_g1363265113880264745_u873147471910547458_m4600.json`. NeatQueue roster counts are 4/4; rendered team counts are 5/4. `[AS] [SMH] cooly` appears only in Games 4-5 with `rank=999`, `team_id=1`, zero kills/damage/impact, and displayed team index 0. The local survev.de source assigns rank 999 to spectators (`server/src/game/objects/player.ts`, `kill`).
- Root cause: `calculate_queue_match_stats` aggregates spectator rows without filtering them and preserves each identity's first raw team ID. Team 1's participants first used ID 1, then IDs 2 and 3 in Games 4-5. Cooly's later spectator ID 1 collides with that earlier ID, so he survives the top-two-team filter. Other spectators first had IDs outside the retained groups, explaining intermittent inclusion.
- Replayed the five logged scoreboards through the current calculator with mocked metadata/roster IDs and no network access: reproduced 5/4 and score 4-1. Excluding rank-999 rows in the replay restored 4/4 while keeping score 4-1 and all eight participants' aggregate stats unchanged. This is diagnosis, not an implemented fix; no new TODO item was added. Relevant existing backlog: stable per-round team mapping and actual queue-team selection. Prior silent-request code changes remain untouched; only this memory note was added in this analysis.

### Silent Queue Requests and Accuracy Backlog (2026-11-03, local changes)
- Removed public cooldown and in-flight wait replies from `reply_to_queue_mention`; both triggers still share the same 30-second cooldown and active-lookup guard. Suppressed requests do not fall through to command handling or emit typing indicators. Updated README and existing cooldown/concurrency tests.
- Added all nine reviewed accuracy issues to the Approved TODO List as **Not started**, retaining severity, reproduction context, and acceptance criteria. No queue calculation fixes were implemented. The review used synthetic API inputs, not live matches; its 211 passing tests did not establish accuracy in those edge cases.
- All 211 tests passed with `.venv/Scripts/python.exe -m unittest discover tests`; updated regressions verify silent suppression for both triggers, shared cooldown, concurrent requests, and normal resumption after expiry. `git diff --check` passed. No live Discord verification performed.
- Started from clean commit `a134178`. Changes are in `leaderboard_bot.py`, `tests/test_queue_mentions.py`, `README.md`, and `MEMORY.md`; no commit, push, or deployment.

### Plain-Text Scoreboard Shortcut (2026-11-03, local changes)
- Added `score?` to `reply_to_queue_mention` so anyone in `queue-<number>` can request the scoreboard without mentioning the bot. Both triggers share finality-aware replies, the 30-second channel cooldown, and concurrent-request protection. Exact whole-message matching ignores case and surrounding whitespace.
- Verified all 211 tests with `.venv/Scripts/python.exe -m unittest discover tests`; added four tests and extended finality/concurrency cases. `git diff --check` passed. No live Discord verification or deployment performed.
- Started from clean commit `acb524d` (the prior mention feature is now committed). This session leaves changes in `leaderboard_bot.py`, `tests/test_queue_mentions.py`, `README.md`, and `MEMORY.md`; no commit or push. Earlier handovers describing the mention implementation as uncommitted are historical.

### Queue-Channel Mention Shortcut (2026-11-02, local changes)
- Added `reply_to_queue_mention` ahead of automatic message detection in `leaderboard_bot.py`. Reads the queue number from the channel name and reuses `build_queue_stats_payload`; the voting panel is not used as a roster or finality signal. Replies disable outgoing mentions and show typing while fetching.
- The user confirmed current stats (including final queues) and a 30-second per-channel cooldown with a brief wait reply on 2026-11-02. The implementation already matches: cooldown starts after successful delivery of stats/lookup errors, and concurrent requests receive a wait reply. Unexpected lookup/send failures release the guard for retry. Recording these approvals required documentation changes only; no code changed after the 207-test pass.
- All 207 tests passed, including 11 new mention tests for routing, finality-aware controls, cooldown/concurrency, errors, and compatibility with automatic results. `git diff --check` passed. No live Discord messages, commit, push, or deployment performed.
- Started from clean commit `8555c6f`. This change touches `leaderboard_bot.py`, `README.md`, `MEMORY.md`, and new `tests/test_queue_mentions.py`. Prior IN PROGRESS work is committed in `8555c6f`; earlier handover notes describing it as uncommitted are historical.
- Cross-check found older architecture notes use outdated names: the actual listener is `on_raw_message_edit`, automatic posting/backfill live in `leaderboard_bot.py`, and the panel scanner is `find_queue_panel_message` (200 messages). NeatQueue uses `/api/v1/history/{guild_id}` filtered to the requested game; current lookup windows use the selected start and end directly. Consult code when older notes differ.

### Queue Card Status Update (2026-11-02, local changes)
- Added the centered `IN PROGRESS` label and live leader wording in `image_utils.py`; `queue_stats_service.py` supplies the queue's finality flag.
- Verified all 196 tests with `.venv/Scripts/python.exe -m unittest discover tests`, including live, tied and finished image text and service propagation. Visually inspected the rendered card; the status fits above the score without overlap. `git diff --check` passed.
- Updated the command description and design specification. Existing README edits, untracked shared instruction/memory files, and newly approved backlog tasks were preserved. No commit, push, deployment or live Discord check performed for this UI change.
- Current diagnostic JSON uses `diagnostics.neatqueue.winner`, `diagnostics.window.from_ms` / `to_ms`, `anchors`, and `round_selection`; consult the actual code when older notes below use different field or function names.

### Most Recent Work Completed (2026-11-02)
1. **Queue Lookup Diagnostics (`commit 5ee7af5`):**
   - Added structured `diagnostics` dict to `calculate_queue_match_stats()` in [`neatqueue_client.py`](neatqueue_client.py) and persisted it via `write_queue_stats_command_log()` in [`queue_stats_service.py`](queue_stats_service.py) (including attaching partial diagnostics to `QueueStatsError` when a lookup fails).
2. **Ongoing Queue `winner: -2` Time Window Fix (`commit dd2b4d1`):**
   - Fixed a bug exposed by Queue `#4600` where running `/queue_stats 4600` while the queue was still in progress returned `winner: -2` from NeatQueue's history API. Previously, `winner: -2` was treated as a finished match with an invalid `end_time`, triggering the 10-minute fallback window (`QUEUE_MATCH_FALLBACK_DURATION_MS`) and cutting off Games 3–5. Now `winner in (None, -2)` is treated as ongoing (`is_finished = False`), keeping the window open through the current time (`now`).
3. **Ongoing Queue Roster & Start-Time Fallback (`commit 22e6c84`):**
   - Updated `calculate_queue_match_stats()` so that if an ongoing queue's Discord panel message (`Queue#<id>`) cannot be found or read in the tracked channels, the bot falls back to the pending NeatQueue history entry's roster and `start_time` when available, recording `roster_source` (`"discord_panel"` vs `"neatqueue_history"`) and `history_players_per_team` in `diagnostics`.
4. **Shared Memory & Agent Instructions Initialisation:**
   - Created [`MEMORY.md`](MEMORY.md) and [`AGENTS.md`](AGENTS.md), and separated internal diagnostic/fallback notes from human-facing documentation in [`README.md`](README.md).

### What the Next Agent Should Know
- **Working Tree Status:** MEMORY.md and AGENTS.md are created, README.md is updated, and the active TODO list includes 3 newly approved tasks (combining profile/history, combining monthly/weekly leaderboards, and modernizing inventory/market cards). Working tree also contains uncommitted changes in image_utils.py, queue_stats_service.py, and tests adding an IN PROGRESS indicator and leader text on non-final queue cards (196 passing unit tests in 	ests/).
- **If Investigating Queue Discrepancies:** Check the JSON files in `log/queue_stats_<match_id>_*.json` (especially comparing any in-progress run against the final run) and inspect the `diagnostics` block (`finality_reason`, `neatqueue_winner`, `roster_source`, `window_start_ms`/`window_end_ms`, `anchor_lookups`, and `candidate_rounds`).

