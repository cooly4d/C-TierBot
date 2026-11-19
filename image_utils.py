"""Font loading and every PIL-based image generator used by the bot's slash commands.

Kept self-contained (no db/discord-client/network imports) so images can be
generated and unit-tested independently of the rest of the bot.
"""
import functools
import json
import math
import os
import re
from datetime import datetime, timezone
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

import design_tokens as tokens

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


# Item icons and names exported from survev.de by tools/export_item_icons.mts.
ITEM_ICON_DIR = os.path.join(_CURRENT_DIR, "assets", "items")
_ITEM_TYPE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")  # "_..." files are the bot's own art


@functools.lru_cache(maxsize=1)
def load_item_manifest() -> dict[str, dict]:
    """{type: {"kind", "name", "rarity"}} for every exported item, or {} when the icons aren't installed."""
    try:
        with open(os.path.join(ITEM_ICON_DIR, "manifest.json"), encoding="utf-8") as file:
            items = json.load(file).get("items")
    except (OSError, ValueError, AttributeError):
        return {}
    return items if isinstance(items, dict) else {}


@functools.lru_cache(maxsize=512)
def load_item_icon(item_type: str | None) -> Image.Image | None:
    """The item's square RGBA icon, or None for unknown types. The type comes from the survev.de API, so it is
    checked before it becomes part of a file path."""
    if not isinstance(item_type, str) or not _ITEM_TYPE_RE.match(item_type):
        return None
    try:
        with Image.open(os.path.join(ITEM_ICON_DIR, f"{item_type}.png")) as icon:
            return icon.convert("RGBA")
    except (OSError, ValueError):
        return None


def paste_item_icon(image: Image.Image, item_type: str | None, x: int, y: int, size: int) -> bool:
    """Draws the item's icon as a size x size square at (x, y). Returns False when there's no icon."""
    icon = _sized_item_icon(item_type, size)
    if icon is None:
        return False
    image.paste(icon, (x, y), icon)
    return True


@functools.lru_cache(maxsize=1024)
def _sized_item_icon(item_type: str | None, size: int) -> Image.Image | None:
    icon = load_item_icon(item_type)
    if icon is None or icon.size == (size, size):
        return icon
    return icon.resize((size, size), Image.LANCZOS)


@functools.lru_cache(maxsize=1)
def _load_fries_icon() -> Image.Image | None:
    try:
        with Image.open(os.path.join(ITEM_ICON_DIR, "_golden_fries.png")) as icon:
            return icon.convert("RGBA")
    except (OSError, ValueError):
        return None


def draw_fries_amount(image: Image.Image, draw: ImageDraw.ImageDraw, xy: tuple[int, int], amount: int, font, fill,
                      prefix: str = "", align: str = "left") -> None:
    """Draws "{prefix}{amount:,}" followed by the Golden Fries icon (or the word "fries" without the icon).
    With align="right", xy[0] is where the icon (or text) ends instead of where the text starts."""
    text = f"{prefix}{amount:,}"
    icon = _load_fries_icon()
    if icon is None:
        text = f"{text} fries"
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    size = max(12, round((bottom - top) * 1.25))
    x, y = xy
    if align == "right":
        x -= (right - left) + (size + 6 if icon is not None else 0)
    draw.text((x, y), text, font=font, fill=fill)
    if icon is None:
        return
    left, top, right, bottom = draw.textbbox((x, y), text, font=font)
    icon = icon.resize((size, size), Image.LANCZOS)
    image.paste(icon, (int(right + 6), int((top + bottom) / 2 - size / 2)), icon)


def prettify_shop_item_type(item_type: str | None) -> str:
    if not item_type:
        return "Unknown"
    known = load_item_manifest().get(item_type)
    if isinstance(known, dict) and known.get("name"):
        return str(known["name"])
    return item_type.replace("_", " ").title()


def shop_item_rarity(item_type: str | None) -> int | None:
    known = load_item_manifest().get(item_type or "")
    rarity = known.get("rarity") if isinstance(known, dict) else None
    return rarity if isinstance(rarity, int) else None


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
            group = {"name": name, "rarity": rarity, "count": 0, "value": item.get("value"), "type": item.get("type")}
            grouped[key] = group
        group["count"] += 1
        if group["value"] is None:
            group["value"] = item.get("value")
        if not group["type"]:
            group["type"] = item.get("type")
    return list(grouped.values())


INVENTORY_RARITY_FILTERS: dict[str, str] = {
    "all": "All rarities",
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

# Older names for the colours in design_tokens, kept because the renderers and tests use them.
QUEUE_IMG_BG = tokens.CANVAS
QUEUE_IMG_HEADER_BG = tokens.HEADER_TOP
QUEUE_IMG_HEADER_GRADIENT_END = tokens.HEADER_BOTTOM
QUEUE_IMG_ROW_ALT = tokens.PANEL_RAISED
QUEUE_IMG_TEXT = tokens.TEXT
QUEUE_IMG_MUTED = tokens.TEXT_MUTED
QUEUE_IMG_ACCENT = tokens.ACCENT
QUEUE_IMG_WIN = tokens.WIN
QUEUE_IMG_LOSE = tokens.LOSE

QUEUE_IMG_CARD_BG = tokens.PANEL
QUEUE_IMG_CARD_RADIUS = 18
QUEUE_IMG_CARD_INNER_PAD_X = 20
QUEUE_IMG_CARD_BOTTOM_PAD = 16
QUEUE_IMG_TEAM_BANNER_HEIGHT = 58
QUEUE_IMG_COL_HEADER_HEIGHT = 48
QUEUE_IMG_WIN_BANNER_BG = tokens.WIN_BANNER
QUEUE_IMG_LOSE_BANNER_BG = tokens.LOSE_BANNER
QUEUE_IMG_NEUTRAL_BANNER_BG = tokens.NEUTRAL_BANNER
QUEUE_IMG_ROW_DIVIDER = tokens.DIVIDER

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
QUEUE_IMG_HISTORY_COLUMN_RATIOS = [0.0, 0.22, 0.48, 0.68, 0.84]
QUEUE_IMG_HISTORY_COLUMN_LABELS = ["Queue", "Result", "Kills", "Avg Dmg", "Impact"]
HISTORY_METRICS = ("damage", "impact", "kills")


def normalize_history_metric(metric: str | None) -> str:
    key = (metric or "damage").strip().lower()
    return key if key in HISTORY_METRICS else "damage"
QUEUE_IMG_NAME_PADDING_RIGHT = 16  # gap kept clear before the Kills column starts
QUEUE_IMG_UNLINKED_ICON_SIZE = 30
QUEUE_IMG_UNLINKED_ICON_GAP = 10

# Item card outlines and rarity labels, keyed by rarity int (0 Stock ... 5 Mythic, see INVENTORY_RARITY_FILTERS).
RARITY_COLORS: dict[int, tuple[int, int, int]] = tokens.RARITY


def rarity_color(rarity: int) -> tuple[int, int, int]:
    return RARITY_COLORS.get(rarity, QUEUE_IMG_MUTED)


INVENTORY_GRID_COLUMNS = 4
INVENTORY_CARD_HEIGHT = 196
INVENTORY_CARD_GAP = 24
INVENTORY_ITEMS_PER_PAGE = 12
INVENTORY_ICON_SIZE = 116
INVENTORY_HEADER_HEIGHT = 140
INVENTORY_FOOTER_HEIGHT = 64

SHOP_GRID_COLUMNS = 2
SHOP_CARD_HEIGHT = 206
SHOP_CARD_GAP = 24
SHOP_ITEMS_PER_PAGE = 6
SHOP_ICON_SIZE = 104
SHOP_ICON_CELL_WIDTH = 122
# Offer slot ids from the survev.de market API (shared/types/user.ts ShopOffer.slot). survev.de also returns two
# Premium daily offers, in slots 4 and 5, for every account.
SHOP_SLOT_LABELS = {0: "Daily pick", 1: "Daily bundle", 2: "Weekly pick", 3: "Weekly bundle", 4: "Premium daily",
                    5: "Premium daily"}
# Which slots each /market button shows ("all" shows every offer). Used by the card and by the page count.
MARKET_MODE_SLOTS = {"daily": (0, 1, 4, 5), "weekly": (2, 3)}


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


@functools.lru_cache(maxsize=16)
def _build_unlinked_icon(
    size_px: int,
    link_color: tuple[int, int, int] = QUEUE_IMG_MUTED,
    slash_color: tuple[int, int, int] = QUEUE_IMG_LOSE,
) -> Image.Image:
    """Renders an anti-aliased vector 'broken/slashed chain link' RGBA icon of `size_px` x `size_px`.
    Built once per size and shared between renders, so paste it but never draw on it."""
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


def partial_games_label(entry: dict, series_games: int | None) -> str | None:
    """'1/7' for a player who played only part of the series (a substitute or a leaver), else None."""
    games = (entry.get("stats") or {}).get("games")
    if not series_games or games is None or games >= series_games:
        return None
    return f"{games}/{series_games}"


def winners_from_history(match_history: list[bool | None] | None, series_winner_index: int | None,
                         num_teams: int) -> list[int | None]:
    """Each game's winning team index, rebuilt from the older `match_history` (True = won by the series winner).
    Used when a caller has no per-game winners; with no series winner, or more than two teams, a loss can't be
    attributed, so that game's winner is None."""
    winners: list[int | None] = []
    for result in match_history or []:
        if result is None or series_winner_index is None:
            winners.append(None)
        elif result:
            winners.append(series_winner_index)
        else:
            winners.append(1 - series_winner_index if num_teams == 2 else None)
    return winners


def _player_key(entry: dict) -> str:
    """Who a result row belongs to, so the rows of a player who switched sides mid-series count as one person."""
    if entry.get("discord_id"):
        return f"discord:{entry['discord_id']}"
    if entry.get("slug"):
        return f"slug:{entry['slug']}"
    return f"name:{(entry.get('username') or entry.get('display_name') or '').lower()}:{id(entry)}"


def pick_mvp(teams: list[list[dict]], series_games: int | None) -> tuple[int, int] | None:
    """(team, row) of the series MVP: the best impact among players who played every game of the series. A player
    on both sides (one row per side) counts once, with their games added up and impact averaged over the scored
    games. None when nobody qualifies or the best score, as the card shows it, is shared."""
    if not series_games:
        return None
    players: dict[str, dict] = {}
    for team_index, team in enumerate(teams):
        for row_index, entry in enumerate(team):
            stats = entry.get("stats") or {}
            games = stats.get("games") or 0
            player = players.setdefault(_player_key(entry), {"games": 0, "points": 0.0, "scored": 0, "row": None})
            player["games"] += games
            if stats.get("impact") is not None and games:
                player["points"] += stats["impact"] * games
                player["scored"] += games
            if player["row"] is None or games > player["row"][0]:
                player["row"] = (games, team_index, row_index)  # the tag goes on their main row
    scored = [(int(player["points"] / player["scored"] + 0.5), player["row"][1:]) for player in players.values()
              if player["scored"] and player["games"] >= series_games]
    if not scored:
        return None
    best = max(score for score, _ in scored)
    leaders = [row for score, row in scored if score == best]
    return leaders[0] if len(leaders) == 1 else None


def _draw_match_timeline(draw: ImageDraw.ImageDraw, winners: list[int | None], num_teams: int,
                         highlight_index: int | None, team_colors: list[tuple[int, int, int]], S: int) -> None:
    """The MATCH TIMELINE box: one labelled row (lane) per team, and each game's dot in the lane of the team that
    won it, coloured like that team's panel. A game with no known winner is a hollow ring between the lanes.
    Position and the T1/T2 labels carry the result, so it reads without colour and never moves once played."""
    box_width, box_height = 560, 156
    box_x, box_y = QUEUE_IMG_WIDTH - QUEUE_IMG_PADDING - box_width, 27
    draw.rounded_rectangle([box_x * S, box_y * S, (box_x + box_width) * S, (box_y + box_height) * S],
                           radius=20 * S, fill=tokens.TIMELINE_FILL, outline=QUEUE_IMG_MUTED, width=2 * S)
    draw.text(((box_x + box_width / 2) * S, (box_y + 12) * S), "MATCH TIMELINE",
              font=load_font(tokens.QUEUE_TIMELINE_LABEL * S, "bold"), fill=QUEUE_IMG_TEXT, anchor="mt")

    lanes = max(2, num_teams)
    if lanes == 2:
        lane_y = [box_y + 64, box_y + 100]
    else:
        lane_y = [box_y + 56 + 52 * index / (lanes - 1) for index in range(lanes)]
    middle_y = (lane_y[0] + lane_y[-1]) / 2
    line_x0, line_x1 = box_x + 70, box_x + box_width - 30
    lane_font = load_font(tokens.QUEUE_TIMELINE_LABEL * S, "bold")
    for lane in range(lanes):
        color = team_colors[lane] if lane < len(team_colors) else QUEUE_IMG_MUTED
        draw.text(((box_x + 22) * S, lane_y[lane] * S), f"T{lane + 1}", font=lane_font, fill=color, anchor="lm")
        draw.line([(line_x0 * S, lane_y[lane] * S), (line_x1 * S, lane_y[lane] * S)], fill=tokens.TIMELINE_LANE,
                  width=2 * S)

    count = len(winners)
    label_font = load_font(tokens.QUEUE_TIMELINE_LABEL * S)
    first_x, last_x = line_x0, line_x1
    spacing = (last_x - first_x) / (count - 1) if count > 1 else 0
    radius = 11
    for index, winner in enumerate(winners):
        x = first_x + spacing * index if count > 1 else (first_x + last_x) / 2
        if winner is None or not 0 <= winner < lanes:
            y = middle_y
            draw.ellipse([(x - radius) * S, (y - radius) * S, (x + radius) * S, (y + radius) * S],
                         outline=QUEUE_IMG_MUTED, width=3 * S)
        else:
            y = lane_y[winner]
            color = team_colors[winner] if winner < len(team_colors) else QUEUE_IMG_MUTED
            draw.ellipse([(x - radius) * S, (y - radius) * S, (x + radius) * S, (y + radius) * S], fill=color)
        if index == highlight_index:
            ring = radius + 6
            draw.ellipse([(x - ring) * S, (y - ring) * S, (x + ring) * S, (y + ring) * S],
                         outline=QUEUE_IMG_ACCENT, width=4 * S)
        draw.text((x * S, (box_y + 128) * S), f"G{index + 1}", font=label_font,
                  fill=QUEUE_IMG_TEXT if index == highlight_index else QUEUE_IMG_MUTED, anchor="mt")


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
    game_winners: list[int | None] | None,
    highlight_index: int | None,
    status_text: str = "",
    series_games: int | None = None,
    mvp: tuple[int, int] | None = None,
) -> BytesIO:
    """Draws one queue card: a header (title, result line, score, game timeline) above one table panel per team.
    The series result image and the single-game card share this and differ only in what they pass in.
    `game_winners` holds each game's winning team index (None when unknown) for the timeline.
    With `series_games`, players who played fewer games than that get an orange 'played/total' label.
    `mvp` is the (team, row) of the player who gets the MVP tag."""
    S = 2  # 2x supersampling factor for smooth anti-aliased circles, borders, and text
    num_teams = max(len(teams), 1)
    max_rows = max((len(team) for team in teams), default=0)

    panel_width = (QUEUE_IMG_WIDTH - QUEUE_IMG_PADDING * (num_teams + 1)) // num_teams
    panel_x_positions = [QUEUE_IMG_PADDING + i * (panel_width + QUEUE_IMG_PADDING) for i in range(num_teams)]
    inner_width = panel_width - 2 * QUEUE_IMG_CARD_INNER_PAD_X
    columns = [round(ratio * inner_width) for ratio in ratios]

    # The footer explains the unlinked icon and the orange played/total label, so it only appears when the card
    # has one of them; the legend quotes the first real played/total label on this card.
    any_guest = any(entry.get("guest") for team in teams for entry in team)
    example = next((label for team in teams for entry in team if (label := partial_games_label(entry, series_games))),
                   None)
    has_footer = any_guest or example is not None

    panel_top = QUEUE_IMG_HEADER_HEIGHT + QUEUE_IMG_PADDING
    col_header_top = panel_top + QUEUE_IMG_TEAM_BANNER_HEIGHT
    rows_top = col_header_top + QUEUE_IMG_COL_HEADER_HEIGHT
    panel_bottom = rows_top + max(max_rows, 1) * QUEUE_IMG_ROW_HEIGHT + QUEUE_IMG_CARD_BOTTOM_PAD
    height = panel_bottom + QUEUE_IMG_PADDING + (22 if has_footer else 0)

    def team_style(team_index: int) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
        """(text/border colour, banner fill) of a team's panel on this card."""
        if panel_winner_index == team_index:
            return QUEUE_IMG_WIN, QUEUE_IMG_WIN_BANNER_BG
        if panel_winner_index is not None:
            return QUEUE_IMG_LOSE, QUEUE_IMG_LOSE_BANNER_BG
        return QUEUE_IMG_MUTED, QUEUE_IMG_NEUTRAL_BANNER_BG

    team_colors = [team_style(index)[0] for index in range(num_teams)]

    image = Image.new("RGB", (QUEUE_IMG_WIDTH * S, height * S), QUEUE_IMG_BG)
    draw = ImageDraw.Draw(image)

    title_font = load_font(tokens.QUEUE_TITLE * S, "bold")
    subtitle_font = load_font(tokens.QUEUE_RESULT * S, "bold")
    team_header_font = load_font(tokens.QUEUE_TEAM * S, "bold")
    header_font = load_font(tokens.QUEUE_LABEL * S, "bold")
    body_font = load_font(tokens.QUEUE_BODY * S)
    body_font_bold = load_font(tokens.QUEUE_BODY * S, "bold")
    duration_font = load_font(tokens.QUEUE_META * S, "bold")
    mvp_font = load_font(tokens.QUEUE_MVP * S, "bold")

    # Top banner with subtle gradient
    _draw_header_gradient(draw, QUEUE_IMG_HEADER_HEIGHT * S, QUEUE_IMG_WIDTH * S)

    draw.text((QUEUE_IMG_PADDING * S, 28 * S), title, font=title_font, fill=QUEUE_IMG_TEXT)
    draw.text((QUEUE_IMG_PADDING * S, 96 * S), subtitle, font=subtitle_font, fill=subtitle_color)

    if status_text:
        draw.text((QUEUE_IMG_WIDTH * S / 2, 48 * S), status_text,
                  font=header_font, fill=QUEUE_IMG_ACCENT, anchor="mt")

    if game_winners:
        _draw_match_timeline(draw, game_winners, num_teams, highlight_index, team_colors, S)

    score_font = load_font(tokens.QUEUE_SCORE * S, "bold")
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
        team_color, banner_bg = team_style(team_index)

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
            is_mvp = mvp == (team_index, row_index)

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
                    games_label = partial_games_label(entry, series_games)
                    if games_label:
                        games_bbox = draw.textbbox((0, 0), games_label, font=header_font)
                        games_reserve = games_bbox[2] - games_bbox[0] + QUEUE_IMG_UNLINKED_ICON_GAP * S
                        max_name_width = max(0, max_name_width - games_reserve)
                    mvp_width = draw.textlength("MVP", font=mvp_font) + 22 * S
                    if is_mvp:
                        max_name_width = max(0, max_name_width - mvp_width - 12 * S)
                    value = truncate_to_width(draw, value, font, max_name_width)
                    draw.text((cell_x, cell_y), value, font=font, fill=fill)
                    name_bbox = draw.textbbox((0, 0), value, font=font)
                    next_x = cell_x + name_bbox[2] - name_bbox[0]
                    if is_guest:
                        icon_x = round(next_x + QUEUE_IMG_UNLINKED_ICON_GAP * S)
                        icon_y = round((row_box_top + row_box_bottom - unlinked_row_icon.height) / 2)
                        image.paste(unlinked_row_icon, (icon_x, icon_y), unlinked_row_icon)
                        next_x = icon_x + unlinked_row_icon.width
                    if games_label:
                        games_y = _vcenter_y(draw, header_font, row_box_top, row_box_bottom)
                        draw.text((next_x + QUEUE_IMG_UNLINKED_ICON_GAP * S, games_y), games_label,
                                  font=header_font, fill=QUEUE_IMG_ACCENT)
                        next_x += QUEUE_IMG_UNLINKED_ICON_GAP * S + draw.textlength(games_label, font=header_font)
                    if is_mvp:
                        pill_x = next_x + 12 * S
                        pill_mid = (row_box_top + row_box_bottom) / 2
                        draw.rounded_rectangle([pill_x, pill_mid - 17 * S, pill_x + mvp_width, pill_mid + 17 * S],
                                               radius=9 * S, fill=QUEUE_IMG_ACCENT)
                        draw.text((pill_x + mvp_width / 2, pill_mid), "MVP", font=mvp_font, fill=QUEUE_IMG_BG,
                                  anchor="mm")
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

    if has_footer:
        footer_font = load_font(tokens.QUEUE_FOOTER * S)
        footer_icon = _build_unlinked_icon(24 * S)
        parts: list[tuple[str, tuple[int, int, int]]] = []  # (text, colour); "" stands for the icon
        if any_guest:
            parts += [("", QUEUE_IMG_MUTED), ("Unlinked: /verify to join the leaderboards", QUEUE_IMG_MUTED)]
        if example:
            played, total = example.split("/")
            if any_guest:
                parts.append(("   ·   ", QUEUE_IMG_MUTED))
            parts.append((f"{example} = played {played} of {total} games", QUEUE_IMG_ACCENT))
        icon_gap = 10 * S
        widths = [footer_icon.width + icon_gap if not text else draw.textlength(text, font=footer_font)
                  for text, _ in parts]
        footer_x = (QUEUE_IMG_WIDTH * S - sum(widths)) / 2
        footer_y = _vcenter_y(draw, footer_font, panel_bottom * S, height * S)
        for (text, color), width in zip(parts, widths):
            if text:
                draw.text((footer_x, footer_y), text, font=footer_font, fill=color)
            else:
                icon_y = round((panel_bottom * S + height * S - footer_icon.height) / 2)
                image.paste(footer_icon, (round(footer_x), icon_y), footer_icon)
            footer_x += width

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
    *,
    is_final: bool = True,
    game_winners: list[int | None] | None = None,
) -> BytesIO:
    """The series result card. `game_winners` is each game's winning team index for the timeline; without it the
    winners are rebuilt from `match_history`. Finished series with a winner tag the MVP (see `pick_mvp`)."""
    num_teams = max(len(teams), 1)
    if winning_team_index is not None and 0 <= winning_team_index < num_teams:
        result_word = "Wins" if is_final else "leads"
        subtitle, subtitle_color = f"Team {winning_team_index + 1} {result_word}", QUEUE_IMG_WIN
    else:
        subtitle, subtitle_color = "Result unknown" if is_final else "No team leading", QUEUE_IMG_MUTED

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

    series_games = len(match_history) if match_history else None
    if game_winners is None:
        game_winners = winners_from_history(match_history, winning_team_index, num_teams)
    has_winner = winning_team_index is not None and 0 <= winning_team_index < num_teams
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
        game_winners=game_winners,
        highlight_index=len(game_winners) - 1 if game_winners else None,
        status_text="" if is_final else "IN PROGRESS",
        series_games=series_games,
        # The MVP is picked by impact, so only cards that show the Impact column get one.
        mvp=pick_mvp(teams, series_games) if is_final and has_winner and labels is QUEUE_IMG_COLUMN_LABELS_IMPACT
        else None,
    )


def generate_queue_game_image(
    match_id: str,
    game_number: int,
    total_games: int,
    teams: list[list[dict]],
    winner_index: int | None,
    match_history: list[bool | None] | None,
    series_score: list[int],
    game_winners: list[int | None] | None = None,
) -> BytesIO:
    """Card for one game of a queue's series: that game's winner and per-player kills, damage and impact, with the
    series score through this game and the timeline ringing this game. `teams` holds the same entries as the result
    image, with `stats` set to this single game's numbers. `game_winners` places the series' games on the timeline;
    without it the timeline shows how many games there were but no winners."""
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

    if game_winners is None:
        game_winners = [None] * len(match_history or [])
    in_timeline = 0 <= game_number - 1 < len(game_winners)
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
        game_winners=game_winners,
        highlight_index=game_number - 1 if in_timeline else None,
    )


def save_palette_png(image: Image.Image) -> BytesIO:
    """PNG with a 256-colour palette: about a quarter of the size of a full-colour PNG for these flat-colour cards,
    so Discord uploads and shows a new page faster."""
    buffer = BytesIO()
    image.quantize(256, method=Image.Quantize.FASTOCTREE).save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


def _draw_compact_header(image: Image.Image, draw: ImageDraw.ImageDraw, title: str, subtitle: str,
                         amount_label: str | None = None, amount: int | None = None) -> None:
    """The /inventory, /market and /compare header: title and muted subtitle on the left and, when given, a labelled
    Golden Fries amount on the right (the inventory's total value or the player's balance)."""
    draw.rectangle([0, 0, QUEUE_IMG_WIDTH, INVENTORY_HEADER_HEIGHT], fill=QUEUE_IMG_HEADER_BG)
    draw.line([(0, INVENTORY_HEADER_HEIGHT), (QUEUE_IMG_WIDTH, INVENTORY_HEADER_HEIGHT)],
              fill=QUEUE_IMG_ROW_DIVIDER, width=2)
    right_edge = QUEUE_IMG_WIDTH - QUEUE_IMG_PADDING
    amount_width = 0
    if amount_label is not None and amount is not None:
        label_font = load_font(tokens.CARD_AMOUNT_LABEL, "bold")
        amount_font = load_font(tokens.CARD_AMOUNT, "bold")
        label_width = draw.textbbox((0, 0), amount_label, font=label_font)[2]
        draw.text((right_edge - label_width, 22), amount_label, font=label_font, fill=QUEUE_IMG_MUTED)
        draw_fries_amount(image, draw, (right_edge, 52), amount, amount_font, QUEUE_IMG_ACCENT, align="right")
        amount_width = max(label_width, draw.textbbox((0, 0), f"{amount:,}", font=amount_font)[2] + 70)

    text_width = right_edge - amount_width - QUEUE_IMG_PADDING - 32
    title_font = load_font(tokens.CARD_TITLE, "bold")
    subtitle_font = load_font(tokens.CARD_SUBTITLE, "bold")
    draw.text((QUEUE_IMG_PADDING, 18), truncate_to_width(draw, title, title_font, text_width),
              font=title_font, fill=QUEUE_IMG_TEXT)
    draw.text((QUEUE_IMG_PADDING, 88), truncate_to_width(draw, subtitle, subtitle_font, text_width),
              font=subtitle_font, fill=QUEUE_IMG_MUTED)


def draw_credit_footer(draw: ImageDraw.ImageDraw, height: int, *, text: str = tokens.CREDIT_LINE,
                       size: int = tokens.FOOTER, scale: int = 1, area_top: float | None = None) -> None:
    """The credit line centred at the bottom of a card. With `area_top`, it is centred vertically between that and
    the bottom edge; otherwise its top sits 36px above the bottom. `scale` is the card's supersampling factor."""
    font = load_font(size * scale)
    width = draw.textbbox((0, 0), text, font=font)[2]
    if area_top is None:
        y = height * scale - 36 * scale
    else:
        y = _vcenter_y(draw, font, area_top * scale, height * scale)
    draw.text(((QUEUE_IMG_WIDTH * scale - width) / 2, y), text, font=font, fill=QUEUE_IMG_MUTED)


def _draw_empty_state(draw: ImageDraw.ImageDraw, text: str, box_top: float, box_height: float) -> None:
    """A muted one-line message centred in a box the width of the card, for a grid with nothing to show."""
    font = load_font(30, "bold")
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    draw.text(((QUEUE_IMG_WIDTH - (right - left)) / 2, box_top + (box_height - (bottom - top)) / 2), text,
              font=font, fill=QUEUE_IMG_MUTED)


def generate_inventory_image(username: str, items: list[dict], page: int = 0, rarity_filter: str = "all") -> BytesIO:
    rarity_key = normalize_inventory_rarity_filter(rarity_filter)

    all_grouped = group_inventory_items(items)
    grouped_items = all_grouped
    if rarity_key != "all":
        rarity_value = int(rarity_key)
        grouped_items = [entry for entry in grouped_items if int(entry.get("rarity", -1)) == rarity_value]
    grouped_items = sorted(grouped_items, key=lambda entry: (entry["rarity"], entry["value"] or 0), reverse=True)

    total_pages = max(1, -(-len(grouped_items) // INVENTORY_ITEMS_PER_PAGE))
    page = max(0, min(page, total_pages - 1))
    shown_items = grouped_items[page * INVENTORY_ITEMS_PER_PAGE:(page + 1) * INVENTORY_ITEMS_PER_PAGE]

    grid_top = INVENTORY_HEADER_HEIGHT + 32
    grid_width = QUEUE_IMG_WIDTH - QUEUE_IMG_PADDING * 2
    card_width = (grid_width - INVENTORY_CARD_GAP * (INVENTORY_GRID_COLUMNS - 1)) // INVENTORY_GRID_COLUMNS
    rows = max(1, -(-len(shown_items) // INVENTORY_GRID_COLUMNS))
    height = grid_top + rows * INVENTORY_CARD_HEIGHT + (rows - 1) * INVENTORY_CARD_GAP + INVENTORY_FOOTER_HEIGHT

    image = Image.new("RGB", (QUEUE_IMG_WIDTH, height), QUEUE_IMG_BG)
    draw = ImageDraw.Draw(image)

    name_font = load_font(tokens.ITEM_NAME, "bold")
    rarity_font = load_font(tokens.ITEM_RARITY, "bold")
    value_font = load_font(tokens.ITEM_VALUE, "bold")

    # Whose inventory and what's in it on the left, its total worth (whatever the filter) on the right.
    item_count = len(items)
    subtitle_parts = [f"{item_count:,} item{'' if item_count == 1 else 's'}", f"{len(all_grouped):,} unique"]
    if rarity_key != "all":
        subtitle_parts.append(f"{len(grouped_items):,} {INVENTORY_RARITY_FILTERS[rarity_key]}")
    subtitle_parts.append(f"Page {page + 1} of {total_pages}")
    _draw_compact_header(image, draw, f"{username}'s Inventory", " · ".join(subtitle_parts), "TOTAL VALUE",
                         compute_inventory_worth(items))

    if not shown_items:
        _draw_empty_state(draw, "No items in this rarity.", grid_top, INVENTORY_CARD_HEIGHT)

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
        draw.text((x0 + 16, y0 + 14), name_text, font=name_font, fill=QUEUE_IMG_TEXT)

        # Icon in the bottom-right corner, under the name, beside the rarity line and value.
        paste_item_icon(
            image, entry.get("type"), x1 - 14 - INVENTORY_ICON_SIZE, y1 - 12 - INVENTORY_ICON_SIZE, INVENTORY_ICON_SIZE
        )

        # The rarity in words, in its colour, so it doesn't rest on the border colour alone; duplicates after it.
        rarity_line = tokens.RARITY_NAMES.get(entry["rarity"], "")
        if entry["count"] > 1:
            rarity_line = f"{rarity_line}  ·  x{entry['count']}" if rarity_line else f"x{entry['count']}"
        if rarity_line:
            draw.text((x0 + 16, y0 + 56), rarity_line, font=rarity_font, fill=rarity_color(entry["rarity"]))

        value = entry["value"]
        if isinstance(value, (int, float)) and value:
            draw_fries_amount(image, draw, (x0 + 16, y1 - 48), int(value), value_font, QUEUE_IMG_ACCENT)
        else:
            draw.text((x0 + 16, y1 - 48), "No value", font=value_font, fill=QUEUE_IMG_MUTED)

    draw_credit_footer(draw, height)
    return save_palette_png(image)


def generate_shop_image(username: str, market_data: dict, mode: str = "all", page: int = 0) -> BytesIO:
    balance = market_data.get("balance", 0)
    offers = market_data.get("offers", [])

    if mode in MARKET_MODE_SLOTS:
        shown_offers = [offer for offer in offers if offer.get("slot") in MARKET_MODE_SLOTS[mode]]
        mode_label = "Daily offers" if mode == "daily" else "Weekly offers"
    else:
        shown_offers = offers[:]
        mode_label = "All offers"

    shown_offers = sorted(shown_offers, key=lambda o: o.get("slot", 0))
    total_pages = max(1, -(-len(shown_offers) // SHOP_ITEMS_PER_PAGE))
    
    # Clamp page
    page = max(0, min(page, total_pages - 1))
    
    start_idx = page * SHOP_ITEMS_PER_PAGE
    end_idx = start_idx + SHOP_ITEMS_PER_PAGE
    page_offers = shown_offers[start_idx:end_idx]

    grid_top = INVENTORY_HEADER_HEIGHT + 32
    grid_width = QUEUE_IMG_WIDTH - QUEUE_IMG_PADDING * 2
    card_width = (grid_width - SHOP_CARD_GAP * (SHOP_GRID_COLUMNS - 1)) // SHOP_GRID_COLUMNS
    rows = max(1, -(-len(page_offers) // SHOP_GRID_COLUMNS))
    height = grid_top + rows * SHOP_CARD_HEIGHT + (rows - 1) * SHOP_CARD_GAP + INVENTORY_FOOTER_HEIGHT

    image = Image.new("RGB", (QUEUE_IMG_WIDTH, height), QUEUE_IMG_BG)
    draw = ImageDraw.Draw(image)

    kind_font = load_font(tokens.OFFER_KIND)
    item_font = load_font(tokens.OFFER_TITLE, "bold")
    price_font = load_font(tokens.OFFER_PRICE, "bold")
    status_font = load_font(tokens.OFFER_STATUS, "bold")
    caption_font = load_font(tokens.OFFER_CAPTION, "bold")

    # Whose shop and which offers on the left, their Golden Fries balance on the right.
    subtitle_parts = [mode_label, f"{len(shown_offers)} offer{'' if len(shown_offers) == 1 else 's'}"]
    if total_pages > 1:
        subtitle_parts.append(f"Page {page + 1} of {total_pages}")
    _draw_compact_header(image, draw, f"{username}'s Shop", " · ".join(subtitle_parts), "BALANCE",
                         int(balance or 0))

    if not page_offers:
        _draw_empty_state(draw, "No offers right now.", grid_top, SHOP_CARD_HEIGHT)

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
        card_color = tokens.SHOP_BOUGHT_FILL if purchased else tokens.SHOP_AVAILABLE_FILL
        border_color = QUEUE_IMG_LOSE if purchased else QUEUE_IMG_WIN
        
        draw.rounded_rectangle(
            [x0, y0, x1, y1], radius=16, fill=card_color, outline=border_color, width=3
        )

        # Item icons, right-aligned, each with its name under it in its rarity colour.
        item_types = [item.get("type") for item in offer.get("items", [])][:4]
        icons_left = x1 - 16 - len(item_types) * SHOP_ICON_CELL_WIDTH
        for slot_index, item_type in enumerate(item_types):
            cell_x = icons_left + slot_index * SHOP_ICON_CELL_WIDTH
            icon_x = cell_x + (SHOP_ICON_CELL_WIDTH - SHOP_ICON_SIZE) // 2
            paste_item_icon(image, item_type, icon_x, y0 + 18, SHOP_ICON_SIZE)
            rarity = shop_item_rarity(item_type)
            if len(item_types) == 1:  # the title already names a single item, so show its rarity instead
                caption = INVENTORY_RARITY_FILTERS.get(str(rarity), "") if rarity is not None else ""
            else:
                caption = prettify_shop_item_type(item_type)
            caption = truncate_to_width(draw, caption, caption_font, SHOP_ICON_CELL_WIDTH - 8)
            caption_width = draw.textbbox((0, 0), caption, font=caption_font)[2]
            draw.text(
                (cell_x + (SHOP_ICON_CELL_WIDTH - caption_width) / 2, y0 + 18 + SHOP_ICON_SIZE + 12),
                caption,
                font=caption_font,
                fill=rarity_color(rarity) if rarity is not None else QUEUE_IMG_MUTED,
            )

        # Offer kind, title, price and status down the left side.
        text_width = max(80, icons_left - x0 - 28)
        slot = offer.get("slot")
        slot_text = SHOP_SLOT_LABELS.get(slot, f"Slot {slot}")
        draw.text((x0 + 16, y0 + 14), slot_text, font=kind_font, fill=QUEUE_IMG_MUTED)

        if len(item_types) == 1:
            title = prettify_shop_item_type(item_types[0])
        else:
            title = f"{len(offer.get('items', []))} items"
        title = truncate_to_width(draw, title, item_font, text_width)
        draw.text((x0 + 16, y0 + 46), title, font=item_font, fill=QUEUE_IMG_TEXT)

        price = offer.get("price")
        if isinstance(price, (int, float)):
            draw_fries_amount(image, draw, (x0 + 16, y0 + 92), int(price), price_font, QUEUE_IMG_ACCENT)
        else:
            draw.text((x0 + 16, y0 + 92), "?", font=price_font, fill=QUEUE_IMG_ACCENT)

        status_text = "Bought" if purchased else "Available"
        status_color = QUEUE_IMG_LOSE if purchased else QUEUE_IMG_WIN
        draw.text((x0 + 16, y1 - 42), status_text, font=status_font, fill=status_color)

    draw_credit_footer(draw, height)
    return save_palette_png(image)


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


def _format_play_time(duration_ms: int) -> str:
    """Hours and minutes, no seconds ("31h 07m", "42m"): /compare compares totals, not stopwatch times."""
    total_minutes = max(0, int(duration_ms)) // 60_000
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"


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
    """/compare: two players side by side, survev.de rows then NeatQueue rows. `*_stats` is None for a player who
    isn't linked, or {"unavailable": True} when their survev.de history couldn't be read in full; `*_worth` is None
    when their inventory couldn't be read. "-" means there is nothing to show and "N/A" that a lookup failed.
    On each row the higher value is green and the lower red; values that print the same are a tie."""
    left_linked, right_linked = left_stats is not None, right_stats is not None

    def stat_fields(stats):
        if stats is None or stats.get("unavailable"):
            return None, None, None
        games = max(1, stats["games"])
        return stats["wins"], stats["kills"] / games, stats["damage"]

    def gap(linked: bool) -> str:
        """What an empty survev.de cell says: the lookup failed for a linked player, or there's nothing to show."""
        return "N/A" if linked else "-"

    def cell(value, empty: str, text: str | None = None) -> tuple[str, float | None]:
        if value is None:
            return empty, None
        return (text if text is not None else f"{value:,}"), value

    def queue_fields(summary):
        summary = summary or {}
        playtime_ms = summary.get("total_duration_ms") if summary.get("matches", 0) else None
        return playtime_ms, summary.get("all_time_avg_damage"), summary.get("monthly_avg_damage")

    def side(stats, linked, worth, summary, tier):
        wins, kills_per_game, damage = stat_fields(stats)
        play, avg, month_avg = queue_fields(summary)
        tier_text, rating = format_squad_tier(tier, verified=linked)
        return [
            cell(wins, gap(linked)),
            cell(kills_per_game, gap(linked), None if kills_per_game is None else f"{kills_per_game:.2f}"),
            cell(damage, gap(linked)),
            (tier_text, rating),
            cell(worth, gap(linked), None if worth is None else f"{worth:,} GF"),
            cell(play, "-", None if play is None else _format_play_time(play)),
            cell(avg, "-", None if avg is None else f"{avg:,.0f}"),
            cell(month_avg, "-", None if month_avg is None else f"{month_avg:,.0f}"),
        ]

    left_cells = side(left_stats, left_linked, left_worth, left_queue_summary, left_tier)
    right_cells = side(right_stats, right_linked, right_worth, right_queue_summary, right_tier)
    month = datetime.now(timezone.utc).strftime("%B")
    sections = [
        ("SURVEV.DE · ALL MODES", ["Wins", "Kills / Game", "Total Damage", "Squad Tier", "Inventory Worth"]),
        ("NEATQUEUE 4V4S", ["Play Time", "Avg Damage", f"{month} Avg Damage"]),
    ]

    name_font = load_font(tokens.COMPARE_NAME, "bold")
    status_font = load_font(tokens.COMPARE_STATUS)
    section_font = load_font(tokens.COMPARE_SECTION, "bold")
    label_font = load_font(tokens.COMPARE_LABEL, "bold")
    value_font = load_font(tokens.COMPARE_VALUE, "bold")

    card_left, card_right = QUEUE_IMG_PADDING, QUEUE_IMG_WIDTH - QUEUE_IMG_PADDING
    card_top = INVENTORY_HEADER_HEIGHT + 32
    name_block, row_height, section_height = 110, 80, 52
    row_count = sum(len(labels) for _, labels in sections)
    card_bottom = card_top + name_block + row_count * row_height + len(sections) * section_height
    height = card_bottom + INVENTORY_FOOTER_HEIGHT

    image = Image.new("RGB", (QUEUE_IMG_WIDTH, height), QUEUE_IMG_BG)
    draw = ImageDraw.Draw(image)
    _draw_compact_header(image, draw, "Stats comparison", "All-time stats are from verification date.")

    center_x = QUEUE_IMG_WIDTH / 2
    left_center, right_center = (card_left + center_x) / 2, (center_x + card_right) / 2
    left_value_right, right_value_left = center_x - 210, center_x + 210
    draw.rounded_rectangle([card_left, card_top, card_right, card_bottom], radius=20, fill=tokens.PANEL,
                           outline=tokens.DIVIDER, width=2)

    name_width = (card_right - card_left) / 2 - 60
    for name, linked, center in ((left_name, left_linked, left_center), (right_name, right_linked, right_center)):
        draw.text((center, card_top + 22), truncate_to_width(draw, name, name_font, name_width), font=name_font,
                  fill=QUEUE_IMG_TEXT, anchor="ma")
        draw.text((center, card_top + 72), "Linked" if linked else "Not linked", font=status_font,
                  fill=QUEUE_IMG_MUTED, anchor="ma")

    y = card_top + name_block
    cell_index = 0
    for title, labels in sections:
        draw.rectangle([card_left + 2, y, card_right - 2, y + section_height], fill=tokens.HEADER_TOP)
        draw.text((center_x, y + section_height / 2), title, font=section_font, fill=QUEUE_IMG_ACCENT, anchor="mm")
        y += section_height
        for row_index, label in enumerate(labels):
            (left_text, left_num), (right_text, right_num) = left_cells[cell_index], right_cells[cell_index]
            cell_index += 1
            if row_index % 2:
                draw.rectangle([card_left + 2, y, card_right - 2, y + row_height], fill=tokens.PANEL_RAISED)
            if left_text == right_text and left_num is not None and right_num is not None:
                right_num = left_num  # values that print the same are coloured as a tie
            left_fill, right_fill = _pick_compare_row_colors(left_num, right_num)
            if left_num is None:
                left_fill = QUEUE_IMG_MUTED
            if right_num is None:
                right_fill = QUEUE_IMG_MUTED
            draw.text((center_x, _vcenter_y(draw, label_font, y, y + row_height)), label, font=label_font,
                      fill=QUEUE_IMG_MUTED, anchor="ma")
            value_y = _vcenter_y(draw, value_font, y, y + row_height)
            draw.text((left_value_right, value_y), left_text, font=value_font, fill=left_fill, anchor="ra")
            draw.text((right_value_left, value_y), right_text, font=value_font, fill=right_fill)
            y += row_height

    draw_credit_footer(draw, height)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


def generate_history_image(player_name: str, history_rows: list[dict], metric: str = "damage") -> BytesIO:
    """Generates a 1600px-wide card displaying a player's last 10 queues:
    a gradient header (name, last-N record, win rate), a minimalist sparkline panel for the selected metric,
    and a newest-first table of queues with result, kills, avg damage, and impact.
    """
    metric = normalize_history_metric(metric)
    S = 2  # Supersampling factor
    num_rows = max(len(history_rows), 1)

    sparkline_top = QUEUE_IMG_HEADER_HEIGHT + QUEUE_IMG_PADDING
    sparkline_height = 256
    sparkline_bottom = sparkline_top + sparkline_height

    table_top = sparkline_bottom + 24
    col_header_top = table_top
    rows_top = col_header_top + QUEUE_IMG_COL_HEADER_HEIGHT
    table_bottom = rows_top + num_rows * QUEUE_IMG_ROW_HEIGHT + QUEUE_IMG_CARD_BOTTOM_PAD
    height = table_bottom + QUEUE_IMG_PADDING + 14

    image = Image.new("RGB", (QUEUE_IMG_WIDTH * S, height * S), QUEUE_IMG_BG)
    draw = ImageDraw.Draw(image)

    subtitle_font = load_font(38 * S, "bold")
    header_font = load_font(30 * S, "bold")
    spark_axis_font = load_font(22 * S, "bold")
    spark_val_font = load_font(23 * S, "bold")
    body_font = load_font(35 * S)
    body_font_bold = load_font(35 * S, "bold")
    duration_font = load_font(22 * S, "bold")

    # 1. Header gradient
    _draw_header_gradient(draw, QUEUE_IMG_HEADER_HEIGHT * S, QUEUE_IMG_WIDTH * S)

    # Title: shrink a long name before shortening it, and always keep "'s History" readable.
    title_suffix = "'s History"
    max_title_width = (QUEUE_IMG_WIDTH - QUEUE_IMG_PADDING * 2) * S
    for title_size in (62, 56, 50, 46):
        title_font = load_font(title_size * S, "bold")
        title_text = f"{player_name}{title_suffix}"
        if draw.textbbox((0, 0), title_text, font=title_font)[2] <= max_title_width:
            break
    else:
        suffix_width = draw.textbbox((0, 0), title_suffix, font=title_font)[2]
        title_text = truncate_to_width(draw, player_name, title_font, max_title_width - suffix_width) + title_suffix
    draw.text((QUEUE_IMG_PADDING * S, 28 * S), title_text, font=title_font, fill=QUEUE_IMG_TEXT)

    wins = sum(1 for r in history_rows if r.get("won") is True)
    losses = sum(1 for r in history_rows if r.get("won") is False)
    n = len(history_rows)
    if wins > losses:
        subtitle_color = QUEUE_IMG_WIN
    elif losses > wins:
        subtitle_color = QUEUE_IMG_LOSE
    else:
        subtitle_color = QUEUE_IMG_MUTED
    subtitle_text = f"Last {n} {'Queue' if n == 1 else 'Queues'} · {wins}W - {losses}L"
    draw.text((QUEUE_IMG_PADDING * S, 96 * S), subtitle_text, font=subtitle_font, fill=subtitle_color)

    tot_gms = sum(r.get("games") or 1 for r in history_rows)
    decided = wins + losses
    if decided > 0:
        win_rate = round(100 * wins / decided)
        meta_text = f"Win Rate: {win_rate}% · {tot_gms} {'Game' if tot_gms == 1 else 'Games'} Played"
    else:
        meta_text = f"{tot_gms} {'Game' if tot_gms == 1 else 'Games'} Played"
    draw.text((QUEUE_IMG_PADDING * S, 152 * S), meta_text, font=duration_font, fill=QUEUE_IMG_MUTED)

    # 2. Sparkline Panel
    card_x0 = QUEUE_IMG_PADDING
    card_x1 = QUEUE_IMG_WIDTH - QUEUE_IMG_PADDING
    card_radius = QUEUE_IMG_CARD_RADIUS * S
    spark_header_bottom = sparkline_top + 52

    draw.rounded_rectangle(
        [card_x0 * S, sparkline_top * S, card_x1 * S, sparkline_bottom * S],
        radius=card_radius,
        fill=QUEUE_IMG_CARD_BG,
        outline=QUEUE_IMG_ROW_DIVIDER,
        width=2 * S,
    )

    metric_labels = {
        "damage": "AVG DAMAGE TREND",
        "impact": "AVERAGE IMPACT TREND (0–100)",
        "kills": "TOTAL KILLS TREND",
    }
    spark_title = metric_labels.get(metric, "TREND")
    spark_title_y = _vcenter_y(draw, header_font, sparkline_top * S, spark_header_bottom * S)
    draw.text(((card_x0 + 24) * S, spark_title_y), spark_title, font=header_font, fill=QUEUE_IMG_TEXT)

    divider_y = spark_header_bottom * S
    draw.line([((card_x0 + 16) * S, divider_y), ((card_x1 - 16) * S, divider_y)], fill=QUEUE_IMG_ROW_DIVIDER, width=1 * S)

    chronological = list(reversed(history_rows))
    P = len(chronological)

    def get_val(row):
        if metric == "damage":
            return row.get("avg_damage")
        elif metric == "impact":
            return row.get("impact")
        else:
            return row.get("kills")

    valid_points = [(i, get_val(r)) for i, r in enumerate(chronological) if get_val(r) is not None]

    plot_x0 = (card_x0 + 68) * S
    plot_x1 = (card_x1 - 68) * S
    plot_y0 = (sparkline_top + 106) * S
    plot_y1 = (sparkline_bottom - 72) * S
    area_base_y = (sparkline_bottom - 50) * S

    if P == 0 or not valid_points:
        empty_text = "No data available for this metric."
        empty_bbox = draw.textbbox((0, 0), empty_text, font=body_font)
        empty_x = (QUEUE_IMG_WIDTH * S - (empty_bbox[2] - empty_bbox[0])) / 2
        empty_y = _vcenter_y(draw, body_font, spark_header_bottom * S, sparkline_bottom * S)
        draw.text((empty_x, empty_y), empty_text, font=body_font, fill=QUEUE_IMG_MUTED)
    else:
        values = [v for _, v in valid_points]
        min_val = min(values)
        max_val = max(values)

        def pt_coords(idx, val):
            if P <= 1:
                cx = (plot_x0 + plot_x1) / 2
            else:
                cx = plot_x0 + idx * (plot_x1 - plot_x0) / (P - 1)
            if max_val == min_val:
                cy = (plot_y0 + plot_y1) / 2
            else:
                cy = plot_y1 - ((val - min_val) / (max_val - min_val)) * (plot_y1 - plot_y0)
            return cx, cy

        # Subtle mid reference line and bottom plot baseline
        mid_guide_y = (plot_y0 + plot_y1) / 2
        draw.line([(plot_x0, mid_guide_y), (plot_x1, mid_guide_y)], fill=(29, 39, 59), width=1 * S)
        draw.line([(plot_x0, area_base_y), (plot_x1, area_base_y)], fill=QUEUE_IMG_ROW_DIVIDER, width=1 * S)

        coords = [pt_coords(idx, val) for idx, val in valid_points]
        if len(coords) > 1:
            area_poly = [(coords[0][0], area_base_y)] + coords + [(coords[-1][0], area_base_y)]
            # Blend the translucent fill only inside the area's bounding box (plus a margin): the layer is fully
            # transparent everywhere else, so blending the whole canvas gave the same pixels for far more work.
            left = max(0, math.floor(min(x for x, _ in area_poly)) - 2)
            top = max(0, math.floor(min(y for _, y in area_poly)) - 2)
            right = min(image.width, math.ceil(max(x for x, _ in area_poly)) + 3)
            bottom = min(image.height, math.ceil(max(y for _, y in area_poly)) + 3)
            area_layer = Image.new("RGBA", (right - left, bottom - top), (0, 0, 0, 0))
            area_draw = ImageDraw.Draw(area_layer)
            area_draw.polygon([(x - left, y - top) for x, y in area_poly], fill=(255, 165, 50, 28))
            region = image.crop((left, top, right, bottom)).convert("RGBA")
            image.paste(Image.alpha_composite(region, area_layer).convert("RGB"), (left, top))
            draw = ImageDraw.Draw(image)

            draw.line(coords, fill=QUEUE_IMG_ACCENT, width=4 * S, joint="curve")

        # X-axis tick marks and queue # labels below area_base_y
        axis_label_y = _vcenter_y(draw, spark_axis_font, area_base_y + 4 * S, (sparkline_bottom - 6) * S)
        for idx in range(P):
            if P <= 1:
                cx = (plot_x0 + plot_x1) / 2
            else:
                cx = plot_x0 + idx * (plot_x1 - plot_x0) / (P - 1)

            draw.line([(cx, area_base_y), (cx, area_base_y + 5 * S)], fill=QUEUE_IMG_ROW_DIVIDER, width=2 * S)
            match_label = f"#{chronological[idx]['match_id']}"
            is_latest = idx == P - 1
            x_fill = QUEUE_IMG_TEXT if is_latest else QUEUE_IMG_MUTED
            x_bbox = draw.textbbox((0, 0), match_label, font=spark_axis_font)
            x_text_x = cx - (x_bbox[2] - x_bbox[0]) / 2 - x_bbox[0]
            draw.text((x_text_x, axis_label_y), match_label, font=spark_axis_font, fill=x_fill)

        # Draw data dots first, then value pills on top so pills are never clipped by lines or rings
        for idx, val in valid_points:
            cx, cy = pt_coords(idx, val)
            won = chronological[idx].get("won")
            if won is True:
                dot_color = QUEUE_IMG_WIN
            elif won is False:
                dot_color = QUEUE_IMG_LOSE
            else:
                dot_color = QUEUE_IMG_MUTED

            is_latest = idx == P - 1
            r = 7 * S
            if is_latest:
                ring_r = 13 * S
                draw.ellipse([cx - ring_r, cy - ring_r, cx + ring_r, cy + ring_r], outline=QUEUE_IMG_ACCENT, width=3 * S)
            draw.ellipse([cx - r - 2 * S, cy - r - 2 * S, cx + r + 2 * S, cy + r + 2 * S], fill=QUEUE_IMG_CARD_BG)
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=dot_color)

        for idx, val in valid_points:
            cx, cy = pt_coords(idx, val)
            is_latest = idx == P - 1

            if metric == "damage":
                v_text = f"{val:,.0f}"
            elif metric == "impact":
                v_text = f"{int(val + 0.5)}"
            else:
                v_text = str(int(val))

            v_bbox = draw.textbbox((0, 0), v_text, font=spark_val_font)
            text_w = v_bbox[2] - v_bbox[0]
            text_h = v_bbox[3] - v_bbox[1]

            pad_x = 9 * S
            pad_y = 5 * S
            pill_w = text_w + 2 * pad_x
            pill_h = text_h + 2 * pad_y

            marker_clearance = (16 if is_latest else 10) * S
            pill_bottom = cy - marker_clearance
            pill_top = pill_bottom - pill_h
            pill_left = cx - pill_w / 2
            pill_right = cx + pill_w / 2

            pill_outline = QUEUE_IMG_ACCENT if is_latest else QUEUE_IMG_ROW_DIVIDER
            v_fill = QUEUE_IMG_ACCENT if is_latest else QUEUE_IMG_TEXT

            draw.rounded_rectangle(
                [pill_left, pill_top, pill_right, pill_bottom],
                radius=8 * S,
                fill=QUEUE_IMG_BG,
                outline=pill_outline,
                width=2 * S if is_latest else 1 * S,
            )
            v_x = pill_left + pad_x - v_bbox[0]
            v_y = pill_top + pad_y - v_bbox[1]
            draw.text((v_x, v_y), v_text, font=spark_val_font, fill=v_fill)

    # 3. Table Card
    draw.rounded_rectangle(
        [card_x0 * S, table_top * S, card_x1 * S, table_bottom * S],
        radius=card_radius,
        fill=QUEUE_IMG_CARD_BG,
        outline=QUEUE_IMG_ROW_DIVIDER,
        width=2 * S,
    )

    inner_pad_x = 24
    inner_width = (card_x1 - card_x0) - 2 * inner_pad_x
    inner_x0 = card_x0 + inner_pad_x
    columns = [round(ratio * inner_width) for ratio in QUEUE_IMG_HISTORY_COLUMN_RATIOS]
    col_widths = [columns[i + 1] - columns[i] for i in range(len(columns) - 1)] + [inner_width - columns[-1]]

    active_col_map = {"kills": "Kills", "damage": "Avg Dmg", "impact": "Impact"}
    active_col_label = active_col_map.get(metric)

    header_y = _vcenter_y(draw, header_font, col_header_top * S, rows_top * S)
    for col_idx, label in enumerate(QUEUE_IMG_HISTORY_COLUMN_LABELS):
        label_x = (inner_x0 + columns[col_idx]) * S
        label_bbox = draw.textbbox((0, 0), label, font=header_font)
        label_width = label_bbox[2] - label_bbox[0]
        if col_idx != 0 and col_widths[col_idx] * S > label_width:
            label_x += (col_widths[col_idx] * S - label_width) / 2
        header_fill = QUEUE_IMG_ACCENT if label == active_col_label else QUEUE_IMG_TEXT
        draw.text((label_x, header_y), label, font=header_font, fill=header_fill)

    draw.line(
        [((card_x0 + 12) * S, rows_top * S), ((card_x1 - 12) * S, rows_top * S)],
        fill=QUEUE_IMG_ROW_DIVIDER,
        width=1 * S,
    )

    if not history_rows:
        empty_y = _vcenter_y(draw, body_font, rows_top * S, (rows_top + QUEUE_IMG_ROW_HEIGHT) * S)
        draw.text((inner_x0 * S, empty_y), "No queue history found.", font=body_font, fill=QUEUE_IMG_MUTED)

    for row_idx, row in enumerate(history_rows):
        row_top = rows_top + row_idx * QUEUE_IMG_ROW_HEIGHT
        row_box_top = (row_top + 4) * S
        row_box_bottom = (row_top + QUEUE_IMG_ROW_HEIGHT - 4) * S

        if row_idx % 2 == 0:
            draw.rounded_rectangle(
                [(card_x0 + 12) * S, row_box_top, (card_x1 - 12) * S, row_box_bottom],
                radius=10 * S,
                fill=QUEUE_IMG_ROW_ALT,
            )

        match_id_str = f"#{row['match_id']}"
        won = row.get("won")
        wins_list = row.get("team_round_wins")
        my_team = row.get("team_index")

        if isinstance(wins_list, list) and len(wins_list) >= 2 and my_team is not None and 0 <= my_team < len(wins_list):
            my_score = wins_list[my_team]
            opp_score = wins_list[1 - my_team] if len(wins_list) == 2 else sum(w for i, w in enumerate(wins_list) if i != my_team)
            score_str = f"{my_score} - {opp_score}"
            if won is True:
                res_str = f"W · {score_str}"
                res_fill = QUEUE_IMG_WIN
            elif won is False:
                res_str = f"L · {score_str}"
                res_fill = QUEUE_IMG_LOSE
            else:
                res_str = f"T · {score_str}"
                res_fill = QUEUE_IMG_MUTED
        else:
            if won is True:
                res_str = "WIN"
                res_fill = QUEUE_IMG_WIN
            elif won is False:
                res_str = "LOSS"
                res_fill = QUEUE_IMG_LOSE
            else:
                res_str = "-"
                res_fill = QUEUE_IMG_MUTED

        kills_val = row.get("kills")
        kills_str = str(kills_val) if kills_val is not None else "-"
        kills_fill = QUEUE_IMG_TEXT if kills_val is not None else QUEUE_IMG_MUTED

        avg_val = row.get("avg_damage")
        avg_str = f"{avg_val:,.0f}" if avg_val is not None else "-"
        avg_fill = QUEUE_IMG_ACCENT if avg_val is not None else QUEUE_IMG_MUTED

        imp_val = row.get("impact")
        imp_str = str(int(imp_val + 0.5)) if imp_val is not None else "-"
        imp_fill = QUEUE_IMG_TEXT if imp_val is not None else QUEUE_IMG_MUTED

        cells = [
            (match_id_str, body_font_bold, QUEUE_IMG_TEXT, False),
            (res_str, body_font_bold, res_fill, True),
            (kills_str, body_font, kills_fill, True),
            (avg_str, body_font_bold, avg_fill, True),
            (imp_str, body_font_bold, imp_fill, True),
        ]

        for col_idx, (text_val, cell_font, fill_color, center_align) in enumerate(cells):
            cell_x = (inner_x0 + columns[col_idx]) * S
            cell_y = _vcenter_y(draw, cell_font, row_box_top, row_box_bottom)
            if center_align:
                val_bbox = draw.textbbox((0, 0), text_val, font=cell_font)
                val_w = val_bbox[2] - val_bbox[0]
                cell_x += (col_widths[col_idx] * S - val_w) / 2
            draw.text((cell_x, cell_y), text_val, font=cell_font, fill=fill_color)

    # 4. Footer attribution
    draw_credit_footer(draw, height, scale=S, area_top=table_bottom)

    image = image.resize((QUEUE_IMG_WIDTH, height), resample=Image.Resampling.LANCZOS)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer
