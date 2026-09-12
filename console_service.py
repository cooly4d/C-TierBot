"""Host console interface for C-TierBot.

Runs an interactive, non-blocking command listener on the host terminal
so the bot owner can send messages, inspect servers/channels, view metrics,
run database queries, and control the bot directly from stdin without
relying on Discord interactions.
"""
import asyncio
import os
import shlex
import sqlite3
import sys
import time
from datetime import datetime, timezone

import discord
from discord.ext import commands

from db import DB_PATH

_START_TIME = time.time()


def _format_table(headers: list[str], rows: list[list[str]]) -> str:
    """Renders a simple ASCII table for terminal display."""
    if not rows:
        return "(empty)"
    widths = [len(h) for h in headers]
    for row in rows:
        for idx, cell in enumerate(row):
            widths[idx] = max(widths[idx], len(str(cell)))

    header_line = " | ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    separator = "-+-".join("-" * widths[i] for i in range(len(headers)))
    row_lines = [
        " | ".join(str(cell).ljust(widths[i]) for i, cell in enumerate(row))
        for row in rows
    ]
    return f"{header_line}\n{separator}\n" + "\n".join(row_lines)


async def _handle_send(bot: commands.Bot, raw_args: str):
    """Usage: send <channel_id> <message>"""
    parts = raw_args.split(None, 1)
    if len(parts) < 2:
        print("[Console Error] Usage: send <channel_id> <message>")
        return

    channel_id_str, message_text = parts[0], parts[1]
    if not channel_id_str.isdigit():
        print(f"[Console Error] Invalid channel ID: {channel_id_str}")
        return

    channel_id = int(channel_id_str)
    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except Exception as exc:
            print(f"[Console Error] Could not find or fetch channel {channel_id}: {exc}")
            return

    if not isinstance(channel, (discord.TextChannel, discord.Thread, discord.VoiceChannel)):
        print(f"[Console Error] Channel {channel_id} ({type(channel).__name__}) cannot receive text messages.")
        return

    try:
        sent_msg = await channel.send(message_text)
        guild_name = channel.guild.name if hasattr(channel, "guild") and channel.guild else "Direct/Unknown"
        print(f"[Console] ✅ Sent message (ID: {sent_msg.id}) to #{channel.name} in '{guild_name}'")
    except Exception as exc:
        print(f"[Console Error] Failed to send message: {exc}")


async def _handle_dm(bot: commands.Bot, raw_args: str):
    """Usage: dm <user_id> <message>"""
    parts = raw_args.split(None, 1)
    if len(parts) < 2:
        print("[Console Error] Usage: dm <user_id> <message>")
        return

    user_id_str, message_text = parts[0], parts[1]
    if not user_id_str.isdigit():
        print(f"[Console Error] Invalid user ID: {user_id_str}")
        return

    user_id = int(user_id_str)
    user = bot.get_user(user_id)
    if user is None:
        try:
            user = await bot.fetch_user(user_id)
        except Exception as exc:
            print(f"[Console Error] Could not find or fetch user {user_id}: {exc}")
            return

    try:
        sent_msg = await user.send(message_text)
        print(f"[Console] ✅ DM sent (ID: {sent_msg.id}) to @{user.name} ({user.id})")
    except Exception as exc:
        print(f"[Console Error] Failed to send DM (user may have DMs closed or blocked): {exc}")


async def _handle_reply(bot: commands.Bot, raw_args: str):
    """Usage: reply <channel_id> <message_id> <message>"""
    parts = raw_args.split(None, 2)
    if len(parts) < 3:
        print("[Console Error] Usage: reply <channel_id> <message_id> <message>")
        return

    channel_id_str, message_id_str, reply_text = parts[0], parts[1], parts[2]
    if not channel_id_str.isdigit() or not message_id_str.isdigit():
        print("[Console Error] Both channel_id and message_id must be numeric IDs.")
        return

    channel_id = int(channel_id_str)
    message_id = int(message_id_str)

    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except Exception as exc:
            print(f"[Console Error] Could not fetch channel {channel_id}: {exc}")
            return

    try:
        target_message = await channel.fetch_message(message_id)
        sent_reply = await target_message.reply(reply_text)
        print(f"[Console] ✅ Replied to message {message_id} in #{channel.name} (Reply ID: {sent_reply.id})")
    except Exception as exc:
        print(f"[Console Error] Failed to fetch target message or send reply: {exc}")


def _handle_servers(bot: commands.Bot):
    """Lists all servers/guilds the bot is currently in."""
    guilds = bot.guilds
    if not guilds:
        print("[Console] The bot is not currently in any servers.")
        return

    print(f"\n[Console] Connected Servers ({len(guilds)} total):")
    headers = ["#", "Server Name", "Guild ID", "Members"]
    rows = []
    for idx, guild in enumerate(guilds, 1):
        rows.append([str(idx), guild.name, str(guild.id), str(guild.member_count)])
    print(_format_table(headers, rows))
    print()


async def _handle_channels(bot: commands.Bot, raw_args: str):
    """Usage: channels <guild_id>"""
    guild_id_str = raw_args.strip()
    if not guild_id_str.isdigit():
        print("[Console Error] Usage: channels <guild_id>")
        return

    guild_id = int(guild_id_str)
    guild = bot.get_guild(guild_id)
    if guild is None:
        try:
            guild = await bot.fetch_guild(guild_id)
        except Exception as exc:
            print(f"[Console Error] Could not find guild with ID {guild_id}: {exc}")
            return

    text_channels = [ch for ch in guild.channels if isinstance(ch, (discord.TextChannel, discord.Thread))]
    if not text_channels:
        print(f"[Console] No accessible text channels found in '{guild.name}'.")
        return

    print(f"\n[Console] Text Channels in '{guild.name}' ({len(text_channels)} channels):")
    headers = ["Name", "Channel ID", "Category"]
    rows = []
    for ch in text_channels:
        category_name = ch.category.name if getattr(ch, "category", None) else "(none)"
        rows.append([f"#{ch.name}", str(ch.id), category_name])
    print(_format_table(headers, rows))
    print()


def _handle_status(bot: commands.Bot):
    """Displays bot uptime, ping, and DB stats."""
    uptime_sec = int(time.time() - _START_TIME)
    uptime_str = f"{uptime_sec // 3600}h {(uptime_sec % 3600) // 60}m {uptime_sec % 60}s"
    latency_ms = round(bot.latency * 1000, 2) if bot.latency else "N/A"

    # Quick DB counts
    user_count = 0
    match_count = 0
    try:
        with sqlite3.connect(DB_PATH) as conn:
            user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            match_count = conn.execute("SELECT COUNT(*) FROM processed_matches").fetchone()[0]
    except Exception as exc:
        print(f"[Console Warning] DB query failed during status check: {exc}")

    print("\n[Console] === Bot Status ===")
    print(f"  User:         {bot.user} (ID: {bot.user.id if bot.user else 'Unknown'})")
    print(f"  Websocket:    {latency_ms} ms")
    print(f"  Uptime:       {uptime_str}")
    print(f"  Servers:      {len(bot.guilds)}")
    print(f"  Linked Users: {user_count}")
    print(f"  Matches Logged:{match_count}")
    print("===========================\n")


def _handle_sql(raw_args: str):
    """Usage: sql <query>"""
    query = raw_args.strip()
    if not query:
        print("[Console Error] Usage: sql <SQL_QUERY>")
        return

    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute(query)

            if query.strip().upper().startswith("SELECT"):
                rows = cursor.fetchall()
                if not rows:
                    print("[Console] Query returned 0 rows.")
                    return
                headers = [desc[0] for desc in cursor.description] if cursor.description else []
                string_rows = [[str(item) if item is not None else "NULL" for item in row] for row in rows]
                print(f"\n{_format_table(headers, string_rows)}\n({len(rows)} rows)\n")
            else:
                conn.commit()
                print(f"[Console] ✅ Query executed. Rows affected: {cursor.rowcount}")
    except Exception as exc:
        print(f"[Console SQL Error] {exc}")


async def _handle_sync(bot: commands.Bot):
    """Manually forces slash command synchronization."""
    print("[Console] Synchronizing slash commands with Discord...")
    try:
        synced = await bot.tree.sync()
        print(f"[Console] ✅ Successfully synced {len(synced)} slash commands.")
    except Exception as exc:
        print(f"[Console Error] Failed to sync slash commands: {exc}")


def _handle_help():
    """Prints command list and syntax."""
    help_text = """
=== C-TierBot Host Console Commands ===
  send <channel_id> <message>          - Send a message to a channel
  dm <user_id> <message>               - Send a private direct message to a user
  reply <channel_id> <msg_id> <text>   - Reply to a specific message in a channel
  servers                              - List all servers the bot is in
  channels <guild_id>                  - List all text channels in a server
  status                               - View bot uptime, ping, and DB metrics
  sql <query>                          - Execute an SQL query on leaderboard.db
  sync                                 - Force sync application slash commands
  clear                                - Clear the terminal screen
  help                                 - Show this help menu
  exit / quit                          - Gracefully disconnect the bot and exit
========================================
"""
    print(help_text)


async def execute_console_command(bot: commands.Bot, line: str):
    """Parses and routes a single line of console input."""
    trimmed = line.strip()
    if not trimmed:
        return

    parts = trimmed.split(None, 1)
    command_name = parts[0].lower()
    raw_args = parts[1] if len(parts) > 1 else ""

    if command_name == "help":
        _handle_help()
    elif command_name == "send":
        await _handle_send(bot, raw_args)
    elif command_name == "dm":
        await _handle_dm(bot, raw_args)
    elif command_name == "reply":
        await _handle_reply(bot, raw_args)
    elif command_name in ("servers", "guilds"):
        _handle_servers(bot)
    elif command_name == "channels":
        await _handle_channels(bot, raw_args)
    elif command_name == "status":
        _handle_status(bot)
    elif command_name == "sql":
        _handle_sql(raw_args)
    elif command_name == "sync":
        await _handle_sync(bot)
    elif command_name == "clear":
        os.system("cls" if os.name == "nt" else "clear")
    elif command_name in ("exit", "quit", "stop"):
        print("[Console] Shutting down bot gracefully...")
        await bot.close()
    else:
        print(f"[Console] Unknown command: '{command_name}'. Type 'help' for available commands.")


async def start_console_listener(bot: commands.Bot):
    """Background task reading stdin asynchronously without blocking the event loop."""
    await bot.wait_until_ready()

    # If stdin is completely unavailable (e.g. background daemon/systemd where stdin is
    # /dev/null or closed), readline returns immediate EOF which would spin a CPU loop.
    # We do NOT check isatty() because stdin is still readable in SSH, screen, and tmux
    # sessions even though those aren't technically TTYs.
    if sys.stdin is None or sys.stdin.closed:
        return

    print("\n" + "=" * 50)
    print(" [Host Console] Ready! Type 'help' for available commands.")
    print("=" * 50 + "\n")

    while not bot.is_closed():
        try:
            # sys.stdin.readline is run in a separate worker thread via asyncio.to_thread
            # so the Discord event loop continues running smoothly without interruption.
            line = await asyncio.to_thread(sys.stdin.readline)
            if not line:
                # EOF reached (e.g. stream closed)
                await asyncio.sleep(1)
                continue

            await execute_console_command(bot, line)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            print(f"[Console Loop Error] {exc}")
            await asyncio.sleep(0.5)
