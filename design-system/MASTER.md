# Design System Master File — C-TierBot

> **STATUS:** Locked Source of Truth (Extracted from existing `C-TierBot` codebase)
> **LOGIC:** When building or modifying a specific image renderer or Discord UI view, first check `design-system/pages/[page-name].md`.
> If that file exists, its rules **override** this Master file.
> If not, strictly follow the tokens, geometry, typography, and component patterns locked below. Do **not** invent new palettes or font pairings.

---

- **Project:** C-TierBot (`survev.de` & NeatQueue Discord Bot)
- **Primary Rendering Stack:** Python `Pillow` (`PIL.Image`, `PIL.ImageDraw`, `PIL.ImageFont`) in [`image_utils.py`](file:///c:/Users/Awang/local/C-TierBot/image_utils.py) + `discord.py` persistent `View` / `Embed` components in [`discord_ui.py`](file:///c:/Users/Awang/local/C-TierBot/discord_ui.py), [`leaderboard_service.py`](file:///c:/Users/Awang/local/C-TierBot/leaderboard_service.py), and [`queue_stats_service.py`](file:///c:/Users/Awang/local/C-TierBot/queue_stats_service.py)
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
| Timeline Box Fill | `(28, 40, 64)` | `#1C2840` | Fill for the `MATCH TIMELINE` rounded container inside the queue result header |

### 1.2 Typography & Semantic Accent Colors

| Constant / Role | RGB Tuple | Hex | Usage |
|---|---|---|---|
| `QUEUE_IMG_TEXT` | `(235, 237, 240)` | `#EBEDF0` | Primary foreground text (match title, score separator, column headers, player names, kills, total damage, timeline labels) |
| `QUEUE_IMG_MUTED` | `(168, 183, 207)` | `#A8B7CF` | Secondary text & neutral chrome (duration subtitle, footers, pagination subheads, empty states, timeline border/axis, tie/unknown team states) |
| `QUEUE_IMG_ACCENT` | `(255, 165, 50)` | `#FFA532` | Primary highlight accent: `Avg` damage column values, latest game outer ring on `MATCH TIMELINE`, Golden Fries balances, item worth/prices |
| `QUEUE_IMG_WIN` | `(108, 199, 128)` | `#6CC780` | Semantic Win / Positive state: `"Team X Wins"` subtitle, winning score, winning team title & `2px` card border, timeline win dot, available shop offer border/status, higher stat in `/compare` |
| `QUEUE_IMG_LOSE` | `(214, 96, 96)` | `#D66060` | Semantic Loss / Negative state: losing score, losing team title & `2px` card border, timeline loss dot, purchased shop offer border/status, lower stat in `/compare` |
| `QUEUE_IMG_WIN_BADGE` | `(34, 106, 72)` | `#226A48` | Module-level win badge fill token |
| `QUEUE_IMG_TEAM_SCORE_BADGE_BG` | `(18, 84, 54)` | `#125436` | Module-level team score badge background token |
| `QUEUE_IMG_TEAM_SCORE_BADGE_TEXT` | `(235, 237, 240)` | `#EBEDF0` | Module-level team score badge foreground token |

### 1.3 Team Header Banner Tints (`generate_queue_result_image`)

| Constant | RGB Tuple | Hex | Condition |
|---|---|---|---|
| `QUEUE_IMG_WIN_BANNER_BG` | `(27, 56, 47)` | `#1B382F` | Winning team (`winning_team_index == team_index`) top banner fill; paired with `QUEUE_IMG_WIN` text and bottom border |
| `QUEUE_IMG_LOSE_BANNER_BG` | `(56, 31, 41)` | `#381F29` | Losing team (`winning_team_index is not None`) top banner fill; paired with `QUEUE_IMG_LOSE` text and bottom border |
| `QUEUE_IMG_NEUTRAL_BANNER_BG` | `(30, 41, 62)` | `#1E293E` | Unknown / tied result (`winning_team_index is None`) top banner fill; paired with `QUEUE_IMG_MUTED` text and bottom border |

### 1.4 Rarity Outline Palette (`RARITY_COLORS`)

Used by `rarity_color(rarity)` for `4px` card borders in `generate_inventory_image()`:

| Rarity Key | Label (`rarity_label`) | RGB Tuple | Hex | Visual |
|---|---|---|---|---|
| `0` | `Stock` | `(140, 140, 140)` | `#8C8C8C` | Grey |
| `1` | `Common` | `(85, 190, 90)` | `#55BE5A` | Green |
| `2` | `Uncommon` | `(85, 190, 90)` | `#55BE5A` | Green |
| `3` | `Rare` | `(110, 190, 245)` | `#6EBEF5` | Light Blue |
| `4` | `Epic` | `(176, 90, 220)` | `#B05ADC` | Purple |
| `5` | `Mythic` | `(191, 87, 0)` | `#BF5700` | Burnt Orange |
| Fallback | `Rarity {n}` | `QUEUE_IMG_MUTED` `(168, 183, 207)` | `#A8B7CF` | Muted Slate |

### 1.5 Renderer-Specific Surface Overrides

- **Shop Offer Cards (`generate_shop_image`)**:
  - **Available (`purchased == False`):** Fill `(40, 60, 40)` / `#283C28`, Border `QUEUE_IMG_WIN` `#6CC780` (`3px`), Status label `"◯ Available"` in `QUEUE_IMG_WIN`
  - **Purchased (`purchased == True`):** Fill `(60, 40, 40)` / `#3C2828`, Border `QUEUE_IMG_LOSE` `#D66060` (`3px`), Status label `"✓ Bought"` in `QUEUE_IMG_LOSE`
- **Stats Comparison Card (`generate_compare_image`)**:
  - **Card Surface (`card_fill`):** `(26, 30, 42)` / `#1A1E2A`
  - **Card Border & Row Dividers (`card_outline`):** `(55, 62, 80)` / `#373E50`
  - **Alternating Row Fill (`row_alt_fill`):** `(32, 37, 50)` / `#202532`
  - **Center Metric Accent Bars (`bar_color`):** `(88, 101, 242)` / `#5865F2` (Discord Blurple)

### 1.6 Discord Embed Color Tokens

| Discord Color | Hex | Applied Commands / Views |
|---|---|---|
| `discord.Color.blurple()` | `#5865F2` | `/profile`, `/inventory`, `/market` |
| `discord.Color.gold()` | `#F1C40F` | `/leaderboard_weekly`, `/leaderboard_season`, `/leaderboard_fries`, `/goldenfries`, `/hall_of_fame` |
| `discord.Color.purple()` | `#9B59B6` | `/leaderboard_monthly` |

---

## 2. Typography

### 2.1 Font Family & Resolution Chain

Loaded via `load_font(size: int, weight: str = "regular")` in [`image_utils.py`](file:///c:/Users/Awang/local/C-TierBot/image_utils.py#L12-L69):

1. **Environment Override:** `QUEUE_FONT_PATH` (regular) / `QUEUE_FONT_BOLD_PATH` (bold)
2. **Locked Primary Font:** **Quattrocento Sans**
   - Regular: `fonts/QuattrocentoSans-Regular.ttf` (or `/usr/share/fonts/truetype/quattrocento/QuattrocentoSans-Regular.ttf`)
   - Bold (`weight in ("bold", "semibold")`): `fonts/QuattrocentoSans-Bold.ttf` (or `/usr/share/fonts/truetype/quattrocento/QuattrocentoSans-Bold.ttf`)
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
| `team_header_font` | `36px` | `72px` | `bold` | `team_color` (`WIN`/`LOSE`/`MUTED`) | Centered team card banner title (`Team 1`, `Team 2`) |
| `body_font_bold` | `31px` | `62px` | `bold` | `QUEUE_IMG_TEXT` (cols 0, 2), `QUEUE_IMG_ACCENT` (col 3) | Player name (`col 0`), Total Damage (`col 2`), Avg Damage (`col 3`) |
| `body_font` | `31px` | `62px` | `regular` | `QUEUE_IMG_TEXT` / `QUEUE_IMG_MUTED` | Kills (`col 1`), timeline node labels (`G1`, `G2`), `"No players"` empty state |
| `header_font` | `28px` | `56px` | `bold` | `QUEUE_IMG_TEXT` | `MATCH TIMELINE` header & table column headers (`Player`, `Kills`, `Dmg`, `Avg`) |
| `footer_font` | `22px` | `44px` | `regular` | `QUEUE_IMG_MUTED` | Header duration (`Match Duration: 18m 42s`) & bottom verification CTA footer |

#### Compare Image (`generate_compare_image`)

| Token / Variable | Size | Weight | Color | Usage |
|---|---|---|---|---|
| `title_font` | `48px` | `bold` | `QUEUE_IMG_TEXT` | `"Stats comparison"` header title |
| `name_font` | `38px` | `bold` | `QUEUE_IMG_TEXT` | Left and right player names in card header |
| `value_font` | `36px` | `bold` | `WIN` / `LOSE` / `TEXT` / `MUTED` | Left and right stat values per row |
| `subtitle_font` | `24px` | `bold` | `QUEUE_IMG_MUTED` | Header subtitle (`"All-time stats are from verification date."`) |
| `label_font` | `22px` | `bold` | `QUEUE_IMG_MUTED` | Centered metric name between blurple bars |
| `status_font` | `20px` | `regular` | `QUEUE_IMG_MUTED` | `"Verified"` / `"Not verified yet"` sub-label |
| `footer_font` | `16px` | `regular` | `QUEUE_IMG_MUTED` | Bottom attribution footer |

#### Inventory & Shop Images (`generate_inventory_image`, `generate_shop_image`)

| Token / Variable | Size | Weight | Color | Usage |
|---|---|---|---|---|
| `title_font` | `44px` | `bold` | `QUEUE_IMG_TEXT` | Header title (`"{username}'s Inventory"`, `"survev.de Shop"`) |
| `empty_font` | `30px` | `bold` | `QUEUE_IMG_MUTED` | Empty state message (`"No items in this rarity."`) |
| `subtitle_font` | `22px` | `bold` | `QUEUE_IMG_MUTED` | Page & count subtitle in header |
| `name_font` | `21px` | `bold` | `QUEUE_IMG_TEXT` | Inventory card skin name |
| `value_font` / `price_font` | `20px` | `bold` | `QUEUE_IMG_ACCENT` | Item worth / offer price (`"{price:,} 💰"`, `"Stock"`) |
| `item_font` / `count_font` | `18px` | `bold` | `QUEUE_IMG_TEXT` / `QUEUE_IMG_MUTED` | Shop offer item list / Inventory duplicate count (`"x3"`) |
| `status_font` | `16px` | `regular` | `ACCENT` / `MUTED` / `WIN` / `LOSE` | Shop balance line, `"Slot N"`, and `"◯ Available"` / `"✓ Bought"` |
| `footer_font` | `15px` | `regular` | `QUEUE_IMG_MUTED` | Bottom pagination (`"Page X of Y"`) & API attribution |

### 2.3 Text Alignment, Baseline & Truncation Rules

- **Consistent Row Baseline (`_vcenter_y`):** Always compute vertical centering inside a row or banner box using `"Ay0"` as the reference glyph bounding box (`box_top + (box_bottom - box_top - (bbox[3] - bbox[1])) / 2 - bbox[1]`) so that text with and without descenders/ascenders shares an identical baseline across columns.
- **Pixel-Width Ellipsis (`truncate_to_width`):** Never allow player names or item labels to overflow into adjacent columns or card borders. Trim character-by-character until `text + "…"` fits within `max_width`.
- **Unlinked Player Suffix:** Guests without a linked Discord account are suffixed with `" (unlinked)"` prior to width truncation.
- **Number Formatting:**
  - Integers (damage, worth, prices, balances): comma-separated (`f"{value:,}"`)
  - Averages (`avg_damage`): rounded integer with commas (`f"{avg_damage:,.0f}"`)
  - Ratios (`K/D Ratio`): 2 decimal places (`f"{kd:.2f}"`)
  - Durations (`format_duration_ms`): `"Xh YYm ZZs"`, `"Ym ZZs"`, or `"Zs"`

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
  - Column start ratios: `[0.0, 0.48, 0.66, 0.84]` for `["Player", "Kills", "Dmg", "Avg"]`:
    - `Player` (`0.00–0.48`, `48%` width): Left-aligned at `inner_x0`, right-padded by `QUEUE_IMG_NAME_PADDING_RIGHT = 16px`.
    - `Kills` (`0.48–0.66`, `18%` width): Center-aligned within column width; regular weight (`body_font`).
    - `Dmg` (`0.66–0.84`, `18%` width): Center-aligned within column width; bold weight (`body_font_bold`).
    - `Avg` (`0.84–1.00`, `16%` width): Center-aligned within column width; bold weight (`body_font_bold`) in `QUEUE_IMG_ACCENT` (`#FFA532`).
- **Inset Alternating Row Highlight:**
  - Drawn on even rows (`row_index % 2 == 0`) with a `12px` horizontal inset from the outer card edges (`x0 + 12` to `x0 + panel_width - 12`) and `4px` top/bottom vertical inset (`84px` pill height inside the `92px` row slot).

### 3.3 Grid Layouts (Inventory, Shop, Compare)

- **Inventory Grid (`generate_inventory_image`):**
  - `INVENTORY_GRID_COLUMNS = 4`, `INVENTORY_ITEMS_PER_PAGE = 12` (up to 3 rows)
  - `INVENTORY_CARD_HEIGHT = 150px`, `INVENTORY_CARD_GAP = 24px`, card width = `360px`
  - Bottom margin = `QUEUE_IMG_PADDING + 60` (`104px`) to house two-line centered pagination + attribution footer.
- **Shop Grid (`generate_shop_image`):**
  - `SHOP_GRID_COLUMNS = 2`, `SHOP_ITEMS_PER_PAGE = 6` (up to 3 rows)
  - `SHOP_CARD_HEIGHT = 160px`, `SHOP_CARD_GAP = 24px`, card width = `744px`
  - Bottom margin = `QUEUE_IMG_PADDING + 60` (`104px`).
- **Compare Split Table (`generate_compare_image`):**
  - Full-width card from `x = 44px` to `1556px` (`1512px` wide), `card_top = 254px`
  - Player header block height: `name_block_height = 130px`
  - Stat row height: `row_height = 92px` (7 rows)
  - Center label zone: `label_half_width = 190px` around `center_x = 800px` (`bar_x_left = 610px`, `bar_x_right = 990px`), with `6px`-wide vertical Blurple bars spanning `22%` to `78%` of each row's height and stat values anchored `20px` outside the bars.

---

## 4. Border Radiuses, Borders & Dividers

| Component | Border Radius (1x) | Border Width (1x) | Border / Divider Color |
|---|---|---|---|
| Compare Main Card | `24px` | `2px` | `(55, 62, 80)` / `#373E50` |
| Match Timeline Box (Header) | `20px` | `2px` | `QUEUE_IMG_MUTED` (`#A8B7CF`) |
| Queue Result Team Panel Card (`QUEUE_IMG_CARD_RADIUS`) | `18px` | `2px` | `team_color` (`QUEUE_IMG_WIN`, `QUEUE_IMG_LOSE`, or `QUEUE_IMG_MUTED`) |
| Queue Result Team Top Banner | `17px` top corners, `0px` bottom corners | `2px` bottom line | `team_color` (`WIN`/`LOSE`/`MUTED`) |
| Inventory Item Card | `16px` | `4px` | `rarity_color(rarity)` (`RARITY_COLORS`) |
| Shop Offer Card | `16px` | `3px` | `QUEUE_IMG_WIN` (available) or `QUEUE_IMG_LOSE` (purchased) |
| Queue Result Alternating Row Pill | `10px` | `0px` (fill only) | Fill: `QUEUE_IMG_ROW_ALT` (`#1C2537`) |
| Column Header Bottom Divider | — | `1px` (`12px` inset from card edges) | `QUEUE_IMG_ROW_DIVIDER` (`#26324A`) |
| Compare Row Divider | — | `1px` (full card width) | `(55, 62, 80)` / `#373E50` |
| Match Timeline Axis & Dots | `12px` dot (`14px` last game, `18px` ring) | `2px` axis, `4px` ring | Axis: `QUEUE_IMG_MUTED`, Dots: `WIN`/`LOSE`/`MUTED`, Ring: `QUEUE_IMG_ACCENT` |

---

## 5. Component Patterns & Architecture

### 5.1 Top Header Banner (`_draw_header_gradient`)
- **Structure:** Full-width `1600 x 210` horizontal strip at `y = 0` with a vertical line-by-line interpolated gradient from `QUEUE_IMG_HEADER_BG` (`#182137`) to `QUEUE_IMG_HEADER_GRADIENT_END` (`#223049`).
- **Queue Result Header Layout:**
  - **Left Column (`x = 44`):** Match ID (`#3674`) at `y = 28`, Winner subtitle (`Team 1 Wins`) at `y = 96`, Match Duration / Games Played metadata at `y = 152`.
  - **Center Scoreboard (`x = centered`, `y = 88`):** Large `72px` bold round score (`left_score - right_score`), where the winning side's digit is colored `QUEUE_IMG_WIN` and the losing side's digit is colored `QUEUE_IMG_LOSE`, separated by `" - "` in `QUEUE_IMG_TEXT`.
  - **Right Column (`MATCH TIMELINE`, `x = 996`, `y = 32`, `560 x 146`):** Displayed when `match_history` is present. Features centered `"MATCH TIMELINE"` header at `y + 14`, horizontal axis at `y + 74` (`28px` horizontal inset), color-coded game outcome dots (`G1`, `G2`, …), and an outer `QUEUE_IMG_ACCENT` ring highlighting the final game.

### 5.2 Team Result Card (`generate_queue_result_image`)
Rendered in a strict 5-layer painter's order per team panel so corners and borders stay razor-sharp:
1. **Card Surface Fill:** `rounded_rectangle` (`radius=18`, fill `QUEUE_IMG_CARD_BG`).
2. **Tinted Team Header Banner:** `rounded_rectangle` (`radius=17`, fill `banner_bg`) for the top corners, overlaid with a flat `rectangle` covering the bottom `18px` of the banner strip so the bottom edge is square, followed by a `2px` horizontal line at `col_header_top` in `team_color` and centered `Team N` title.
3. **Column Header Strip:** `48px` tall strip with `Player` (left-aligned) and `Kills`, `Dmg`, `Avg` (centered in their column slices) plus a `1px` inset bottom divider in `QUEUE_IMG_ROW_DIVIDER`.
4. **Alternating Player Rows:** `92px` tall slots. Even rows (`0, 2, 4...`) draw an inset rounded rectangle (`12px` X-inset, `4px` Y-inset, `radius=10`, fill `QUEUE_IMG_ROW_ALT`). `Player`, `Dmg`, and `Avg` use `body_font_bold`; `Kills` uses regular `body_font`; `Avg` is highlighted in `QUEUE_IMG_ACCENT`.
5. **Outer Card Border:** Final `rounded_rectangle` (`radius=18`, `outline=team_color`, `width=2`) drawn last on top of the banner and card fill to prevent inner fills from clipping the border stroke.

### 5.3 Discord Interactive Views (`discord_ui.py`)
- **Centralized Dispatch Pattern:** All interactive component clicks and select changes are routed through `log_interaction` in [`leaderboard_bot.py`](file:///c:/Users/Awang/local/C-TierBot/leaderboard_bot.py#L87-L178) via `custom_id` matching (`queue_result_verify`, `leaderboard_weekly`, `leaderboard_monthly`, `leaderboard_sort`, `season_prev:*`, `season_next:*`, `inventory_prev`, `inventory_next`, `inventory_rarity`, `market_prev`, `market_next`, `market_mode_*`).
- **Button Hierarchy:**
  - Active filter/tab toggle (`Weekly` vs `Monthly`, `Daily` vs `Weekly` vs `All`) uses `discord.ButtonStyle.primary` (Blurple); inactive tabs use `discord.ButtonStyle.secondary` (Grey).
  - Pagination controls (`◀ Previous`, `Next ▶`) use `discord.ButtonStyle.primary` and disable automatically at boundary pages (`current_page <= 0` or `current_page >= total_pages - 1`).
  - Self-service verification CTA on queue results (`QueueResultView`) uses a single persistent `discord.ButtonStyle.secondary` button (`label="Account unlinked?  Verify here for a gf."`, `custom_id="queue_result_verify"`).
- **Rank Formatting in Embeds:** Top 3 positions use medal emojis (`🥇`, `🥈`, `🥉`); rank 4+ uses inline code backticks (`` `#4` `` .. `` `#10` ``).

