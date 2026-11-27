"""Names on the public website: the same names the bot shows, never an ID.

A linked player is shown by their current display name in the main server (what a Discord mention of them shows
there), read from the bot's member cache, so no Discord API call is ever made for a web request. When that member
isn't in the main server's cache (they left, or the cache isn't ready yet), the site falls back to the name the bot
stored while calculating a main-server queue. Unlinked players keep their stored in-game name.

Every label and every public slug also passes a final check that keeps Discord IDs (and the bot's own
`Player {discord_id}` fallback) off the site.
"""
import re

UNKNOWN_PLAYER = "Unknown player"
_LONG_DIGITS = re.compile(r"\d{15,}")
_PLAYER_ID_FALLBACK = re.compile(r"^Player \d+$")
_SLUG = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_QUEUE_ID = re.compile(r"[0-9]{1,10}")


def public_queue_id(value) -> str | None:
    """A stored queue number as the site may show and link it (1-10 ASCII digits, as /queues/<n> accepts), or None
    for anything else a writer stored, so a stray Discord ID never reaches a page or a link."""
    text = str(value) if value is not None and not isinstance(value, bool) else ""
    return text if _QUEUE_ID.fullmatch(text) else None


def safe_label(label) -> str:
    """The label, or `Unknown player` when it's empty, holds a run of 15+ digits, or is `Player <digits>`."""
    text = str(label).strip() if label is not None else ""
    if not text or _LONG_DIGITS.search(text) or _PLAYER_ID_FALLBACK.match(text):
        return UNKNOWN_PLAYER
    return text


def safe_slug(slug) -> bool:
    """Whether a slug may appear in a public URL: plain URL characters (the whole string, so no trailing newline),
    not a "." or ".." path segment that a browser would resolve away, and no run of 15+ digits."""
    return (isinstance(slug, str) and bool(_SLUG.fullmatch(slug)) and slug not in (".", "..")
            and not _LONG_DIGITS.search(slug))


class NameResolver:
    """Current main-server display names from the bot's member cache, with the stored-name fallback.

    Reads only the main server's cache, so the site never picks a nickname from another server itself. `bot` may
    be None (the local preview), in which case every name is the stored fallback."""

    def __init__(self, bot, guild_id: int):
        self.bot = bot
        self.guild_id = guild_id

    def _guild(self):
        if self.bot is None:
            return None
        try:
            return self.bot.get_guild(self.guild_id)
        except Exception:
            return None

    @property
    def ready(self) -> bool:
        """Whether the member cache is complete; pages rendered before that aren't stored in the page cache. A guild
        Discord marks unavailable (an outage) isn't ready, even though its old cache still says chunked."""
        guild = self._guild()
        return (guild is not None and not getattr(guild, "unavailable", False)
                and bool(getattr(guild, "chunked", False)))

    def name(self, discord_id, fallback) -> str:
        """The current main-server name once the member cache is complete; until then (or without a bot) the
        stored name, so a page never mixes a half-loaded cache with stored names."""
        if discord_id is not None and self.ready:
            guild = self._guild()
            if guild is not None:
                try:
                    member = guild.get_member(int(discord_id))
                except (TypeError, ValueError):
                    member = None
                if member is not None:
                    return safe_label(getattr(member, "display_name", None) or getattr(member, "name", None))
        return safe_label(fallback)
