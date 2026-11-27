# Product

<!-- impeccable:product-schema 1 -->

## Platform

discord

C-TierBot's main surface is Discord. It also serves a read-only website for its home server (queue archive, leaderboard, player pages, Hall of Fame; `design-system/pages/web.md`), with no iOS or Android surface. Its visual output is raster PNG cards rendered server-side with Pillow (`image_utils.py`), posted into Discord alongside native embeds, buttons, selects and ephemeral messages (`discord_ui.py`, `leaderboard_service.py`, `queue_stats_service.py`). Discord owns the chrome, fonts and layout around every card; the bot controls only the image pixels and the embed/component content.

## Users

Members of one home competitive survev.de Discord community that plays 4v4 scrim queues through NeatQueue. Other servers can use the bot, but they are incidental; design and feature decisions serve the home community first.

- **Players** check scrim results, their own stats, comparisons with friends, cosmetics and the shop. They mostly view the cards in **desktop Discord**, often between or right after games.
- **Server admins** register NeatQueue result channels (`/setup`, `/unsetup`) and manage Hall of Fame records.
- **The host operator** runs the bot on PebbleHost and uses the terminal console (`HOST_COMMANDS.md`) for diagnostics, SQL, messaging and analytics.

## Product Purpose

Bring the community's survev.de stats and scrim results into Discord so nobody has to open the site. Players link their survev.de account once with `/verify`. After that, finished NeatQueue queues are turned automatically into a results card with per-player kills, damage, impact, series score and rosters, and a per-game breakdown. Profiles, leaderboards, comparisons, inventories and shop offers are rendered on demand.

Success means scrim results show up on their own, are accurate (the right games, teams and substitutes), and read instantly in the channel where the queue happened.

## Positioning

C-TierBot joins NeatQueue's queue records to each linked player's own survev.de match history. It resolves teams game by game, so substitutes, leavers and spectators are attributed correctly, and it renders the scrim series as one card. A generic stats bot or NeatQueue alone cannot produce a validated, per-game, substitute-aware scrim scorecard for this community.

## Operating Context

- Results post automatically in tracked NeatQueue channels. Players can also type `score?` or mention the bot in a `queue-<number>` channel for live (**IN PROGRESS**) or final stats.
- Cards are viewed inline in Discord channels and ephemeral replies, primarily on desktop. Paging and filters work through buttons and selects whose state lives in their `custom_id`s, so the buttons keep working after a restart.
- Data comes from the survev.de API (OAuth per player) and the NeatQueue API. Neither project is affiliated with C-TierBot.
- Development spans two computers and several coding agents. `MEMORY.md` and `AGENTS.md` are the shared operating record.

## Capabilities and Constraints

- Python 3.13 in production, `discord.py`, SQLite (`leaderboard.db`), Pillow for every rendered image.
- The flagship surface is the **queue result card** (`generate_queue_result_image`) and its **Game breakdown**. Future design work protects these first.
- Other surfaces: `/profile` (Overview embed + History card), `/compare` card, `/leaderboard_season` / `/leaderboard_old` / `/leaderboard_fries` embeds, `/inventory` and `/market` item grids, `/hall_of_fame`, `/goldenfries`, and the `/verify` DM flow.
- Images render at 1600px wide (the queue card is 2x supersampled), with the bundled Quattrocento Sans font so Windows and Linux render the same.
- Item icons come from a survev.de checkout (`tools/export_item_icons.mts`) and are never hand-edited.
- Image rendering runs off the event loop. All survev.de calls share one HTTP session and one rate limiter.
- **Visual authority:** `design-system/MASTER.md` is the locked visual system for cards and embeds, and `MEMORY.md` → *Requirements and Approved Decisions* lists the rejected designs (boxed center scoreboard, `/leaderboard_season` podium/summary banner, `(unlinked)` text, impact explanation subtext, `Oldest ───► Newest` label). Both must be honored.
- Open product decisions: how to show a linked player who played some games logged out (currently a separate guest row), and how finished queues with incomplete survev.de data should behave (see `MEMORY.md` Known Issues and the P1 TODOs).

## Brand Commitments

- Name: **C-TierBot**. The README voice is plain and friendly ("does the boring stuff for you") and avoids hype.
- Discord copy stays short. Confirmed examples: the `/verify` success DM is just `✅ **Account Linked!**`, and `/profile` keeps emoji-prefixed stat lines (the user tried a no-emoji layout and rejected it).
- Always say the stats come from survev.de and NeatQueue and that the project is not affiliated with either.

## Evidence on Hand

- Real item art: `assets/items/` (128px icons + `manifest.json`).
- Font files: `QuattrocentoSans-Bold.ttf`, `QuattrocentoSans-Regular.ttf`.
- Ko-fi support link (README). There are no testimonials, user counts, server counts or usage metrics; never fabricate them.

## Product Principles

1. **Accuracy before polish.** A wrong score or misattributed player is worse than a plain card. Never publish or render unvalidated results as final.
2. **The queue card is the product.** It must read at a glance in the channel: who won, the series score, and who carried.
3. **Stay inside Discord.** Answer the question in the channel and avoid sending players to the site.
4. **The community decides.** Changes serve the home community's habits, and their past rejections are binding.
5. **Quiet automation.** No spammy replies, wait messages or noise. Cooldowns and duplicate suppression stay silent.

## Accessibility & Inclusion

Primary viewing is desktop Discord, but cards are 1600px PNGs that Discord scales down in the chat column, so text sizes and contrast must survive that downscale. Win/loss is never shown by green/red alone; it is always paired with text such as "Team X Wins" and with the score.
