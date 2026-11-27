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
- **Header**: the C-TierBot logo (32px, `web/static/logo-96.webp`; master `assets/logo.png`) beside the name. The
  logo is also the browser icon. Its blue appears nowhere else on the site.
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
- Whole queue rows and leaderboard rows are links; hover and focus draw a ring round the row.
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
