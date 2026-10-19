"""Font loading and every PIL-based image generator used by the bot's slash commands.

Kept self-contained (no db/discord-client/network imports) so images can be
generated and unit-tested independently of the rest of the bot.
"""
import os
from datetime import datetime, timezone
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

_CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))

QUEUE_FONT_PATHS = [
    os.getenv("QUEUE_FONT_PATH"),
    os.path.join(_CURRENT_DIR, "fonts", "QuattrocentoSans-Regular.ttf"),
    os.path.join(_CURRENT_DIR, "QuattrocentoSans-Regular.ttf"),
    "fonts/QuattrocentoSans-Regular.ttf",
    "QuattrocentoSans-Regular.ttf",
    "/usr/share/fonts/truetype/quattrocento/QuattrocentoSans-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
]
QUEUE_FONT_BOLD_PATHS = [
    os.getenv("QUEUE_FONT_BOLD_PATH"),
    os.path.join(_CURRENT_DIR, "fonts", "QuattrocentoSans-Bold.ttf"),
    os.path.join(_CURRENT_DIR, "QuattrocentoSans-Bold.ttf"),
    "fonts/QuattrocentoSans-Bold.ttf",
    "QuattrocentoSans-Bold.ttf",
    "/usr/share/fonts/truetype/quattrocento/QuattrocentoSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
]


def resolve_queue_font_path(paths: list[str | None]) -> str | None:
    for path in paths:
        if not path:
            continue
        if os.path.isfile(path):
            return path
        try:
            ImageFont.truetype(path, 12)
            return path
        except Exception:
            continue
    return None


QUEUE_FONT_PATH = resolve_queue_font_path(QUEUE_FONT_PATHS)
QUEUE_FONT_BOLD_PATH = resolve_queue_font_path(QUEUE_FONT_BOLD_PATHS) or QUEUE_FONT_PATH


def load_font(size: int, weight: str = "regular"):
    font_path = QUEUE_FONT_BOLD_PATH if weight in ("bold", "semibold") else QUEUE_FONT_PATH
    if font_path is not None:
        try:
            return ImageFont.truetype(font_path, size)
        except Exception:
            pass

    if weight in ("bold", "semibold"):
        fallback_names = ("DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf", "FreeSansBold.ttf")
    else:
        fallback_names = ("DejaVuSans.ttf", "LiberationSans-Regular.ttf", "FreeSans.ttf")

    for fallback in fallback_names:
        try:
            return ImageFont.truetype(fallback, size)
        except Exception:
            continue

    return ImageFont.load_default()


def prettify_shop_item_type(item_type: str | None) -> str:
    if not item_type:
        return "Unknown"
    return item_type.replace("_", " ").title()


def compute_inventory_worth(items: list[dict]) -> int:
    total_worth = 0
    for item in items:
        value = item.get("value")
        if isinstance(value, (int, float)):
            total_worth += int(value)
            continue
        price_paid = item.get("pricePaid")
        if isinstance(price_paid, (int, float)):
            total_worth += int(price_paid)
    return total_worth


def group_inventory_items(items: list[dict]) -> list[dict]:
    """Collapses duplicate copies of the same skin into one entry with a count."""
    grouped: dict[tuple, dict] = {}
    for item in items:
        name = item.get("name", "Unknown Item")
        rarity = item.get("rarity", 0)
        key = (name, rarity)
        group = grouped.get(key)
        if group is None:
            group = {"name": name, "rarity": rarity, "count": 0, "value": item.get("value")}
            grouped[key] = group
        group["count"] += 1
        if group["value"] is None:
            group["value"] = item.get("value")
    return list(grouped.values())


INVENTORY_RARITY_FILTERS: dict[str, str] = {
    "all": "🔄️ All Rarities",
    "0": "Stock",
    "1": "Common",
    "2": "Uncommon",
    "3": "Rare",
    "4": "Epic",
    "5": "Mythic",
}


def normalize_inventory_rarity_filter(rarity_filter: str | None) -> str:
    key = (rarity_filter or "all").strip().lower()
    return key if key in INVENTORY_RARITY_FILTERS else "all"


def build_inventory_rarity_counts(grouped_items: list[dict]) -> dict[str, int]:
    counts = {key: 0 for key in INVENTORY_RARITY_FILTERS}
    counts["all"] = len(grouped_items)
    for entry in grouped_items:
        rarity_key = str(entry.get("rarity", ""))
        if rarity_key in counts:
            counts[rarity_key] += 1
    return counts


# --- Queue result image styling (module-level so any renderer can reuse/tweak it) ---
QUEUE_IMG_WIDTH = 1600
QUEUE_IMG_PADDING = 44
QUEUE_IMG_HEADER_HEIGHT = 210
QUEUE_IMG_ROW_HEIGHT = 92

QUEUE_IMG_BG = (18, 24, 37)
QUEUE_IMG_HEADER_BG = (24, 33, 55)
QUEUE_IMG_HEADER_GRADIENT_END = (34, 48, 73)
QUEUE_IMG_ROW_ALT = (28, 37, 55)
QUEUE_IMG_TEXT = (235, 237, 240)
QUEUE_IMG_MUTED = (168, 183, 207)
QUEUE_IMG_ACCENT = (255, 165, 50)
QUEUE_IMG_WIN = (108, 199, 128)
QUEUE_IMG_LOSE = (214, 96, 96)

QUEUE_IMG_CARD_BG = (22, 30, 46)
QUEUE_IMG_CARD_RADIUS = 18
QUEUE_IMG_CARD_INNER_PAD_X = 20
QUEUE_IMG_CARD_BOTTOM_PAD = 16
QUEUE_IMG_TEAM_BANNER_HEIGHT = 58
QUEUE_IMG_COL_HEADER_HEIGHT = 48
QUEUE_IMG_WIN_BANNER_BG = (27, 56, 47)
QUEUE_IMG_LOSE_BANNER_BG = (56, 31, 41)
QUEUE_IMG_NEUTRAL_BANNER_BG = (30, 41, 62)
QUEUE_IMG_ROW_DIVIDER = (38, 50, 74)

# Column offsets as a fraction of a team panel's inner width: Player, Kills, Damage, Avg Damage
QUEUE_IMG_COLUMN_RATIOS = [0.0, 0.40, 0.60, 0.80]
QUEUE_IMG_COLUMN_LABELS = ["Player", "Kills", "Dmg", "Avg"]
# The same table with survev.de's impact score as a fifth column, used when at least one player has a score.
QUEUE_IMG_COLUMN_RATIOS_IMPACT = [0.0, 0.36, 0.47, 0.64, 0.80]
QUEUE_IMG_COLUMN_LABELS_IMPACT = ["Player", "Kills", "Dmg", "Avg", "Impact"]
# Single-game card: no Avg column, because one game's damage already is its average.
QUEUE_IMG_GAME_COLUMN_RATIOS = [0.0, 0.40, 0.60, 0.80]
QUEUE_IMG_GAME_COLUMN_LABELS = ["Player", "Kills", "Dmg", "Impact"]
QUEUE_IMG_GAME_COLUMN_RATIOS_NO_IMPACT = [0.0, 0.50, 0.75]
QUEUE_IMG_GAME_COLUMN_LABELS_NO_IMPACT = ["Player", "Kills", "Dmg"]
QUEUE_IMG_NAME_PADDING_RIGHT = 16  # gap kept clear before the Kills column starts
QUEUE_IMG_UNLINKED_ICON_SIZE = 24
QUEUE_IMG_UNLINKED_ICON_GAP = 10

# Grid card outline colors, keyed by rarity int (0 Stock ... 5 Mythic, see INVENTORY_RARITY_FILTERS).
RARITY_COLORS: dict[int, tuple[int, int, int]] = {
    0: (140, 140, 140),   # Stock - grey
    1: (85, 190, 90),     # Common - green
    2: (85, 190, 90),     # Uncommon - green
    3: (110, 190, 245),   # Rare - light blue
    4: (176, 90, 220),    # Epic - purple
    5: (191, 87, 0),      # Mythic - burnt orange
}


def rarity_color(rarity: int) -> tuple[int, int, int]:
    return RARITY_COLORS.get(rarity, QUEUE_IMG_MUTED)


INVENTORY_GRID_COLUMNS = 4
INVENTORY_CARD_HEIGHT = 150
INVENTORY_CARD_GAP = 24
INVENTORY_ITEMS_PER_PAGE = 12

SHOP_GRID_COLUMNS = 2
SHOP_CARD_HEIGHT = 160
SHOP_CARD_GAP = 24
SHOP_ITEMS_PER_PAGE = 6


def truncate_to_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> str:
    """Shortens text with an ellipsis so it never overflows into the next column."""
    if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
        return text
    while text and draw.textbbox((0, 0), text + "…", font=font)[2] > max_width:
        text = text[:-1]
    return f"{text}…" if text else "…"


def format_duration_ms(duration_ms: int) -> str:
    total_seconds = max(0, int(duration_ms)) // 1000
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}h {minutes:02d}m {seconds:02d}s"
    if minutes > 0:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


def _draw_header_gradient(draw: ImageDraw.ImageDraw, header_height: int, width: int = QUEUE_IMG_WIDTH):
    for y in range(header_height):
        ratio = y / max(1, header_height - 1)
        gradient_color = (
            QUEUE_IMG_HEADER_BG[0] + int((QUEUE_IMG_HEADER_GRADIENT_END[0] - QUEUE_IMG_HEADER_BG[0]) * ratio),
            QUEUE_IMG_HEADER_BG[1] + int((QUEUE_IMG_HEADER_GRADIENT_END[1] - QUEUE_IMG_HEADER_BG[1]) * ratio),
            QUEUE_IMG_HEADER_BG[2] + int((QUEUE_IMG_HEADER_GRADIENT_END[2] - QUEUE_IMG_HEADER_BG[2]) * ratio),
        )
        draw.line([(0, y), (width, y)], fill=gradient_color)


def _vcenter_y(draw: ImageDraw.ImageDraw, font: ImageFont.FreeTypeFont, box_top: float, box_bottom: float) -> float:
    """Computes a consistent vertical y-offset for a given font within [box_top, box_bottom]
    so all columns in the same row share an identical typographic baseline."""
    bbox = draw.textbbox((0, 0), "Ay0", font=font)
    return box_top + (box_bottom - box_top - (bbox[3] - bbox[1])) / 2 - bbox[1]


def _build_unlinked_icon(
    size_px: int,
    link_color: tuple[int, int, int] = QUEUE_IMG_MUTED,
    slash_color: tuple[int, int, int] = QUEUE_IMG_LOSE,
) -> Image.Image:
    """Renders an anti-aliased vector 'broken/slashed chain link' RGBA icon of `size_px` x `size_px`."""
    ss = 4
    n = max(16, size_px * ss)
    layer = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    ldraw = ImageDraw.Draw(layer)
    cy = n / 2
    stroke = max(2, round(n * 0.105))
    r = round(n * 0.185)

    # Left and right link cuffs
    ly0, ly1 = round(cy - r), round(cy + r)
    ldraw.rounded_rectangle([round(n * 0.04), ly0, round(n * 0.52), ly1], radius=r, outline=(*link_color, 255), width=stroke)
    ldraw.rounded_rectangle([round(n * 0.48), ly0, round(n * 0.96), ly1], radius=r, outline=(*link_color, 255), width=stroke)

    # Clear center break channel and draw the slash pill before rotating 45 degrees
    gap_half = round(n * 0.14)
    ldraw.rectangle([round(n / 2 - gap_half), 0, round(n / 2 + gap_half), n], fill=(0, 0, 0, 0))
    sx0 = round(n / 2 - stroke / 2)
    sx1 = round(n / 2 + stroke / 2)
    ldraw.rounded_rectangle([sx0, round(n * 0.08), sx1, round(n * 0.92)], radius=max(1, stroke // 2), fill=(*slash_color, 255))

    rotated = layer.rotate(45, resample=Image.Resampling.BICUBIC)
    return rotated.resize((size_px, size_px), resample=Image.Resampling.LANCZOS)


def has_impact_scores(teams: list[list[dict]]) -> bool:
    """True when survev.de scored at least one player (not every map is rated, so the Impact column is optional)."""
    return any((entry.get("stats") or {}).get("impact") is not None for team in teams for entry in team)


def shows_impact_column(teams: list[list[dict]]) -> bool:
    """Whether the queue image gets an Impact column: someone was scored, and it is the standard two-team
    layout (a third panel is too narrow for five columns)."""
    return len(teams) <= 2 and has_impact_scores(teams)


def _queue_cell(label: str, entry: dict, player_label: str) -> tuple[str, bool, tuple[int, int, int]]:
    """Text, weight (bold?) and color of one player-row cell for the column with this header label."""
    stats = entry["stats"]
    if label == "Kills":
        return str(stats["kills"]), False, QUEUE_IMG_TEXT
    if label == "Dmg":
        return f"{stats['damage']:,}", True, QUEUE_IMG_TEXT
    if label == "Avg":
        games = stats["games"]
        avg_damage = stats["damage"] / games if games else 0
        return f"{avg_damage:,.0f}", True, QUEUE_IMG_ACCENT
    if label == "Impact":
        impact = stats.get("impact")
        if impact is None:
            return "-", True, QUEUE_IMG_MUTED
        return str(int(impact + 0.5)), True, QUEUE_IMG_TEXT
    return player_label, True, QUEUE_IMG_TEXT


def _render_queue_card(
    teams: list[list[dict]],
    labels: list[str],
    ratios: list[float],
    title: str,
    subtitle: str,
    subtitle_color: tuple[int, int, int],
    meta_text: str,
    team_scores: list[int],
    leader_index: int | None,
    panel_winner_index: int | None,
    match_history: list[bool | None] | None,
    highlight_index: int | None,
) -> BytesIO:
    """Draws one queue card: a header (title, result line, score, game timeline) above one table panel per team.
    The series result image and the single-game card share this and differ only in what they pass in."""
    S = 2  # 2x supersampling factor for smooth anti-aliased circles, borders, and text
    num_teams = max(len(teams), 1)
    max_rows = max((len(team) for team in teams), default=0)

    panel_width = (QUEUE_IMG_WIDTH - QUEUE_IMG_PADDING * (num_teams + 1)) // num_teams
    panel_x_positions = [QUEUE_IMG_PADDING + i * (panel_width + QUEUE_IMG_PADDING) for i in range(num_teams)]
    inner_width = panel_width - 2 * QUEUE_IMG_CARD_INNER_PAD_X
    columns = [round(ratio * inner_width) for ratio in ratios]

    panel_top = QUEUE_IMG_HEADER_HEIGHT + QUEUE_IMG_PADDING
    col_header_top = panel_top + QUEUE_IMG_TEAM_BANNER_HEIGHT
    rows_top = col_header_top + QUEUE_IMG_COL_HEADER_HEIGHT
    panel_bottom = rows_top + max(max_rows, 1) * QUEUE_IMG_ROW_HEIGHT + QUEUE_IMG_CARD_BOTTOM_PAD
    height = panel_bottom + QUEUE_IMG_PADDING + 14

    image = Image.new("RGB", (QUEUE_IMG_WIDTH * S, height * S), QUEUE_IMG_BG)
    draw = ImageDraw.Draw(image)

    title_font = load_font(66 * S, "bold")
    subtitle_font = load_font(38 * S, "bold")
    team_header_font = load_font(38 * S, "bold")
    header_font = load_font(30 * S, "bold")
    body_font = load_font(35 * S)
    body_font_bold = load_font(35 * S, "bold")
    duration_font = load_font(22 * S, "bold")
    footer_font = load_font(22 * S)

    # Top banner with subtle gradient
    _draw_header_gradient(draw, QUEUE_IMG_HEADER_HEIGHT * S, QUEUE_IMG_WIDTH * S)

    draw.text((QUEUE_IMG_PADDING * S, 28 * S), title, font=title_font, fill=QUEUE_IMG_TEXT)
    draw.text((QUEUE_IMG_PADDING * S, 96 * S), subtitle, font=subtitle_font, fill=subtitle_color)

    if match_history:
        history_count = len(match_history)

        timeline_box_width = 560 * S
        timeline_box_height = 146 * S
        timeline_box_x = (QUEUE_IMG_WIDTH - QUEUE_IMG_PADDING) * S - timeline_box_width
        timeline_box_y = 32 * S
        timeline_box_fill = (28, 40, 64)
        draw.rounded_rectangle(
            [timeline_box_x, timeline_box_y, timeline_box_x + timeline_box_width, timeline_box_y + timeline_box_height],
            radius=20 * S,
            fill=timeline_box_fill,
            outline=QUEUE_IMG_MUTED,
            width=2 * S
        )

        title_text = "MATCH TIMELINE"
        title_bbox = draw.textbbox((0, 0), title_text, font=header_font)
        title_x = timeline_box_x + (timeline_box_width - (title_bbox[2] - title_bbox[0])) / 2
        draw.text((title_x, timeline_box_y + 14 * S), title_text, font=header_font, fill=QUEUE_IMG_TEXT)

        axis_y = timeline_box_y + 74 * S
        axis_x0 = timeline_box_x + 28 * S
        axis_x1 = timeline_box_x + timeline_box_width - 28 * S
        draw.line([(axis_x0, axis_y), (axis_x1, axis_y)], fill=QUEUE_IMG_MUTED, width=2 * S)

        spacing = 0 if history_count <= 1 else (axis_x1 - axis_x0) / (history_count - 1)

        for idx, result in enumerate(match_history):
            x = axis_x0 + spacing * idx
            dot_color = QUEUE_IMG_MUTED if result is None else (QUEUE_IMG_WIN if result else QUEUE_IMG_LOSE)
            dot_radius = (14 if idx == highlight_index else 12) * S
            dot_bbox = [x - dot_radius, axis_y - dot_radius, x + dot_radius, axis_y + dot_radius]
            draw.ellipse(dot_bbox, fill=dot_color)

            if idx == highlight_index:
                highlight_bbox = [x - dot_radius - 4 * S, axis_y - dot_radius - 4 * S, x + dot_radius + 4 * S, axis_y + dot_radius + 4 * S]
                draw.ellipse(highlight_bbox, outline=QUEUE_IMG_ACCENT, width=4 * S)

            label = f"G{idx + 1}"
            label_bbox = draw.textbbox((0, 0), label, font=body_font)
            label_x = x - (label_bbox[2] - label_bbox[0]) / 2
            draw.text((label_x, axis_y + dot_radius + 10 * S), label, font=body_font, fill=QUEUE_IMG_TEXT)

    score_font = load_font(72 * S, "bold")
    if num_teams == 2:
        left_text = str(team_scores[0])
        right_text = str(team_scores[1])
        separator_text = " - "
        left_color = QUEUE_IMG_TEXT
        right_color = QUEUE_IMG_TEXT

        if leader_index is not None:
            if leader_index == 0:
                left_color = QUEUE_IMG_WIN
                right_color = QUEUE_IMG_LOSE
            elif leader_index == 1:
                left_color = QUEUE_IMG_LOSE
                right_color = QUEUE_IMG_WIN

        left_width = draw.textbbox((0, 0), left_text, font=score_font)[2]
        sep_width = draw.textbbox((0, 0), separator_text, font=score_font)[2]
        right_width = draw.textbbox((0, 0), right_text, font=score_font)[2]
        total_width = left_width + sep_width + right_width

        x_pos = (QUEUE_IMG_WIDTH * S - total_width) / 2
        y_pos = 88 * S

        draw.text((x_pos, y_pos), left_text, font=score_font, fill=left_color)
        draw.text((x_pos + left_width, y_pos), separator_text, font=score_font, fill=QUEUE_IMG_TEXT)
        draw.text((x_pos + left_width + sep_width, y_pos), right_text, font=score_font, fill=right_color)
    else:
        score_text = " / ".join(str(score) for score in team_scores)
        score_bbox = draw.textbbox((0, 0), score_text, font=score_font)
        x_pos = (QUEUE_IMG_WIDTH * S - (score_bbox[2] - score_bbox[0])) / 2
        y_pos = 88 * S
        draw.text((x_pos, y_pos), score_text, font=score_font, fill=QUEUE_IMG_TEXT)

    if meta_text:
        draw.text((QUEUE_IMG_PADDING * S, 152 * S), meta_text, font=duration_font, fill=QUEUE_IMG_MUTED)

    card_radius = QUEUE_IMG_CARD_RADIUS * S
    col_widths = [columns[i + 1] - columns[i] for i in range(len(columns) - 1)] + [inner_width - columns[-1]]

    for team_index in range(num_teams):
        team_players = teams[team_index] if team_index < len(teams) else []
        x0 = panel_x_positions[team_index]
        is_winner = panel_winner_index == team_index
        if is_winner:
            team_color = QUEUE_IMG_WIN
            banner_bg = QUEUE_IMG_WIN_BANNER_BG
        elif panel_winner_index is not None:
            team_color = QUEUE_IMG_LOSE
            banner_bg = QUEUE_IMG_LOSE_BANNER_BG
        else:
            team_color = QUEUE_IMG_MUTED
            banner_bg = QUEUE_IMG_NEUTRAL_BANNER_BG

        # 1. Rounded card surface fill
        draw.rounded_rectangle(
            [x0 * S, panel_top * S, (x0 + panel_width) * S, panel_bottom * S],
            radius=card_radius,
            fill=QUEUE_IMG_CARD_BG,
        )

        # 2. Tinted team header banner (rounded top corners, flat bottom edge)
        draw.rounded_rectangle(
            [x0 * S + S, panel_top * S + S, (x0 + panel_width) * S - S, col_header_top * S],
            radius=card_radius - S,
            fill=banner_bg,
        )
        draw.rectangle(
            [x0 * S + S, (col_header_top - QUEUE_IMG_CARD_RADIUS) * S, (x0 + panel_width) * S - S, col_header_top * S],
            fill=banner_bg,
        )
        draw.line(
            [(x0 * S + S, col_header_top * S), ((x0 + panel_width) * S - S, col_header_top * S)],
            fill=team_color,
            width=2 * S,
        )

        team_label = f"Team {team_index + 1}"
        team_label_bbox = draw.textbbox((0, 0), team_label, font=team_header_font)
        team_label_x = x0 * S + (panel_width * S - (team_label_bbox[2] - team_label_bbox[0])) / 2
        team_label_y = _vcenter_y(draw, team_header_font, panel_top * S, col_header_top * S)
        draw.text((team_label_x, team_label_y), team_label, font=team_header_font, fill=team_color)

        # 3. Column header strip + subtle bottom divider
        inner_x0 = x0 + QUEUE_IMG_CARD_INNER_PAD_X
        header_y = _vcenter_y(draw, header_font, col_header_top * S, rows_top * S)
        for col_idx, label in enumerate(labels):
            label_x = (inner_x0 + columns[col_idx]) * S
            label_bbox = draw.textbbox((0, 0), label, font=header_font)
            label_width = label_bbox[2] - label_bbox[0]
            if col_idx != 0 and col_widths[col_idx] * S > label_width:
                label_x += (col_widths[col_idx] * S - label_width) / 2
            draw.text((label_x, header_y), label, font=header_font, fill=QUEUE_IMG_TEXT)

        draw.line(
            [((x0 + 12) * S, rows_top * S), ((x0 + panel_width - 12) * S, rows_top * S)],
            fill=QUEUE_IMG_ROW_DIVIDER,
            width=1 * S,
        )

        if not team_players:
            empty_y = _vcenter_y(draw, body_font, rows_top * S, (rows_top + QUEUE_IMG_ROW_HEIGHT) * S)
            draw.text((inner_x0 * S, empty_y), "No players", font=body_font, fill=QUEUE_IMG_MUTED)

        # 4. Player rows with inset rounded alternating row highlight
        unlinked_row_icon = _build_unlinked_icon(QUEUE_IMG_UNLINKED_ICON_SIZE * S)
        for row_index, entry in enumerate(team_players):
            row_top = rows_top + row_index * QUEUE_IMG_ROW_HEIGHT
            row_box_top = (row_top + 4) * S
            row_box_bottom = (row_top + QUEUE_IMG_ROW_HEIGHT - 4) * S
            if row_index % 2 == 0:
                draw.rounded_rectangle(
                    [(x0 + 12) * S, row_box_top, (x0 + panel_width - 12) * S, row_box_bottom],
                    radius=10 * S,
                    fill=QUEUE_IMG_ROW_ALT,
                )

            player_label = entry.get("display_name") or entry.get("username") or "Unknown"
            is_guest = entry.get("guest", False)

            for col_idx, label in enumerate(labels):
                value, bold, fill = _queue_cell(label, entry, player_label)
                font = body_font_bold if bold else body_font
                cell_x = (inner_x0 + columns[col_idx]) * S
                cell_y = _vcenter_y(draw, font, row_box_top, row_box_bottom)
                if col_idx == 0:
                    max_name_width = (columns[1] - columns[0] - QUEUE_IMG_NAME_PADDING_RIGHT) * S
                    if is_guest:
                        icon_reserve = (QUEUE_IMG_UNLINKED_ICON_SIZE + QUEUE_IMG_UNLINKED_ICON_GAP) * S
                        max_name_width = max(0, max_name_width - icon_reserve)
                    value = truncate_to_width(draw, value, font, max_name_width)
                    draw.text((cell_x, cell_y), value, font=font, fill=fill)
                    if is_guest:
                        name_bbox = draw.textbbox((0, 0), value, font=font)
                        name_width = name_bbox[2] - name_bbox[0]
                        icon_x = round(cell_x + name_width + QUEUE_IMG_UNLINKED_ICON_GAP * S)
                        icon_y = round((row_box_top + row_box_bottom - unlinked_row_icon.height) / 2)
                        image.paste(unlinked_row_icon, (icon_x, icon_y), unlinked_row_icon)
                else:
                    value_bbox = draw.textbbox((0, 0), value, font=font)
                    value_width = value_bbox[2] - value_bbox[0]
                    centered_x = cell_x + (col_widths[col_idx] * S - value_width) / 2
                    draw.text((centered_x, cell_y), value, font=font, fill=fill)

        # 5. Crisp rounded outer card border drawn over banner & card fill
        draw.rounded_rectangle(
            [x0 * S, panel_top * S, (x0 + panel_width) * S, panel_bottom * S],
            radius=card_radius,
            outline=team_color,
            width=2 * S,
        )

    footer_icon = _build_unlinked_icon(18 * S)
    footer_icon_gap = 8 * S
    footer_text = "Unlinked — link your survev.de account with /verify to appear in future leaderboards!"
    footer_bbox = draw.textbbox((0, 0), footer_text, font=footer_font)
    footer_text_width = footer_bbox[2] - footer_bbox[0]
    total_footer_width = footer_icon.width + footer_icon_gap + footer_text_width
    footer_start_x = (QUEUE_IMG_WIDTH * S - total_footer_width) / 2
    footer_y = _vcenter_y(draw, footer_font, panel_bottom * S, height * S)
    footer_icon_y = round((panel_bottom * S + height * S - footer_icon.height) / 2)
    image.paste(footer_icon, (round(footer_start_x), footer_icon_y), footer_icon)
    draw.text(
        (footer_start_x + footer_icon.width + footer_icon_gap, footer_y),
        footer_text,
        font=footer_font,
        fill=QUEUE_IMG_MUTED,
    )

    image = image.resize((QUEUE_IMG_WIDTH, height), resample=Image.Resampling.LANCZOS)

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


def generate_queue_result_image(
    match_id: str,
    teams: list[list[dict]],
    winning_team_index: int | None,
    match_history: list[bool | None] | None = None,
    team_round_wins: list[int] | None = None,
    duration_ms: int | None = None,
) -> BytesIO:
    num_teams = max(len(teams), 1)
    if winning_team_index is not None and 0 <= winning_team_index < num_teams:
        subtitle, subtitle_color = f"Team {winning_team_index + 1} Wins", QUEUE_IMG_WIN
    else:
        subtitle, subtitle_color = "Result unknown", QUEUE_IMG_MUTED

    if team_round_wins is not None and len(team_round_wins) == num_teams:
        team_scores = team_round_wins
    else:
        # Fallback for compatibility if explicit round totals are unavailable.
        team_scores = [max((entry["stats"].get("wins", 0) for entry in team if entry["stats"]), default=0) for team in teams]

    if duration_ms is not None and duration_ms > 0:
        meta_text = f"Match Duration: {format_duration_ms(duration_ms)}"
    else:
        total_games = len(match_history) if match_history else 0
        meta_text = f"{total_games} {'Game' if total_games == 1 else 'Games'} Played" if total_games else ""

    # The Impact column only appears when survev.de scored at least one player (not every map is rated).
    if shows_impact_column(teams):
        ratios, labels = QUEUE_IMG_COLUMN_RATIOS_IMPACT, QUEUE_IMG_COLUMN_LABELS_IMPACT
    else:
        ratios, labels = QUEUE_IMG_COLUMN_RATIOS, QUEUE_IMG_COLUMN_LABELS

    return _render_queue_card(
        teams=teams,
        labels=labels,
        ratios=ratios,
        title=f"#{match_id}",
        subtitle=subtitle,
        subtitle_color=subtitle_color,
        meta_text=meta_text,
        team_scores=team_scores,
        leader_index=winning_team_index,
        panel_winner_index=winning_team_index,
        match_history=match_history,
        highlight_index=len(match_history) - 1 if match_history else None,
    )


def generate_queue_game_image(
    match_id: str,
    game_number: int,
    total_games: int,
    teams: list[list[dict]],
    winner_index: int | None,
    match_history: list[bool | None] | None,
    series_score: list[int],
) -> BytesIO:
    """Card for one game of a queue's series: that game's winner and per-player kills, damage and impact, with the
    series score through this game and the timeline ringing this game. `teams` holds the same entries as the result
    image, with `stats` set to this single game's numbers."""
    num_teams = max(len(teams), 1)
    if winner_index is not None and 0 <= winner_index < num_teams:
        subtitle, subtitle_color = f"Team {winner_index + 1} won Game {game_number}", QUEUE_IMG_WIN
    else:
        subtitle, subtitle_color = "Winner unknown", QUEUE_IMG_MUTED

    scores = (list(series_score) + [0] * num_teams)[:num_teams]
    meta_text = f"Game {game_number} of {total_games}"
    if num_teams == 2:
        meta_text += f" · Series {scores[0]} - {scores[1]}"
    leaders = [index for index, score in enumerate(scores) if score == max(scores)]
    leader_index = leaders[0] if len(leaders) == 1 else None

    if has_impact_scores(teams):
        ratios, labels = QUEUE_IMG_GAME_COLUMN_RATIOS, QUEUE_IMG_GAME_COLUMN_LABELS
    else:
        ratios, labels = QUEUE_IMG_GAME_COLUMN_RATIOS_NO_IMPACT, QUEUE_IMG_GAME_COLUMN_LABELS_NO_IMPACT

    in_timeline = bool(match_history) and 0 <= game_number - 1 < len(match_history)
    return _render_queue_card(
        teams=teams,
        labels=labels,
        ratios=ratios,
        title=f"#{match_id}",
        subtitle=subtitle,
        subtitle_color=subtitle_color,
        meta_text=meta_text,
        team_scores=scores,
        leader_index=leader_index,
        panel_winner_index=winner_index,
        match_history=match_history,
        highlight_index=game_number - 1 if in_timeline else None,
    )


def generate_inventory_image(username: str, items: list[dict], page: int = 0, rarity_filter: str = "all") -> BytesIO:
    rarity_key = normalize_inventory_rarity_filter(rarity_filter)

    grouped_items = group_inventory_items(items)
    if rarity_key != "all":
        rarity_value = int(rarity_key)
        grouped_items = [entry for entry in grouped_items if int(entry.get("rarity", -1)) == rarity_value]
    grouped_items.sort(key=lambda entry: (entry["rarity"], entry["value"] or 0), reverse=True)
    
    start_idx = page * INVENTORY_ITEMS_PER_PAGE
    end_idx = start_idx + INVENTORY_ITEMS_PER_PAGE
    shown_items = grouped_items[start_idx:end_idx]
    total_pages = max(1, -(-len(grouped_items) // INVENTORY_ITEMS_PER_PAGE))

    header_height = QUEUE_IMG_HEADER_HEIGHT
    grid_top = header_height + QUEUE_IMG_PADDING
    grid_width = QUEUE_IMG_WIDTH - QUEUE_IMG_PADDING * 2
    card_width = (grid_width - INVENTORY_CARD_GAP * (INVENTORY_GRID_COLUMNS - 1)) // INVENTORY_GRID_COLUMNS
    rows = max(1, -(-len(shown_items) // INVENTORY_GRID_COLUMNS))
    height = grid_top + rows * (INVENTORY_CARD_HEIGHT + INVENTORY_CARD_GAP) + QUEUE_IMG_PADDING + 60

    image = Image.new("RGB", (QUEUE_IMG_WIDTH, height), QUEUE_IMG_BG)
    draw = ImageDraw.Draw(image)

    title_font = load_font(44, "bold")
    subtitle_font = load_font(22, "bold")
    name_font = load_font(21, "bold")
    count_font = load_font(18, "bold")
    value_font = load_font(20, "bold")
    footer_font = load_font(15)

    _draw_header_gradient(draw, header_height)

    draw.text((QUEUE_IMG_PADDING, 26), f"{username}'s Inventory", font=title_font, fill=QUEUE_IMG_TEXT)
    draw.text(
        (QUEUE_IMG_PADDING, 86),
        f"Page {page + 1}/{total_pages} ({len(shown_items)} of {len(grouped_items)} unique skins)",
        font=subtitle_font,
        fill=QUEUE_IMG_MUTED
    )

    if not shown_items:
        empty_text = "No items in this rarity."
        empty_font = load_font(30, "bold")
        empty_bbox = draw.textbbox((0, 0), empty_text, font=empty_font)
        empty_x = (QUEUE_IMG_WIDTH - (empty_bbox[2] - empty_bbox[0])) / 2
        empty_y = grid_top + (INVENTORY_CARD_HEIGHT / 2)
        draw.text((empty_x, empty_y), empty_text, font=empty_font, fill=QUEUE_IMG_MUTED)

    for idx, entry in enumerate(shown_items):
        col = idx % INVENTORY_GRID_COLUMNS
        row = idx // INVENTORY_GRID_COLUMNS
        x0 = QUEUE_IMG_PADDING + col * (card_width + INVENTORY_CARD_GAP)
        y0 = grid_top + row * (INVENTORY_CARD_HEIGHT + INVENTORY_CARD_GAP)
        x1 = x0 + card_width
        y1 = y0 + INVENTORY_CARD_HEIGHT

        draw.rounded_rectangle(
            [x0, y0, x1, y1], radius=16, fill=QUEUE_IMG_ROW_ALT, outline=rarity_color(entry["rarity"]), width=4
        )

        name_text = truncate_to_width(draw, entry["name"], name_font, card_width - 32)
        draw.text((x0 + 16, y0 + 18), name_text, font=name_font, fill=QUEUE_IMG_TEXT)

        if entry["count"] > 1:
            draw.text((x0 + 16, y0 + 52), f"x{entry['count']}", font=count_font, fill=QUEUE_IMG_MUTED)

        value = entry["value"]
        value_text = f"{value:,} 💰" if isinstance(value, (int, float)) and value else "Stock"
        draw.text((x0 + 16, y1 - 42), value_text, font=value_font, fill=QUEUE_IMG_ACCENT)

    page_text = f"Page {page + 1} of {total_pages}"
    page_width = draw.textbbox((0, 0), page_text, font=footer_font)[2]
    page_x = (QUEUE_IMG_WIDTH - page_width) / 2
    draw.text((page_x, height - 52), page_text, font=footer_font, fill=QUEUE_IMG_MUTED)

    footer_text = "Data courtesy of survev.de API :)"
    footer_width = draw.textbbox((0, 0), footer_text, font=footer_font)[2]
    footer_x = (QUEUE_IMG_WIDTH - footer_width) / 2
    draw.text((footer_x, height - QUEUE_IMG_PADDING + 8), footer_text, font=footer_font, fill=QUEUE_IMG_MUTED)

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


def generate_shop_image(username: str, market_data: dict, mode: str = "all", page: int = 0) -> BytesIO:
    balance = market_data.get("balance", 0)
    offers = market_data.get("offers", [])

    # Filter by mode
    if mode == "daily":
        shown_offers = [offer for offer in offers if offer.get("slot") in (0, 1)]
        mode_label = "Daily Offers"
    elif mode == "weekly":
        shown_offers = [offer for offer in offers if offer.get("slot") in (2, 3)]
        mode_label = "Weekly Offers"
    else:
        shown_offers = offers[:]
        mode_label = "All Offers"

    shown_offers = sorted(shown_offers, key=lambda o: o.get("slot", 0))
    total_pages = max(1, -(-len(shown_offers) // SHOP_ITEMS_PER_PAGE))
    
    # Clamp page
    page = max(0, min(page, total_pages - 1))
    
    start_idx = page * SHOP_ITEMS_PER_PAGE
    end_idx = start_idx + SHOP_ITEMS_PER_PAGE
    page_offers = shown_offers[start_idx:end_idx]

    header_height = QUEUE_IMG_HEADER_HEIGHT
    grid_top = header_height + QUEUE_IMG_PADDING
    grid_width = QUEUE_IMG_WIDTH - QUEUE_IMG_PADDING * 2
    card_width = (grid_width - SHOP_CARD_GAP * (SHOP_GRID_COLUMNS - 1)) // SHOP_GRID_COLUMNS
    rows = max(1, -(-len(page_offers) // SHOP_GRID_COLUMNS))
    height = grid_top + rows * (SHOP_CARD_HEIGHT + SHOP_CARD_GAP) + QUEUE_IMG_PADDING + 60

    image = Image.new("RGB", (QUEUE_IMG_WIDTH, height), QUEUE_IMG_BG)
    draw = ImageDraw.Draw(image)

    title_font = load_font(44, "bold")
    subtitle_font = load_font(22, "bold")
    item_font = load_font(18, "bold")
    price_font = load_font(20, "bold")
    status_font = load_font(16)
    footer_font = load_font(15)

    # Gradient header
    _draw_header_gradient(draw, header_height)

    draw.text((QUEUE_IMG_PADDING, 26), "survev.de Shop", font=title_font, fill=QUEUE_IMG_TEXT)
    draw.text(
        (QUEUE_IMG_PADDING, 86),
        f"{username}'s {mode_label} — Page {page + 1}/{total_pages} ({len(page_offers)} of {len(shown_offers)} offers)",
        font=subtitle_font,
        fill=QUEUE_IMG_MUTED
    )

    balance_text = f"Balance: {balance:,} Golden Fries 💰"
    draw.text((QUEUE_IMG_PADDING, 130), balance_text, font=status_font, fill=QUEUE_IMG_ACCENT)

    # Render cards
    for idx, offer in enumerate(page_offers):
        col = idx % SHOP_GRID_COLUMNS
        row = idx // SHOP_GRID_COLUMNS
        x0 = QUEUE_IMG_PADDING + col * (card_width + SHOP_CARD_GAP)
        y0 = grid_top + row * (SHOP_CARD_HEIGHT + SHOP_CARD_GAP)
        x1 = x0 + card_width
        y1 = y0 + SHOP_CARD_HEIGHT

        # Card background and border - color by status
        purchased = offer.get("purchased", False)
        card_color = (60, 40, 40) if purchased else (40, 60, 40)
        border_color = QUEUE_IMG_LOSE if purchased else QUEUE_IMG_WIN
        
        draw.rounded_rectangle(
            [x0, y0, x1, y1], radius=16, fill=card_color, outline=border_color, width=3
        )

        # Slot info
        slot = offer.get("slot", "?")
        slot_text = f"Slot {slot}"
        draw.text((x0 + 12, y0 + 12), slot_text, font=status_font, fill=QUEUE_IMG_MUTED)

        # Items
        item_types = offer.get("items", [])
        item_label = ", ".join(prettify_shop_item_type(item.get("type")) for item in item_types[:2])
        if len(item_types) > 2:
            item_label += ", …"
        item_label = truncate_to_width(draw, item_label, item_font, card_width - 32)
        draw.text((x0 + 12, y0 + 40), item_label, font=item_font, fill=QUEUE_IMG_TEXT)

        # Price
        price = offer.get("price")
        price_text = f"{price:,} 💰" if price is not None else "?"
        draw.text((x0 + 12, y0 + 75), price_text, font=price_font, fill=QUEUE_IMG_ACCENT)

        # Status
        status_text = "✓ Bought" if purchased else "◯ Available"
        status_color = QUEUE_IMG_LOSE if purchased else QUEUE_IMG_WIN
        draw.text((x0 + 12, y1 - 30), status_text, font=status_font, fill=status_color)

    # Page indicator
    page_text = f"Page {page + 1} of {total_pages}"
    page_width = draw.textbbox((0, 0), page_text, font=footer_font)[2]
    page_x = (QUEUE_IMG_WIDTH - page_width) / 2
    draw.text((page_x, height - 52), page_text, font=footer_font, fill=QUEUE_IMG_MUTED)

    footer_text = "Data courtesy of survev.de API :)"
    footer_width = draw.textbbox((0, 0), footer_text, font=footer_font)[2]
    footer_x = (QUEUE_IMG_WIDTH - footer_width) / 2
    draw.text((footer_x, height - QUEUE_IMG_PADDING + 8), footer_text, font=footer_font, fill=QUEUE_IMG_MUTED)

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


def _pick_compare_row_colors(left_num: float | None, right_num: float | None) -> tuple:
    if left_num is None and right_num is None:
        return QUEUE_IMG_MUTED, QUEUE_IMG_MUTED
    if left_num is None:
        return QUEUE_IMG_MUTED, QUEUE_IMG_TEXT
    if right_num is None:
        return QUEUE_IMG_TEXT, QUEUE_IMG_MUTED
    if left_num == right_num:
        return QUEUE_IMG_TEXT, QUEUE_IMG_TEXT
    return (QUEUE_IMG_WIN, QUEUE_IMG_LOSE) if left_num > right_num else (QUEUE_IMG_LOSE, QUEUE_IMG_WIN)


def format_squad_tier(tier: dict | None, verified: bool) -> tuple[str, int | None]:
    """Text for a /compare tier cell plus the numeric rating used to color the better side.
    `tier` is {"tier", "rating", "region"}, None when unranked, or {"unavailable": True} when the lookup failed."""
    if not verified:
        return "-", None
    if tier is None:
        return "Unranked", None
    if tier.get("unavailable"):
        return "N/A", None
    return f"{tier['tier']} · {tier['rating']}", tier["rating"]


def generate_compare_image(
    left_name: str,
    left_stats: dict | None,
    left_worth: int | None,
    left_queue_summary: dict | None,
    right_name: str,
    right_stats: dict | None,
    right_worth: int | None,
    right_queue_summary: dict | None,
    left_tier: dict | None = None,
    right_tier: dict | None = None,
) -> BytesIO:
    title_font = load_font(48, "bold")
    subtitle_font = load_font(24, "bold")
    name_font = load_font(38, "bold")
    status_font = load_font(20)
    label_font = load_font(22, "bold")
    value_font = load_font(36, "bold")
    footer_font = load_font(16)

    def stat_fields(stats):
        if stats is None:
            return None, None, None
        games = max(1, stats["games"])
        return stats["wins"], stats["kills"] / games, stats["damage"]

    left_wins, left_kd, left_damage = stat_fields(left_stats)
    right_wins, right_kd, right_damage = stat_fields(right_stats)

    def queue_fields(summary):
        summary = summary or {}
        matches = summary.get("matches", 0)
        playtime_ms = summary.get("total_duration_ms") if matches else None
        return playtime_ms, summary.get("all_time_avg_damage"), summary.get("monthly_avg_damage")

    left_playtime_ms, left_all_avg, left_monthly_avg = queue_fields(left_queue_summary)
    right_playtime_ms, right_all_avg, right_monthly_avg = queue_fields(right_queue_summary)

    left_tier_text, left_tier_rating = format_squad_tier(left_tier, verified=left_stats is not None)
    right_tier_text, right_tier_rating = format_squad_tier(right_tier, verified=right_stats is not None)

    def fmt(value, suffix="", none_text="-", decimals=None):
        if value is None:
            return none_text
        if decimals is not None:
            return f"{value:{decimals}}{suffix}"
        return f"{value:,}{suffix}"

    rows = [
        ("All-Time Wins", fmt(left_wins), fmt(right_wins), left_wins, right_wins),
        ("K/D Ratio", fmt(left_kd, decimals=".2f"), fmt(right_kd, decimals=".2f"), left_kd, right_kd),
        ("Total Damage", fmt(left_damage), fmt(right_damage), left_damage, right_damage),
        ("Squad Tier", left_tier_text, right_tier_text, left_tier_rating, right_tier_rating),
        (
            "Inventory Worth",
            fmt(left_worth, suffix=" GF"),
            fmt(right_worth, suffix=" GF"),
            left_worth,
            right_worth,
        ),
        (
            "4v4 Play Time",
            format_duration_ms(left_playtime_ms) if left_playtime_ms is not None else "-",
            format_duration_ms(right_playtime_ms) if right_playtime_ms is not None else "-",
            left_playtime_ms,
            right_playtime_ms,
        ),
        (
            "All-Time Avg Damage",
            fmt(left_all_avg, decimals=",.0f", none_text="N/A"),
            fmt(right_all_avg, decimals=",.0f", none_text="N/A"),
            left_all_avg,
            right_all_avg,
        ),
        (
            f"{datetime.now(timezone.utc).strftime('%B')} Avg Damage",
            fmt(left_monthly_avg, decimals=",.0f", none_text="No data"),
            fmt(right_monthly_avg, decimals=",.0f", none_text="No data"),
            left_monthly_avg,
            right_monthly_avg,
        ),
    ]

    card_left = QUEUE_IMG_PADDING
    card_right = QUEUE_IMG_WIDTH - QUEUE_IMG_PADDING
    card_top = QUEUE_IMG_HEADER_HEIGHT + QUEUE_IMG_PADDING
    name_block_height = 130
    row_height = 92
    card_bottom = card_top + name_block_height + len(rows) * row_height
    height = card_bottom + QUEUE_IMG_PADDING * 2

    image = Image.new("RGB", (QUEUE_IMG_WIDTH, height), QUEUE_IMG_BG)
    draw = ImageDraw.Draw(image)

    _draw_header_gradient(draw, QUEUE_IMG_HEADER_HEIGHT)

    draw.text((QUEUE_IMG_PADDING, 28), "Stats comparison", font=title_font, fill=QUEUE_IMG_TEXT)
    draw.text((QUEUE_IMG_PADDING, 92), "All-time stats are from verification date.", font=subtitle_font, fill=QUEUE_IMG_MUTED)

    card_fill = (26, 30, 42)
    card_outline = (55, 62, 80)
    row_alt_fill = (32, 37, 50)
    bar_color = (88, 101, 242)
    center_x = (card_left + card_right) / 2
    left_center_x = (card_left + center_x) / 2
    right_center_x = (center_x + card_right) / 2
    label_half_width = 190
    bar_x_left = center_x - label_half_width
    bar_x_right = center_x + label_half_width

    draw.rounded_rectangle([card_left, card_top, card_right, card_bottom], radius=24, fill=card_fill, outline=card_outline, width=2)

    def draw_centered(text, center, y, font, fill):
        bbox = draw.textbbox((0, 0), text, font=font)
        x = center - (bbox[2] - bbox[0]) / 2
        draw.text((x, y), text, font=font, fill=fill)

    draw_centered(left_name, left_center_x, card_top + 22, name_font, QUEUE_IMG_TEXT)
    draw_centered(right_name, right_center_x, card_top + 22, name_font, QUEUE_IMG_TEXT)

    left_status = "Verified" if left_stats is not None else "Not verified yet"
    right_status = "Verified" if right_stats is not None else "Not verified yet"
    draw_centered(left_status, left_center_x, card_top + 74, status_font, QUEUE_IMG_MUTED)
    draw_centered(right_status, right_center_x, card_top + 74, status_font, QUEUE_IMG_MUTED)

    rows_top = card_top + name_block_height
    draw.line([(card_left, rows_top), (card_right, rows_top)], fill=card_outline, width=2)

    for idx, (label, left_value, right_value, left_num, right_num) in enumerate(rows):
        y_top = rows_top + idx * row_height
        if idx % 2 == 0:
            draw.rectangle([card_left + 2, y_top, card_right - 2, y_top + row_height], fill=row_alt_fill)

        bar_top = y_top + row_height * 0.22
        bar_bottom = y_top + row_height * 0.78
        draw.rectangle([bar_x_left - 3, bar_top, bar_x_left + 3, bar_bottom], fill=bar_color)
        draw.rectangle([bar_x_right - 3, bar_top, bar_x_right + 3, bar_bottom], fill=bar_color)

        left_fill, right_fill = _pick_compare_row_colors(left_num, right_num)

        label_bbox = draw.textbbox((0, 0), label, font=label_font)
        label_y = y_top + (row_height - (label_bbox[3] - label_bbox[1])) / 2 - label_bbox[1]
        draw_centered(label, center_x, label_y, label_font, QUEUE_IMG_MUTED)

        value_bbox = draw.textbbox((0, 0), left_value, font=value_font)
        value_y = y_top + (row_height - (value_bbox[3] - value_bbox[1])) / 2 - value_bbox[1]
        left_value_x = bar_x_left - 20 - (value_bbox[2] - value_bbox[0])
        draw.text((left_value_x, value_y), left_value, font=value_font, fill=left_fill)

        value_bbox = draw.textbbox((0, 0), right_value, font=value_font)
        value_y = y_top + (row_height - (value_bbox[3] - value_bbox[1])) / 2 - value_bbox[1]
        draw.text((bar_x_right + 20, value_y), right_value, font=value_font, fill=right_fill)

        if idx < len(rows) - 1:
            draw.line([(card_left, y_top + row_height), (card_right, y_top + row_height)], fill=card_outline, width=1)

    footer_text = "Data courtesy of survev.de API :)"
    footer_width = draw.textbbox((0, 0), footer_text, font=footer_font)[2]
    footer_x = (QUEUE_IMG_WIDTH - footer_width) / 2
    draw.text((footer_x, height - QUEUE_IMG_PADDING + 8), footer_text, font=footer_font, fill=QUEUE_IMG_MUTED)

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer
