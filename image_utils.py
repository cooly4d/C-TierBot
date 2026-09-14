"""Font loading and every PIL-based image generator used by the bot's slash commands.

Kept self-contained (no db/discord-client/network imports) so images can be
generated and unit-tested independently of the rest of the bot.
"""
import os
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

QUEUE_FONT_PATHS = [
    os.getenv("QUEUE_FONT_PATH"),
    "fonts/QuattrocentoSans-Regular.ttf",
    "QuattrocentoSans-Regular.ttf",
    "/usr/share/fonts/truetype/quattrocento/QuattrocentoSans-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
]
QUEUE_FONT_BOLD_PATHS = [
    os.getenv("QUEUE_FONT_BOLD_PATH"),
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


def truncate_text(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 1].rstrip() + "…"


def rarity_label(rarity: int) -> str:
    return {
        0: "Stock",
        1: "Common",
        2: "Uncommon",
        3: "Rare",
        4: "Epic",
        5: "Mythic"
    }.get(rarity, f"Rarity {rarity}")


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
QUEUE_IMG_TEAM_HEADER_HEIGHT = 84
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
QUEUE_IMG_WIN_BADGE = (34, 106, 72)
QUEUE_IMG_TEAM_SCORE_BADGE_BG = (18, 84, 54)
QUEUE_IMG_TEAM_SCORE_BADGE_TEXT = (235, 237, 240)

# Column offsets as a fraction of a team panel's width: Player, Kills, Damage, Avg Damage
QUEUE_IMG_COLUMN_RATIOS = [0.0, 0.48, 0.66, 0.84]
QUEUE_IMG_COLUMN_LABELS = ["Player", "Kills", "Dmg", "Avg"]
QUEUE_IMG_NAME_PADDING_RIGHT = 16  # gap kept clear before the Kills column starts

# Grid card outline colors, keyed by the same rarity ints as rarity_label.
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


def _draw_header_gradient(draw: ImageDraw.ImageDraw, header_height: int):
    for y in range(header_height):
        ratio = y / max(1, header_height - 1)
        gradient_color = (
            QUEUE_IMG_HEADER_BG[0] + int((QUEUE_IMG_HEADER_GRADIENT_END[0] - QUEUE_IMG_HEADER_BG[0]) * ratio),
            QUEUE_IMG_HEADER_BG[1] + int((QUEUE_IMG_HEADER_GRADIENT_END[1] - QUEUE_IMG_HEADER_BG[1]) * ratio),
            QUEUE_IMG_HEADER_BG[2] + int((QUEUE_IMG_HEADER_GRADIENT_END[2] - QUEUE_IMG_HEADER_BG[2]) * ratio),
        )
        draw.line([(0, y), (QUEUE_IMG_WIDTH, y)], fill=gradient_color)


def generate_queue_result_image(
    match_id: str,
    teams: list[list[dict]],
    winning_team_index: int | None,
    match_history: list[bool | None] | None = None,
    team_round_wins: list[int] | None = None,
) -> BytesIO:
    num_teams = max(len(teams), 1)
    max_rows = max((len(team) for team in teams), default=0)

    panel_width = (QUEUE_IMG_WIDTH - QUEUE_IMG_PADDING * (num_teams + 1)) // num_teams
    panel_x_positions = [QUEUE_IMG_PADDING + i * (panel_width + QUEUE_IMG_PADDING) for i in range(num_teams)]
    columns = [round(ratio * panel_width) for ratio in QUEUE_IMG_COLUMN_RATIOS]

    rows_top = QUEUE_IMG_HEADER_HEIGHT + QUEUE_IMG_PADDING + QUEUE_IMG_TEAM_HEADER_HEIGHT
    height = rows_top + max(max_rows, 1) * QUEUE_IMG_ROW_HEIGHT + QUEUE_IMG_PADDING

    image = Image.new("RGB", (QUEUE_IMG_WIDTH, height), QUEUE_IMG_BG)
    draw = ImageDraw.Draw(image)

    title_font = load_font(66, "bold")
    subtitle_font = load_font(38, "bold")
    team_header_font = load_font(36, "bold")
    header_font = load_font(28, "bold")
    body_font = load_font(31)
    body_font_bold = load_font(31, "bold")
    footer_font = load_font(22)

    # Top banner with subtle gradient
    _draw_header_gradient(draw, QUEUE_IMG_HEADER_HEIGHT)

    draw.text((QUEUE_IMG_PADDING, 28), f"#{match_id}", font=title_font, fill=QUEUE_IMG_TEXT)

    if winning_team_index is not None and 0 <= winning_team_index < num_teams:
        winner_text = f"Team {winning_team_index + 1} Wins"
        winner_color = QUEUE_IMG_WIN
    else:
        winner_text = "Result unknown"
        winner_color = QUEUE_IMG_MUTED

    draw.text((QUEUE_IMG_PADDING, 96), winner_text, font=subtitle_font, fill=winner_color)

    if match_history:
        history_count = len(match_history)
        slice_start = 0
        history_slice = match_history

        timeline_box_width = 560
        timeline_box_height = 146
        timeline_box_x = QUEUE_IMG_WIDTH - QUEUE_IMG_PADDING - timeline_box_width
        timeline_box_y = 32
        timeline_box_fill = (28, 40, 64)
        draw.rounded_rectangle(
            [timeline_box_x, timeline_box_y, timeline_box_x + timeline_box_width, timeline_box_y + timeline_box_height],
            radius=20,
            fill=timeline_box_fill,
            outline=QUEUE_IMG_MUTED,
            width=2
        )

        title_text = "MATCH TIMELINE"
        title_bbox = draw.textbbox((0, 0), title_text, font=header_font)
        title_x = timeline_box_x + (timeline_box_width - (title_bbox[2] - title_bbox[0])) / 2
        draw.text((title_x, timeline_box_y + 14), title_text, font=header_font, fill=QUEUE_IMG_TEXT)

        axis_y = timeline_box_y + 74
        axis_x0 = timeline_box_x + 28
        axis_x1 = timeline_box_x + timeline_box_width - 28
        draw.line([(axis_x0, axis_y), (axis_x1, axis_y)], fill=QUEUE_IMG_MUTED, width=2)

        spacing = 0 if history_count <= 1 else (axis_x1 - axis_x0) / (history_count - 1)

        for idx, result in enumerate(history_slice):
            x = axis_x0 + spacing * idx
            dot_color = QUEUE_IMG_MUTED if result is None else (QUEUE_IMG_WIN if result else QUEUE_IMG_LOSE)
            dot_radius = 12 if idx < history_count - 1 else 14
            dot_bbox = [x - dot_radius, axis_y - dot_radius, x + dot_radius, axis_y + dot_radius]
            draw.ellipse(dot_bbox, fill=dot_color)

            if idx == history_count - 1:
                highlight_bbox = [x - dot_radius - 4, axis_y - dot_radius - 4, x + dot_radius + 4, axis_y + dot_radius + 4]
                draw.ellipse(highlight_bbox, outline=QUEUE_IMG_ACCENT, width=4)

            label = f"G{slice_start + idx + 1}"
            label_bbox = draw.textbbox((0, 0), label, font=body_font)
            label_x = x - (label_bbox[2] - label_bbox[0]) / 2
            draw.text((label_x, axis_y + dot_radius + 10), label, font=body_font, fill=QUEUE_IMG_TEXT)

    score_font = load_font(72, "bold")
    if team_round_wins is not None and len(team_round_wins) == num_teams:
        team_scores = team_round_wins
    else:
        # Fallback for compatibility if explicit round totals are unavailable.
        team_scores = [max((entry["stats"].get("wins", 0) for entry in team if entry["stats"]), default=0) for team in teams]
    if num_teams == 2:
        left_text = str(team_scores[0])
        right_text = str(team_scores[1])
        separator_text = " - "
        left_color = QUEUE_IMG_TEXT
        right_color = QUEUE_IMG_TEXT

        if winning_team_index is not None:
            if winning_team_index == 0:
                left_color = QUEUE_IMG_WIN
                right_color = QUEUE_IMG_LOSE
            elif winning_team_index == 1:
                left_color = QUEUE_IMG_LOSE
                right_color = QUEUE_IMG_WIN

        left_width = draw.textbbox((0, 0), left_text, font=score_font)[2]
        sep_width = draw.textbbox((0, 0), separator_text, font=score_font)[2]
        right_width = draw.textbbox((0, 0), right_text, font=score_font)[2]
        total_width = left_width + sep_width + right_width

        x_pos = (QUEUE_IMG_WIDTH - total_width) / 2
        y_pos = 88

        draw.text((x_pos, y_pos), left_text, font=score_font, fill=left_color)
        draw.text((x_pos + left_width, y_pos), separator_text, font=score_font, fill=QUEUE_IMG_TEXT)
        draw.text((x_pos + left_width + sep_width, y_pos), right_text, font=score_font, fill=right_color)
    else:
        score_text = " / ".join(str(score) for score in team_scores)
        score_bbox = draw.textbbox((0, 0), score_text, font=score_font)
        x_pos = (QUEUE_IMG_WIDTH - (score_bbox[2] - score_bbox[0])) / 2
        y_pos = 88
        draw.text((x_pos, y_pos), score_text, font=score_font, fill=QUEUE_IMG_TEXT)

    draw.text((QUEUE_IMG_PADDING, 152), "Data courtesy of NeatQueue & survev.de APIs :)", font=footer_font, fill=QUEUE_IMG_MUTED)

    panel_top = QUEUE_IMG_HEADER_HEIGHT + QUEUE_IMG_PADDING

    for team_index in range(num_teams):
        team_players = teams[team_index] if team_index < len(teams) else []
        x0 = panel_x_positions[team_index]
        is_winner = winning_team_index == team_index
        team_color = QUEUE_IMG_WIN if is_winner else (QUEUE_IMG_LOSE if winning_team_index is not None else QUEUE_IMG_MUTED)

        team_label = f"Team {team_index + 1}"
        team_label_width = draw.textbbox((0, 0), team_label, font=team_header_font)[2]
        team_label_x = x0 + (panel_width - team_label_width) / 2
        draw.text((team_label_x, panel_top), team_label, font=team_header_font, fill=team_color)

        header_y = panel_top + QUEUE_IMG_TEAM_HEADER_HEIGHT - 34
        col_widths = [columns[i + 1] - columns[i] for i in range(len(columns) - 1)] + [panel_width - columns[-1]]
        for col_idx, label in enumerate(QUEUE_IMG_COLUMN_LABELS):
            label_x = x0 + columns[col_idx]
            label_width = draw.textbbox((0, 0), label, font=header_font)[2]
            if col_idx != 0 and col_widths[col_idx] > label_width:
                label_x += (col_widths[col_idx] - label_width) / 2
            draw.text((label_x, header_y), label, font=header_font, fill=QUEUE_IMG_TEXT)

        if not team_players:
            draw.text((x0, rows_top), "No players", font=body_font, fill=QUEUE_IMG_MUTED)

        for row_index, entry in enumerate(team_players):
            row_top = rows_top + row_index * QUEUE_IMG_ROW_HEIGHT
            row_bottom = row_top + QUEUE_IMG_ROW_HEIGHT - 8
            if row_index % 2 == 0:
                draw.rectangle([x0, row_top, x0 + panel_width, row_bottom], fill=QUEUE_IMG_ROW_ALT)

            stats = entry["stats"]
            player_label = entry.get("display_name") or entry.get("username") or "Unknown"
            is_guest = entry.get("guest", False)

            if is_guest:
                # No /verify'd Discord user owns this survev.de account — the name shown is already
                # their real in-game username, just flag that they're not linked.
                player_label = f"{player_label} (unlinked)"

            games = stats["games"]
            avg_damage = stats["damage"] / games if games else 0

            row_values = [
                player_label,
                str(stats["kills"]),
                f"{stats['damage']:,}",
                f"{avg_damage:,.0f}"
            ]
            for col_idx, value in enumerate(row_values):
                fill = QUEUE_IMG_ACCENT if col_idx == 3 else QUEUE_IMG_TEXT
                font = body_font_bold if col_idx in (0, 2, 3) else body_font
                cell_x = x0 + columns[col_idx]
                if col_idx == 0:
                    max_name_width = columns[1] - columns[0] - QUEUE_IMG_NAME_PADDING_RIGHT
                    value = truncate_to_width(draw, value, font, max_name_width)
                    draw.text((cell_x, row_top + 20), value, font=font, fill=fill)
                else:
                    value_width = draw.textbbox((0, 0), value, font=font)[2]
                    centered_x = cell_x + (col_widths[col_idx] - value_width) / 2
                    draw.text((centered_x, row_top + 20), value, font=font, fill=fill)

        panel_bottom = rows_top + max(max_rows, 1) * QUEUE_IMG_ROW_HEIGHT
        draw.rectangle([x0 - 8, panel_top - 8, x0 + panel_width + 8, panel_bottom], outline=team_color, width=2)

    footer_text = "Link your survev.de account with /verify to appear in future leaderboards!"
    footer_width = draw.textbbox((0, 0), footer_text, font=footer_font)[2]
    footer_x = (QUEUE_IMG_WIDTH - footer_width) / 2
    draw.text((footer_x, height - QUEUE_IMG_PADDING + 6), footer_text, font=footer_font, fill=QUEUE_IMG_MUTED)

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


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

    balance_text = f"Balance: {balance:,} <:goldenfries:1535978920481136700>"
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


def generate_goldenfries_image(username: str, balance: int) -> BytesIO:
    header_height = QUEUE_IMG_HEADER_HEIGHT
    height = header_height + QUEUE_IMG_PADDING * 3 + 220
    image = Image.new("RGB", (QUEUE_IMG_WIDTH, height), QUEUE_IMG_BG)
    draw = ImageDraw.Draw(image)

    title_font = load_font(44, "bold")
    subtitle_font = load_font(24, "bold")
    counter_font = load_font(96, "bold")
    body_font = load_font(20)
    footer_font = load_font(15)

    _draw_header_gradient(draw, header_height)

    draw.text((QUEUE_IMG_PADDING, 26), "survev.de Golden Fries", font=title_font, fill=QUEUE_IMG_TEXT)
    draw.text((QUEUE_IMG_PADDING, 86), f"{username}'s balance", font=subtitle_font, fill=QUEUE_IMG_MUTED)

    balance_text = f"{balance:,}"
    balance_width = draw.textbbox((0, 0), balance_text, font=counter_font)[2]
    balance_x = (QUEUE_IMG_WIDTH - balance_width) / 2
    balance_y = header_height + QUEUE_IMG_PADDING
    draw.text((balance_x, balance_y), balance_text, font=counter_font, fill=QUEUE_IMG_ACCENT)

    label_text = "Golden Fries"
    label_width = draw.textbbox((0, 0), label_text, font=body_font)[2]
    label_x = (QUEUE_IMG_WIDTH - label_width) / 2
    draw.text((label_x, balance_y + 110), label_text, font=body_font, fill=QUEUE_IMG_TEXT)

    description = "idk what to put here but this is a placeholder for now"
    desc_width = draw.textbbox((0, 0), description, font=body_font)[2]
    desc_x = (QUEUE_IMG_WIDTH - desc_width) / 2
    draw.text((desc_x, balance_y + 150), description, font=body_font, fill=QUEUE_IMG_MUTED)

    footer_text = "Data courtesy of survev.de API :)"
    footer_width = draw.textbbox((0, 0), footer_text, font=footer_font)[2]
    footer_x = (QUEUE_IMG_WIDTH - footer_width) / 2
    draw.text((footer_x, height - QUEUE_IMG_PADDING + 8), footer_text, font=footer_font, fill=QUEUE_IMG_MUTED)

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


def _format_compare_duration_ms(duration_ms: int) -> str:
    total_seconds = max(0, int(duration_ms)) // 1000
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}h {minutes:02d}m {seconds:02d}s"
    if minutes > 0:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


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


def generate_compare_image(
    left_name: str,
    left_stats: dict | None,
    left_worth: int | None,
    left_queue_summary: dict | None,
    right_name: str,
    right_stats: dict | None,
    right_worth: int | None,
    right_queue_summary: dict | None,
) -> BytesIO:
    title_font = load_font(48, "bold")
    subtitle_font = load_font(24, "bold")
    name_font = load_font(38, "bold")
    status_font = load_font(20)
    label_font = load_font(22, "bold")
    value_font = load_font(36, "bold")
    footer_font = load_font(16)

    def stat_fields(stats, worth):
        if stats is None:
            return None, None, None
        games = max(1, stats["games"])
        return stats["wins"], stats["kills"] / games, stats["damage"]

    left_wins, left_kd, left_damage = stat_fields(left_stats, left_worth)
    right_wins, right_kd, right_damage = stat_fields(right_stats, right_worth)

    def queue_fields(summary):
        summary = summary or {}
        matches = summary.get("matches", 0)
        playtime_ms = summary.get("total_duration_ms") if matches else None
        return playtime_ms, summary.get("all_time_avg_damage"), summary.get("monthly_avg_damage")

    left_playtime_ms, left_all_avg, left_monthly_avg = queue_fields(left_queue_summary)
    right_playtime_ms, right_all_avg, right_monthly_avg = queue_fields(right_queue_summary)

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
        (
            "Inventory Worth",
            fmt(left_worth, suffix=" \U0001f4b0"),
            fmt(right_worth, suffix=" \U0001f4b0"),
            left_worth,
            right_worth,
        ),
        (
            "Play Time",
            _format_compare_duration_ms(left_playtime_ms) if left_playtime_ms is not None else "-",
            _format_compare_duration_ms(right_playtime_ms) if right_playtime_ms is not None else "-",
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
            "Monthly Avg Damage",
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

    draw.text((QUEUE_IMG_PADDING, 28), "survev.de Compare (wip broken asf)", font=title_font, fill=QUEUE_IMG_TEXT)
    draw.text((QUEUE_IMG_PADDING, 92), "Stats side-by-side, best in each row highlighted", font=subtitle_font, fill=QUEUE_IMG_MUTED)

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


def generate_leaderboard_fries_image(leaderboard_rows: list[dict]) -> BytesIO:
    header_height = QUEUE_IMG_HEADER_HEIGHT
    row_height = QUEUE_IMG_ROW_HEIGHT
    table_top = header_height + QUEUE_IMG_PADDING
    height = table_top + max(len(leaderboard_rows), 1) * row_height + QUEUE_IMG_PADDING
    image = Image.new("RGB", (QUEUE_IMG_WIDTH, height), QUEUE_IMG_BG)
    draw = ImageDraw.Draw(image)

    title_font = load_font(46, "bold")
    subtitle_font = load_font(24, "bold")
    header_font = load_font(18, "bold")
    body_font = load_font(20)
    footer_font = load_font(15)

    _draw_header_gradient(draw, header_height)

    draw.text((QUEUE_IMG_PADDING, 26), "Server Golden Fries Leaderboard", font=title_font, fill=QUEUE_IMG_TEXT)
    draw.text((QUEUE_IMG_PADDING, 84), "Top verified users by their survev.de Golden Fries balance.", font=subtitle_font, fill=QUEUE_IMG_MUTED)

    table_width = QUEUE_IMG_WIDTH - QUEUE_IMG_PADDING * 2
    table_x = QUEUE_IMG_PADDING
    columns = [
        table_x,
        table_x + round(table_width * 0.10),
        table_x + round(table_width * 0.55),
        table_x + round(table_width * 0.80)
    ]
    col_widths = [columns[i + 1] - columns[i] for i in range(len(columns) - 1)] + [table_x + table_width - columns[-1]]

    header_y = table_top
    labels = ["#", "Player", "Balance", "Status"]
    for col_idx, label in enumerate(labels):
        label_x = columns[col_idx]
        label_width = draw.textbbox((0, 0), label, font=header_font)[2]
        if col_idx == 0:
            draw.text((label_x, header_y), label, font=header_font, fill=QUEUE_IMG_TEXT)
        elif col_idx == 1:
            draw.text((label_x, header_y), label, font=header_font, fill=QUEUE_IMG_TEXT)
        else:
            draw.text((label_x + (col_widths[col_idx] - label_width) / 2, header_y), label, font=header_font, fill=QUEUE_IMG_TEXT)

    row_y = header_y + row_height
    for row_index, entry in enumerate(leaderboard_rows):
        if row_index % 2 == 0:
            draw.rectangle([table_x, row_y, table_x + table_width, row_y + row_height], fill=QUEUE_IMG_ROW_ALT)

        balance = entry["balance"]
        display_name = entry.get("display_name") or f"Player {entry['discord_id']}"
        row_values = [
            str(entry["rank"]),
            display_name,
            f"{balance:,}",
            "Verified"
        ]

        for col_idx, value in enumerate(row_values):
            font = body_font
            fill = QUEUE_IMG_ACCENT if col_idx == 2 else QUEUE_IMG_TEXT
            cell_x = columns[col_idx]
            if col_idx == 0:
                draw.text((cell_x, row_y + 18), value, font=font, fill=fill)
            elif col_idx == 1:
                draw.text((cell_x, row_y + 18), value, font=font, fill=fill)
            else:
                value_width = draw.textbbox((0, 0), value, font=font)[2]
                centered_x = cell_x + (col_widths[col_idx] - value_width) / 2
                draw.text((centered_x, row_y + 18), value, font=font, fill=fill)

        row_y += row_height

    footer_text = "Data courtesy of survev.de API :)"
    footer_width = draw.textbbox((0, 0), footer_text, font=footer_font)[2]
    footer_x = (QUEUE_IMG_WIDTH - footer_width) / 2
    draw.text((footer_x, height - QUEUE_IMG_PADDING + 8), footer_text, font=footer_font, fill=QUEUE_IMG_MUTED)

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


def generate_leaderboard_image(period: str, days: int, leaderboard_rows: list[dict]) -> BytesIO:
    header_height = QUEUE_IMG_HEADER_HEIGHT
    row_height = QUEUE_IMG_ROW_HEIGHT
    table_top = header_height + QUEUE_IMG_PADDING
    height = table_top + max(len(leaderboard_rows), 1) * row_height + QUEUE_IMG_PADDING
    image = Image.new("RGB", (QUEUE_IMG_WIDTH, height), QUEUE_IMG_BG)
    draw = ImageDraw.Draw(image)

    title_font = load_font(46, "bold")
    subtitle_font = load_font(24, "bold")
    header_font = load_font(18, "bold")
    body_font = load_font(20)
    footer_font = load_font(15)

    _draw_header_gradient(draw, header_height)

    draw.text((QUEUE_IMG_PADDING, 26), f"Server {period} Leaderboard", font=title_font, fill=QUEUE_IMG_TEXT)
    draw.text((QUEUE_IMG_PADDING, 84), f"Performance over the past {days} days (Sorted by Kills)", font=subtitle_font, fill=QUEUE_IMG_MUTED)

    table_width = QUEUE_IMG_WIDTH - QUEUE_IMG_PADDING * 2
    table_x = QUEUE_IMG_PADDING
    columns = [
        table_x,
        table_x + round(table_width * 0.10),
        table_x + round(table_width * 0.42),
        table_x + round(table_width * 0.60),
        table_x + round(table_width * 0.78)
    ]
    col_widths = [columns[i + 1] - columns[i] for i in range(len(columns) - 1)] + [table_x + table_width - columns[-1]]

    header_y = table_top
    labels = ["#", "Player", "Kills", "Wins", "Damage"]
    for col_idx, label in enumerate(labels):
        label_x = columns[col_idx]
        label_width = draw.textbbox((0, 0), label, font=header_font)[2]
        if col_idx == 0:
            draw.text((label_x, header_y), label, font=header_font, fill=QUEUE_IMG_TEXT)
        else:
            draw.text((label_x + (col_widths[col_idx] - label_width) / 2, header_y), label, font=header_font, fill=QUEUE_IMG_TEXT)

    row_y = header_y + row_height
    for row_index, entry in enumerate(leaderboard_rows):
        if row_index % 2 == 0:
            draw.rectangle([table_x, row_y, table_x + table_width, row_y + row_height], fill=QUEUE_IMG_ROW_ALT)

        stats = entry["stats"]
        display_name = entry.get("display_name") or f"Player {entry['discord_id']}"
        row_values = [
            str(entry["rank"]),
            display_name,
            str(stats["kills"]),
            str(stats["wins"]),
            f"{stats['damage']:,}"
        ]

        for col_idx, value in enumerate(row_values):
            font = body_font
            fill = QUEUE_IMG_TEXT if col_idx != 2 else QUEUE_IMG_ACCENT
            cell_x = columns[col_idx]
            if col_idx == 0:
                draw.text((cell_x, row_y + 14), value, font=font, fill=fill)
            elif col_idx == 1:
                draw.text((cell_x, row_y + 14), value, font=font, fill=fill)
            else:
                value_width = draw.textbbox((0, 0), value, font=font)[2]
                centered_x = cell_x + (col_widths[col_idx] - value_width) / 2
                draw.text((centered_x, row_y + 14), value, font=font, fill=fill)

        row_y += row_height

    footer_text = "Data courtesy of survev.de API :)"
    footer_width = draw.textbbox((0, 0), footer_text, font=footer_font)[2]
    footer_x = (QUEUE_IMG_WIDTH - footer_width) / 2
    draw.text((footer_x, height - QUEUE_IMG_PADDING + 8), footer_text, font=footer_font, fill=QUEUE_IMG_MUTED)

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer
