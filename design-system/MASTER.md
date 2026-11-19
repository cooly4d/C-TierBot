# Design System Master File — C-TierBot

> **STATUS:** Locked Source of Truth (Extracted from existing `C-TierBot` codebase)
> **LOGIC:** When building or modifying a specific image renderer or Discord UI view, first check `design-system/pages/[page-name].md`.
> If that file exists, its rules **override** this Master file.
> If not, strictly follow the tokens, geometry, typography, and component patterns locked below. Do **not** invent new palettes or font pairings.

---

- **Project:** C-TierBot (`survev.de` & NeatQueue Discord Bot)
- **Primary Rendering Stack:** Python `Pillow` (`PIL.Image`, `PIL.ImageDraw`, `PIL.ImageFont`) in [`image_utils.py`](file:///c:/Users/Awang/local/C-TierBot/image_utils.py) + `discord.py` persistent `View` / `Embed` components in [`discord_ui.py`](file:///c:/Users/Awang/local/C-TierBot/discord_ui.py), [`leaderboard_service.py`](file:///c:/Users/Awang/local/C-TierBot/leaderboard_service.py), and [`queue_stats_service.py`](file:///c:/Users/Awang/local/C-TierBot/queue_stats_service.py)
- **Tokens:** every colour, the credit line, and the text sizes of the queue, inventory, shop and compare cards live in [`design_tokens.py`](../design_tokens.py), named by role (`CANVAS`, `PANEL`, `PANEL_RAISED`, `DIVIDER`, `TEXT`, `TEXT_MUTED`, `ACCENT`, `WIN`, `LOSE`, …). `image_utils.py` keeps the older `QUEUE_IMG_*` names as aliases. Change values there, not in renderers. The history card still sets its own text sizes. `tests/test_golden_images.py` compares 16 card fixtures with `tests/golden/` (re-save with `UPDATE_GOLDEN=1` after an intended change, then look at the pictures).
- **Theme Polarity:** Dark-only (`#121825` deep navy-slate canvas with elevated dark navy cards, high-contrast off-white text, semantic win/loss tints, and warm gold/amber accents)

---

## 1. Color Palette (Locked Tokens)

### 1.1 Core Surfaces & Backgrounds

| Constant / Role | RGB Tuple | Hex | Usage |
|---|---|---|---|
| `QUEUE_IMG_BG` | `(18, 24, 37)` | `#121825` | Root canvas background across all generated PNG images (`queue_stats`, `inventory`, `shop`, `compare`) |
| `QUEUE_IMG_HEADER_BG` | `(24, 33, 55)` | `#182137` | Top header banner vertical gradient start (`y = 0`) |
| `QUEUE_IMG_HEADER_GRADIENT_END` | `(34, 48, 73)` | `#223049` | Top header banner vertical gradient end (`y = header_height - 1`) via `_draw_header_gradient()` |
| `QUEUE_IMG_CARD_BG` | `(22, 30, 46)` | `#161E2E` | Primary team panel card surface in `generate_queue_result_image()` |
| `QUEUE_IMG_ROW_ALT` | `(28, 37, 55)` | `#1C2537` | Alternating even-row (`row_index % 2 == 0`) inset highlight pill in queue results; also item card fill in `generate_inventory_image()` |
| `QUEUE_IMG_ROW_DIVIDER` | `(38, 50, 74)` | `#26324A` | `1px` subtle divider line separating column headers from player rows |
| Timeline Box Fill (`TIMELINE_FILL`) | `(28, 40, 64)` | `#1C2840` | Fill for the `MATCH TIMELINE` rounded container inside the queue result header |
| Timeline Lane (`TIMELINE_LANE`) | `(52, 66, 94)` | `#34425E` | The faint `2px` line each team's row of game dots sits on (2026-11-19) |

### 1.2 Typography & Semantic Accent Colors

| Constant / Role | RGB Tuple | Hex | Usage |
|---|---|---|---|
| `QUEUE_IMG_TEXT` | `(235, 237, 240)` | `#EBEDF0` | Primary foreground text (match title, score separator, column headers, player names, kills, total damage, timeline labels) |
| `QUEUE_IMG_MUTED` | `(168, 183, 207)` | `#A8B7CF` | Secondary text & neutral chrome (duration subtitle, footers, pagination subheads, empty states, timeline border/axis, tie/unknown team states) |
| `QUEUE_IMG_ACCENT` | `(255, 165, 50)` | `#FFA532` | Primary highlight accent: `Avg` damage column values, latest game outer ring on `MATCH TIMELINE`, Golden Fries balances, item worth/prices |
| `QUEUE_IMG_WIN` | `(108, 199, 128)` | `#6CC780` | Semantic Win / Positive state: `"Team X Wins"` subtitle, winning score, winning team title & `2px` card border, the winning team's timeline lane label and dots, available shop offer border/status, higher stat in `/compare` |
| `QUEUE_IMG_LOSE` | `(214, 96, 96)` | `#D66060` | Semantic Loss / Negative state: losing score, losing team title & `2px` card border, timeline loss dot, purchased shop offer border/status, lower stat in `/compare` |

### 1.3 Team Header Banner Tints (`generate_queue_result_image`)

| Constant | RGB Tuple | Hex | Condition |
|---|---|---|---|
| `QUEUE_IMG_WIN_BANNER_BG` | `(27, 56, 47)` | `#1B382F` | Winning team (`winning_team_index == team_index`) top banner fill; paired with `QUEUE_IMG_WIN` text and bottom border |
| `QUEUE_IMG_LOSE_BANNER_BG` | `(56, 31, 41)` | `#381F29` | Losing team (`winning_team_index is not None`) top banner fill; paired with `QUEUE_IMG_LOSE` text and bottom border |
| `QUEUE_IMG_NEUTRAL_BANNER_BG` | `(30, 41, 62)` | `#1E293E` | Unknown / tied result (`winning_team_index is None`) top banner fill; paired with `QUEUE_IMG_MUTED` text and bottom border |

### 1.4 Rarity Outline Palette (`RARITY_COLORS`)

Used by `rarity_color(rarity)` for `4px` card borders in `generate_inventory_image()`. Since 2026-11-19 (user-approved) these are survev.de's exact values (`RARITY_COLORS` in `client/src/stats/js/loadoutModal.ts`):

| Rarity Key | Label (`INVENTORY_RARITY_FILTERS`) | RGB Tuple | Hex | Visual |
|---|---|---|---|---|
| `0` | `Stock` | `(197, 197, 197)` | `#C5C5C5` | Light grey (same as Common, as on survev.de) |
| `1` | `Common` | `(197, 197, 197)` | `#C5C5C5` | Light grey |
| `2` | `Uncommon` | `(18, 255, 0)` | `#12FF00` | Green |
| `3` | `Rare` | `(0, 222, 255)` | `#00DEFF` | Cyan |
| `4` | `Epic` | `(246, 0, 255)` | `#F600FF` | Magenta |
| `5` | `Mythic` | `(217, 97, 0)` | `#D96100` | Orange |

Since 2026-11-19 (user-approved) the rarity is also written under each inventory item's name in its colour (`Uncommon  ·  x3`), so it never rests on the border colour alone, and Common no longer shares Uncommon's green. Items without a value say `No value` in muted text instead of `Stock` in accent. No backing square behind icons (the user declined it).
| Fallback (unknown key) | — | `QUEUE_IMG_MUTED` `(168, 183, 207)` | `#A8B7CF` | Muted Slate |

### 1.5 Renderer-Specific Surface Overrides

- **Shop Offer Cards (`generate_shop_image`)**:
  - **Available (`purchased == False`):** Fill `(40, 60, 40)` / `#283C28`, Border `QUEUE_IMG_WIN` `#6CC780` (`3px`), Status label `"Available"` in `QUEUE_IMG_WIN`
  - **Purchased (`purchased == True`):** Fill `(60, 40, 40)` / `#3C2828`, Border `QUEUE_IMG_LOSE` `#D66060` (`3px`), Status label `"Bought"` in `QUEUE_IMG_LOSE`
- **Stats Comparison Card (`generate_compare_image`)** (shared palette since 2026-11-19): card `PANEL` with a `DIVIDER` border, odd rows `PANEL_RAISED`, section bands `HEADER_TOP`. The private greys and the blurple bars are gone.

### 1.6 Discord Embed Color Tokens

| Discord Color | Hex | Applied Commands / Views |
|---|---|---|
| `discord.Color.blurple()` | `#5865F2` | `/profile` (incl. the `survev.de Squad Tier` field), `/inventory`, `/market`, Game breakdown embeds |
| `discord.Color.gold()` | `#F1C40F` | `/leaderboard_old` (Weekly), `/leaderboard_season`, `/leaderboard_fries`, `/goldenfries`, `/hall_of_fame` |
| `discord.Color.purple()` | `#9B59B6` | `/leaderboard_old` (Monthly) |

---

## 2. Typography

### 2.1 Font Family & Resolution Chain

Loaded via `load_font(size: int, weight: str = "regular")` in [`image_utils.py`](file:///c:/Users/Awang/local/C-TierBot/image_utils.py#L12-L69):

1. **Environment Override:** `QUEUE_FONT_PATH` (regular) / `QUEUE_FONT_BOLD_PATH` (bold)
2. **Locked Primary Font:** **Quattrocento Sans**, bundled in the repository root (`QuattrocentoSans-Regular.ttf`, `QuattrocentoSans-Bold.ttf`, resolved from `_CURRENT_DIR`). There is no `fonts/` folder; `load_font` also tries `fonts/…` and `/usr/share/fonts/truetype/quattrocento/…` before the fallbacks below.
   - Bold is used for `weight in ("bold", "semibold")`.
3. **System Fallback Chain (in order):**
   - `DejaVuSans.ttf` / `DejaVuSans-Bold.ttf`
   - `LiberationSans-Regular.ttf` / `LiberationSans-Bold.ttf`
   - `FreeSans.ttf` / `FreeSansBold.ttf`
   - `ImageFont.load_default()`

### 2.2 Typographic Scale

All sizes below are expressed in **1x logical canvas pixels** (on the `1600px`-wide canvas). In `generate_queue_result_image()`, font sizes are multiplied by the supersampling factor `S = 2` during drawing and downscaled back to `1x`.

#### Queue Result Image (`generate_queue_result_image`)

| Token / Variable | 1x Size | 2x (`S=2`) Size | Weight | Color | Usage |
|---|---|---|---|---|---|
| `score_font` | `72px` | `144px` | `bold` | `QUEUE_IMG_WIN` / `QUEUE_IMG_LOSE` / `QUEUE_IMG_TEXT` | Center top round score (`4 - 2`) |
| `title_font` | `66px` | `132px` | `bold` | `QUEUE_IMG_TEXT` | Match ID header (`#3674`) |
| `subtitle_font` | `38px` | `76px` | `bold` | `QUEUE_IMG_WIN` / `QUEUE_IMG_MUTED` | Winner announcement (`Team 1 Wins` / `Result unknown`) |
| `team_header_font` | `38px` | `76px` | `bold` | `team_color` (`WIN`/`LOSE`/`MUTED`) | Centered team card banner title (`Team 1`, `Team 2`) |
| `body_font_bold` | `35px` | `70px` | `bold` | `QUEUE_IMG_TEXT` (cols 0, 2, Impact), `QUEUE_IMG_ACCENT` (Avg) | Player name (`col 0`), Total Damage, Avg Damage, Impact score (`QUEUE_IMG_MUTED` `-` when unscored) |
| `body_font` | `35px` | `70px` | `regular` | `QUEUE_IMG_TEXT` / `QUEUE_IMG_MUTED` | Kills (`col 1`), timeline node labels (`G1`, `G2`), `"No players"` empty state |
| `header_font` | `30px` | `60px` | `bold` | `QUEUE_IMG_TEXT` | `MATCH TIMELINE` header & table column headers (`Player`, `Kills`, `Dmg`, `Avg`, `Impact`) |
| `duration_font` | `30px` | `60px` | `bold` | `QUEUE_IMG_MUTED` | Header duration (`Match Duration: 18m 42s`) / Games Played label (was `22px` until 2026-11-19) |
| `footer_font` | `26px` | `52px` | `regular` | `QUEUE_IMG_MUTED` / `QUEUE_IMG_ACCENT` | Footer legend (unlinked icon, played/total), only when the card needs it (was `22px`) |
| Timeline labels | `26px` | `52px` | `bold` (title, `T1`/`T2`) / `regular` (`G1`…) | `QUEUE_IMG_TEXT`, team colour, `QUEUE_IMG_MUTED` | `MATCH TIMELINE` title, lane labels, game labels (highlighted game in `QUEUE_IMG_TEXT`) |
| `mvp_font` | `24px` | `48px` | `bold` | `QUEUE_IMG_BG` on an `QUEUE_IMG_ACCENT` pill | `MVP` tag (finished series only) |

#### Compare Image (`generate_compare_image`)

| Token / Variable | Size | Weight | Color | Usage |
|---|---|---|---|---|
| Header title / subtitle | `54px` / `28px` | `bold` | `TEXT` / `MUTED` | Shared compact header: `"Stats comparison"`, `"All-time stats are from verification date."` |
| `name_font` | `38px` | `bold` | `QUEUE_IMG_TEXT` | Left and right player names |
| `status_font` | `20px` | `regular` | `QUEUE_IMG_MUTED` | `"Linked"` / `"Not linked"` under each name |
| `section_font` | `22px` | `bold` | `QUEUE_IMG_ACCENT` | Section bands `SURVEV.DE · ALL MODES`, `NEATQUEUE 4V4S` |
| `label_font` | `30px` | `bold` | `QUEUE_IMG_MUTED` | Centered metric name (was `22px`) |
| `value_font` | `36px` | `bold` | `WIN` / `LOSE` / `TEXT` / `MUTED` | Stat values: higher green, lower red on every row (the user kept red/green, 2026-11-19); values that print the same are a tie (`TEXT`); `-` / `N/A` muted |
| Footer | `22px` | `regular` | `QUEUE_IMG_MUTED` | Credit line (`CREDIT_LINE`) |

#### Inventory & Shop Images (`generate_inventory_image`, `generate_shop_image`)

| Token / Variable | Size | Weight | Color | Usage |
|---|---|---|---|---|
Sizes raised 2026-11-19 (user-approved) so names, prices and status survive Discord's downscale.

| Token / Variable | Size | Weight | Color | Usage |
|---|---|---|---|---|
| `title_font` | `54px` | `bold` | `QUEUE_IMG_TEXT` | Header title (`"{username}'s Inventory"`, `"{username}'s Shop"`, `"Stats comparison"`) |
| `empty_font` | `30px` | `bold` | `QUEUE_IMG_MUTED` | Empty state message (`"No items in this rarity."`) |
| `subtitle_font` | `28px` | `bold` | `QUEUE_IMG_MUTED` | Count, filter/mode and page subtitle in header |
| `label_font` / `amount_font` | `22px` / `48px` | `bold` | `QUEUE_IMG_MUTED` / `QUEUE_IMG_ACCENT` | Header `"TOTAL VALUE"` (inventory) or `"BALANCE"` (shop) label and amount |
| `name_font` | `32px` | `bold` | `QUEUE_IMG_TEXT` | Inventory card skin name |
| `rarity_font` | `24px` | `bold` | `rarity_color(rarity)` | Inventory rarity line (`"Uncommon  ·  x3"`) |
| `value_font` | `30px` | `bold` | `QUEUE_IMG_ACCENT` (`MUTED` for `"No value"`) | Item worth (`draw_fries_amount`) |
| `item_font` / `price_font` | `34px` | `bold` | `QUEUE_IMG_TEXT` / `QUEUE_IMG_ACCENT` | Shop offer title (item name, or `"4 items"`) / price |
| `kind_font` | `24px` | `regular` | `QUEUE_IMG_MUTED` | Offer kind (`"Daily pick"`, `"Weekly bundle"`, `"Premium daily"`) |
| `caption_font` | `20px` | `bold` | `rarity_color(rarity)` | Name under each bundle icon; rarity label under a single offer's icon |
| `status_font` | `24px` | `bold` | `WIN` / `LOSE` | `"Available"` / `"Bought"` |

- **Item icons:** `assets/items/{type}.png` (128 px, exported by `tools/export_item_icons.mts`), pasted with `paste_item_icon`. Inventory cards (`196px` tall) put a `116px` icon in the bottom-right corner under the name; shop cards (`206px`) right-align up to four `104px` icons in `122px` cells. Items with no icon keep a text-only card.
- **No emoji in images:** Quattrocento has no emoji or symbol glyphs (`💰`, `✓`, `◯` render as dots). Draw amounts with `draw_fries_amount` and keep status labels plain text.
| Footer (`draw_credit_footer`) | `22px` | `regular` | `QUEUE_IMG_MUTED` | The one credit line on every card that has one (inventory, shop, compare, history): `"Stats from survev.de and NeatQueue"` (`CREDIT_LINE`). Text embeds carry no credit footer (user, 2026-11-19). |

### 2.3 Text Alignment, Baseline & Truncation Rules

- **Consistent Row Baseline (`_vcenter_y`):** Always compute vertical centering inside a row or banner box using `"Ay0"` as the reference glyph bounding box (`box_top + (box_bottom - box_top - (bbox[3] - bbox[1])) / 2 - bbox[1]`) so that text with and without descenders/ascenders shares an identical baseline across columns.
- **Pixel-Width Ellipsis (`truncate_to_width`):** Never allow player names or item labels to overflow into adjacent columns or card borders. Trim character-by-character until `text + "…"` fits within `max_width`.
- **Unlinked Player Vector Icon (`_build_unlinked_icon`):** Guests without a linked Discord account (`guest == True`) display a `30x30` (`QUEUE_IMG_UNLINKED_ICON_SIZE = 30`, was `24` until 2026-11-19) 45°-rotated broken chain-link vector icon (`QUEUE_IMG_MUTED` link cuffs + `QUEUE_IMG_LOSE` slash) placed `10px` (`QUEUE_IMG_UNLINKED_ICON_GAP = 10`) to the right of their truncated username, with `(QUEUE_IMG_UNLINKED_ICON_SIZE + QUEUE_IMG_UNLINKED_ICON_GAP)` reserved from `max_name_width`. A matching `24x24` icon (was `18`) prefixes the footer legend `"Unlinked: /verify to join the leaderboards"`, drawn only when the card has an unlinked player. With neither an unlinked player nor a played/total label the card has no footer band (`height = panel_bottom + 44`; with a footer, `+ 66`).
- **MVP tag (approved 2026-11-19):** on a finished series with a winner, when the Impact column is shown, the player with the highest impact (rounded as displayed) among those who played every game gets an `MVP` pill after their name (and after the icon or played label): `24px` bold `QUEUE_IMG_BG` text on a `QUEUE_IMG_ACCENT` rounded rectangle (`34px` tall, radius `9`, `11px` side padding, `12px` gap), its width reserved from `max_name_width`. No tag when nobody qualifies or the top score is shared (`pick_mvp`).
- **Partial-Series Label (`partial_games_label`, approved 2026-11-04):** On the series result image only (never the per-game card), a player who played fewer games than the series (a substitute or a leaver) gets a `played/total` label (e.g. `1/7`) in `header_font` (`30px` bold) `QUEUE_IMG_ACCENT` (orange, user request 2026-11-04), `10px` after their name or unlinked icon; its width plus the gap is reserved from `max_name_width` so the name truncates first. When any player has one, the footer legend shows `"1/7 = played 1 of 7 games"` in `QUEUE_IMG_ACCENT` (after `"   ·   "` when the unlinked part is shown too), quoting the first label on the card.
- **Number Formatting:**
  - Integers (damage, worth, prices, balances): comma-separated (`f"{value:,}"`)
  - Averages (`avg_damage`): rounded integer with commas (`f"{avg_damage:,.0f}"`)
  - Kills per game (`/compare` `Kills / Game`, renamed from `K/D Ratio` on 2026-11-19 because it is kills ÷ games): 2 decimal places
  - Impact scores (survev.de, `0`–`100`): whole numbers rounded half-up (`72.5` → `73`); unscored → `-`
  - Squad tier: survev.de ladder letters as returned (`F E D C B- B A- A S`, up to two characters) followed by ` · ` and the whole-number rating (`A- · 72`)
  - Durations (`format_duration_ms`): `"Xh YYm ZZs"`, `"Ym ZZs"`, or `"Zs"`; `/compare` play time drops the seconds (`"31h 07m"`)

---

## 3. Layout, Spacing & Grid Geometry

### 3.1 Global Canvas & Header Constants

| Constant | Value (1x) | Usage |
|---|---|---|
| `QUEUE_IMG_WIDTH` | `1600px` | Fixed horizontal width across all generated images |
| `QUEUE_IMG_PADDING` | `44px` | Outer horizontal margin, vertical gap below header, and horizontal gap between team cards |
| `QUEUE_IMG_HEADER_HEIGHT` | `210px` | Height of the top gradient header banner |
| Supersampling Factor (`S`) | `2` (`3200px` render width) | Used in `generate_queue_result_image()` and downsampled via `Image.Resampling.LANCZOS` |

### 3.2 Queue Result Team Cards & Table Grid

- **Dynamic Multi-Team Split:**
  - `panel_width = (QUEUE_IMG_WIDTH - QUEUE_IMG_PADDING * (num_teams + 1)) // num_teams`
  - For a standard 2-team match: `panel_width = 734px` (`x0 = 44px` for Team 1, `x0 = 822px` for Team 2, `44px` gap between panels).
- **Vertical Rhythm:**
  - `panel_top = QUEUE_IMG_HEADER_HEIGHT + QUEUE_IMG_PADDING` (`254px`)
  - `QUEUE_IMG_TEAM_BANNER_HEIGHT = 58px` (`col_header_top = 312px`)
  - `QUEUE_IMG_COL_HEADER_HEIGHT = 48px` (`rows_top = 360px`)
  - `QUEUE_IMG_ROW_HEIGHT = 92px` per player row (minimum 1 row height even when empty)
  - `QUEUE_IMG_CARD_BOTTOM_PAD = 16px` below the last row
  - Bottom footer area height = `QUEUE_IMG_PADDING + 14` (`58px` below `panel_bottom`)
- **Horizontal Table Columns (`QUEUE_IMG_COLUMN_RATIOS`):**
  - Card inner width: `inner_width = panel_width - 2 * QUEUE_IMG_CARD_INNER_PAD_X` (`QUEUE_IMG_CARD_INNER_PAD_X = 20px`, so `inner_width = 694px` for 2 teams).
  - Column start ratios: `[0.0, 0.40, 0.60, 0.80]` for `["Player", "Kills", "Dmg", "Avg"]`:
    - `Player` (`0.00–0.40`, `40%` width / `278px`): Left-aligned at `inner_x0`, right-padded by `QUEUE_IMG_NAME_PADDING_RIGHT = 16px`. Comfortably fits usernames up to 14 characters plus the 24px unlinked badge without premature ellipsis.
    - `Kills` (`0.40–0.60`, `20%` width / `139px`): Center-aligned within column width; regular weight (`body_font`).
    - `Dmg` (`0.60–0.80`, `20%` width / `139px`): Center-aligned within column width; bold weight (`body_font_bold`).
    - `Avg` (`0.80–1.00`, `20%` width / `139px`): Center-aligned within column width; bold weight (`body_font_bold`) in `QUEUE_IMG_ACCENT` (`#FFA532`).
  - **Impact layout (`QUEUE_IMG_COLUMN_RATIOS_IMPACT`)** — used by `generate_queue_result_image` whenever `shows_impact_column(teams)` is true (at least one player has a survev.de impact score **and** the result has two teams or fewer; three-team results keep the 4-column layout because a third panel is too narrow for five columns). Ratios `[0.0, 0.36, 0.47, 0.64, 0.80]` for `["Player", "Kills", "Dmg", "Avg", "Impact"]` → `250px / 76px / 118px / 111px / 139px`:
    - `Player` keeps the same ellipsis rule (`234px` usable, about 11–12 characters; the unlinked badge still reserves `34px`).
    - `Impact`: bold `body_font_bold` in `QUEUE_IMG_TEXT`, centered, header label `"Impact"`. Value = the player's mean per-game impact over the games survev.de scored, rounded half-up to a whole number (`0`–`100`); a real score of `0` prints `0`; no scored game prints `-` in `QUEUE_IMG_MUTED`. No bar or color coding: the number is the whole signal (color is never the only indicator).
  - **Game card layouts (`QUEUE_IMG_GAME_COLUMN_*`)** — one game's numbers, so no `Avg` column: `["Player", "Kills", "Dmg", "Impact"]` at `[0.0, 0.40, 0.60, 0.80]`, or `["Player", "Kills", "Dmg"]` at `[0.0, 0.50, 0.75]` when nobody was scored.
- **Inset Alternating Row Highlight:**
  - Drawn on even rows (`row_index % 2 == 0`) with a `12px` horizontal inset from the outer card edges (`x0 + 12` to `x0 + panel_width - 12`) and `4px` top/bottom vertical inset (`84px` pill height inside the `92px` row slot).

### 3.3 Grid Layouts (Inventory, Shop, Compare)

- **Inventory Grid (`generate_inventory_image`):**
  - `INVENTORY_GRID_COLUMNS = 4`, `INVENTORY_ITEMS_PER_PAGE = 12` (up to 3 rows)
  - `INVENTORY_CARD_HEIGHT = 196px` (was `180`), `INVENTORY_CARD_GAP = 24px`, card width = `360px`. Name at `y0 + 14`, rarity line at `y0 + 56`, value at `y1 - 48`.
  - Compact header (`_draw_compact_header`, shared with the shop and compare; `INVENTORY_HEADER_HEIGHT = 140px`, was `128`; flat `QUEUE_IMG_HEADER_BG` with a `2px` `QUEUE_IMG_ROW_DIVIDER` rule, no gradient): `54px` title at `y = 18` and a `28px` muted subtitle at `y = 88` (`"54 items · 13 unique · Page 1 of 2"`, plus `"9 Rare"` when filtered) on the left; right-aligned `22px` `"TOTAL VALUE"` label at `y = 22` over a `48px` `QUEUE_IMG_ACCENT` amount at `y = 52` with the Golden Fries icon (`compute_inventory_worth` of the whole inventory, whatever the filter). The amount is optional (compare has none). Grid starts `32px` below.
  - Footer (`INVENTORY_FOOTER_HEIGHT = 64px`, was `56`): the credit line only; the page number is in the subtitle and the embed footer.
  - Saved with `save_palette_png` (256-colour palette, about a quarter of the size) so page switches upload faster.
- **Shop Grid (`generate_shop_image`):**
  - `SHOP_GRID_COLUMNS = 2`, `SHOP_ITEMS_PER_PAGE = 6` (up to 3 rows)
  - `SHOP_CARD_HEIGHT = 206px` (was `190`), `SHOP_CARD_GAP = 24px`, card width = `744px`. Kind at `y0 + 14`, title at `y0 + 46`, price at `y0 + 92`, status at `y1 - 42`.
  - Slots: `SHOP_SLOT_LABELS` names slots 0 to 3 and labels survev.de's two Premium daily offers (slots 4 and 5) `"Premium daily"`. `MARKET_MODE_SLOTS = {"daily": (0, 1, 4, 5), "weekly": (2, 3)}` drives both the card and the page count.
  - Same compact header and footer as the inventory (`_draw_compact_header`, `draw_credit_footer`): title `"{username}'s Shop"`, subtitle `"Weekly offers · 2 offers"` (plus `"Page X of Y"` only when there is more than one page), right-aligned `"BALANCE"` amount. Empty state `"No offers right now."`. Saved with `save_palette_png`.
- **Compare Split Table (`generate_compare_image`, reworked 2026-11-19):**
  - Compact header (`140px`, no amount). Full-width card from `x = 44px` to `1556px`, `card_top = 172px`, radius `20`, `PANEL` fill, `2px` `DIVIDER` border; `INVENTORY_FOOTER_HEIGHT` below it for the credit line.
  - Name block `110px` (name at `+22`, `Linked` / `Not linked` at `+72`), then two `52px` section bands (`SURVEV.DE · ALL MODES`: Wins, Kills / Game, Total Damage, **Squad Tier**, Inventory Worth; `NEATQUEUE 4V4S`: Play Time, Avg Damage, `{Month} Avg Damage`) with `80px` rows, odd rows `PANEL_RAISED`.
  - Values right-aligned at `x = 590` (left player) and left-aligned at `x = 1010` (right player), labels centred, all on `_vcenter_y` baselines. The person who ran the command is on the left; names are display names.
  - Empty cells: `-` means nothing to show (not linked, no queues), `N/A` means a lookup failed (survev.de history not read in full, inventory or tier lookup failed).
  - **Squad Tier row** (`format_squad_tier`): value `"A- · 72"` (survev.de tier ladder `F E D C B- B A- A S`, `·`, rating `0`–`100`), `"Unranked"` when the player has no rated Squad games, `"N/A"` when the lookup failed, `"-"` when the player isn't linked. The better rating is `QUEUE_IMG_WIN`, the lower `QUEUE_IMG_LOSE`, unranked/unavailable stay `QUEUE_IMG_MUTED`.

---

## 4. Border Radiuses, Borders & Dividers

| Component | Border Radius (1x) | Border Width (1x) | Border / Divider Color |
|---|---|---|---|
| Compare Main Card | `20px` | `2px` | `DIVIDER` (`#26324A`) |
| Match Timeline Box (Header) | `20px` | `2px` | `QUEUE_IMG_MUTED` (`#A8B7CF`) |
| Queue Result Team Panel Card (`QUEUE_IMG_CARD_RADIUS`) | `18px` | `2px` | `team_color` (`QUEUE_IMG_WIN`, `QUEUE_IMG_LOSE`, or `QUEUE_IMG_MUTED`) |
| Queue Result Team Top Banner | `17px` top corners, `0px` bottom corners | `2px` bottom line | `team_color` (`WIN`/`LOSE`/`MUTED`) |
| Inventory Item Card | `16px` | `4px` | `rarity_color(rarity)` (`RARITY_COLORS`) |
| Shop Offer Card | `16px` | `3px` | `QUEUE_IMG_WIN` (available) or `QUEUE_IMG_LOSE` (purchased) |
| Queue Result Alternating Row Pill | `10px` | `0px` (fill only) | Fill: `QUEUE_IMG_ROW_ALT` (`#1C2537`) |
| Column Header Bottom Divider | — | `1px` (`12px` inset from card edges) | `QUEUE_IMG_ROW_DIVIDER` (`#26324A`) |
| Compare Row Divider | — | `1px` (full card width) | `(55, 62, 80)` / `#373E50` |
| Match Timeline Lanes & Dots | `11px` dot radius, `17px` ring radius | `2px` lane line, `3px` hollow ring, `4px` accent ring | Lanes: `TIMELINE_LANE`; dots: the winning team's panel colour (`WIN`/`LOSE`/`MUTED`); unknown: hollow `MUTED` ring; highlight: `QUEUE_IMG_ACCENT` |

---

## 5. Component Patterns & Architecture

### 5.1 Top Header Banner (`_draw_header_gradient`)
- **Structure:** Full-width `1600 x 210` horizontal strip at `y = 0` with a vertical line-by-line interpolated gradient from `QUEUE_IMG_HEADER_BG` (`#182137`) to `QUEUE_IMG_HEADER_GRADIENT_END` (`#223049`).
- **Queue Result Header Layout:**
  - **Ongoing queue status:** When `is_final` is false, display `IN PROGRESS` centered above the series score (`x = 800`, text top `y = 48`, `30px` bold, `QUEUE_IMG_ACCENT`, no container). The left subtitle reads `Team N leads`, or `No team leading` when there is no leader. Finished cards omit this status and retain `Team N Wins` / `Result unknown`.
  - **Left Column (`x = 44`):** Match ID (`#3674`) at `y = 28`, Winner subtitle (`Team 1 Wins`) at `y = 96`, Match Duration / Games Played metadata at `y = 152`.
  - **Center Scoreboard (`x = centered`, `y = 88`):** Large `72px` bold round score (`left_score - right_score`), where the winning side's digit is colored `QUEUE_IMG_WIN` and the losing side's digit is colored `QUEUE_IMG_LOSE`, separated by `" - "` in `QUEUE_IMG_TEXT`.
  - **Right Column (`MATCH TIMELINE`, `x = 996`, `y = 27`, `560 x 156`; two-lane design approved 2026-11-19, `_draw_match_timeline`):** Displayed when the series has games. Centered `"MATCH TIMELINE"` title (`26px` bold) at `y + 12`. One lane per team (`T1` at `y + 64`, `T2` at `y + 100`; three teams spread between `y + 56` and `y + 108`), each with a `T{n}` label (`26px` bold, coloured like that team's panel) at `x + 22` and a `2px` `TIMELINE_LANE` line from `x + 70` to `x + 530`. Each game's dot (`11px` radius) sits in the lane of the team that **won that game**, filled with that team's panel colour; a game with no known winner is a hollow `3px` `QUEUE_IMG_MUTED` ring on the midline. The highlighted game (the last on the series card, the selected one on the game card) gets a `4px` `QUEUE_IMG_ACCENT` ring (`17px` radius). Game labels (`26px` regular, `QUEUE_IMG_MUTED`, the highlighted one `QUEUE_IMG_TEXT`) sit at `y + 128`; when they don't fit, only the first, last and even games are labelled, as bare numbers. The first and last dots sit at the ends of the lane line, every game is labelled `G{n}` (no thinning: the label-spacing fix, audit #32, was skipped), and a single game is centred on the lane line. Winners come from each game's `winning_display_team_index` (snapshots: `games[].winner`); without them `winners_from_history` rebuilds them from `match_history` and the series winner. Because position and the lane label carry the result, a tied or live series still shows who won each game, and a dot never moves once its game is played.

### 5.2 Team Result Card (`generate_queue_result_image`, `_render_queue_card`)
`generate_queue_result_image` and `generate_queue_game_image` (5.4) both draw through `_render_queue_card`, so the header, panels and footer below are shared; they only differ in the strings, scores, column layout and highlighted game they pass in.
Rendered in a strict 5-layer painter's order per team panel so corners and borders stay razor-sharp:
1. **Card Surface Fill:** `rounded_rectangle` (`radius=18`, fill `QUEUE_IMG_CARD_BG`).
2. **Tinted Team Header Banner:** `rounded_rectangle` (`radius=17`, fill `banner_bg`) for the top corners, overlaid with a flat `rectangle` covering the bottom `18px` of the banner strip so the bottom edge is square, followed by a `2px` horizontal line at `col_header_top` in `team_color` and centered `Team N` title.
3. **Column Header Strip:** `48px` tall strip with `Player` (left-aligned) and the remaining column labels (`Kills`, `Dmg`, `Avg`, plus `Impact` when shown), centered in their column slices, plus a `1px` inset bottom divider in `QUEUE_IMG_ROW_DIVIDER`.
4. **Alternating Player Rows:** `92px` tall slots. Even rows (`0, 2, 4...`) draw an inset rounded rectangle (`12px` X-inset, `4px` Y-inset, `radius=10`, fill `QUEUE_IMG_ROW_ALT`). `Player`, `Dmg`, `Avg` and `Impact` use `body_font_bold`; `Kills` uses regular `body_font`; `Avg` is highlighted in `QUEUE_IMG_ACCENT`. Cell text comes from `_queue_cell(label, entry, player_label)`.
5. **Outer Card Border:** Final `rounded_rectangle` (`radius=18`, `outline=team_color`, `width=2`) drawn last on top of the banner and card fill to prevent inner fills from clipping the border stroke.

### 5.3 Discord Interactive Views (`discord_ui.py`)
- **Centralized Dispatch Pattern:** All interactive component clicks and select changes are routed through `log_interaction` in [`leaderboard_bot.py`](file:///c:/Users/Awang/local/C-TierBot/leaderboard_bot.py#L87-L178) via `custom_id` matching (`queue_result_verify`, `queue_result_games`, `qgame_prev:*`, `qgame_next:*`, `leaderboard_weekly`, `leaderboard_monthly`, `leaderboard_sort`, `season_prev:*`, `season_next:*`, `season_stat:*`, `qgame_pick:*`, `hof_reset:*`, `inv_prev:*`, `inv_next:*`, `inv_rarity:*`, `mkt_prev:*`, `mkt_next:*`, `mkt_mode:*`, `history_metric:*`). Paged views carry their state in the `custom_id` (whose data, filter or mode, and target page), so they keep working after a restart; the pre-2026-11-05 `/inventory` and `/market` ids (`inventory_prev`, `market_mode_daily`, …) now answer privately that the menu expired.
- **Button Hierarchy:**
  - Active filter/tab toggle (`Weekly` vs `Monthly`, `Daily` vs `Weekly` vs `All`) uses `discord.ButtonStyle.primary` (Blurple); inactive tabs use `discord.ButtonStyle.secondary` (Grey).
  - Pagination controls (`◀ Previous`, `Next ▶`) use `discord.ButtonStyle.primary` and disable automatically at boundary pages (`current_page <= 0` or `current_page >= total_pages - 1`).
  - Queue results (`QueueResultView`): finished queues show a primary `Game breakdown` button (`custom_id="queue_result_games"`) **before** the single persistent `discord.ButtonStyle.secondary` verification CTA (`label="Account unlinked?  Verify here for a gf."`, `custom_id="queue_result_verify"`). Unfinished queues only show the verification CTA. Both variants are shared singletons (`get_queue_result_view(include_breakdown)`), not one view per message.
  - `/leaderboard_season` (`SeasonalLeaderboardView`): Previous/Next (only when there is more than one page, `season_prev:{month}:{stat}:{page}`) above a `Rank by` select (`season_stat:{month}`) offering Avg Damage (default), Avg Impact and Avg Kills; picking a stat returns to page 1. The two-line entry format is kept (`{emoji} {label}: **value** (N games)`).
  - Game breakdown paging (`QueueGamesPagerView`) follows the same pagination rule with the queue number and target page carried in the `custom_id` (`qgame_prev:{match_id}:{page}`, `qgame_next:{match_id}:{page}`), a finite `900s` timeout (these live in ephemeral messages) and clicks handled in `log_interaction`. Below the buttons, a `Jump to game` select (`qgame_pick:{match_id}`, the option value is the page; added 2026-11-19) lists every game as `Game N · Team X won` with `Top avg dmg: name (damage)` as its description (one game's average damage is its damage), the current game selected.
- **Rank Formatting in Embeds:** Top 3 positions use medal emojis (`🥇`, `🥈`, `🥉`); rank 4+ uses inline code backticks (`` `#4` `` .. `` `#10` ``).

### 5.4 Game Card (`generate_queue_game_image`) and Game Breakdown Message
One game of a finished queue, opened by the `Game breakdown` button as an **ephemeral** message (only the clicker sees it), built from the snapshot saved when the result was posted (`queue_results` table, 90-day retention).
- **Header (shared `_render_queue_card`):** `#{match_id}` title; subtitle `Team N won Game G` in `QUEUE_IMG_WIN` (`Winner unknown` in `QUEUE_IMG_MUTED`); meta line `Game G of N · Series A - B`; centered `72px` series score *through this game* (leader `QUEUE_IMG_WIN`, trailer `QUEUE_IMG_LOSE`, tie `QUEUE_IMG_TEXT`); the two-lane `MATCH TIMELINE` (5.1) with every game of the series placed by its winner and the `QUEUE_IMG_ACCENT` ring on the selected game. On this card the panels follow this game's winner, so the lanes' colours do too.
- **Panels:** the winner of *this game* gets the win banner and border, the other team the lose styling, an unknown winner the neutral styling. Columns per 3.2 (`Player`, `Kills`, `Dmg`, `Impact`, or without `Impact` when nobody was scored). Same unlinked-player icon and footer legend as the series image.
- **Embed (`build_queue_game_payload`):** Blurple (`discord.Color.blurple()`), title `Game G of N · Queue #id`, description with the winner/series line plus `Top damage` and `Top impact` (names in bold), footer explaining Impact only when it is shown. The image carries Discord alt text (`discord.File(description=...)`) listing every player's kills, damage and impact (capped at 1000 characters), so the card is never image-only.
- **Empty state:** a missing, expired or unreadable snapshot answers privately with what happened and the exact `/queue_stats {match_id}` command that rebuilds it.

### 5.5 Player History Card (`generate_history_image`)
A unified `1600px`-wide card displaying a player's last 10 finalized 4v4 queues in the current server, paired with interactive metric toggle buttons (`Damage`, `Impact`, `Kills`) in Discord. Since 2026-11-05 it is `/profile`'s **History** view (an embed with no title, since the card names the player); the separate `/history` command was removed. `/profile`'s **Overview** view is a text embed of short emoji-prefixed stat lines.
- **Top Header Banner (`1600 x 210`, `_draw_header_gradient`):**
  - **Left Column:** Title (`62px` bold `QUEUE_IMG_TEXT`) `f"{player_name}'s History"` at `y = 28`; Subtitle (`38px` bold) `Last N Queues · XW - YL` colored `QUEUE_IMG_WIN` / `QUEUE_IMG_LOSE` / `QUEUE_IMG_MUTED`; Meta line (`22px` bold `QUEUE_IMG_MUTED` at `y = 152`) `Win Rate: {win_rate}% · {tot_gms} Game(s) Played` (or `{tot_gms} Game(s) Played` when undecided).
  - **Right side:** empty since 2026-11-05 — the `AVG DAMAGE` / `AVG IMPACT` / `TOTAL KILLS` summary box was removed at the user's request. The title shrinks from `62px` to `46px` for long names before shortening the name with `…`, always keeping `'s History`.
- **Minimalist Sparkline Panel (`256px` tall, `radius=18`, fill `QUEUE_IMG_CARD_BG`, border `QUEUE_IMG_ROW_DIVIDER`):**
  - Card header strip (`52px` tall): metric title on left (`AVG DAMAGE TREND`, `AVERAGE IMPACT TREND (0–100)`, or `TOTAL KILLS TREND` in `30px` bold `QUEUE_IMG_TEXT`), followed by a `1px` horizontal divider.
  - Chronological plot from left (oldest) to right (newest), with `#match_id` labels along the X-axis (`area_base_y + 4` to `sparkline_bottom - 6`, latest queue in bold `QUEUE_IMG_TEXT`) and vertical `5px` tick marks extending down from a continuous baseline rule (`area_base_y = sparkline_bottom - 50`).
  - `plot_y1 = sparkline_bottom - 72` keeps the minimum data point `22px` above the baseline rule so the translucent area fill (`fill=(255, 165, 50, 28)`) never pinches to zero height and never collides with X-axis queue numbers.
  - `4px` `QUEUE_IMG_ACCENT` (`#FFA532`) line, `7px` radius dots colored by result (`QUEUE_IMG_WIN` green, `QUEUE_IMG_LOSE` red, `QUEUE_IMG_MUTED` grey), and `13px` outer `QUEUE_IMG_ACCENT` ring on the latest queue.
  - Pill-backed value badges: every data point displays its numeric value placed **above** its marker inside a dark rounded pill badge (`fill=QUEUE_IMG_BG`, `outline=QUEUE_IMG_ACCENT` with accent text for latest / `QUEUE_IMG_ROW_DIVIDER` with light text for others) with exact `textbbox` top-bearing compensation (`16px` clearance above the latest outer ring, `10px` above normal dots).
- **10-Queue Table Card (`radius=18`, fill `QUEUE_IMG_CARD_BG`, border `QUEUE_IMG_ROW_DIVIDER`):**
  - Column header strip at `48px` height with labels `["Queue", "Result", "Kills", "Avg Dmg", "Impact"]` at ratios `[0.0, 0.22, 0.48, 0.68, 0.84]`, highlighting the active metric's column header in `QUEUE_IMG_ACCENT`.
  - Ordered **newest-first** with alternating even-row `QUEUE_IMG_ROW_ALT` pills (`radius=10`, `12px` X-inset).
  - Cells: `Queue` (`#3674`), `Result` (`W · 3 - 1` in green / `L · 1 - 3` in red / `-` in muted), `Kills` (regular body), `Avg Dmg` (bold accent `#FFA532`), `Impact` (bold text `0–100`, or `-` in muted).
- **Discord Interaction (`ProfileView`):** `Overview` / `History` tab buttons (`profile_tab:{tab}:{guild_id}:{discord_id}`, active tab primary); the History view adds a second row:
  - Stateless button row: `Damage`, `Impact`, `Kills` with `custom_id="history_metric:{metric}:{guild_id}:{discord_id}"` (the id format predates the merge, so buttons on old `/history` messages redraw them as `/profile` messages).
  - Active button is `discord.ButtonStyle.primary` (Blurple); inactive buttons are `discord.ButtonStyle.secondary` (Grey).



