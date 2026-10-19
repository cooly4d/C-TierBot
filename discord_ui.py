"""Discord UI components: persistent Views/Selects for the leaderboard, queue-result
verify and Game breakdown buttons, and inventory/shop pagination, plus the message-refresh handlers they trigger.

Note: the actual click/select dispatch lives in `log_interaction` (leaderboard_bot.py) —
these views' own callbacks are mostly no-ops (see the comment on QueueResultView).
"""
import discord

from bot_config import LEADERBOARD_SORT_CONFIG
from image_utils import (
    INVENTORY_RARITY_FILTERS,
    normalize_inventory_rarity_filter,
)
from leaderboard_service import (
    build_inventory_image_payload,
    build_seasonal_leaderboard_payload,
    build_shop_image_payload,
    generate_leaderboard_embed,
)
from queue_stats_service import build_queue_game_payload, parse_queue_match_id, queue_games_unavailable
from survev_client import run_survev_verification

MAX_PAGINATION_STATE_ENTRIES = 200

# Store pagination state: msg_id -> (target_user, access_token, mode/rarity, current_page, total_pages)
inventory_pagination_state: dict[int, tuple[discord.User, str, str, int, int]] = {}
market_pagination_state: dict[int, tuple[discord.User, str, str, int, int]] = {}


def store_pagination_state(state_dict: dict, msg_id: int, value: tuple):
    """Stores pagination state for msg_id while evicting oldest entries above MAX_PAGINATION_STATE_ENTRIES."""
    state_dict.pop(msg_id, None)
    state_dict[msg_id] = value
    while len(state_dict) > MAX_PAGINATION_STATE_ENTRIES:
        oldest_key = next(iter(state_dict))
        state_dict.pop(oldest_key, None)


async def refresh_leaderboard_message(interaction: discord.Interaction, period: str, days: int, sort_by: str = "kills"):
    if not interaction.response.is_done():
        try:
            await interaction.response.defer()
        except discord.HTTPException:
            pass
    embed = await generate_leaderboard_embed(period=period, days=days, sort_by=sort_by)
    await interaction.message.edit(embed=embed, view=LeaderboardView(period, sort_by))


class LeaderboardSortSelect(discord.ui.Select):
    def __init__(self, selected_sort: str = "kills"):
        sort_key = selected_sort if selected_sort in LEADERBOARD_SORT_CONFIG else "kills"
        options = [
            discord.SelectOption(label=cfg["label"], value=key, default=(key == sort_key))
            for key, cfg in LEADERBOARD_SORT_CONFIG.items()
        ]
        super().__init__(
            placeholder="Select leaderboard stat",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="leaderboard_sort"
        )

    async def callback(self, interaction: discord.Interaction):
        # Handled by log_interaction
        pass


class LeaderboardView(discord.ui.View):
    def __init__(self, initial_period: str = "Weekly", initial_sort: str = "kills"):
        super().__init__(timeout=None)
        self.initial_sort = initial_sort if initial_sort in LEADERBOARD_SORT_CONFIG else "kills"

        if initial_period == "Weekly":
            self.weekly_button.style = discord.ButtonStyle.primary
            self.monthly_button.style = discord.ButtonStyle.secondary
        else:
            self.weekly_button.style = discord.ButtonStyle.secondary
            self.monthly_button.style = discord.ButtonStyle.primary

        self.add_item(LeaderboardSortSelect(self.initial_sort))

    @discord.ui.button(label="Weekly", style=discord.ButtonStyle.primary, custom_id="leaderboard_weekly")
    async def weekly_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Handled by log_interaction
        pass

    @discord.ui.button(label="Monthly", style=discord.ButtonStyle.secondary, custom_id="leaderboard_monthly")
    async def monthly_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Handled by log_interaction
        pass


class SeasonalLeaderboardView(discord.ui.View):
    """Prev/Next paging for /leaderboard_season; clicks are handled in log_interaction."""
    def __init__(self, month: str, page: int, total_pages: int):
        super().__init__(timeout=None)
        # custom_id carries the month and target page, so paging keeps working after a restart.
        self.add_item(discord.ui.Button(
            label="◀ Previous",
            style=discord.ButtonStyle.primary,
            custom_id=f"season_prev:{month}:{max(0, page - 1)}",
            disabled=page <= 0,
        ))
        self.add_item(discord.ui.Button(
            label="Next ▶",
            style=discord.ButtonStyle.primary,
            custom_id=f"season_next:{month}:{min(total_pages - 1, page + 1)}",
            disabled=page >= total_pages - 1,
        ))


async def build_seasonal_leaderboard_message(guild_id: int, month: str | None = None, page: int = 0):
    """Returns (embed, view); view is None when the whole leaderboard fits on one page."""
    embed, month_key, page, total_pages = await build_seasonal_leaderboard_payload(guild_id, month, page)
    view = SeasonalLeaderboardView(month_key, page, total_pages) if total_pages > 1 else None
    return embed, view


async def refresh_seasonal_leaderboard_message(interaction: discord.Interaction, month: str, page: int):
    if not interaction.response.is_done():
        try:
            await interaction.response.defer()
        except discord.HTTPException:
            pass
    embed, view = await build_seasonal_leaderboard_message(interaction.guild_id, month, page)
    await interaction.message.edit(embed=embed, view=view)


class QueueResultView(discord.ui.View):
    """Persistent view attached to queue-result messages, so anyone missing from the roster can self-serve
    a verification link (visible only to them, in the same channel) instead of having to know /verify exists.
    Finished queues also get a Game breakdown button (handled in log_interaction, like the other buttons)."""
    def __init__(self, include_breakdown: bool = False):
        super().__init__(timeout=None)
        if not include_breakdown:
            self.remove_item(self.breakdown_button)

    @discord.ui.button(label="Game breakdown", style=discord.ButtonStyle.primary, custom_id="queue_result_games")
    async def breakdown_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Handled by log_interaction
        pass

    @discord.ui.button(label="Account unlinked?  Verify here for a gf.", style=discord.ButtonStyle.secondary, custom_id="queue_result_verify")
    async def verify_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        await run_survev_verification(interaction.user.id, lambda **kw: interaction.followup.send(ephemeral=True, **kw))

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item):
        # discord.py's default View.on_error just logs to stderr, which is easy to miss — surface it
        # to the clicking user too so a failure is never silent.
        print(f"ERROR - QueueResultView item {item} failed: {error!r}")
        message = f"❌ Something went wrong: `{error}`"
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            pass


_queue_result_views: dict[bool, QueueResultView] = {}


def get_queue_result_view(include_breakdown: bool = False):
    """One shared view per variant (with or without the Game breakdown button), reused for every result message."""
    if include_breakdown not in _queue_result_views:
        _queue_result_views[include_breakdown] = QueueResultView(include_breakdown)
    return _queue_result_views[include_breakdown]


class QueueGamesPagerView(discord.ui.View):
    """Previous/Next buttons under a Game breakdown card; clicks are handled in log_interaction. The queue and the
    target page live in each custom_id (like the seasonal leaderboard), so nothing has to be kept in memory."""
    def __init__(self, match_id: str, page: int, total_pages: int):
        super().__init__(timeout=900)  # finite: these live in ephemeral messages, so don't hold them forever
        self.add_item(discord.ui.Button(
            label="◀ Previous",
            style=discord.ButtonStyle.primary,
            custom_id=f"qgame_prev:{match_id}:{max(0, page - 1)}",
            disabled=page <= 0,
        ))
        self.add_item(discord.ui.Button(
            label="Next ▶",
            style=discord.ButtonStyle.primary,
            custom_id=f"qgame_next:{match_id}:{min(total_pages - 1, page + 1)}",
            disabled=page >= total_pages - 1,
        ))


def parse_game_page_id(custom_id: str) -> tuple[str, int]:
    """Splits "qgame_prev:{match_id}:{page}" / "qgame_next:..." into (match_id, page). The queue number is whatever
    sits between the first and last colon, so an unusual one can't break the page lookup."""
    _, rest = custom_id.split(":", 1)
    match_id, page = rest.rsplit(":", 1)
    return match_id, int(page)


async def build_queue_game_message(guild_id: int | None, match_id: str, page: int):
    """Returns (embed, file, view, None) for one game of a queue's breakdown, or (None, None, None, error_text).
    The view is None when the queue only has a single game."""
    embed, file, page, total_pages, error_text = await build_queue_game_payload(guild_id, match_id, page)
    if error_text:
        return None, None, None, error_text
    view = QueueGamesPagerView(match_id, page, total_pages) if total_pages > 1 else None
    return embed, file, view, None


async def show_queue_game_breakdown(interaction: discord.Interaction):
    """Game breakdown button: opens the first game as an ephemeral message only the clicker sees."""
    await interaction.response.defer(ephemeral=True)
    match_id = parse_queue_match_id(interaction.message)
    if match_id is None:
        await interaction.followup.send(queue_games_unavailable(None), ephemeral=True)
        return

    embed, file, view, error_text = await build_queue_game_message(interaction.guild_id, match_id, 0)
    if error_text:
        await interaction.followup.send(error_text, ephemeral=True)
        return
    options = {"view": view} if view is not None else {}  # followup.send rejects view=None
    await interaction.followup.send(embed=embed, file=file, ephemeral=True, **options)


async def refresh_queue_game_message(interaction: discord.Interaction, match_id: str, page: int):
    """Previous/Next buttons: swaps the ephemeral breakdown message over to another game."""
    await interaction.response.defer()
    embed, file, view, error_text = await build_queue_game_message(interaction.guild_id, match_id, page)
    if error_text:
        await interaction.followup.send(error_text, ephemeral=True)
        return
    # Ephemeral messages can only be edited through the interaction itself, not interaction.message.edit.
    await interaction.edit_original_response(embed=embed, attachments=[file], view=view)


class InventoryRaritySelect(discord.ui.Select):
    def __init__(self, selected_rarity: str = "all", rarity_counts: dict[str, int] | None = None):
        rarity_key = normalize_inventory_rarity_filter(selected_rarity)
        counts = rarity_counts or {key: 0 for key in INVENTORY_RARITY_FILTERS}
        options = [
            discord.SelectOption(
                label=f"{label} ({counts.get(value, 0)})",
                value=value,
                default=(value == rarity_key)
            )
            for value, label in INVENTORY_RARITY_FILTERS.items()
        ]
        super().__init__(
            placeholder="Select rarity",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="inventory_rarity"
        )

    async def callback(self, interaction: discord.Interaction):
        # Handled by log_interaction
        pass


class InventoryPaginationView(discord.ui.View):
    """Persistent view for paginating through inventory items."""
    def __init__(self, total_pages: int, rarity_filter: str = "all", rarity_counts: dict[str, int] | None = None):
        super().__init__(timeout=None)
        self.total_pages = total_pages
        self.rarity_filter = normalize_inventory_rarity_filter(rarity_filter)
        self.rarity_counts = rarity_counts or {key: 0 for key in INVENTORY_RARITY_FILTERS}
        self.add_item(InventoryRaritySelect(self.rarity_filter, self.rarity_counts))

    def update_button_states(self, current_page: int):
        self.prev_button.disabled = current_page <= 0
        self.next_button.disabled = current_page >= self.total_pages - 1

    @discord.ui.button(label="◀ Previous", style=discord.ButtonStyle.primary, custom_id="inventory_prev")
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Handled by log_interaction
        pass

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.primary, custom_id="inventory_next")
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Handled by log_interaction
        pass


class ShopPaginationView(discord.ui.View):
    """Persistent view for toggling Daily/Weekly/All shop offers and paginating through them."""
    def __init__(self, total_pages: int, mode: str = "all"):
        super().__init__(timeout=None)
        self.total_pages = total_pages
        self.mode = mode if mode in ("daily", "weekly", "all") else "all"
        self._update_mode_button_styles(self.mode)

    def _update_mode_button_styles(self, mode: str):
        self.daily_button.style = discord.ButtonStyle.primary if mode == "daily" else discord.ButtonStyle.secondary
        self.weekly_button.style = discord.ButtonStyle.primary if mode == "weekly" else discord.ButtonStyle.secondary
        self.all_button.style = discord.ButtonStyle.primary if mode == "all" else discord.ButtonStyle.secondary

    def update_button_states(self, current_page: int, mode: str | None = None):
        if mode in ("daily", "weekly", "all"):
            self.mode = mode
        self._update_mode_button_styles(self.mode)
        self.prev_button.disabled = current_page <= 0
        self.next_button.disabled = current_page >= self.total_pages - 1

    @discord.ui.button(label="Daily", style=discord.ButtonStyle.secondary, custom_id="market_mode_daily", row=0)
    async def daily_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Handled by log_interaction
        pass

    @discord.ui.button(label="Weekly", style=discord.ButtonStyle.secondary, custom_id="market_mode_weekly", row=0)
    async def weekly_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Handled by log_interaction
        pass

    @discord.ui.button(label="All", style=discord.ButtonStyle.primary, custom_id="market_mode_all", row=0)
    async def all_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Handled by log_interaction
        pass

    @discord.ui.button(label="◀ Previous", style=discord.ButtonStyle.primary, custom_id="market_prev", row=1)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Handled by log_interaction
        pass

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.primary, custom_id="market_next", row=1)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Handled by log_interaction
        pass


async def refresh_market_message(interaction: discord.Interaction, target_user: discord.User, access_token: str, mode: str, page: int, msg_id: int):
    """Refresh the market message with a new mode or page using cached market data when available."""
    await interaction.response.defer()
    content, file, filename, error_text, total_pages = await build_shop_image_payload(
        target_user, access_token, mode=mode, page=page, use_cache=True
    )
    if error_text or not file:
        await interaction.followup.send("Failed to reload shop offers.")
        return

    clamped_page = max(0, min(page, total_pages - 1))
    store_pagination_state(market_pagination_state, msg_id, (target_user, access_token, mode, clamped_page, total_pages))

    embed = discord.Embed(title=content, color=discord.Color.blurple())
    embed.set_image(url=f"attachment://{filename}")
    embed.set_footer(text=f"Page {clamped_page + 1} of {total_pages}")

    view = ShopPaginationView(total_pages, mode=mode)
    view.update_button_states(clamped_page, mode=mode)

    await interaction.message.edit(attachments=[file], embed=embed, view=view)


async def refresh_inventory_message(interaction: discord.Interaction, target_user: discord.User, access_token: str, rarity_filter: str, page: int, msg_id: int):
    """Refresh the inventory message with a new rarity or page using cached inventory data when available."""
    await interaction.response.defer()
    rarity_key = normalize_inventory_rarity_filter(rarity_filter)
    content, file, error_text, total_pages, filename, rarity_counts = await build_inventory_image_payload(
        target_user, access_token, page=page, rarity_filter=rarity_key, use_cache=True
    )
    if error_text or not file:
        await interaction.followup.send("Failed to reload inventory.")
        return

    total_pages = total_pages or 1
    clamped_page = max(0, min(page, total_pages - 1))
    store_pagination_state(
        inventory_pagination_state,
        msg_id,
        (target_user, access_token, rarity_key, clamped_page, total_pages),
    )

    rarity_label_text = INVENTORY_RARITY_FILTERS[rarity_key]
    embed = discord.Embed(title=f"{content} ({rarity_label_text})", color=discord.Color.blurple())
    embed.set_image(url=f"attachment://{filename}")
    embed.set_footer(text=f"Page {clamped_page + 1} of {total_pages}")

    view = InventoryPaginationView(total_pages, rarity_key, rarity_counts)
    view.update_button_states(clamped_page)

    await interaction.message.edit(attachments=[file], embed=embed, view=view)
