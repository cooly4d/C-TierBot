# Website (`web/`): page rules

> Overrides `design-system/MASTER.md` for the website only. The cards stay governed by MASTER.md, and the site
> reuses its tokens: `/tokens.css` is generated from `design_tokens.py` at startup, so the two can't drift.

## Direction

Direction A, "card room" (chosen by the user 2026-11-26 from three mocked directions): the Discord result card
opened up into a website. Same palette, same components, same voice. Read/Operate surface: scanability first.

## Components (from the cards)

- **Series band** (queue page): `HEADER_TOP` band, `DIVIDER` 2px border, radius 20. Queue number 3.25rem bold,
  result line in the winner's colour ("Team 1 wins"), meta line muted; series score 4rem with the winner's digit
  `WIN`, loser's `LOSE`, a tie or unknown result in `TEXT`. Never boxed (rejected design).
- **MATCH TIMELINE**: `TIMELINE_FILL` box, 2px `TEXT_MUTED` border, radius 20; one lane per team on a 2px
  `TIMELINE_LANE` line; a game's dot sits in its winner's lane, filled with that team's series colour; an unknown
  winner is a hollow `TEXT_MUTED` ring on a midline row; the selected game has a 4px `ACCENT` ring. Each dot is a
  link to its game (hover: the ring at 45% `ACCENT`; keyboard focus: the ring in `TEXT`).
- **Header**: one glass bar holding the C-TierBot logo (32px, `web/static/logo-96.webp`; master
  `assets/logo.png`) beside the name, the search box, then the page links, each with a 20px line icon (1.75
  stroke; hidden below 30rem so the three fit on one row); the current link sits on an `ACCENT` 12% lozenge with
  a 38% `ACCENT` hairline. From 64rem up it is a full-height sidebar (16.5rem) with the links stacked, and the
  page sits beside it, left-aligned, up to 96rem wide (user, 2026-11-28: use the empty side space). From 48rem to
  64rem it is a sticky top bar (logo and search, links below); on phones it is static, one column. The name
  appears once, in the bar: the home page's heading is "SMH stats" (user's wording) and the loading screen has no
  caption. The logo is also the browser icon; its
  blue appears nowhere else on the site except the loading screen's tesseract (the user's own GIF).
- **Glass** (refresh, 2026-11-27): only on layers that float over the page: the header bar, the chart tooltip, the
  loading screen. `HEADER_TOP` at 72%, `blur(18px) saturate(150%)`, a 9% `TEXT` hairline, a 10% `TEXT` top edge
  and a soft offset shadow; solid `HEADER_TOP` where backdrop-filter isn't supported. The search results are solid
  (a blur nested in the bar's glass can't see the page). The canvas has two faint fixed glows (`HEADER_BOTTOM` top
  left, `ACCENT` 6% top right) so the glass has something to bend. Panels stay opaque card panels with a 4% top
  edge and a soft drop shadow.
- **Search**: a GET form to `/search` (players by shown name or page address, queues by number; a query that is
  exactly one saved queue's number with no player match goes straight to it). With JavaScript it is a combobox
  over `/search.json`: grouped Players / Queues options, the typed part in `ACCENT`, a match on the page address
  alone shows "survev.de: <address>", Up/Down/Enter/Escape, `/` or Ctrl+K to focus. Only players who already have
  a page are searchable; digits never match page addresses.
- **Home**: the heading "SMH stats", then the newest queue as a header band ("Queue #N", the result in words in the winner's colour, "Latest
  result · date · duration", the series score digits in `WIN` / `LOSE`, both rosters on their 13% / 9% washes;
  the whole band links to the queue), then Recent queues beside the month's avg-dmg board, then the Hall of Fame
  as one strip of four plaques divided by hairlines (value in `ACCENT`).
- **Compact by default** (user, 2026-11-28: too much shown at once): a queue row's date, time and duration, the
  latest result's date and duration, and a leaderboard row's games played (`.detail`) stay hidden (faded, still
  read by screen readers) until their row is hovered or holds focus. The queue number grows to 1.25rem while they
  are hidden. "Detailed", a checkbox at the bottom of the sidebar (top right on phones, beside the links on
  tablets), shows them all; boot.js applies the saved choice (`localStorage` `ctb-detailed`) before the first paint,
  and without JavaScript the checkbox works on its own page through `:has(:checked)`. On touch screens the latest
  result's date line is removed rather than faded.
- **QOL pass** (2026-11-28, the user's go-ahead on eight items plus Codex's review): the Leaderboard page always
  shows "N games" (it explains the weighted ranking; only the home board hides it, on the name's line); the
  archive has a heading per day ("Today", "Yesterday", "24 Nov"; UTC from the server, regrouped by the visitor's
  own days in site.js); every date is a `<time data-ms>` shown in the visitor's timezone with the UTC text as its
  tooltip; queue pages have Older / Newer pills (nearest saved queue numbers in this server; Left / Right arrow
  keys too) and a Copy link pill (copies the address as it is, game included; hidden where the clipboard can't be
  written), as do player pages; the mark notes under queue tables are a "Notes on these stats" disclosure when
  compact and the full legend when detailed, and the chart's "Hover, tap or tab" hint shows only when detailed;
  the leaderboard header is left-aligned with month pills on the right; the Hall of Fame page uses the home
  page's tiles, larger, and the single-game record opens its game when the saved breakdown has it.
- **Loading screen**: the first page of a tab session only (boot.js, before first paint), never with reduced motion
  or without JavaScript: the tesseract (`web/static/tesseract.webp`, screen-blended onto `CANVAS`, corners faded)
  over a 95% `CANVAS` glass and a 3px `ACCENT` bar filling over 1.1 s; then the glass clears (520 ms).
  At least 1.1 s, never past about 1.9 s.
- **Order** (2026-11-28): the archive, the home page's latest result and recent queues go newest first by queue
  number, never by a saved time (rerunning an old queue saved it again and put it on top). A queue dated only by
  its save time joins the day heading above it.
- **Queue rows** (home, archive): the winner is shown quietly, not in words: the winning roster on a 9% `WIN` wash
  with its "Team N:" label in `WIN`, the losing one on a 7% `LOSE` wash with its label in `--lose-text`. The score
  beside the number says the same without colour.
- **Team panels**: `PANEL`, radius 18, 2px border and tinted head in `WIN` / `LOSE` / `TEXT_MUTED`
  (`WIN_BANNER` / `LOSE_BANNER` / `NEUTRAL_BANNER`). Head text says the result in words ("Team 1 · won 4-2").
- **Lose-coloured text** uses `--lose-text` (#E07070), a touch lighter than the card's `LOSE`, so small text meets
  WCAG AA (4.7:1 or more) on every dark surface of the site; `LOSE` itself (4.0:1 on the lose banner) stays on
  marks: dots, borders, keys and bars.
  Game panels follow that game's winner.
- **Stat tables**: odd rows `PANEL_RAISED` pills (radius 10), tabular figures, player column sticky when the table
  scrolls sideways on a phone, `Avg` in `ACCENT`, `—` for missing values (never 0).
- **Marks**: MVP pill (`ACCENT` fill, `CANVAS` text), `played/total` in `ACCENT`, the broken-chain unlinked icon
  with screen-reader text, `*` for logged-out players matched by name across games (with a legend line).
- **Leaderboard**: rows with an `ACCENT` 12% bar behind them (length = value / top value), rank, name, games and
  the value in `ACCENT`. Same ranking as `/leaderboard_season`. No podium or summary banner (rejected design).
- **Trend chart** (player page): the History card on the web. One series, `ACCENT` 2.5px line, `ACCENT` 28%→0
  area, three hairline gridlines, dots coloured by result with a 2px panel ring, `#match` labels on every third
  queue, the latest value in the chart header (no per-point labels), and the recent-queues table as its table view.

## Motion (anime.js v4, `web/static/site.js`)

- Focal moment: the timeline plays the series once (dots drop into the winner's lane, 380 ms each, 140 ms apart;
  the score counts up as each lands), only when the known winners reproduce the stored score.
- Charts: clip reveal left to right (1100 ms, `cubic-bezier(0.85, 0, 0.15, 1)`), dots fade in as it passes; the
  crosshair and tooltip glide between points (180 ms) on hover and keyboard focus and snap in when they first
  appear; metric switches tween by queue (500 ms) while the tick labels and latest value count in step.
- Page changes cross-fade (old 140 ms out, new 260 ms in; `site.css`). Leaderboard: bars grow and values count
  up (500 ms, 30 ms stagger, first 10 rows); rank-by and month changes slide the rows and the "Rank by" selection
  (`leaderboard.css`); leaving a leaderboard drops the row names so nothing flies across pages.
- Whole queue rows and leaderboard rows are links; hover and focus draw a ring round the row, the row lifts 1px
  and tints (`TIMELINE_FILL`); a board row's bar brightens and its rank turns `ACCENT`; a stat table's row
  lights up across the table under the pointer.
- Home: the newest queue's score digits count up (520 ms, 80 ms apart), then the winners' wash sweeps in from the
  left (620 ms). The band lifts 2px on hover and shows "Open".
- Page links: on hover or keyboard focus the label slides 6px right (280 ms) while its icon plays once: the
  queue rows pull in to 45% and stretch back, the leaderboard bars dip to 35% and spring up (rows and bars 70 ms
  apart), the trophy rocks on its base (620 ms).
- Header: the current page's lozenge slides to the new link on a page change (view transition, 380 ms) while
  the header itself stays put; a soft highlight follows the pointer across the bar (fine pointers only); the
  bar's shadow deepens once the page scrolls. Search results drop in (180 ms).
- A timeline dot selects its game like the game buttons, then scrolls the breakdown into view if it's below the
  fold (smooth; instant under reduced motion). From the keyboard, focus follows to that game's button.
- Operator panel console: each output block fades and rises 4px (200 ms); "Running…" pulses until the output
  arrives. Neither runs under reduced motion. While a command runs, Run and the quick buttons (Clear too) are
  disabled.
- Older player-history pages: the chart's 16 queues as a table in a closed disclosure under the chart, styled like
  the console's command list; its header marks the chart's metric in the accent, like the history table's.
- Arrivals `cubic-bezier(0.16, 1, 0.3, 1)`; feedback 120-160 ms; state 240-260 ms; nothing loops.
- The server renders the final state; reduced motion shows it with no movement and no page cross-fade, while
  colour feedback still eases.

## Constraints

No inline styles or scripts (CSP), no third-party requests, every page works without JavaScript, win/loss always
in words as well as colour, Quattrocento Sans self-hosted as `.woff2`.
