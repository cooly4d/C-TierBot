"""Operator panel console, queue list win/loss tints, clickable timeline dots and the logo (plan r14)."""
import asyncio
import logging
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from aiohttp.test_utils import TestClient, TestServer

import console_service as cs
import panel_sql
import web_admin
import web_server
try:
    import web_fixture as f
    from test_web_admin import ORIGIN, config
except ImportError:  # run as tests.test_web_console
    from tests import web_fixture as f
    from tests.test_web_admin import ORIGIN, config

STATIC = Path(web_server.__file__).resolve().parent / "web" / "static"
FORM = {"Origin": ORIGIN, "Content-Type": "application/x-www-form-urlencoded"}


def token_db(tmp: str) -> str:
    path = os.path.join(tmp, "t.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE users (discord_id INTEGER PRIMARY KEY, access_token TEXT NOT NULL, slug TEXT, username TEXT)")
    conn.execute("INSERT INTO users VALUES (1, ?, 'hex', 'Hex')", (f.CANARY_TOKEN,))
    conn.execute("CREATE TABLE queue_results (guild_id, match_id, snapshot, saved_at)")
    conn.executemany("INSERT INTO queue_results VALUES (?, ?, ?, 'now')",
                     [(i, str(i), "line one\nline two" if i == 0 else "x" * 500) for i in range(10)])
    conn.commit()
    conn.close()
    return path


class ReadOnlySqlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = token_db(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def sql(self, query, **kw):
        return cs.run_readonly_sql(self.path, query, **kw)

    def test_the_access_token_reads_as_null_however_it_is_asked_for(self):
        for query in ("SELECT * FROM users", "SELECT access_token FROM users",
                      "SELECT discord_id FROM users WHERE access_token LIKE 'C%'",
                      "SELECT substr(access_token, 1, 6) AS t FROM users", "SELECT (SELECT access_token FROM users) AS t",
                      "SELECT hex(access_token) FROM users", "SELECT u.ACCESS_TOKEN FROM USERS u",
                      "SELECT length(access_token) FROM users"):
            out = self.sql(query)
            self.assertNotIn(f.CANARY_TOKEN, out, query)
            self.assertNotIn(f.CANARY_TOKEN.encode().hex().upper(), out, query)
            self.assertIn("users.access_token is hidden", out, query)
        self.assertIn("0 rows", self.sql("SELECT discord_id FROM users WHERE access_token LIKE 'C%'"))
        self.assertIn("hex", self.sql("SELECT slug FROM users"))
        self.assertNotIn("hidden", self.sql("SELECT slug FROM users"))

    def test_every_write_schema_change_attach_and_setting_is_refused(self):
        for query in ("INSERT INTO queue_results VALUES (1, 2, 3, 4)", "UPDATE users SET slug = 'x'",
                      "DELETE FROM queue_results", "REPLACE INTO queue_results VALUES (1, 2, 3, 4)",
                      "CREATE TABLE z (a)", "CREATE TEMP TABLE z (a)",
                      "CREATE VIEW v AS SELECT access_token FROM users", "DROP TABLE queue_results",
                      "CREATE TRIGGER t AFTER UPDATE ON users BEGIN SELECT 1; END", "ALTER TABLE queue_results ADD c",
                      "ATTACH DATABASE 'x.db' AS x", "PRAGMA journal_mode = WAL", "PRAGMA query_only = OFF",
                      "BEGIN", "VACUUM", "SELECT load_extension('x')", "SELECT * FROM sqlite_dbpage"):
            out = self.sql(query)
            self.assertTrue(out.startswith("[Console SQL Error]"), (query, out))
        conn = sqlite3.connect(self.path)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM queue_results").fetchone()[0], 10)
        self.assertEqual(conn.execute("SELECT slug FROM users").fetchone()[0], "hex")
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0], 2)
        conn.close()
        self.assertIn("only reads", self.sql("UPDATE users SET slug = 'x'"))

    def test_reads_schema_lookups_and_one_statement_only(self):
        self.assertIn("access_token", self.sql("PRAGMA table_info(users)"))  # the column's name, not its value
        self.assertIn("access_token", self.sql("SELECT name FROM pragma_table_info('users')"))
        self.assertIn("one statement", self.sql("SELECT 1; SELECT 2"))
        self.assertIn("(1 row)", self.sql("SELECT 1;"))
        self.assertIn("syntax error", self.sql("garbage"))
        self.assertIn("Usage: sql", self.sql("   "))

    def test_time_rows_cells_and_value_size_are_capped(self):
        start = time.monotonic()
        out = self.sql("WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT COUNT(*) FROM c",
                       seconds=0.2)
        self.assertIn("Stopped after 0.2 s", out)
        self.assertLess(time.monotonic() - start, 2)
        out = self.sql("SELECT guild_id FROM queue_results", max_rows=4)
        self.assertIn("(first 4 rows shown)", out)
        out = self.sql("SELECT snapshot FROM queue_results")
        self.assertIn("line one line two", out)  # a multi-line value stays on its row
        self.assertIn("x" * (panel_sql.MAX_CELL - 1) + "…", out)
        self.assertNotIn("x" * panel_sql.MAX_CELL, out)
        self.assertIn("too big", self.sql("SELECT length(randomblob(5000000))"))


class PanelLinesTests(unittest.TestCase):
    def test_terminal_prefixes_become_kinds(self):
        lines = cs.panel_lines("\n[Console] ✅ Sent message (ID: 5)\n[Console Error] bad\n[Console SQL Error] nope\n"
                               "[Console Warning] careful\n[Console] plain\ntable | row\n")
        self.assertEqual(lines, [
            {"text": "Sent message (ID: 5)", "kind": "ok"}, {"text": "Error: bad", "kind": "err"},
            {"text": "SQL error: nope", "kind": "err"}, {"text": "careful", "kind": "warn"},
            {"text": "plain", "kind": ""}, {"text": "table | row", "kind": ""}])
        cut = cs.panel_lines("y" * 50, limit=10)
        self.assertEqual(cut[0]["text"], "y" * 10)
        self.assertEqual(cut[-1]["kind"], "warn")


class PanelConsoleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = token_db(self.tmp.name)
        self.shutdown = MagicMock()
        self.console = cs.PanelConsole(MagicMock(), self.shutdown, db_path=self.path)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    def text(self, lines):
        return "\n".join(line["text"] for line in lines)

    async def test_help_lists_every_command(self):
        out = self.text(await self.console.run("help"))
        for usage, _ in cs.PANEL_COMMANDS:
            self.assertIn(usage, out)
        self.assertEqual(await self.console.run("   "), [])

    async def test_stop_asks_first_then_stops_once_after_the_reply(self):
        out = await self.console.run("stop")
        self.assertIn("stop confirm", self.text(out))
        await asyncio.sleep(cs.PANEL_STOP_DELAY + 0.1)
        self.shutdown.assert_not_called()
        with self.assertLogs("console_service", level="WARNING"):
            out = await self.console.run("STOP confirm")
        self.assertIn("Stopping the bot", self.text(out))
        self.assertTrue(self.console.stop_requested)
        await asyncio.sleep(cs.PANEL_STOP_DELAY + 0.1)
        self.shutdown.assert_not_called()  # nothing until the website says the reply has been sent
        self.console.stop_after_reply()
        self.shutdown.assert_not_called()
        await asyncio.sleep(cs.PANEL_STOP_DELAY + 0.1)
        self.shutdown.assert_called_once()
        self.console.stop_after_reply()
        self.assertIn("Already stopping", self.text(await self.console.run("stop confirm")))
        await asyncio.sleep(cs.PANEL_STOP_DELAY + 0.1)
        self.shutdown.assert_called_once()

    async def test_a_stop_whose_reply_is_never_reported_still_happens(self):
        with patch.object(cs, "PANEL_STOP_FALLBACK", 0.1), self.assertLogs("console_service", level="WARNING"):
            await self.console.run("stop confirm")
        await asyncio.sleep(0.1 + cs.PANEL_STOP_DELAY + 0.2)
        self.shutdown.assert_called_once()

    async def test_exit_quit_and_clear_never_stop_the_bot(self):
        for command in ("exit", "quit", "clear"):
            await self.console.run(command)
        await asyncio.sleep(cs.PANEL_STOP_DELAY + 0.1)
        self.shutdown.assert_not_called()
        self.assertFalse(self.console.bot.close.called)

    async def test_sql_is_the_read_only_one(self):
        out = self.text(await self.console.run("sql SELECT * FROM users"))
        self.assertNotIn(f.CANARY_TOKEN, out)
        self.assertIn("hidden", out)
        self.assertIn("only reads", self.text(await self.console.run("sql DELETE FROM users")))

    async def test_other_commands_run_the_console_handlers_and_come_back_as_lines(self):
        channel = MagicMock(spec=discord.TextChannel)
        channel.name = "general"
        channel.guild.name = "Guild"
        channel.send = AsyncMock(return_value=MagicMock(id=42))
        self.console.bot.get_channel.return_value = channel
        lines = await self.console.run("send 112233 Hello <b>there</b>")
        channel.send.assert_awaited_once_with("Hello <b>there</b>")
        self.assertEqual(lines[0]["kind"], "ok")
        self.assertNotIn("✅", lines[0]["text"])
        self.assertEqual(self.text(await self.console.run("send nope hi")), "Error: Invalid channel ID: nope")

    async def test_one_command_at_a_time_and_a_slow_one_times_out(self):
        gate = asyncio.Event()

        async def slow(_bot, _line):
            await gate.wait()

        self.console.timeout = 5
        with patch.object(cs, "execute_console_command", side_effect=slow):
            first = asyncio.ensure_future(self.console.run("winrate"))
            await asyncio.sleep(0.05)
            self.assertTrue(self.console.busy)
            self.assertIn("still running", self.text(await self.console.run("status")))
            gate.set()
            await first
        self.assertFalse(self.console.busy)

        async def never(_bot, _line):
            await asyncio.sleep(60)

        self.console.timeout = 0.1
        with patch.object(cs, "execute_console_command", side_effect=never):
            out = self.text(await self.console.run("queue 4821"))
        self.assertIn("Stopped waiting after 0.1 s", out)
        self.assertFalse(self.console.busy)


class FakeConsole:
    reference = cs.PANEL_COMMANDS
    max_line = 50

    def __init__(self):
        self.seen = []

    async def run(self, line):
        self.seen.append(line)
        return [{"text": f"ran {line}", "kind": ""}, {"text": "<b>bad</b>", "kind": "err"}]


class ConsoleHttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "site.db")
        f.standard_db(self.path).close()
        self.console = FakeConsole()
        self.app = web_server.create_app(f.HOME, self.path, admin_config=config(), log_dir=self.tmp.name,
                                         console=self.console)
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        site = self.app["site"]
        self.cookie = {"Cookie": f"{web_admin.COOKIE_NAME}={web_admin.make_session(site.admin)}"}

    async def asyncTearDown(self):
        await self.client.close()
        site = self.app["site"]
        site.db.executor.shutdown(wait=True)
        site.checker.executor.shutdown(wait=True)
        self.tmp.cleanup()

    async def post(self, command, headers=None, **kw):
        h = {**FORM, **self.cookie, **(headers or {})}
        return await self.client.post("/admin/console", data=f"command={command}", headers=h,
                                      allow_redirects=False, **kw)

    async def test_signed_out_cross_origin_and_non_form_posts_run_nothing(self):
        resp = await self.client.post("/admin/console", data="command=status", headers=FORM, allow_redirects=False)
        self.assertEqual((resp.status, resp.headers["Location"]), (303, "/admin/login"))
        self.assertEqual((await self.post("status", {"Origin": "https://evil.example"})).status, 403)
        self.assertEqual((await self.post("status", {"Origin": ""})).status, 403)
        self.assertEqual((await self.post("status", {"Content-Type": "text/plain"})).status, 400)
        self.assertEqual((await self.post("x" * 9000)).status, 400)
        self.assertEqual(self.console.seen, [])

    async def test_json_for_the_script_and_the_panel_page_without_it(self):
        resp = await self.post("status+now", {"Accept": "application/json"})
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.headers["Cache-Control"], "no-store")
        data = await resp.json()
        self.assertEqual(data["command"], "status now")
        self.assertEqual(data["lines"][0], {"text": "ran status now", "kind": ""})
        posted = await self.post("%3Cscript%3Ealert(1)%3C%2Fscript%3E")
        self.assertEqual(posted.status, 303)  # POST, then GET: a reload never posts the command again
        location = posted.headers["Location"]
        self.assertRegex(location, r"^/admin/\?run=[A-Za-z0-9_-]{16,64}#console$")
        page = await self.client.get(location.split("#")[0], headers=self.cookie, allow_redirects=False)
        body = await page.text()
        self.assertEqual(page.status, 200)
        self.assertIn('class="console-entry"', body)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", body)
        self.assertIn('<span class="k-err">&lt;b&gt;bad&lt;/b&gt;</span>', body)
        self.assertNotIn("<script>alert(1)", body)
        self.assertEqual(self.console.seen, ["status now", "<script>alert(1)</script>"])

    async def test_too_long_commands_are_refused_and_only_the_name_is_logged(self):
        data = await (await self.post("send+123+" + "y" * 60, {"Accept": "application/json"})).json()
        self.assertIn("at most 50 characters", data["lines"][0]["text"])
        self.assertEqual(self.console.seen, [])
        with self.assertLogs("web_server", level=logging.INFO) as logs:
            await self.post("send+123+secret+message+text", {"Accept": "application/json"})
        text = "\n".join(logs.output)
        self.assertIn("admin console: send", text)
        self.assertNotIn("secret message text", text)

    async def test_the_panel_shows_the_console_and_get_goes_to_it(self):
        resp = await self.client.get("/admin/", headers=self.cookie, allow_redirects=False)
        body = await resp.text()
        for part in ('id="console"', 'action="/admin/console#console"', 'name="command"', 'maxlength="50"',
                     "data-console-tools hidden", "All commands",
                     "queue &lt;match_id&gt; [guild_id]"):
            self.assertIn(part, body)
        resp = await self.client.get("/admin/console", headers=self.cookie, allow_redirects=False)
        self.assertEqual((resp.status, resp.headers["Location"]), (303, "/admin/#console"))

    async def test_without_a_bot_the_console_says_so(self):
        await self.client.close()
        self.app["site"].db.executor.shutdown(wait=True)
        self.app["site"].checker.executor.shutdown(wait=True)
        self.app = web_server.create_app(f.HOME, self.path, admin_config=config(), log_dir=self.tmp.name)
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        data = await (await self.post("status", {"Accept": "application/json"})).json()
        self.assertIn("off in the local preview", data["lines"][0]["text"])
        body = await (await self.client.get("/admin/", headers=self.cookie)).text()
        self.assertIn("off in the local preview", body)
        self.assertNotIn('name="command"', body)


class StartWebsiteTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_bot_hands_the_site_a_panel_console(self):
        import leaderboard_bot as app
        with patch.object(app, "WEB_PORT", 8080), \
                patch("web_server.start_web_server", new=AsyncMock(return_value=None)) as start:
            await app.start_website()
        console = start.await_args.kwargs["console"]
        self.assertIsInstance(console, cs.PanelConsole)
        self.assertIs(console.shutdown, app.request_shutdown)


class PublicPageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "site.db")
        conn = f.standard_db(self.path)
        f.add_row(conn, f.HOME, 4600, name="Nobody", winner=None, score=None)
        conn.commit()
        conn.close()
        self.app = web_server.create_app(f.HOME, self.path, clock=lambda: f.ms(2026, 11, 26) / 1000)
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.app["site"].db.executor.shutdown(wait=True)
        self.tmp.cleanup()

    async def text(self, url):
        resp = await self.client.get(url)
        self.assertEqual(resp.status, 200, url)
        return await resp.text()

    def row(self, body, match_id):
        return body.split(f'href="/queues/{match_id}"', 1)[1].split("</li>", 1)[0]

    async def test_the_queue_list_tints_the_winner_instead_of_saying_it(self):
        body = await self.text("/queues")
        won = self.row(body, 4821)  # team 1 won 4-2
        self.assertNotIn(" wins", won)
        self.assertIn('<span class="sr-only">Team 1 won</span>', won)
        self.assertLess(won.index("q-team q-team-win"), won.index("q-team q-team-lose"))
        lost = self.row(body, 4700)  # team 2 won 1-4
        self.assertLess(lost.index("q-team q-team-lose"), lost.index("q-team q-team-win"))
        unknown = self.row(body, 4600)
        self.assertIn("Result unknown", unknown)
        self.assertNotIn("q-team-win", unknown)
        self.assertIn("Team 1 won", self.row(await self.text("/"), 4821))  # home uses the same row
        css = (STATIC / "site.css").read_text(encoding="utf-8")
        self.assertIn(".q-team-win { background: color-mix(in srgb, var(--win) 9%, transparent); }", css)

    async def test_timeline_dots_are_links_to_their_games_in_game_order(self):
        body = await self.text("/queues/4821?game=3")
        timeline = body.split('class="timeline-grid', 1)[1].split("</figure>", 1)[0]
        self.assertNotIn('class="timeline-grid" aria-hidden', body)
        dots = [part.split(">", 1)[0] for part in timeline.split('<a class="tl-dot')[1:]]
        self.assertEqual(len(dots), 6)
        for n, dot in enumerate(dots, start=1):
            self.assertIn(f'href="?game={n}#games"', dot)
            self.assertIn(f'data-game="{n}"', dot)
            self.assertIn(f'aria-label="Game {n}, Team', dot)
            self.assertEqual('aria-current="true"' in dot, n == 3)
        self.assertIn("is-selected", dots[2])
        js = (STATIC / "site.js").read_text(encoding="utf-8")
        self.assertIn('document.querySelector("[data-timeline]")?.addEventListener("click"', js)
        self.assertIn("revealGame()", js)

    async def test_the_logo_is_in_the_header_and_the_browser_icons(self):
        body = await self.text("/")
        self.assertIn('<img class="brand-mark" src="/static/logo-96.webp" alt="" width="32" height="32">', body)
        self.assertIn('href="/static/favicon-32.png"', body)
        self.assertIn('rel="apple-touch-icon" href="/static/apple-touch-icon.png"', body)
        for name in ("logo-96.webp", "favicon-32.png", "apple-touch-icon.png"):
            resp = await self.client.get(f"/static/{name}")
            self.assertEqual(resp.status, 200, name)
        self.assertFalse((STATIC / "favicon.svg").exists())
        self.assertTrue((Path(web_server.__file__).resolve().parent / "assets" / "logo.png").is_file())


if __name__ == "__main__":
    unittest.main()
