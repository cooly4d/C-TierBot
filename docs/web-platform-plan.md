# C-TierBot web platform: public stats site + operator panel

Status: plan only (user: "just plan it first"). Nothing is built, pushed or changed in production by this plan's
review. Repository: C-TierBot (`main` at `d73dc97`, after the history scrub below). The GitHub repository is **public**, so this file must not
contain the operator's other domain, the VPS address or other infrastructure details. Those live in the private
myVPS inventory and are referred to here by role only.

Revision history:
- r2-r4 (after Codex rounds 1-3, findings WEB-01..17, R2-01..10, R3-01..06): archive from snapshots + stat rows;
  retention configurable and validated; shared DB helpers take a connection; listener failures can't stop Discord;
  admission without waiting; one shutdown deadline measured from SIGTERM with query interruption; per-game-row
  snapshot identity (v2 `key`); encoded SQLite URIs; private, verified preview export.
- **r5 (user decisions of 2026-11-26, second message; not yet reviewed):**
  - hostname `ctier.cooly.dev` (D1);
  - snapshots kept forever is confirmed (D2);
  - the operator panel moves from an SSH-tunnelled Unix socket to `https://ctier.cooly.dev/admin` with a password
    login for the single operator (D4, section 5);
  - names: the site shows the **same names as the bot** (D6, section 3.5). The r3 "in-game names only" rule is
    withdrawn. Player pages become per Discord user, like `/profile` (D5, section 3.5);
  - the interface design is chosen by the user from mockups (section 4).
- **r6 (after Codex round 4, R5-01..10; not re-reviewed yet):** published git history still holds the protected
  values, so the scan targets the publish commit and history is reported separately; the per-IP throttle is
  convenience only; logout sends a `Secure` expiry cookie; the password slot is held until scrypt really ends; one
  helper builds every player URL, redirects included; v2 snapshots derive linkage per game row; the stored-name
  fallback is an explicit exception to the no-other-server-data rule; `/admin` headers apply at the outer boundary;
  certificate check matches Cloudflare's edge certificate; password tool limits match the form.
- **r7 (after Codex round 5, R6-01..03):** the stored-name exception covers any name stored while calculating a
  main-server queue (delivery isn't recorded); the publish scan covers every newly pushed commit's tree and
  message; the per-IP throttle is removed, leaving one global limit.
- **r8 (user decision, 2026-11-26, not yet reviewed):** design direction A (the card design language) with clean
  motion for the statistics; animation library, motion spec, two charts (player trend, leaderboard bars) and their
  tests added (section 4).
- **r9 (after Codex round 6, R8-01..08):** admin `Referrer-Policy: same-origin` so form POSTs carry a real
  `Origin`; public docs no longer cite pre-scrub commit hashes; one trend-value rule (kills per game divided by the
  player's own game count); the score replay only when known winners reproduce the stored score; one exposed
  focus target per chart point; View Transitions only without reduced motion and only on leaderboard pages (site-wide page cross-fades since r12); row
  transition names from the public player URL; metric switches matched by queue, with an empty state.
- **r10 (build, 2026-11-27):** folds in round 7's two wording notes (AC1, AC8) and records what the build changed
  after the design finish review and code inspection 1 (WEB-IMPL-01..22): player summary as a card-style band (no
  tiles), round-value chart ticks, latest-point ring and hollow loss dots, leaderboard bars on a track, the bot's
  number formats, the admin path gate, and stricter parsing, deadlines and shutdown.
- **r11 (build, 2026-11-27):** records what code inspection 2 (WEB-R10-01..18) changed: only 1-10 digit queue
  numbers are listed, linked or shown; a v2 logged-out key carries the bot's guest number; each game row carries
  the calculator's own account link; one read transaction per page job; the panel's file reads share the DB
  executor's admission, deadline and shutdown; timeline lanes fit any series length; small lose-coloured text uses
  a slightly lighter red that meets WCAG AA.
- **r12 (UI/UX and motion pass, 2026-11-27, user request "do a ui/ux quality review, make sure everything has
  clean animations"):** every page change cross-fades (View Transitions, site-wide, only without reduced motion),
  and leaving a leaderboard drops the row names; the "Rank by" selection slides; leaderboard rows are named from
  `data-vt` in CSS (`attr()`), so the names exist at first paint; the crosshair glides between points and the
  tooltip stays up while arrow keys move it; a metric switch counts the axis ticks and the latest value in step
  with the line; colour feedback eases under reduced motion too; whole queue rows and leaderboard rows are links;
  the footer sits at the bottom of short pages; phone layouts for queue rows and the leaderboard header; the login
  error sits under the field; panel dates in the site's format; the metric buttons are worded like the units.
- **r13 (build, 2026-11-27):** records what code inspection 3 (WEB-R12-01..16) changed: the bot's queue-result
  writes run in a worker thread, so a web read can't stall the event loop; panel log reads are regular files only,
  read in chunks that stop at the deadline; the archive's number filter is NUL-safe; each game's table shows its
  own Impact column; a Hall of Fame record links its queue only when that queue has a page; cached pages are used
  only while the member cache is ready; diagnostic log names are unique; the panel origin is canonical and http
  is for the loopback preview only; the preview migrates its copy before listening; one metric tween at a time;
  point targets are exactly their columns; game panel colours ease; metric links keep the history page; stricter
  slugs; and a byte-level protected-values scan before any commit or push. Not changed (WEB-R12-05): the MVP's
  impact weighting for a player on both sides, which matches the result card exactly; the card's own edge case is
  recorded as a Known Issue.
- **r14 (build, 2026-11-27, user requests "lets add console commands to the admin panel", subtle win/loss colouring
  in the queues list instead of "Team 1/2 wins", clickable timeline dots, and the C-TierBot logo):** the panel gains
  a console (5.1) that runs the SSH console's commands, one at a time; the user chose every command with `sql`
  read-only from the panel (access tokens read as NULL; data changes stay in the SSH console) and `stop` behind
  `stop confirm`. Queue rows show the winner as a faint green wash on the winning roster and red on the losing one
  (the score and screen-reader text say it too). Each timeline dot is a link to its game, in game order. The logo
  is in the header and is the browser icon.
- **r15 (build, 2026-11-27, Codex inspection 4 of r13/r14 plus a whole-project security review the user asked
  for; dispositions in the review log):** the panel's `sql` runs in a short-lived process of its own
  (`panel_sql.py`): killed at its time limit, limited on Linux in CPU time, memory, file size and open files, no
  secrets in its environment, and the console stays busy until it has exited. It reads only the bot's own tables,
  their listed columns and SQLite's built-in functions; a column added later reads as NULL until it's listed (a
  test fails until it is), an unlisted table is refused, and `JOIN ... USING` / `NATURAL JOIN` are refused because
  SQLite's authorizer never sees the columns they compare. Console forms carry a run ID: a resubmitted form gets the
  first run's output, never a second run; without JavaScript the reply is a redirect to the panel showing the run
  (a reload or a busy dashboard never repeats a command). `stop confirm` gets a reply of its own, and the bot is
  told to stop only once that reply has been sent. Login and console bodies are read within 5 s, at most 8 at a
  time. `status` reads its counts in a worker thread. Queue-result writes and the Hall of Fame reset share one
  lock held by the worker thread itself, the reset answers before it waits, and a record update is one atomic
  statement. An unavailable guild isn't "ready" for names. Panel log reads refuse links where the system has no
  `O_NOFOLLOW` and treat over-deep JSON as unreadable. A published diagnostic log never replaces another. A BLOB
  queue number is never counted. Older player-history pages list the chart's 16 queues in a table. Enter on a
  timeline dot moves focus to that game's button; Clear is locked while a command runs. SQLite sidecars, partial
  exports, backups and logs are ignored by Git and the Docker build.
- **r16 (build, 2026-11-27, the user's answers to the security review):** one survev.de account, one Discord user
  (SEC-04). The first link keeps the account: `db.save_token` refuses another Discord user's link (an upsert on the
  Discord ID, never `INSERT OR REPLACE`, which would delete the holder's row) and `/verify` says the account is
  already linked to another Discord user; the slug backfill writes only onto the link it read (same token, still no
  slug) and unlinks a second claim it discovers; `migrate_unique_account_links` resolves old duplicates (the Discord
  user with the most recorded queues on the account keeps it) and adds a unique index on `users.slug`. With one
  holder per slug, queue attribution (`get_discord_id_by_slug`) and the site's player URLs agree by construction.
  Leaderboards keep showing every linked player (the user declined SEC-05). Other review findings in the existing
  bot still await the user's decision and are not built.
- **r17 (build, 2026-11-27, after the final review, code inspection 5, FIN-01..13):** `/verify` saves a link only
  once survev.de says which account it is (no profile: nothing is linked and an existing link stays), so a link that
  can't be checked against the one-account rule never exists; link writes run in a worker thread with SQLite's
  usual 5 s wait, a busy database being a link to retry. The duplicate-link clean-up counts a queue per server
  (`guild_id`, queue number) and runs at bot start-up (`leaderboard_bot.enforce_unique_account_links`), no longer
  when `db` is imported, so the backup script never deletes a link. A player URL follows the account's current
  holder: a holder without a page yet gives 404, never a redirect to the account's former player. Console: a run
  whose command is still going is never dropped from the 32 kept, and a command that failed or was cancelled is
  recorded as "stopped before it finished" (a resubmit never runs it again); only `stop confirm`'s own reply reports
  that it went out, and that reply carries the site's headers. Each sign-in's cookie has its own random ID. Panel
  `sql`: the worker cuts its reply at 500,000 characters, and the website stops a worker that sends more than 4 MB,
  draining its pipe so the exit is seen; a table or view named like `json_each` or a `pragma_` function is refused
  like any other unlisted table, and a declared column named `rowid`/`oid`/`_rowid_` needs listing; on Linux the
  query doesn't run if a limit can't be set, and a stricter limit already in place is kept. The chart's table on
  older pages marks the chart's metric like the history table. `.dockerignore` rules match in every folder.

## 1. Goal

A website for the home community's 4v4 NeatQueue scrims, served by the bot itself:

- **Public site:** everyone sees the same pages. No accounts, no per-viewer content.
- **Operator panel:** `/admin` on the same site, behind a password login that only the operator has. Pages are
  read-only; its console (5.1) runs the bot's console commands, with `sql` limited to reads.
- **One Discord server only:** the main one, guild `1363265113880264745`, fixed in code (`HOME_GUILD_ID`).

### User decisions (2026-11-26)
| Decision | Answer |
|---|---|
| Audience | C: public site plus operator panel |
| Access | Public, no visitor logins. Everyone sees the same site. The operator panel is the exception. |
| Servers | Only the main server |
| Scope now | Plan first, build later. Keep planning and reviewing; decide the interface design. |
| Reviews | GPT-6.2 Astra (high) in Codex |
| D1 hostname | `ctier.cooly.dev`. The C-TierBot project must never mention the operator's other domain. |
| D2 snapshots | Keep Game breakdowns forever (`QUEUE_RESULT_RETENTION_DAYS=0` in production; code default stays 90) |
| D4 operator panel | Any method, as long as only the operator gets in. Chosen: `/admin` page with a password login. |
| D6 names | Keep names consistent between the bot and the site |
| Design | Direction A, "the same design language" as the cards, with clean animations for the statistics (bklit-ui charts as the reference; anime.js / GSAP / Motion named as candidate tools) |

### Defaults still standing (user can overrule; marked DEFAULT)
- D3 DEFAULT: ask search engines not to index the site: every response sends `X-Robots-Tag: noindex` and pages
  carry `<meta name="robots" content="noindex">`. `robots.txt` allows crawling, so crawlers can see the noindex.
- D5 DEFAULT: player URLs use the player's survev.de slug (`/players/<slug>`), never the Discord ID. A page covers
  one Discord user (section 3.5).

## 2. Acceptance criteria (observable)

1. With `WEB_PORT` unset or `0`, no web listener opens (the existing console on `CONSOLE_PORT` is unchanged) and the
   bot behaves as today (retention 90 days, logs in `log/`).
   Existing tests pass, apart from the ones this plan deliberately updates (section 10).
2. With `WEB_PORT=8080`, the bot serves these public pages for `HOME_GUILD_ID`:
   - `/`: the latest 10 saved queues, the current month's top 10 by avg dmg, and the Hall of Fame.
   - `/queues?page=N`: every **saved finished queue** of the main server (definition in 3.5), newest first, 25 a
     page. Each row shows date, duration, series score and winner (when stored) and both rosters.
   - `/queues/<number>`: series score, winner, and both rosters with each player's kills, avg dmg and impact. If a
     snapshot exists, one table per game (winner, each player's kills, damage, impact). Without a snapshot, the
     page says the per-game breakdown wasn't saved.
   - `/leaderboard?month=YYYY-MM&stat=damage|impact|kills`: the same ranking and values as
     `/leaderboard_season` for that month and stat, all rows on one page.
   - `/players/<slug>`: a card-style header band (name, the last-16 W-L record and win rate, then the main-server
     totals as text: queues, play time, avg dmg all-time and this month, avg impact; no stat tiles), the trend chart,
     and the queue history (newest first, paged).
   - `/hall-of-fame`: the four records, each shown only if its stored `guild_id` is the main server.
   - `/healthz`: `200 ok`, `Cache-Control: no-store`, whether or not Discord is connected.
3. Leaderboard values and order equal `db.get_seasonal_queue_stats` for the same month and stat. The site calls
   that function, passing in a read-only connection.
4. Names follow the one rule in 3.5: a linked player is shown by their current display name in the main server
   (what a Discord mention of them shows there), falling back to the name the bot stored; an unlinked player by the
   stored in-game name.
5. No **public** response (any status, headers such as `Location` included) contains: an `access_token`, a Discord
   ID, a guild ID, another server's data, a `Player <digits>` fallback name, any run of 15+ digits in a name or
   slug, or diagnostic log content. Every public query and every live member lookup is scoped to the main server.
   One stated exception: a name the bot **stored while calculating a main-server queue** (a stored
   `display_name` or snapshot `name`) may be shown as the fallback name (3.5), even if the bot resolved it in
   another server. The bot stores these before Discord receives the card, and delivery isn't recorded, so the
   exception does not depend on the card having been posted.
6. Invalid values of known params (bad month, unknown stat, page out of range) and unknown queues or slugs get a
   plain 400/404 page, never a 500 or a stack trace. Unknown param **names** are ignored: `/queues?page=1&x=y`
   serves the same page, from the same cache entry, as `/queues?page=1`.
7. Operator panel (section 5):
   - With `WEB_PUBLIC_ORIGIN`, `WEB_ADMIN_PASSWORD_HASH` or `WEB_SESSION_SECRET` unset or malformed, every
     `/admin` path returns the site's standard 404 page (same status and body as an unknown route), but with the
     admin headers (`no-store` etc., section 5).
   - When configured, `GET /admin/login` and `POST /admin/login` are the only paths that need no session. Every
     other `/admin` path without a valid session redirects (`303`) to `/admin/login`, which then serves the form
     with `200`. A wrong password shows "Wrong password" with `401`. The right one sets the session cookie and
     redirects to `/admin/`.
   - After login the panel shows: Discord connection state and latency, uptime, the servers the bot is in, the
     tracked channels, counts (linked users, processed matches, saved snapshots, player stat rows), the newest 50
     diagnostic lookups with readable JSON, and the backup files with size and date. Its console (5.1) runs a
     console command and shows the output; `sql UPDATE ...` there is refused and `users.access_token` reads as NULL.
   - Every `/admin` response (login page included) sends `Cache-Control: no-store`, is never served from the
     public response cache, and is never cached by Cloudflare.
8. Public pages send `Cache-Control: public, max-age=60`, except `/healthz` (`no-store`), pages rendered while the
   member cache is still loading (`max-age=10`, not stored in the page cache, section 3.4), and the old-slug `301`
   (`max-age=60`). 400/404 pages send `max-age=10`; 500 and 503 send `no-store`. Public responses never set or read
   cookies.
9. If the web listener can't start (port in use), the bot logs the error and still connects to Discord. On SIGTERM
   the listener and its worker threads are finished within one 5-second deadline, even with a request in flight, a
   DB read running and a password check running, and the bot shuts down as it does now.

## 3. Architecture

### 3.1 Web server inside the bot process
- `aiohttp.web` on the bot's event loop. aiohttp 3.14.5 is already pinned (discord.py needs it), so this adds no
  web framework.
- `main()` in `leaderboard_bot.py` calls `start_web_server(...)` before `bot.start()`. It wraps the listener setup
  in try/except. On failure it cleans up a half-started runner, logs the error and returns, so Discord startup
  continues. The site doesn't depend on `on_ready`, so it stays up during Discord reconnects.
- **One app, one listener:** TCP `0.0.0.0:WEB_PORT`, which Traefik routes. The operator panel is an aiohttp
  sub-app mounted at `/admin` with its own session middleware, mounted only when the admin config is valid
  (section 5). The admin **headers** are set by a middleware on the root app for every request whose path is
  `/admin` or starts with `/admin/`, whatever produced the response (panel page, login form, redirect, 404 when
  disabled, error), so they never depend on the sub-app existing. Public and admin handlers share no cache.
- **Shutdown, one deadline.** The SIGTERM handler starts one idempotent `shutdown()` task instead of only
  scheduling `bot.close()`. That task records `deadline = monotonic() + 5` **when the signal arrives**. It stops web
  admission right away and runs the web stop and `bot.close()` concurrently. The `finally` in `main()` awaits that
  same task, and starts it if `bot.start` ended for another reason. The web stop:
  1. stop the site accepting connections;
  2. set a `stopping` flag. Every web SQLite connection is opened with `timeout=0.25` (busy wait at most 250 ms per
     lock attempt; the bot's own connections keep the default 5 s), and it has a progress handler that aborts when
     the flag is set or when the **job's** absolute deadline (2 s from admission, covering lock retries and every
     query in the job) has passed. A lock timeout or an abort becomes a `503` with `Retry-After: 5`, never a 500;
  3. `shutdown(wait=False, cancel_futures=True)` on both web executors (DB and password check, 3.4) drops queued
     jobs;
  4. clean up the runner with the time remaining until `deadline` (aiohttp 3.14's shutdown/cleanup timeout
     parameters are checked at build time);
  5. wait for running jobs up to the deadline. A password check is one scrypt call of about 0.1 s, so it ends well
     inside the deadline; DB jobs abort at once (step 2). Python's exit-time join of the worker threads therefore
     doesn't hang.
- **Why in-process:** the data volume holds every user's survev.de `access_token`. A second container on that
  volume would hold every token. One container, one deploy. The web code never selects `users.access_token`.
  In-process is also what lets the site read the bot's member cache for names (3.5).
- **Cost:** every deploy restarts the website too, for a few seconds (the Coolify app runs with no rolling
  updates). Acceptable for this audience.

### 3.2 Files
| File | What it does |
|---|---|
| `web_server.py` | `create_app(guild_id, db_connect, names, admin_config)`, public routes, error pages, public response cache, `start_web_server` / `stop_web_server`. A `__main__` preview entry (below). |
| `web_queries.py` | Read-only, guild-scoped queries. Every function takes a `sqlite3.Connection` and never connects itself: `list_queues`, `get_queue`, `resolve_player_slug`, `get_player_summary` (guild-scoped twin of `get_player_queue_stats_summary`, same formulas), `get_player_history`, `get_hall_of_fame`, `build_queue_roster`. |
| `web_names.py` | `NameResolver`: current main-server display names from the bot's member cache, with the stored-name fallback and the final digit check (3.5). Pure functions apart from reading the cache. |
| `panel_sql.py` | The panel console's read-only `sql`, run as its own short-lived process (section 5.1, r15). Standard library only. |
| `web_admin.py` | The `/admin` sub-app: login/logout, session cookie, throttling (section 5), and panel data (bot status, servers, channels, counts, diagnostic log list/read, backup list). |
| `web/templates/*.html` | Jinja2 templates (autoescape on). |
| `web/static/` | `site.css` (tokens generated from `design_tokens.py`), the self-hosted Quattrocento Sans `.woff2` files, the logo (`logo-96.webp` in the header, `favicon-32.png`, `apple-touch-icon.png`; the master is `assets/logo.png`), `site.js` (motion and chart interaction, an ES module) and `vendor/anime.esm.min.js` + its `LICENSE` (section 4.3). No third-party requests. |
| `web_charts.py` | Server-side SVG geometry for the trend chart and leaderboard bars (scales, paths, dot positions), so charts render without JS and tests can check them. |
| `tools/make_admin_password.py` | Generates a password and its scrypt hash for the env var (section 5). |
| `db.py` (changed) | `get_seasonal_queue_stats` and `get_queue_result` gain an optional `conn` parameter (default unchanged: their own connection). Schema setup and migrations stay as they are for the bot. |
| `tests/test_web_*.py` | `aiohttp.test_utils.AioHTTPTestCase` (unittest) against temp SQLite files. |

- **Read-only connection:** `db_connect()` returns
  `sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=0.25)` for the path the app was
  given. The path is encoded, so `#`, `%` and spaces can't change which file opens. Every web query uses it,
  including the shared `db` helpers (through `conn=`). The exporter (rollout step 2) opens its source the same way.
- **Preview entry:** `python web_server.py --db PATH [--port 8080]`. It parses args and sets
  `os.environ["DB_PATH"] = PATH` **before** importing `db` or `bot_config`, so importing `db` never creates or
  migrates `leaderboard.db` in the working directory. Importing `db` still runs its schema setup on `PATH`, so the
  preview only runs on a disposable copy, never on production. It binds `127.0.0.1` only, with no bot (names use
  the stored fallback, status shows "Discord: not connected"). It refuses to start, **before importing `db`**, when
  the `--db` file is missing or empty, or doesn't open read-only with the `player_queue_stats` and `queue_results`
  tables present. The admin panel works in the preview when the admin env vars are set, with
  `WEB_PUBLIC_ORIGIN=http://127.0.0.1:8080`.
- New dependencies: `jinja2` + `markupsafe`, pinned. Password hashing and session signing use the standard library
  (`hashlib.scrypt`, `hmac`, `secrets`). One vendored front-end file, anime.js v4 (MIT, section 4.3); no npm
  toolchain in the repo or the image.

### 3.3 Config (`bot_config.py`)
- `HOME_GUILD_ID = 1363265113880264745`: a constant, not an env var. The production entry point always passes it.
  The app factory takes `guild_id` so tests can use other IDs.
- `WEB_PORT` (default `0` = off). Not set in the Dockerfile.
- `WEB_PUBLIC_ORIGIN` (production `https://ctier.cooly.dev`): the only `Origin` accepted on admin POSTs (section 5).
  Admin stays off without it.
- `WEB_ADMIN_PASSWORD_HASH`, `WEB_SESSION_SECRET` (secrets, Coolify env only): admin off unless both are set and
  well-formed (section 5).
- `QUEUE_RESULT_RETENTION_DAYS` (default `90`; `0` = keep forever). Read from env and validated (section 6).
- `QUEUE_STATS_LOG_DIR` (default `log`; production `/app/data/log`) (section 7).

### 3.4 Load and event-loop safety
- Web DB reads run in a dedicated `ThreadPoolExecutor(max_workers=2)`, not the default executor that Pillow
   rendering uses. Password checks run in a separate `ThreadPoolExecutor(max_workers=1)` with its own one-slot
  admission (section 5).
- **Admission without waiting:** a plain counter, capacity 4. An uncached public request takes a slot only if one
  is free right now. Otherwise it gets `503` with `Retry-After: 5` at once, so nothing queues behind the counter.
  A slot is released only when its executor job has **finished** (a done-callback on the job's future). If the
  client disconnects, the slot still isn't freed until the SQLite work ends. That keeps at most 4 jobs admitted
  (2 running, 2 queued in the executor), whatever the visitor count. A job's 2-second absolute deadline (3.1)
  bounds how long a slot is held. Admin page reads use the same executor and counter (one operator, small reads).
- Public response cache: only the listed public stats handlers (`/`, `/queues`, `/queues/<n>`, `/leaderboard`,
  `/players/<slug>`, `/hall-of-fame`) use it. Rendered HTML is keyed by route + validated params. 60-second TTL,
  at most 256 entries (LRU). `/healthz`, errors, static files and everything under `/admin` skip it. A page
  rendered while the member cache isn't ready (3.5) is sent with `max-age=10` and not stored in the cache, and
  cached pages are served only while it is ready: when readiness is lost (a guild outage or a re-chunk), every
  cached page is dropped.
- Cloudflare (zone `cooly.dev`, Free plan): a Cache Rule makes `ctier.cooly.dev` responses eligible for cache,
  respecting the origin's `Cache-Control`, **except** URI paths starting with `/admin`, which bypass cache. No
  rate-limiting rule (the free plan can't scope one to this hostname; the counter, cache and login throttle cover
  it).
- **No Pillow rendering on public routes.** The cards are 3200px supersampled renders. Rendering PNGs for
  anonymous visitors would let anyone queue CPU work on the bot. HTML tables only.
- SQLite stays in `delete` journal mode. Web reads are short and use the 250 ms busy timeout. Each page job is
  one read transaction (bounded by the 2-second deadline; about 50 ms at most on production data), so a bot write
  can wait for at most one page job. The bot's queue-result writes (Hall of Fame records, stat rows, snapshot)
  run in a worker thread, one result at a time and in that order, so such a wait never stalls the event loop.
  The bot's other small writes (`/verify`, `/setup`, processed-queue markers) still run on the loop and keep their
  5-second timeout (accepted: rare and short).
- Name lookups (3.5) are dictionary reads on the bot's member cache, done on the event loop after the DB job
  returns. No Discord API call is ever made for a web request.

### 3.5 Data rules for the public site
- **Guild:** every query filters `guild_id = HOME_GUILD_ID`.
- **Saved finished queue** (archive definition): a `match_id` that has a `queue_results` snapshot or
  `player_queue_stats` rows in the main guild (the union; 580 today, 7 of them snapshot-only). It isn't "every
  queue ever played": queues with 4 or fewer observed players or no duration have no stat rows, and queues before
  2026-10-19 have no snapshot. `processed_matches` is not used (it can hold markers for failed lookups).
  - Date: `match_end_ms` / `match_start_ms` from stat rows. For snapshot-only queues, the snapshot's `saved_at`,
    labelled "saved".
  - Duration: from stat rows; "—" for snapshot-only.
  - Roster and score: from the snapshot when there is one. Otherwise from stat rows.
- **Roster per queue:** a snapshot is the authoritative roster for that queue, because a player who switched
  sides appears once per side there. Stat rows (one per player per queue; side switches and stale rows from
  reruns are known limitations, Known Issues 1-2 in MEMORY.md) are used only when there's no snapshot. This plan
  doesn't change the stat-row writer.
- **Names (D6): the same names as the bot.** Checked against the bot's code: Discord mentions in
  `/leaderboard_season` and `/hall_of_fame` (`<@id>`) show a member's **current** display name in the main server;
  `/profile` titles use the member's current display name; result cards show the name stored when the card was
  posted. The site uses one name per person everywhere, matching the mentions:
  - Linked player (has `discord_id`): `NameResolver.name(discord_id)` =
    `bot.get_guild(HOME_GUILD_ID).get_member(discord_id).display_name` (server nickname, then global display name,
    then username: discord.py's `display_name`, the same property the bot's `_member_name` uses). Only the main
    server's member cache is read, so a nickname from another server is never chosen by the site.
  - Until the main server's member cache is complete (`guild.chunked`), every name is the stored fallback, so a
    page never mixes a half-loaded cache with stored names.
  - Fallback when that member isn't in the main server's cache (left the server, or the cache isn't ready yet:
    `guild is None` or `not guild.chunked`): the name the bot stored for that person, newest main-guild source
    first: their newest main-guild stat row's `display_name`, else the snapshot row's `name`, else the Hall of Fame
    record's `display_name`. These are stored during the main-server queue calculation and are normally the names
    on that queue's card (the card itself may have failed to post; delivery isn't recorded).
  - Unlinked player: the stored name, which the bot already sets to the in-game username
    (`neatqueue_client.py:310`), including the `Player (2)` numbering for same-name logged-out players.
  - Final check on every label and every public slug: if it contains a run of 15 or more digits, or matches
    `^Player \d+$` (the bot's `Player {discord_id}` fallback), the label becomes `Unknown player` and a slug isn't
    linked. 0 such stored names today.
  - Consequences, stated on the site's About text: an old queue page shows a player's current name, while the old
    Discord card shows the name they had then. A name change appears within about 2 minutes (60 s app cache +
    60 s Cloudflare cache).
  - Known residual: a stored fallback name was resolved by the bot's `get_user_display_name`, which takes the first
    server where it finds the member, so for a player who has since left the main server it can be a nickname from
    the other server. Usually the bot showed that same name on the main server's card; if that post failed, it
    showed nowhere. Not changed here (a bot change would need approval; noted in Risks).
  - `discord_id` values are used only inside the server for lookups and grouping, and dropped before rendering.
- **Queue roster from a snapshot** (`build_queue_roster`). The site computes series totals itself from the
  per-game rows (each row has its own kills, damage and impact), never from the calculator's aggregated entries.
  - v2 snapshots (new): `build_queue_snapshot` writes, for each **original game row**, an internal `key` built from
    that row alone: `slug:<slug>` when the game row has a slug (a logged-in account), otherwise
    `name:<in-game name, lowercased>#<n>` (a logged-out player; `n` is the bot's same-name guest number, 1 for the
    first, so a literal `Player (2)` and the numbered second `Player` get different keys). A key that appears twice
    on one team in one game is never added up: those rows stay separate with "—" totals, as for v1. It also writes
    the row's own `username` and `slug`. Built before the aggregated entry is
    looked up, so a slug `raven` and a logged-out `Raven` on one team get different keys. `QUEUE_SNAPSHOT_VERSION`
    goes to 2. Grouping is by `(team, key)`. The key, slug and IDs are never rendered. Snapshot-only change; the
    stat-row writer is untouched.
  - v2 linkage is also per game row. Today the writer copies `discord_id`, `guest` and `name` from the aggregated
    entry, whose identity (`player_identity` = slug, else lowercased name, `neatqueue_client.py:412`) is the same
    for slug `raven` and logged-out `Raven`. So in v2 each game row gets `discord_id` only when **that row** has a
    slug and the slug maps to a linked user (`lookup_discord_id`, carried on the game row by the calculator, so
    building the snapshot does no database lookup), `guest` from the same test, and its own game
    name as the stored `name` for unlinked rows. A row without a slug is always unlinked and keeps its own name.
    The site can then show two rows where today's Discord card merges them; the site's split is the correct one
    (recorded as a Known Issue for the card, not changed here).
  - v1 snapshots (existing): linked rows group by `(team, discord_id)`. Unlinked rows group by `(team, name)` only
    when that name appears at most once on that team in every game. Otherwise their per-game rows stay separate
    and their series totals show "—". Each game's table shows its Impact column when any of that game's rows has
    a score, whatever the series totals show.
  - Name-only players (no slug, v1 or v2) can't be told apart from one game to the next, so two same-name
    substitutes in different games merge. Their series totals carry a small "combined by name" marker, with a
    footnote on the queue page.
- **Player pages are per Discord user (D5),** like `/profile`, so one person has one page and one name.
  - `/players/<slug>` → `resolve_player_slug(slug)`: the Discord users whose `users.slug` equals the slug **and**
    who have at least one main-guild stat row. One → that user. Several (none today) → the one with the most
    main-guild queues, tie → lowest `discord_id`. None → look for main-guild stat rows whose **row** `slug` equals
    it (an account the user played on before re-running `/verify`): if those rows belong to exactly one Discord
    user, `301` to `player_url` of that user (below) when it isn't `None`; otherwise 404. An account linked only
    through another server has no page. A slug that fails the final digit check is 404 without a lookup. A slug
    is 1-64 characters of `A-Za-z0-9_.-` (the whole string, so no trailing newline) and never `.` or `..`, which a
    browser would resolve away.
  - The page covers all of that user's main-guild stat rows (every account they played on), headed by their name
    (above).
  - **One helper builds every public player URL:** `player_url(discord_id)` → that user's current
    `users.slug`, only if the slug passes the final digit check **and** `resolve_player_slug(slug)` returns that
    same `discord_id`. Otherwise `None`. Roster links, leaderboard links, Hall of Fame links and the old-slug
    `301` all use it: an old slug redirects only when `player_url` of its single owner is not `None`, else 404. So
    a redirect can't point at a filtered slug, and a user who lost a shared slug to someone else gets an unlinked
    name rather than a link to the other person's page.
  - Unlinked names aren't linked.
- **Hall of Fame:** a record is shown only when its `guild_id` is the main server, otherwise "No record yet". No
  schema change. Holder name by the name rule. Its queue (and game) number links to the queue page only when that
  queue still has one (main-server stat rows or a readable snapshot); otherwise the numbers are plain text, since a
  record can outlive its queue's rows and snapshot.
- **Player totals and history:** main-server-only, so the numbers can differ from Discord `/profile`, which counts
  every server. Both cover **queues with saved player stats** (`player_queue_stats`), like `/profile`.
  Snapshot-only queues (7 today) are not counted or listed on player pages, though they appear in the archive. The
  page says "Queues with saved stats in this server".
- **Legacy rows** (before Queue #4520) lack kills, impact and score: the site shows "—", never 0. The web history
  uses its **own stricter** snapshot enrichment. A snapshot row matches by `discord_id` when both sides have one.
  It matches by name only when the snapshot row has no `discord_id` **and** that name is unique on that team in
  that game. Otherwise the value stays "—". (The Discord `get_player_queue_history` matches by name more loosely.
  That's recorded as a Known Issue, not changed here.)
- Only final results are stored, so the site never shows IN PROGRESS. The P1 issue (finished queues with missing
  scoreboards saved as final) is inherited as-is (Risks).

### 3.6 Name resolution in the bot
- Unchanged. The site reuses the same discord.py `display_name` the bot uses, scoped to the main server (3.5).

## 4. Interface design (direction A, chosen by the user)

### 4.1 Fixed for every direction
- Server-rendered HTML; every page works without JavaScript. No third-party requests: no web fonts from a CDN, no
  analytics, no external images. A strict CSP (`default-src 'self'; img-src 'self'; style-src 'self';
  script-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'`).
- Header: the C-TierBot logo and name; nav: Queues, Leaderboard, Hall of Fame. No link to `/admin` anywhere on
  public pages.
- Footer on every page: `Stats from survev.de and NeatQueue. Not affiliated with either.` (the card credit line,
  extended with the non-affiliation line `PRODUCT.md` requires).
- Copy as on the cards: `avg dmg`, sentence case, `Team 1 wins 4-2`. Win/loss is never shown by colour alone.
  In the queue list (home, archive) the winner is shown quietly, as the user asked: the winning roster sits on a
  faint `WIN` wash with its "Team N:" label in `WIN`, the losing one on a faint `LOSE` wash, and the words "Team N
  won" are screen-reader text; the score beside it says the same without colour. A row with a winner but no score
  shows "Team N won" as text.
  Numbers: comma thousands, impact as whole numbers, `—` for missing values (never 0).
- Times in UTC with the date (`26 Nov 2026, 19:02 UTC`). Durations without seconds.
- Tables keep their columns on a phone: the table scrolls sideways inside its card and the player column stays
  pinned. Contrast meets WCAG AA for text. Tabular figures for every number column.
- The rejected card designs (podium/summary banner on the season leaderboard, boxed centre score, `(unlinked)`
  text, impact explanation subtext, `Oldest ───► Newest` label) stay off the web too.
- Domain separation: own favicon and templates; nothing links to, embeds from or mentions the operator's other
  domain; no shared analytics or tag IDs (there are none). **No link to the GitHub repo** until the user has
  decided otherwise: GitHub's cached copies of the pre-scrub commits stay reachable (section 9, step 0).

### 4.2 Direction A: the card design language on the web
- **Visual system:** the locked card tokens (`design_tokens.py`, `design-system/MASTER.md`) and nothing new: canvas
  `CANVAS`, panels `PANEL` with `DIVIDER` borders, alternating `PANEL_RAISED` row pills (radius 10), team panels
  with `WIN` / `LOSE` borders and `WIN_BANNER` / `LOSE_BANNER` heads, the two-lane MATCH TIMELINE, `ACCENT` for
  avg dmg, the MVP pill, the `played/total` label and the broken-chain unlinked icon. Quattrocento Sans,
  self-hosted (the `.ttf` files in the repo, converted to `.woff2` at build). Card text sizes are scaled for
  1600 px canvases, so the web uses its own rem scale with the same weights and roles. Tokens are emitted into
  `site.css` as CSS custom properties generated from `design_tokens.py`, so the cards and the site can't drift.
- **Pages** follow the mockups on the private design canvas: queue page (series header with score and timeline,
  two team panels, game breakdown), leaderboard, player page, plus home, archive and Hall of Fame in the same parts.
- **Charts (new, the "statistics" the user wants animated),** drawn as server-rendered inline SVG, so they are
  complete without JavaScript:
  - **Player trend** (player page): the History card on the web. The player's last 16 main-server queues with
    saved stats (section 3.5), one series (avg dmg, avg impact or kills per game, switched by three buttons worded
    "Avg dmg", "Avg impact", "Kills per game" that are links without JS: `?metric=`, keeping the history page; on
    history page 2 and later, where the table below holds older queues, a closed `<details>` under the chart lists
    the chart's 16 queues as a table, r15). One function, `trend_value(row, metric)` in `web_charts.py`, gives the value
    for the SVG, tooltip, accessible label and table alike: avg dmg = the row's `avg_damage`; avg impact = the
    row's `impact`; kills per game = the row's queue `kills` divided by **that player's own game count** for the
    queue (`games`, else the existing legacy inference `round(total_damage / avg_damage)` when `avg_damage > 0`,
    as in `db.get_seasonal_queue_stats`). A NULL value or an unknown or zero denominator gives a missing point,
    never 0. (The Discord History card plots total kills; the web labels and computes kills per game.) `ACCENT` line (2.5 px, non-scaling), area fill `ACCENT` 28% → 0% gradient, hairline `DIVIDER`
    gridlines (3 ticks), dots coloured by result (`WIN` / `LOSE`, `TEXT_MUTED` for unknown) with a 2 px panel ring,
    x labels `#match` on every third queue. Gridlines sit at the bottom, middle and top of the plot on round values
    that always contain every point. Loss dots are hollow rings (result never by colour alone); the latest point has
    the card's `ACCENT` ring, and its value is in the chart header (no per-point pills). The recent queues table
    under it is the table view, its active metric column header in `ACCENT`.
  - **Leaderboard bars:** a thin `ACCENT` bar on a `TIMELINE_LANE` track under each name, length = value / top
    value, ending before the value column so a value never sits on its bar. Values, rank and games stay text, in
    `/leaderboard_season`'s own formats (`{:,.0f}`, kills `{:,.1f}`). No podium or summary banner (rejected design).
  - Number formats match the bot: averages `{:,.0f}` as on the cards, impact rounded half up as on the cards.
  - Dot colours were run through the dataviz validator against `PANEL`: win/lose colour-blind separation passes
    (ΔE 11.1 deutan), contrast passes; the "lightness band" and "chroma" checks flag the muted grey, which is the
    intentional "no result" status colour, and every dot's result is also in text (tooltip, table).

### 4.3 Motion
- **Library: anime.js v4** (MIT), vendored as one pinned file `web/static/vendor/anime.esm.min.js` with its
  `LICENSE`, the version and SHA-256 recorded in `docs/DEVELOPMENT.md`-style notes (README "Website" section).
  It has SVG line drawing (`createDrawable`), `stagger`, timelines and a viewport observer (`onScroll`). Loaded as
  `<script type="module" src="/static/site.js">`, which imports it, so `script-src 'self'` holds. Not chosen:
  GSAP (custom, revocable licence that doesn't clearly allow committing the file to this public repo, and a CDN
  would break the no-third-party rule); Motion (MIT and fine, but adds nothing anime.js lacks here). bklit-ui is
  React-only (d3 + Motion), so its look is reproduced in vanilla SVG, not imported.
- **Thesis:** motion tells the series story and makes the numbers readable as they change; nothing decorates.
  - **Focal moment (queue page):** the MATCH TIMELINE plays the series once: each game's dot drops into the lane of
    the team that won it (T1 dots rise from the midline, T2 dots fall to it), 380 ms each, 140 ms apart, and the
    series score counts up as each dot lands, with a 220 ms tick on the digit. The MVP pill fades in after the
    last dot. About 1.3 s in all, played once per page view, only when the header is on screen.
    - Played only when the snapshot's ordered per-game winners exist **and** counting them reproduces the stored
      series score. Otherwise the stored score shows from first paint with no count. A game with no known winner
      is the card's hollow `TEXT_MUTED` ring on the midline (MASTER 5.1) and takes part in the sequence without
      counting. A queue with no snapshot (stat rows only) has no per-game winners, so it shows the stored score and
      no timeline or replay.
  - **Charts (bklit-style):** the trend chart reveals left to right with a clip-path over 1100 ms
    (`cubic-bezier(0.85, 0, 0.15, 1)`, bklit's reveal curve); each dot pops in (280 ms) as the reveal passes it;
    the latest-value pill fades in at the end. A crosshair (1 px muted rule) and tooltip (value first, then
    "Queue #4810 · won 4-2" with a result-coloured line key) follow the pointer and snap to the nearest queue,
    with a 180 ms eased follow. The crosshair snaps into place when it first appears and glides after that; moving
    between points with the arrow keys keeps the crosshair and tooltip up, so they glide too. Switching metric
    tweens the line, area and dots to the new values over 500 ms (bklit's y-domain tween) without a page load when
    JS is on; the three tick labels and the header's latest value count from the old metric's numbers to the new
    ones in step and end on the server's exact text. A switch made while another is still tweening stops it and
    starts from what is on screen, so the line never jumps and no older value lands under the new unit.
    - Points keep fixed chronological queue slots across metrics and are matched by queue number, never by array
      position. Points present in both metrics move; points missing in the new metric fade out and new ones fade
      in; the line breaks across a missing point rather than bridging it. If the new metric has no values at all,
      the chart shows "No impact scores in these queues" (or the metric's wording), no line, crosshair, active
      point or value pill. If keyboard focus was on a point that disappears, focus moves to the nearest remaining
      point, or to the metric button when none remains.
  - **Page changes:** in browsers with cross-document View Transitions, every page change cross-fades (the old
    page out in 140 ms, the new one in over 260 ms). The opt-in `@view-transition { navigation: auto; }` is in
    `site.css`, only inside `@media (prefers-reduced-motion: no-preference)`, so reduced motion gets a plain
    navigation with no browser cross-fade, with or without JS. Other browsers just load the page.
  - **Leaderboard:** on first view, bars grow from the left (500 ms, ease-out, 30 ms stagger, capped at the first
    10 rows) and values count up in step. Changing "Rank by" or month is a normal link; with View Transitions,
    rows slide to their new ranks and the "Rank by" selection slides to its new option (420 ms):
    - a row's `view-transition-name` is `vt-` plus a short hex digest of its validated public player URL from
      `player_url` (section 3.5), carried in a `data-vt` attribute. `leaderboard.css` (loaded only by leaderboard
      pages) reads it with `attr(data-vt type(<custom-ident>), none)`, so the name is there at first paint; where
      the browser lacks that `attr()`, script sets the same name (CSP allows CSSOM writes, not `style`
      attributes). Only leaderboard pages name rows. Rows without a player URL get no name and take the ordinary
      page change. Names are therefore unique per page even when display names repeat, and no Discord ID ever
      reaches the DOM.
    - Leaving a leaderboard page for any other page drops the names (`pageswap`), so rows fade with the page
      instead of flying across it; a row or selection named on only one side of a page change fades with the page.
      Returning from the browser's page cache puts the names back.
  - **Continuity and feedback:** game buttons on the queue page swap the game tables in place with a 260 ms
    fade-and-rise, and the two panels' borders and heads change colour to that game's winner over 240 ms (the
    MASTER rule that game panels follow the game's winner: the newly shown panels start in the outgoing game's
    colours and ease to their own). The selected game's timeline dot gets the `ACCENT`
    ring, which fades in (it is always drawn, transparent when not selected). Hover and focus colour changes
    120-160 ms. Counting numbers animate an `aria-hidden` copy next to screen-reader text holding the final value.
  - **Timeline dots pick games:** each dot in the queue page's MATCH TIMELINE is a link to its game
    (`?game=N#games`), labelled "Game N, Team M won", with `aria-current` on the selected one. The timeline's markup
    runs one column per game, so Tab goes through the dots in game order. With JS a dot selects its game like the
    game buttons (same fade, colour ease and URL), then brings the breakdown into view if the shown game's tables
    start below three quarters of the window (smooth scroll, instant under reduced motion). Hover tints the ring
    `ACCENT` at 45 %, keyboard focus turns it `TEXT`; the target is 24 px wide (the narrowest column) by 32 px.
  - **Rows as links:** a whole queue row (home, archive) opens its queue, and a whole leaderboard row with a player
    page opens that page: the row's own link is stretched over it, so there is one link per row, the keyboard
    focus ring and the hover ring (`TIMELINE_LANE`, 2 px) wrap the row, and screen readers hear just the link.
- **Timing tokens:** feedback 120-160 ms; state 240-260 ms; layout 420-500 ms; focal 1100-1300 ms. Arrivals
  `cubic-bezier(0.16, 1, 0.3, 1)`; the chart reveal uses bklit's in-out curve. No bounce, no loops, nothing
  replays on scroll back.
- **Rules that keep it safe:**
  - The server renders every value, dot and bar in its final state. Script sets the starting state just before
    animating, so a slow or blocked script never hides content, and screen readers get the final values (counters
    and the score digits are `aria-hidden` duplicates of real text such as "Series score 4 to 2"; the timeline's
    dots are links named after their games).
  - `prefers-reduced-motion: reduce` → no movement, no counting, no reveals, no page cross-fades: values and
    charts appear in final form, crosshair and tooltip still work (instant). Colour feedback (hover, selection,
    the game panels' winner colours) still eases, since nothing moves.
  - Only `transform`, `opacity`, `clip-path` and colours animate; charts are at most 50 points of SVG; triggers
    use an IntersectionObserver and fire once per element.
  - Tooltip text is set with `textContent`, never `innerHTML`. Each chart point has exactly one exposed focus
    target: an HTML `<button>` (never inside an `aria-hidden` ancestor) with an `aria-label` ("Queue #4810, won
    4-2, 1,381 avg dmg") whose hit area is its whole column of the plot at full height, so targets never overlap
    and each is centred on its point (24 px+ wide on desktop; on a narrow phone a column can be narrower, and the
    queues table under the chart is the equivalent control, with every value and link). The SVG circle, its ring and any animated copy are decorative and
    `aria-hidden`. Tab and arrow keys move the crosshair. Missing points have no button.
- Prototype on the design canvas (private claude.ai link in MEMORY.md): queue page, player page and leaderboard
  with the motion above, plus a "replay motion" control that exists only in the prototype.
- Build-time design work uses the `impeccable` skill (its `animate` and `polish` passes, code-led; the direction
  is pinned, so no direction roll) and writes `design-system/pages/web.md`, so `MASTER.md` stays the cards'
  source of truth.

## 5. Operator panel: `/admin` with a password login (D4)
- **Single operator, password only.** No usernames, accounts or signup. Off (404) unless
  `WEB_PUBLIC_ORIGIN`, `WEB_ADMIN_PASSWORD_HASH` and `WEB_SESSION_SECRET` are all set and well-formed; a malformed
  value logs a configuration error (never the value) at startup.
- **Password and hash.** `tools/make_admin_password.py` (run on the operator's PC) generates a random password
  (`secrets.token_urlsafe(24)`, 32 characters), prints it once for the password manager, and prints its hash:
  `scrypt:32768:8:1:<salt hex>:<hash hex>` (`hashlib.scrypt`, n=2^15, r=8, p=1, 16-byte salt, 32-byte key,
  `maxmem=64 MiB`). Hex and `:` only, so no `$` for Coolify or the shell to interpolate. The operator pastes the
  hash into the Coolify env var `WEB_ADMIN_PASSWORD_HASH`. A user-chosen password is accepted by the tool only
  when it is 16 to 128 printable ASCII characters, so its form-encoded POST always fits the 1 KB body limit.
- **Where the password goes:** the operator's password manager, and HTTPS from their browser to the login
  handler, which hashes it in memory and drops it. It is never stored on the server, written to the repo or the
  env, or logged; only its hash is stored.
- **Session secret.** `WEB_SESSION_SECRET`: 32 random bytes as 64 hex characters, generated by the same tool.
- **Login** (`GET /admin/login` form, `POST /admin/login`):
  - The POST must carry `Origin` equal to `WEB_PUBLIC_ORIGIN` (else `403`), `Content-Type:
    application/x-www-form-urlencoded`, and a body under 1 KB.
  - Throttle, checked before any hashing: **one global limit.** 20 failed attempts within an hour → every login
    gets `429` with `Retry-After` until an hour after the 20th failure, whoever sends it. With a 32-character
    random password that bounds guessing far below any useful rate. There is no per-IP limit: the client address
    can't be verified (`CF-Connecting-IP` is set by Cloudflare for internet traffic, but another container on the
    Docker network can reach port 8080 directly and send any value), and a forged per-IP bucket could be aimed at
    the operator. `CF-Connecting-IP` is only logged, labelled "reported IP". State is in memory and resets on
    restart. Accepted trade-off: anyone can keep the panel's login closed by failing 20 times an hour; the SSH
    console is unaffected, and a redeploy resets the counter.
  - Verification runs `hashlib.scrypt` in the one-thread password executor (3.4) behind a one-slot admission,
    like the DB counter: the slot is taken **before** the job is submitted and released only by a done-callback on
    the executor future, after the failure or success has been counted. If the client disconnects or the handler
    is cancelled, the slot stays taken until scrypt has really finished. A login that finds the slot taken gets
    `429` at once and submits nothing, so logins can't queue CPU work or touch the DB executor. Comparison is
    `hmac.compare_digest`.
  - Success: `303` to `/admin/` with the cookie below; the global failure count is left as it is. Failure: `401`
    with "Wrong password" under the field (`aria-invalid`, `aria-describedby`). Each attempt logs `admin login ok|failed` with time and IP, never the password or cookie.
- **Session cookie** `__Host-ctier_admin` (`Secure; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200`):
  `base64url(json {"iat", "exp"}) + "." + base64url(HMAC-SHA256(key, payload))`, where
  `key = HMAC-SHA256(WEB_SESSION_SECRET, WEB_ADMIN_PASSWORD_HASH)`. So changing the password or the secret ends every
  session. Sessions last 12 hours from login (no sliding renewal). The check rejects a bad signature, an expired
  `exp`, or an `iat` more than 60 s in the future, and redirects to the login page.
- **Logout:** `POST /admin/logout` (same Origin check) sends an expiry cookie with the same name and the same
  attributes the `__Host-` prefix requires: `__Host-ctier_admin=; Secure; HttpOnly; SameSite=Strict; Path=/;
  Max-Age=0`, no `Domain` (aiohttp's `del_cookie` alone omits `Secure`, which browsers reject for this prefix, so
  the code sets the cookie explicitly). Then `303` to the login page. Sessions are stateless, so "log out
  everywhere" means changing `WEB_SESSION_SECRET` and redeploying.
- **Headers on every `/admin` response** (set by the root-app middleware, 3.1, so they also cover the disabled
  404, redirects and errors): `Cache-Control: no-store`, `X-Robots-Tag: noindex`, the strict CSP from 4.1,
  `Referrer-Policy: same-origin` (not `no-referrer`: under `no-referrer` browsers send `Origin: null` on the
  login and logout form POSTs, which the Origin check would reject), `X-Content-Type-Options: nosniff`. The public
  response cache never sees
  `/admin`, and the Cloudflare Cache Rule bypasses it (3.4).
- One gate at the `/admin` path boundary: with the panel configured, every `/admin` path except `GET` and `POST
  /admin/login` needs a session (the bare `/admin`, unknown paths, logout and any method go to the login page).
  `WEB_PUBLIC_ORIGIN` must be https and is stored in the canonical form browsers send (lower-case host, no
  default port); http is accepted only for a loopback host and only by the local preview's entry point; a hash whose scrypt memory would exceed the
  verify limit is refused at startup. The lockout check and the password-slot reservation happen in one locked step.
- Every panel page is a GET; the console (5.1) and logout are the only POSTs. **Non-goals:** changing data with
  `sql`, and `/setup`, from the panel. They stay in `console.py` over SSH.
- The panel validates log filenames against `^queue_stats_[0-9A-Za-z_.-]+\.json$` with no path separators. A
  file that's missing (pruned between listing and reading) returns "No longer available". A file that won't parse
  shows as "Unreadable" in the list. Log JSON is rendered as escaped text.
- Why not the earlier SSH tunnel or Cloudflare Access: the user wants a simple page with a login. Cloudflare
  Access isn't enabled on the account, and its login page would carry an account-wide team name shared with the
  operator's other domain.

### 5.1 Panel console (r14, hardened in r15 and r17)

The user asked for the console commands in the panel and chose every command, with `sql` read-only.

- **What runs it:** `console_service.PanelConsole`, which the bot builds and passes to `start_web_server(...,
  console=...)`; `web_server` never imports the console or Discord. In the preview there is no bot, so the console
  section says it's off.
- **Commands:** `help`, `status`, `servers`, `channels`, `queue`, `winrate`, `sql`, `send`, `reply`, `dm`, `delete`,
  `sync` and `stop`, through the same handlers as the SSH console (`execute_console_command`), with the output
  captured per command. Differences: `sql` is read-only (below); `stop` only explains itself until `stop confirm`,
  which answers with a reply of its own (a short page without JS); once the website has sent that reply it tells
  the console (`stop_after_reply`), which calls the bot's `request_shutdown` 0.5 s later (Coolify restarts the
  container); if that report never comes, the bot stops 15 s after the confirm anyway. `exit` and `quit` explain
  that there's no prompt to leave; `clear` clears the page's transcript.
- **One at a time:** a second command while one runs gets "Another command is still running" (not queued). A
  command gets 90 s (under Cloudflare's 100 s proxy timeout), then "Stopped waiting"; output over 200,000
  characters is cut.
- **Read-only `sql` (r15):** each query runs in a short-lived process, `python -I panel_sql.py`, which gets the
  request as JSON on stdin and only `PATH`, `TEMP`/`TMP`/`TMPDIR`, `LANG`/`LC_ALL` and `SYSTEMROOT` from the
  environment (no tokens or secrets). The panel kills it 3 s after the query's own 2-second limit, and on a
  cancelled request or a website shutdown; the console stays busy until it has exited (its pipe is drained so the
  exit is seen, within 3 s). The worker cuts its reply at 500,000 characters; a worker that sends more than 4 MB is
  stopped and the panel says the result was too large. On Linux the process limits itself to 4 s of CPU, 1 GiB of
  address space, 256 MB per file (SQLite's temporary sort files) and 64 open files, keeping any stricter limit
  already in place; if a limit can't be set, the query doesn't run. Inside: its own `mode=ro` connection with `PRAGMA query_only`, a 2-second
  progress-handler limit, one statement, the first 200 rows, cells cut at 300 characters, `SQLITE_LIMIT_LENGTH`
  1 MB, 64 columns, no attached databases, no sort threads, and an authorizer with allow-lists: the bot's own
  tables and their listed columns (`panel_sql.READABLE`; the schema tables too), SQLite's built-in functions
  (`panel_sql.FUNCTIONS`), recursive CTEs, `json_each`/`json_tree` and the schema-lookup pragmas (`table_info` and
  the like; a table or view in the database with one of those names is refused like any other unlisted table). `users.access_token` reads as NULL wherever it appears (`SQLITE_IGNORE`), with a note; a column that
  isn't listed (added later) reads as NULL with a note, and a test fails until db.py's every column is listed or
  hidden (a declared column named `rowid`, `oid` or `_rowid_` too: only the implicit row ID is free); a table that
  isn't listed (an SSH-made copy, a view, `sqlite_dbpage`, `dbstat`) and a function that
  isn't (`load_extension`, an extension's) are refused by name. A whole-table read such as `count(*)` is allowed on
  any table: a row count, never a value. `JOIN ... USING` and `NATURAL JOIN` are refused (their words outside
  quotes and comments), because SQLite's authorizer never sees the columns they compare.
- **HTTP:** `POST /admin/console` needs a session, `Origin` equal to `WEB_PUBLIC_ORIGIN`, a form body under 8 KB
  (read within 5 s, at most 8 login or console bodies at once, else 408 or 503) and a command of at most 2,100
  characters. Each form carries a run ID (the panel's hidden field; `site.js` sends a fresh one per command): the
  site remembers the last 32 finished runs for 10 minutes (a run still going is never dropped), bound to the
  session (each sign-in's cookie has its own random ID), and a second post of the same run ID gets the first run's
  output (or "still running", or "stopped before it finished" for a command that failed or was cancelled) instead
  of running the command again. With `Accept:
  application/json` it returns `{"command", "lines": [{"text", "kind"}]}`, which `site.js` appends to the
  transcript; otherwise it redirects (303) to `/admin/?run=<id>#console`, the panel showing that run's output, so
  the console works without JavaScript and a reload, Back or a busy-dashboard retry never repeats a command.
  `GET /admin/console` redirects to `/admin/#console`. The container log gets `admin console: <command name>
  (reported IP ...)`, never the message text or the query.
- **Page:** a terminal-style transcript (`role="log"`, monospace, scrolls) under the bot status, the
  `c-tierbot>` field and Run button, quick buttons for `status`, `servers`, `winrate`, `help` and Clear (JS only), a
  note on the two differences, and the command list in a closed `<details>`. Errors read in the lose red, notes in
  the accent, confirmations in the win green; the terminal's `[Console]` prefixes are dropped. Up and Down recall
  this page's earlier commands (never stored). A running command shows "Running…", which pulses only without
  reduced motion; while it runs, Run and the quick buttons, Clear included, are disabled. If the session ended,
  the command doesn't run and the transcript says to sign in again.

## 6. The 90-day window (D2: keep forever)
- Today `db.save_queue_result` deletes `queue_results` snapshots whose `saved_at` is more than 90 days old
  (`QUEUE_RESULT_RETENTION_DAYS = 90`). Those snapshots are the only place per-game numbers live (Game breakdown,
  "Most Damage in a Single Game").
- Live DB (2026-11-26): 350 snapshots, all main server, oldest 2026-10-19, 1.8 MB (~5 KB each). Nothing deleted
  yet; the first deletion would happen around 2027-01-17. Growth with no limit: ~18 MB a year.
- Change: the retention is read from env `QUEUE_RESULT_RETENTION_DAYS`, **default still 90** (code and tests). `0` =
  keep forever: `save_queue_result` skips the delete, `_seed_single_game_damage_record` uses no cutoff, and
  `queue_games_unavailable` drops "or it's older than N days".
- Validation: one `normalize_retention_days(value)` is used for the env value **and** for any explicit
  `retention_days` argument, before any prune or seed. A non-integer, negative, or over-36,500 (100 years) value
  logs a configuration error and is treated as `0`, so nothing is deleted. One `snapshot_cutoff(now, days)` gives
  the cutoff for both saving and seeding. It returns "no cutoff" for `0`, and also when the date arithmetic would
  overflow, so a units mistake like `7776000` can never raise inside a result post or the startup migration.
  (Otherwise `-1` would put the cutoff in the future, and the next save would delete every snapshot, including
  the new one.)
- Production sets `QUEUE_RESULT_RETENTION_DAYS=0` (user decision; a rollout step independent of `WEB_PORT`). With
  `0`, the Discord Game breakdown button keeps working past 90 days too.
- Seeding note: the Hall of Fame single-game seed has already run in production (the column exists), so `0`
  doesn't re-seed there. On a fresh DB, `0` means older higher snapshots can win the seed. Tests cover both.
- Tests to update or extend: `tests/test_queue_games.py:112` (implicit save prunes a 91-day snapshot at 90),
  `:132` (default is 90), `:400` (unavailable text), `tests/test_queue_result_logic.py:183` (seed migration
  fixture). Each runs at 90 (unchanged expectations) and at 0 (new expectations). New: `-1`, `"abc"` and `7776000`
  keep old and just-saved snapshots, log an error, and raise nothing through `save_queue_result` or the seed
  migration.
- README line 86 says "kept for 90 days". It becomes "kept for 90 days by default; this bot keeps them".

## 7. Diagnostic logs for the operator panel
- Today: `log/` in the container filesystem, newest 200 kept, wiped by a redeploy.
- Change: `QUEUE_STATS_LOG_DIR` from env (default `log`). Production `/app/data/log`, so logs survive redeploys.
  The 200-file cap still applies.
- `write_queue_stats_log` writes to `<name>.json.tmp` (created exclusively; a name already in use is never
  reused or removed) and `os.replace`s it to the final name, so the panel never lists a half-written file (`.tmp`
  doesn't match the listing pattern). Each name ends in a random `_r<8 hex>` tag, so two lookups of one queue in
  the same clock tick get two files. The panel reads only regular files (no links, FIFOs or devices), in 64 KB
  chunks that stop at the job's deadline or a shutdown. Pruning ignores `.tmp` files; leftover
  `.tmp` files older than an hour are deleted during pruning.
- Logs contain IDs and no tokens (existing privacy contract). They're shown only in the operator panel.
- The off-site backup syncs only the backups folder, so `/app/data/log` isn't uploaded. No change.

## 8. Non-goals
- Visitor logins, per-viewer pages, "my stats". The single operator login is the only login.
- Other Discord servers.
- Live / IN PROGRESS queues on the web.
- PNG card rendering on the web.
- Data changes from the operator panel (its `sql` only reads; r14) and `/setup` there. Discord actions from its
  console (`send`, `dm`, `reply`, `delete`, `sync`, `stop`) are in scope since r14, at the user's request.
- survev.de live data (inventories, market, squad tier, golden fries). They need per-user tokens and live calls.
- Changing the Hall of Fame schema, the stat-row writer (side-switch rows, stale rows), or the bot's
  `get_user_display_name`; fixing the P1 queue-accuracy TODOs, the `game_num` catch-up bug, or the Discord
  history's loose name matching.

## 9. Rollout (each production step waits for the user's explicit go-ahead)
0. **History scrub (user decision 2026-11-26: "completely remove github history with any references").**
   Done on 2026-11-26: the few `main` commits whose `MEMORY.md` held the other domain and the VPS address were
   replayed with only those lines changed and force-pushed; the clean baseline is `main` at `d73dc97`. Every older
   commit, both other branches and all PR refs kept their hashes; a fresh clone of every fetchable ref has none of
   the values. The old-to-new hash mapping is kept only in the private myVPS inventory, and public files never cite
   a pre-scrub hash (they would point readers at GitHub's cached copies). GitHub still serves the five old commits by hash, and the repo's Activity page
   links the force-push; the user chose to leave those cached copies (no Support request, no repo recreation).
   So the site **never links to the GitHub repo**. The VPS agent's clone was reset to the new `main`; the other
   computer's clone must be reset to `origin/main` before use, never merged.
1. Build on a branch (`web-platform`) with tests. User reviews the diff.
2. Local preview on an **export**, not a copy, of production data:
   - Write `tools/export_preview_db.py` (`tools/` stays out of the image; it's piped into the container). It:
     - opens the source read-only through the encoded URI (3.2);
     - reads every table inside **one** read transaction (`BEGIN`, then all the SELECTs), so a queue saved during
       the export can't give it mixed versions. In `delete` journal mode that holds a shared lock for about a
       second, and the bot's writes wait within their 5-second busy timeout;
     - creates the output in a fresh `tempfile.mkdtemp()` directory (mode `0700`) in the container's own `/tmp`,
       never on the data volume. The file is created with `os.open(O_CREAT | O_EXCL, 0o600)` and refuses to reuse a
       path;
     - copies `guild_settings`, `processed_matches`, `hall_of_fame`, `player_queue_stats` and `queue_results` by
       explicit column list, and from `users` only `discord_id, slug, username`, with `access_token` set to `''`.
       The new file never held a token, so no leftover pages can contain one;
     - checks the file itself: `SELECT count(*) FROM users WHERE access_token != ''` = 0, and a byte search of the
       file for every real token (compared in memory, never printed) finds nothing;
     - prints the row count of each table to stderr (never data), streams the file to stdout, and deletes its temp
       directory in a `finally`, on success or failure.
   - On the VPS: `umask 077; D=$(mktemp -d)`, then
     `docker exec -i <container> python - < export_preview_db.py > $D/preview.db.part`. Continue only if the exit
     code is 0. Check the received file read-only on the host (`sqlite3 -readonly`): `PRAGMA integrity_check` =
     `ok`, every table present, row counts equal the exporter's. Then `mv` it to `$D/preview.db`, `scp` it to the
     session scratchpad and `rm -rf $D` on the VPS. On any failure `rm -rf $D` and stop.
   - Run `python web_server.py --db <scratch export> --port 8080` (127.0.0.1; it migrates the copy's schema
     before listening) and click through every page,
     including the admin login with a throwaway preview password. Delete the export when done.
3. Merge to `main` and push (auto-deploys). With no new env vars set, production behaves as before.
4. Production config:
   - Coolify env: `WEB_PORT=8080`, `WEB_PUBLIC_ORIGIN=https://ctier.cooly.dev`, `WEB_ADMIN_PASSWORD_HASH`,
     `WEB_SESSION_SECRET` (both marked secret; the operator generates them with the tool and pastes them in),
     `QUEUE_STATS_LOG_DIR=/app/data/log`, `QUEUE_RESULT_RETENTION_DAYS=0`.
   - Coolify app: domain `https://ctier.cooly.dev`, `ports_exposes 8080`. Health check stays off (Uptime Kuma
     watches instead, so a web problem can never block a bot deploy). Traefik issues a Let's Encrypt certificate
     for `ctier.cooly.dev` alone.
   - Cloudflare zone `cooly.dev`: one proxied `A ctier` record to the VPS (address from the private inventory; no
     wildcard, never unproxied). The Cache Rule from 3.4 with the `/admin` bypass.
   - Redeploy.
5. Verify (section 10 manual) and add an Uptime Kuma HTTP monitor on `https://ctier.cooly.dev/healthz`.
6. Update the private myVPS inventory (app domain, DNS record, cache rule, env var names, Maintenance Log) and the
   C-TierBot docs.
- **Rollback:** unset `WEB_PORT` and redeploy (site and panel both off). Retention and log dir are separate env
  vars and can be rolled back on their own. Unsetting `WEB_ADMIN_PASSWORD_HASH` turns only the panel off.

## 10. Verification
Automated (host runs these):
- `python -m unittest discover tests`: all tests pass (350 at last count, 2 expected failures for the `game_num`
  bug). The updated tests are the four in section 6. Plus the new web tests.
- `git diff --check`: clean.
- Protected-values scan (the user's rule): the denylist (other domain, VPS and home addresses, pre-scrub commit
  hashes) and its scanner live **outside** the checkout and the Docker build context, in the private myVPS repo
  (`workspace/c-tierbot-publish-scan/`), never in C-TierBot. Before a commit, `scan.py --index` checks the staged
  tree. Before any push, `scan.py --range <remote-ref>..<local-ref>` checks **every commit the push would newly
  publish**: every blob of each commit's tree, text and binary alike (raw bytes, case-insensitive fixed strings),
  and each whole commit object (message included). The report names commit IDs and paths (or "message") only,
  never a value or a matching line; a scanner error exits 2 and counts as not clean. `scan.py --all` is the
  separate report on history already published, so "the new commits are clean" and "history is clean" are never
  confused. Checked on a throwaway repo with synthetic text, binary and message matches.

New tests must cover:
- Off state: `WEB_PORT=0` → `start_web_server` creates nothing.
- Lifecycle: an occupied port → the error is logged, no runner is left behind, and the code after
  `start_web_server` (the stand-in for `bot.start`) still runs. Shutdown with one client holding a request open,
  one DB worker blocked in a slow SQLite query and one password check running → everything finished within 5 s,
  and the worker threads have exited. A separate connection holding an **exclusive lock** through shutdown → the
  web reader gives up in about 250 ms (503), and shutdown still finishes in time. Measured **from signal
  delivery**, with `bot.close()` deliberately delayed by 3 s → web admission stops at once and everything is done
  within 5 s of the signal. The existing SIGTERM test still passes.
- Guild isolation: a temp DB with two guilds. A row planted in the other guild (same queue number, distinctive
  name) never appears in any public response. The production factory call uses `HOME_GUILD_ID`. A test asserts
  that `main()`'s call path passes that constant, not an env value.
- Canary leakage: the temp DB holds a non-empty synthetic token `CANARY-TOKEN-…` and synthetic 18-digit IDs. No
  public response body (200, 301, 400, 404, 503) contains the token, any planted ID, or a `Player <digits>` name.
- Stored-name exception: a name stored for a main-guild queue that happens to be the distinctive other-guild
  nickname `OtherGuildNick`, for a member absent from the main cache → shown (allowed fallback), including when
  the stats were saved and the Discord send then failed. The same nickname present only in a fake other guild's
  **live** cache, with a different stored name → never shown.
- Names (D6), with a fake member cache:
  - a linked player whose main-server member has nickname `MainNick`, and whose stored `display_name` is
    `OldNick`, shows `MainNick` on the queue page, the leaderboard, the Hall of Fame and the player page;
  - the same member present only in a fake **other** guild's cache → the site shows the stored name, never the
    other guild's nickname;
  - cache not ready (`chunked=False`) → stored names, `max-age=10`, nothing stored in the app cache;
  - unlinked players keep their stored in-game name;
  - a stored name `Player 123456789012345678`, or a slug with an embedded 18-digit number → `Unknown player`, not
    linked. `<script>` in a name is escaped.
- Player URLs: a user's current `users.slug` resolves. After re-verifying from account A to B, `/players/A` → `301`
  to `/players/B` and the page covers both accounts' main-guild queues. A user linked only through another guild
  → 404. Two users sharing a `users.slug` → the one with more main-guild queues gets the page; the other user's
  roster and leaderboard names are unlinked, and an old slug of theirs is 404, not a redirect to the winner. A
  relink to a slug with an 18-digit run → the old slug is 404 and no `Location` header carries the new one
  (`Location` headers are checked like bodies). A participant in a snapshot-only
  queue appears on that queue's page, and their player page count excludes it (as labelled).
- Archive: row-only, snapshot-only and both-sources queues all list. A `processed_matches`-only queue does not.
  Snapshot-only dates are labelled "saved".
- Rosters: a side-switcher from a snapshot appears once per side. A queue with no snapshot uses its rows. Two
  unlinked rows with the same name on one team in one v1-snapshot game stay separate, and their series totals show
  "—". A v2 snapshot with distinct `key`s groups them correctly; a **verified** `raven` (slug, linked) and a
  logged-out `Raven` on one team stay two players with separate totals, the logged-out row is unlinked, keeps the
  name `Raven`, and gets no player link. Two same-name logged-out substitutes in different games merge, with the
  "combined by name" marker. v1 snapshots still load.
- Enrichment: two different IDs that share a name in one snapshot game don't merge. A name-only match happens only
  when the name is unique on that team in that game.
- Preview guard: a missing, empty or table-less `--db` file → the preview exits with an error, and no `db` import
  happens (sentinel cwd unchanged).
- Leaderboard equality with `db.get_seasonal_queue_stats` per stat. `get_player_summary` equals
  `get_player_queue_stats_summary` for a player whose rows are all in one guild.
- Legacy NULLs render "—".
- Bad values of known params → 400/404, never 500. Unknown param names are ignored and give the same cache key. The
  cache stays at or under 256 entries under 1,000 varied requests.
- Load bound: 20 simultaneous cold requests with a slow query → 4 admitted, 16 get 503 at once (counted
  separately). A client that disconnects while admitted keeps its slot until its SQLite job finishes.
- SQLite URI: a DB path containing `#`, `%` and a space opens the intended file read-only. A write is rejected, and
  no sibling file is created.
- Admin config: any of the three admin env vars missing or malformed → every `/admin` path (including
  `/admin/login`) gives the same status and body as an unknown route, plus `Cache-Control: no-store`, and the log
  line doesn't contain the value.
- Admin auth: no cookie, a tampered signature, an expired `exp`, a future `iat`, a cookie made with the old
  password hash or old secret → redirect to login; following that redirect gives the `200` login form (not a
  loop). Right password → `__Host-` cookie with `Secure`, `HttpOnly`, `SameSite=Strict`, `Path=/`, no `Domain`.
  Login POST without `Origin`, or with another origin → 403, and no hash is computed. Logout POST sends
  `Set-Cookie: __Host-ctier_admin=` with `Secure`, `Path=/`, `Max-Age=0` and no `Domain`; with a cookie jar that
  enforces prefixes, the next `GET /admin/` redirects to login.
- Admin throttle: after 19 failures the next attempt still runs; after the 20th, the next gets 429 without hashing
  (counted with a fake hasher), whatever `CF-Connecting-IP` it sends, forged or not; 5 failures sent with the
  operator's reported IP don't stop the operator's correct password; a success doesn't reset the count; an hour
  after the 20th failure logins work again. Two simultaneous logins → one runs, the other gets 429. A login whose client disconnects while scrypt runs →
  the next login still gets 429 until the fake hasher returns, and no second hash is submitted. No log line
  contains the submitted password.
- Admin caching: every `/admin` response, the login page and redirects included, has `Cache-Control: no-store`;
  none enters the public cache; public responses never carry `Set-Cookie` and render the same with or without an
  admin cookie.
- Admin pages: log names with `../`, absolute paths, or a `.tmp` suffix are rejected. A pruned file shows "No
  longer available". A malformed one shows "Unreadable". `<script>` in a log value is escaped.
- `tools/make_admin_password.py`: its hash verifies its password through the same function the app uses, the
  output contains no `$`; a 15-character, a 129-character or a non-ASCII chosen password is refused; a
  128-character password generated by the tool logs in through the real form handler.
- Atomic log write: no `.json` file ever exists half-written (write is temp + `os.replace`).
- Charts: `web_charts.py` maps a known series to the expected path points, tick values and dot positions; a single
  queue, all-equal values and missing (NULL) values render without dividing by zero (missing points are skipped,
  never drawn as 0). `trend_value`: the `tests/test_history.py` fixture with 14 kills over 3 games gives 4.7
  kills per game (SVG, tooltip, label and table agree); a substitute who played 2 of 6 games is divided by 2; a
  legacy row with NULL `games` uses the inference or is missing. A player with damage in every queue and no impact
  scores (the `tests/test_history.py:321` case) renders the impact empty state. Leaderboard bar lengths are value / top value, and a top value of 0 draws no bars.
- Front end, in a real browser at build time (Playwright or the preview in a browser, not part of the unit suite):
  every page with JavaScript disabled shows the same values, dots and bars as with it on; no CSP violation is
  logged with the production headers; with `prefers-reduced-motion: reduce` nothing moves (final state at first
  paint), a rank-by or month navigation has no transition (JS on and off), and the crosshair still works; Tab
  reaches every chart point, and the focused element's accessible role is button with the expected name, also
  after switching to a sparser metric; full → sparse → empty → full metric switches keep each tooltip on the right
  queue. The admin login and logout forms are submitted by a real browser over HTTPS with JavaScript off and the
  browser's own headers (the Origin check passes, a cross-site form fails).
- Panel console (`tests/test_web_console.py`): the token reads as NULL in every query shape tried (select list,
  `WHERE`, `substr`, `hex`, subquery, alias, upper case); every write, schema change, `ATTACH`, setting pragma,
  transaction, `VACUUM` and `load_extension` is refused and the file is unchanged; time, row, cell and value-size
  limits; `stop` waits for `stop confirm` and calls shutdown once, after the reply; a second command is turned
  away; a slow one times out; signed-out, cross-origin and non-form posts run nothing; JSON and no-JS replies are
  escaped; only the command name is logged; the bot hands the site a `PanelConsole`.
- Inspection 4 fixes (`tests/test_web_inspection4_fixes.py`): a resubmitted form runs once, another session can't
  see or reuse a run, a busy dashboard after a command never repeats it, a fresh run ID per panel; slow and crowded
  form bodies get 408 and 503; `stop confirm` replies (both forms) before the bot is told; the real worker answers,
  gets no secrets, is killed past its time, on a cancelled request and at shutdown, and sets its Linux limits;
  unlisted tables, views, functions and late columns, and `USING`/`NATURAL` joins, are refused or read as NULL
  while CTEs, `json_each` and schema lookups work; every db.py column is listed or hidden; a reset waits for a
  queue's writes, also after its caller is cancelled; record updates are atomic under 48 threads; the reset answers
  first; `status` never stalls the loop; an unavailable guild drops cached pages; links (without `O_NOFOLLOW`) and
  deep JSON are unreadable; a log name collision retries; BLOB queue numbers aren't counted; older history pages
  list the chart's 16 queues; ignore rules cover sidecars, exports, backups and logs.
- Timeline: a snapshot whose winners reproduce the score animates; one with an unknown-winner game shows the hollow
  ring and still counts correctly; one whose winners don't reproduce the stored score shows the stored score with
  no count; a row-only queue with a saved score shows it with no timeline.
- Leaderboard transition names: two players with the same display name swapping ranks get different names; a
  shared-slug loser (no player URL) has none; the DOM contains no Discord ID.
- Retention at 90 and at 0 (section 6 list). The preview entry with `--db` never creates or changes a
  `leaderboard.db` in the working directory (test runs it from a temp cwd holding a sentinel file).

Real-data proof (on the export from rollout step 2, no bot, so names use the stored fallback):
- For every month that has data, and each stat, the `/leaderboard` table equals
  `build_seasonal_leaderboard_payload`'s ranking (players, order, values).
- For every linked player whose rows are all in the main guild, the player page summary equals
  `get_player_queue_stats_summary`.
- `/queues` total equals the union count from the export (580 on 2026-11-26; recount at test time).
- Crawl every public page. No body contains an 18-19 digit number, the other guild's ID `1075111506548625438`, or
  `Player <digits>`.

Manual (after deploy):
- `curl -I https://ctier.cooly.dev/` → 200 + `x-robots-tag: noindex`. A second hit within 60 s →
  `cf-cache-status: HIT`.
- `curl https://ctier.cooly.dev/healthz` → `cf-cache-status` is not `HIT`. Stopping the listener (`WEB_PORT=0`
  redeploy) makes the Uptime Kuma check fail on the next probe.
- `curl -I https://ctier.cooly.dev/admin/` → `303` to `/admin/login`, `cache-control: no-store`, and
  `cf-cache-status` is `BYPASS` or `DYNAMIC` (never `HIT`) on repeated requests.
- Log in from the operator's browser → panel loads. Log out → back to the login page, and the browser's back
  button or a fresh `/admin/` goes to the login page too (the cookie is really gone). The throttle is covered by
  the automated tests, not checked by hand (a manual run would close the login for an hour).
- On a phone, the leaderboard and a queue page read without zooming (the player column stays visible).
- Motion check on desktop and phone: the timeline plays once, the score lands on the stored score, the trend
  chart reveals and its crosshair follows; page changes cross-fade and rank-by changes slide rows in a browser with
  View Transitions, and just load elsewhere.
- From outside: the certificate visitors get is Cloudflare's edge certificate for the `cooly.dev` zone (it
  normally covers `cooly.dev` and `*.cooly.dev`); check it validates `ctier.cooly.dev` and contains **no** name
  from the other domain. Separately, through the operator's private path (SSH on the VPS, `openssl s_client
  -servername ctier.cooly.dev` against Traefik locally), check the origin certificate Traefik issued lists only
  `ctier.cooly.dev`. DNS for `ctier.cooly.dev` returns only Cloudflare addresses (the record stays proxied
  throughout); no page source mentions the other domain.
- `docker restart` the bot: the site is back within seconds, Discord commands still work, and a `/queue_stats` run
  still posts. Names show the main-server nicknames once the bot is ready (stored names for the first moments).

## 11. Docs to update when built
- `MEMORY.md`: TODO entry, architecture (new files, env vars, `HOME_GUILD_ID`, snapshot v2 `key`, name rule, admin
  login), Known Issues (loose name matching in Discord history; side-switch/stale stat rows now also visible on the
  web; `get_user_display_name` takes the first server where it finds a member), handover.
- `README.md`: a short "Website" section; line 86 retention wording.
- `PRODUCT.md`: Platform section gains the web surface. Principle 3 ("Stay inside Discord") gets a line saying
  the site is an archive, and Discord stays where results are answered. User to confirm the wording.
- `HOST_COMMANDS.md`: how to create or rotate the admin password and session secret.
- `design-system/pages/web.md`: the web rules for direction A and the motion spec (sections 4.2-4.3).
- `README.md` "Website" section: the vendored anime.js version, its SHA-256 and licence.
- The private myVPS inventory: as in rollout step 6.

## 12. Risks
- **Public exposure of member names and stats.** Server members see these in Discord today. A public site shows
  them, with main-server nicknames, to anyone with the link. Mitigated by noindex and no Discord IDs. No opt-out
  in this version. The user chose public.
- **A password login on the public internet.** Mitigated by a 32-character generated password, scrypt, a global
  throttle, `__Host-` SameSite=Strict cookies, Origin checks and no-store. Since r14 the panel's console can post
  to Discord, delete the bot's messages and stop the bot, so a stolen password can do that too (the user chose
  this, 2026-11-27); it can't change data or read access tokens, and its `sql` runs in a separate, limited
  process (r15). Residual: an attacker can keep the panel's login closed (an hour at a time); the SSH console is
  unaffected. The worker's Linux limits (CPU, memory, file size) are tested with a stand-in `resource` module;
  the first panel `sql` after deploy is their first run on Linux.
- **Direct connections to port 8080** from another container on the Docker network bypass Cloudflare and Traefik.
  Form bodies are read within 5 s, at most 8 at once (r15), but the site sets no time limit on reading request
  headers (aiohttp has none); public traffic arrives through Traefik.
- **Stored fallback names** can be another server's nickname for a player who left the main server (3.5).
- **Inherited accuracy issues** (P1 TODOs, Known Issues 1-2): the site can show the same wrong score, or a stale
  row, that Discord shows.
- **Shared process:** a web bug that blocks the loop would delay Discord handling. Mitigated by the dedicated
  executors, admission counter, cache and no image rendering.
- **Deploy downtime** of a few seconds per push to `main`.
- **Public repo:** infrastructure details must stay in the private inventory (section 10 scan). History was
  scrubbed on 2026-11-26; GitHub's cached copies of the five old commits stay (user's choice, section 9, step 0). A clone
  made before the scrub that merges or pushes its old `main` would bring the values back.

## Appendix A: evidence (repo and live DB, read-only, 2026-11-26)
- `requirements.txt`: discord.py 2.7.1, aiohttp 3.14.5, pillow 13.0.0, python-dotenv 1.2.4; no jinja2.
- `Dockerfile`: python:3.13-slim, `DB_PATH=/app/data/leaderboard.db`, `CONSOLE_PORT=8765`, `CMD python
  leaderboard_bot.py`. `.dockerignore` drops `*.md`, `tests/`, `tools/`, `design-system/`, `log/`, `data/`.
- `bot_config.py:76-79`: `intents.members = True`, so the member cache is populated.
- `leaderboard_bot.py:800` `main()`: SIGTERM handler, `async with bot: await bot.start(token)`, `finally:
  close_http_session()`.
- `db.py`: import-time schema setup and migrations on `DB_PATH` (`:38`, `:339`); per-call `sqlite3.connect(DB_PATH)`
  in helpers; `QUEUE_RESULT_RETENTION_DAYS = 90` (`:27`) used at `:306` and `:710`; `get_seasonal_queue_stats`
  (`:574`) guild-scoped, labels from `MAX(display_name)`; `get_player_queue_stats_summary` (`:628`) all servers.
- `leaderboard_service.py:575`: `/leaderboard_season` rows are `<@discord_id>` mentions;
  `queue_stats_service.py:475-491`: `/hall_of_fame` holders are mentions when a `discord_id` exists, else the
  stored name; `leaderboard_service.py:306` `_member_name` = `display_name`.
- `neatqueue_client.py:285` `get_user_display_name`: first guild containing the member; fallback `Player {id}`.
  `:310`: unlinked players' `display_name` is set to their in-game `username` (or `Unknown`).
- `queue_stats_service.py:39` `QUEUE_SNAPSHOT_VERSION = 1`; `build_queue_snapshot` (`:248`): games → players
  `{team, name, discord_id, guest, kills, damage, impact}`, `name` = the card's display name.
- Live counts (main guild `1363265113880264745`): 350 snapshots (2026-10-19 → 2026-11-26); 573 queues / 4650 rows
  in `player_queue_stats` since 2026-03-15; union 580 (7 snapshot-only); all 4 Hall of Fame records; 0 stored
  `Player <digits>` names; no shared slugs. Other guild `1075111506548625438`: 1 channel, 4 processed matches, no
  stats. `journal_mode=delete`. DB 3.4 MB.
- Infra (private myVPS inventory, read 2026-11-26): the C-TierBot Coolify app has no domain yet, health check off,
  no rolling updates, volume at `/app/data`, auto-deploy on `main`. Zone `cooly.dev` is on the Cloudflare Free plan
  with explicit proxied records only and no wildcard. The VPS firewall accepts web traffic only from Cloudflare.
  Cloudflare Access is not enabled on the account.
