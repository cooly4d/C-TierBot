"""Discord UI components: persistent Views/Selects for the leaderboard, queue-result
verify button, and inventory/shop pagination, plus the message-refresh handlers they trigger.

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
        self.initial_period = initial_period
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
    a verification link (visible only to them, in the same channel) instead of having to know /verify exists."""
    def __init__(self):
        super().__init__(timeout=None)

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


queue_result_view = None


def get_queue_result_view():
    global queue_result_view
    if queue_result_view is None:
        queue_result_view = QueueResultView()
    return queue_result_view


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
    """Persistent view for paginating through shop offers."""
    def __init__(self, total_pages: int):
        super().__init__(timeout=None)
        self.total_pages = total_pages

    def update_button_states(self, current_page: int):
        self.prev_button.disabled = current_page <= 0
        self.next_button.disabled = current_page >= self.total_pages - 1

    @discord.ui.button(label="◀ Previous", style=discord.ButtonStyle.primary, custom_id="market_prev")
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Handled by log_interaction
        pass

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.primary, custom_id="market_next")
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Handled by log_interaction
        pass


async def refresh_market_message(interaction: discord.Interaction, target_user: discord.User, access_token: str, mode: str, page: int, msg_id: int):
    """Refresh the market message with a new page."""
    await interaction.response.defer()
    content, file, filename, error_text, total_pages = await build_shop_image_payload(
        target_user, access_token, mode=mode, page=page
    )
    if error_text or not file:
        await interaction.followup.send("Failed to reload shop offers.")
        return

    clamped_page = max(0, min(page, total_pages - 1))
    store_pagination_state(market_pagination_state, msg_id, (target_user, access_token, mode, clamped_page, total_pages))

    embed = discord.Embed(title=content, color=discord.Color.blurple())
    embed.set_image(url=f"attachment://{filename}")
    embed.set_footer(text=f"Page {clamped_page + 1} of {total_pages}")

    view = ShopPaginationView(total_pages)
    view.update_button_states(clamped_page)

    await interaction.message.edit(attachments=[file], embed=embed, view=view)


async def refresh_inventory_message(interaction: discord.Interaction, target_user: discord.User, access_token: str, rarity_filter: str, page: int, msg_id: int):
    """Refresh the inventory message with a new page."""
    await interaction.response.defer()
    rarity_key = normalize_inventory_rarity_filter(rarity_filter)
    content, file, error_text, total_pages, filename, rarity_counts = await build_inventory_image_payload(
        target_user, access_token, page=page, rarity_filter=rarity_key
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
