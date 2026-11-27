---
version: 1
slug: "web-templates-queue-html"
primary_target: "web/templates/queue.html"
related_targets: ["web/templates/base.html","web/templates/leaderboard.html","web/templates/player.html"]
---

# C-TierBot public site (web/templates/*)

Scope and mode: the public stats site (queue pages, archive, leaderboard, player pages, Hall of Fame) plus the
operator panel. Mode: Read/Operate. Audience: the home Discord community checking scrim results, usually right
after a queue, on desktop and phone. Task: find a queue, read who won and who carried, compare players over a
month. Constraints: server-rendered, works without JavaScript, strict CSP, self-hosted assets, public repo.

## Direction contract

THESIS: the Discord result card, opened up into a website. The queue page reads like the card at a glance (who
won, the series score, who carried) and refuses the generic dark stats dashboard of KPI tiles and chart grids.

OWN-WORLD: the locked card system: CANVAS navy ground, PANEL cards with DIVIDER hairlines, alternating
PANEL_RAISED row pills (radius 10), team panels bordered and headed in WIN / LOSE with their banner tints, the
two-lane MATCH TIMELINE in TIMELINE_FILL with a muted outline, ACCENT gold for avg dmg, the MVP pill, the
played/total label, the broken-chain unlinked icon, Quattrocento Sans bold for names and numbers.

STORY: a player opens a queue link, sees the series score and timeline, finds their row, checks a game, then
follows their name to their trend and the month's leaderboard.

FIRST VIEWPORT: queue page: header band with the queue number (large), "Team N wins" in the winner's colour,
date, games and duration on the left; the series score centred; the MATCH TIMELINE on the right; both team panels
start below it. Primary action: the player names (links) and the game buttons.

FORM: card room (direction A of three mocked directions, chosen by the user). seed: none (pinned) — "Use A i
guess, i want it to have the same design language. However use good and clean animations."

Signature interaction and motion grammar: the timeline plays the series (each game's dot drops into its winner's
lane while the score counts up); the trend chart reveals left to right with a snapping crosshair; leaderboard
bars grow and rows slide to new ranks. Arrivals ease out, nothing loops, reduced motion shows final state.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md,
and every shipping raster carrying its provenance
