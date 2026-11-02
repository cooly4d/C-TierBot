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

The existing features below are implemented. The queue-mention shortcut is a local change pending deployment; the automated unit test suite currently passes `207` tests across `10` test modules via `python -m unittest discover tests`. See Current Focus and Handover for deployment and working-tree status.

### Slash Commands (`16` total in `leaderboard_bot.py`)

| Category | Command | Status & Verification |
| :--- | :--- | :--- |
| **Account Linking** | `/verify` | **Implemented & Verified.** Starts a DM OAuth flow against `survev.de`, polls up to 5 minutes (`VERIFICATION_TIMEOUT_SECONDS = 300`) with `429`/`5xx` retry backoff, stores `(discord_id, access_token, survev_slug)` in `verified_users`, and runs a 30-minute background `slug_backfill_loop`. |
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
     - `is_final = bool(is_finished and winning_team_index is not None)`. Only `is_final` results update Hall of Fame (`hall_of_fame_records`), per-player stats (`player_queue_stats`), and 90-day `Game breakdown` snapshots (`queue_results`).

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
   - `verified_users (discord_id INTEGER PRIMARY KEY, access_token TEXT NOT NULL, survev_slug TEXT)`
   - `guild_settings (guild_id INTEGER, channel_id INTEGER, PRIMARY KEY (guild_id, channel_id))` — auto-migrated from single-channel primary key if legacy schema is detected.
   - `processed_matches (match_id INTEGER, guild_id INTEGER, PRIMARY KEY (match_id, guild_id))`
   - `hall_of_fame_records (guild_id INTEGER, record_key TEXT, value REAL NOT NULL, match_id INTEGER NOT NULL, holder_name TEXT NOT NULL, holder_discord_id INTEGER, holder_slug TEXT, recorded_at TEXT NOT NULL, details_json TEXT, PRIMARY KEY (guild_id, record_key))`
   - `player_queue_stats (match_id INTEGER NOT NULL, guild_id INTEGER NOT NULL, player_key TEXT NOT NULL, discord_id INTEGER, survev_slug TEXT, display_name TEXT NOT NULL, team_num INTEGER NOT NULL, games_played INTEGER NOT NULL, total_damage INTEGER NOT NULL, total_time_alive INTEGER NOT NULL, total_players INTEGER NOT NULL, recorded_at TEXT NOT NULL, kills INTEGER, impact REAL, winning_team_index INTEGER, team_round_wins TEXT, PRIMARY KEY (match_id, guild_id, player_key))`
     - `player_key` is `"discord:{discord_id}"` for linked players or `"guest:{slug_or_name}"` for unlinked players.
     - Legacy rows recorded before `kills`, `impact`, `winning_team_index`, and `team_round_wins` were added are enriched on read in `get_player_queue_history()` from `queue_results.payload_json` if a 90-day snapshot exists.
   - `queue_results (match_id INTEGER NOT NULL, guild_id INTEGER NOT NULL, recorded_at TEXT NOT NULL, payload_json TEXT NOT NULL, PRIMARY KEY (match_id, guild_id))` — pruned automatically after `QUEUE_RESULTS_RETENTION_DAYS = 90`.

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

---

## Approved TODO List

> **Rules for this list:**
> - Only tasks explicitly requested or approved by the user belong here.
> - Discussing, exploring, or suggesting an idea does **not** count as approval.
> - Completed tasks are preserved for historical continuity.

### Active / Pending Tasks

| Task | Status | Context & Acceptance Criteria |
| :--- | :--- | :--- |
| **Combine `/profile` and `/history` commands** | **Not started** | Evaluate and implement combining `/profile` (4v4 play time, all-time/monthly avg damage & impact, Squad tier) and `/history` (recent 10 queues sparkline chart and table) into a single unified player command or interactive view (e.g., toggle buttons / tabs). Acceptance criteria: Users can access both high-level profile stats and recent queue history without cluttering slash commands; preserves metric toggling (`Damage`/`Impact`/`Kills`) and Squad tier display. |
| **Combine `/leaderboard_monthly` and `/leaderboard_weekly`** | **Not started** | Consolidate `/leaderboard_weekly` and `/leaderboard_monthly` into a unified `/leaderboard` command with interactive buttons to switch timeframes (Weekly vs Monthly), matching the unification pattern used for `/market`. Acceptance criteria: Single slash command allowing seamless switching between 7-day and 30-day stats across all metrics (Kills, Wins, Games, Damage) without code duplication. |
| **Visual redesign for `/inventory` and `/market` cards** | **Not started** | Overhaul Pillow image generation for `generate_inventory_image` and `generate_shop_image` in [`image_utils.py`](image_utils.py) to bring them up to the visual fidelity of queue result and history cards under [`design-system/MASTER.md`](design-system/MASTER.md). Acceptance criteria: Upgraded layout, typography, borders/radiuses, item rarity accents, and container styling making inventory and shop offer images modern and visually cohesive. |

### Completed Tasks

| Task | Status | Context & Acceptance Criteria |
| :--- | :--- | :--- |
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

1. **Strict Anchor GUID Intersection When a Linked Player Plays Logged Out or Subs Mid-Series (Confirmed Bug — Fix NOT Yet Approved):**
   - **Location:** [`neatqueue_client.py`](neatqueue_client.py) (`set.intersection(*anchor_guid_sets)`).
   - **What happens:** If a queue has multiple verified anchor players and one of them plays part of the series logged out of `survev.de` (`slug: null` on the scoreboard) or substitutes in mid-series, that player's `survev.de` match history only contains the subset of games they played while logged in. Because `calculate_queue_match_stats` intersects all non-empty `anchor_guid_sets`, the series is truncated to only the games where **every** active anchor was logged in (observed in Queue `#4444`, where `Qualle` only logged in for Game 5, causing the bot to initially report `1 - 0` instead of `4 - 1`).
   - **Status:** Root cause was diagnosed and explained to the user, including a potential majority/plurality voting fix, but the user **has not yet requested or approved** changing the intersection algorithm. Do not modify this logic without explicit user approval.

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

