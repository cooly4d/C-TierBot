"""Regression tests for code inspection 4: INS4-01..14 (the changes since inspection 3) and the security review's
findings in the website and the panel console (SEC-11..15). INS4-11 (keyboard focus after a timeline dot) and
INS4-12 (Clear while a command runs) are also checked in Chrome; see the review log."""
import asyncio
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp.test_utils import TestClient, TestServer

import console_service as cs
import db
import panel_sql
import queue_stats_service as qss
import web_admin
import web_queries as q
import web_server
from web_names import NameResolver
try:
    import web_fixture as f
    from test_web_admin import ORIGIN, config
    from test_web_console import FORM, FakeConsole, token_db
    from test_web_server import SiteTestCase, fake_bot
except ImportError:  # run as tests.test_web_inspection4_fixes
    from tests import web_fixture as f
    from tests.test_web_admin import ORIGIN, config
    from tests.test_web_console import FORM, FakeConsole, token_db
    from tests.test_web_server import SiteTestCase, fake_bot

ROOT = Path(__file__).resolve().parent.parent
RUN = "r" * 20


# --- INS4-01, INS4-06, SEC-13, SEC-14: the console's HTTP side -------------------------------------------------

class StopConsole(FakeConsole):
    def __init__(self):
        super().__init__()
        self.stop_requested = False
        self.replies_sent = 0

    async def run(self, line):
        self.seen.append(line)
        if line == "stop confirm":
            self.stop_requested = True
        return [{"text": f"ran {line}", "kind": ""}]

    def stop_after_reply(self):
        self.replies_sent += 1


class ConsoleRunTests(unittest.IsolatedAsyncioTestCase):
    console_class = FakeConsole

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "site.db")
        f.standard_db(self.path).close()
        self.console = self.console_class()
        self.app = web_server.create_app(f.HOME, self.path, admin_config=config(), log_dir=self.tmp.name,
                                         console=self.console)
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        self.site = self.app["site"]
        self.cookie = {"Cookie": f"{web_admin.COOKIE_NAME}={web_admin.make_session(self.site.admin)}"}

    async def asyncTearDown(self):
        await self.client.close()
        self.site.db.executor.shutdown(wait=True)
        self.site.checker.executor.shutdown(wait=True)
        self.tmp.cleanup()

    async def post(self, body, cookie=None, json_reply=False):
        headers = {**FORM, **(cookie or self.cookie)}
        if json_reply:
            headers["Accept"] = "application/json"
        return await self.client.post("/admin/console", data=body, headers=headers, allow_redirects=False)

    async def panel(self, url="/admin/", cookie=None):
        resp = await self.client.get(url, headers=cookie or self.cookie, allow_redirects=False)
        return resp, await resp.text()

    async def test_a_resubmitted_form_never_runs_the_command_twice(self):
        first = await self.post(f"command=dm+1+hello&run={RUN}")
        again = await self.post(f"command=dm+1+hello&run={RUN}")  # Back and resubmit, or a double click
        self.assertEqual((first.status, again.status), (303, 303))
        self.assertEqual(first.headers["Location"], f"/admin/?run={RUN}#console")
        self.assertEqual(again.headers["Location"], first.headers["Location"])
        data = await (await self.post(f"command=dm+1+hello&run={RUN}", json_reply=True)).json()
        self.assertEqual(data["lines"][0]["text"], "ran dm 1 hello")
        self.assertEqual(self.console.seen, ["dm 1 hello"])
        _, body = await self.panel(f"/admin/?run={RUN}")
        self.assertIn("ran dm 1 hello", body)

    async def test_another_session_never_sees_or_reuses_a_run(self):
        await self.post(f"command=dm+1+hello&run={RUN}")
        other = {"Cookie": f"{web_admin.COOKIE_NAME}="
                           f"{web_admin.make_session(self.site.admin, now=time.time() - 60)}"}
        _, body = await self.panel(f"/admin/?run={RUN}", cookie=other)
        self.assertNotIn("ran dm 1 hello", body)
        resp = await self.post(f"command=status&run={RUN}", cookie=other)
        self.assertNotIn(RUN, resp.headers["Location"])  # its own run, under a new ID
        self.assertEqual(self.console.seen, ["dm 1 hello", "status"])

    async def test_a_busy_panel_after_a_command_never_repeats_it(self):
        resp = await self.post(f"command=dm+1+hello&run={RUN}")
        location = resp.headers["Location"].split("#")[0]
        real_run = self.site.db.run
        calls = []

        async def busy_once(*args):
            calls.append(1)
            if len(calls) == 1:
                raise web_server.Busy()
            return await real_run(*args)

        with patch.object(self.site.db, "run", side_effect=busy_once):
            busy, _ = await self.panel(location)
            self.assertEqual(busy.status, 503)
            ok, body = await self.panel(location)  # the retry is a GET: nothing runs again
        self.assertEqual(ok.status, 200)
        self.assertIn("ran dm 1 hello", body)
        self.assertEqual(self.console.seen, ["dm 1 hello"])

    async def test_the_panel_form_carries_a_fresh_run_id(self):
        ids = []
        for _ in range(2):
            _, body = await self.panel()
            ids.append(re.search(r'name="run" value="([A-Za-z0-9_-]+)"', body).group(1))
        self.assertNotEqual(ids[0], ids[1])
        self.assertTrue(all(web_server._RUN_ID.fullmatch(i) for i in ids))
        resp = await self.post("command=status&run=bad id")  # an invalid ID gets a fresh one
        self.assertRegex(resp.headers["Location"], r"^/admin/\?run=[A-Za-z0-9_-]{16,64}#console$")

    async def test_slow_or_crowded_form_bodies_are_turned_away(self):
        server = self.client.server
        with patch.object(web_server, "FORM_READ_SECONDS", 0.3):
            reader, writer = await asyncio.open_connection(server.host, server.port)
            writer.write(b"POST /admin/login HTTP/1.1\r\nHost: x\r\nOrigin: " + ORIGIN.encode() +
                         b"\r\nContent-Type: application/x-www-form-urlencoded\r\nContent-Length: 40\r\n\r\npassword=")
            await writer.drain()
            status = await asyncio.wait_for(reader.readline(), 3)
            writer.close()
        self.assertIn(b" 408 ", status)
        self.assertEqual(self.site.form_readers, 0)
        self.site.form_readers = web_server.MAX_FORM_READERS
        resp = await self.client.post("/admin/login", data="password=x", headers=FORM, allow_redirects=False)
        self.assertEqual(resp.status, 503)
        self.site.form_readers = 0


class StopReplyTests(ConsoleRunTests):
    console_class = StopConsole

    async def test_stop_confirm_replies_before_the_bot_is_told(self):
        resp = await self.post(f"command=stop+confirm&run={RUN}")
        body = await resp.text()
        self.assertEqual(resp.status, 200)  # a page of its own, not a redirect to a panel that's going away
        self.assertIn("Stopping the bot", body)
        self.assertEqual(self.console.replies_sent, 1)

    async def test_the_scripts_stop_reply_too(self):
        data = await (await self.post(f"command=stop+confirm&run={RUN}", json_reply=True)).json()
        self.assertEqual(data["lines"][0]["text"], "ran stop confirm")
        self.assertEqual(self.console.replies_sent, 1)

    # The base class's tests run here too, with a console that never stops.


# --- INS4-05, SEC-12: panel sql in its own process ---------------------------------------------------------

class PanelSqlWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = token_db(self.tmp.name)
        self.console = cs.PanelConsole(MagicMock(), MagicMock(), db_path=self.path)
        self.procs = []
        real = asyncio.create_subprocess_exec

        async def spawn(*args, **kwargs):
            proc = await real(*args, **kwargs)
            self.procs.append(proc)
            return proc

        self.enterContext(patch.object(cs.asyncio, "create_subprocess_exec", side_effect=spawn))

    async def asyncTearDown(self):
        self.tmp.cleanup()

    def worker(self, source: str) -> Path:
        path = Path(self.tmp.name) / "worker.py"
        path.write_text(source, encoding="utf-8")
        return path

    def text(self, lines):
        return "\n".join(line["text"] for line in lines)

    async def test_the_real_worker_answers(self):
        out = self.text(await self.console.run("sql SELECT count(*) AS n, max(slug) FROM users"))
        self.assertIn("(1 row)", out)
        self.assertIn("hex", out)
        self.assertEqual(self.procs[0].returncode, 0)

    async def test_the_worker_gets_no_secrets(self):
        script = self.worker("import json, os, sys\nsys.stdin.read()\nprint('|'.join(sorted(os.environ)))\n")
        with patch.object(cs, "_SQL_WORKER", script), \
                patch.dict(os.environ, {"DISCORD_TOKEN": "x", "WEB_SESSION_SECRET": "y", "SURVEV_CLIENT_SECRET": "z"}):
            out = self.text(await self.console.run("sql SELECT 1"))
        names = set(out.strip().split("|"))
        self.assertFalse(names & {"DISCORD_TOKEN", "WEB_SESSION_SECRET", "SURVEV_CLIENT_SECRET"}, names)
        self.assertTrue(names <= set(cs._SQL_WORKER_ENV), names)

    async def test_a_worker_past_its_time_is_killed_and_the_console_frees_up(self):
        script = self.worker("import time\ntime.sleep(60)\n")
        start = time.monotonic()
        with patch.object(cs, "_SQL_WORKER", script), patch.object(cs, "PANEL_SQL_SECONDS", 0.2), \
                patch.object(cs, "PANEL_SQL_GRACE", 0.5):
            out = self.text(await self.console.run("sql SELECT 1"))
        self.assertIn("Stopped after 0.2 s", out)
        self.assertLess(time.monotonic() - start, 5)
        self.assertIsNotNone(self.procs[0].returncode)  # the process is gone, not left running
        self.assertFalse(self.console.busy)
        self.assertIsNone(self.console._sql_proc)

    async def test_a_cancelled_request_kills_the_worker_before_the_console_frees_up(self):
        script = self.worker("import time\ntime.sleep(60)\n")
        with patch.object(cs, "_SQL_WORKER", script):
            task = asyncio.ensure_future(self.console.run("sql SELECT 1"))
            while self.console._sql_proc is None:
                await asyncio.sleep(0.02)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertIsNotNone(self.procs[0].returncode)
        self.assertFalse(self.console.busy)

    async def test_website_shutdown_ends_a_running_worker(self):
        script = self.worker("import time\ntime.sleep(60)\n")
        with patch.object(cs, "_SQL_WORKER", script):
            task = asyncio.ensure_future(self.console.run("sql SELECT 1"))
            while self.console._sql_proc is None:
                await asyncio.sleep(0.02)
            start = time.monotonic()
            server = web_server.WebServer.__new__(web_server.WebServer)
            server.site = SimpleNamespace(db=SimpleNamespace(begin_stop=lambda: None), checker=None,
                                          console=self.console)
            server.begin_stop()
            out = self.text(await task)
        self.assertLess(time.monotonic() - start, 3)
        self.assertIn("stopped before it finished", out)

    def test_linux_limits_are_set_on_the_worker(self):
        calls = {}
        fake = SimpleNamespace(RLIM_INFINITY=-1, RLIMIT_CPU=0, RLIMIT_AS=1, RLIMIT_FSIZE=2, RLIMIT_NOFILE=3,
                               getrlimit=lambda which: (-1, -1),
                               setrlimit=lambda which, value: calls.__setitem__(which, value))
        with patch.dict(sys.modules, {"resource": fake}):
            panel_sql.limit_resources(2.0)
        self.assertEqual(calls, {0: (4, 4), 1: (panel_sql.LIMIT_MEMORY,) * 2, 2: (panel_sql.LIMIT_FILE,) * 2,
                                 3: (panel_sql.LIMIT_FILES_OPEN,) * 2})


# --- SEC-11: only the bot's own tables, its listed columns and SQLite's built-in functions -------------------

class PanelSqlAllowListTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = token_db(self.tmp.name)
        conn = sqlite3.connect(self.path)
        conn.execute("CREATE TABLE users_copy AS SELECT * FROM users")  # a maintenance copy made over SSH
        conn.execute("CREATE VIEW tokens AS SELECT access_token FROM users")
        conn.execute("ALTER TABLE users ADD COLUMN later TEXT")
        conn.execute("UPDATE users SET later = 'LATER-VALUE'")
        conn.commit()
        conn.close()

    def tearDown(self):
        self.tmp.cleanup()

    def sql(self, query):
        out = panel_sql.run_readonly_sql(self.path, query)
        self.assertNotIn(f.CANARY_TOKEN, out, query)
        return out

    def test_tables_outside_the_list_are_refused(self):
        for query in ("SELECT * FROM users_copy", "SELECT access_token FROM users_copy", "SELECT * FROM tokens",
                      "SELECT u.slug FROM users u JOIN users_copy c ON c.discord_id = u.discord_id"):
            out = self.sql(query)
            self.assertIn("can't use table", out, query)
        for query in ("SELECT * FROM sqlite_dbpage", "SELECT * FROM dbstat"):
            self.assertTrue(self.sql(query).startswith("[Console SQL Error]"), query)  # refused, or not built in
        self.assertIn("(1 row)", self.sql("SELECT count(*) FROM users_copy"))  # a row count, never a value

    def test_functions_outside_the_list_are_refused(self):
        for query in ("SELECT readfile('x')", "SELECT load_extension('x')", "SELECT fts3_tokenizer('simple')",
                      "SELECT sqlite_compileoption_used('ENABLE_DBSTAT_VTAB')", "SELECT edit('x')"):
            out = self.sql(query)
            self.assertTrue(out.startswith("[Console SQL Error]"), (query, out))
        self.assertIn("can't use function sqlite_compileoption_used()",
                      self.sql("SELECT sqlite_compileoption_used('ENABLE_DBSTAT_VTAB')"))
        out = self.sql("SELECT lower(slug), printf('%d', discord_id), json_extract('{\"a\":1}', '$.a'), "
                       "strftime('%Y', 'now'), row_number() OVER (), count(*) OVER () FROM users")
        self.assertIn("(1 row)", out)

    def test_joins_the_authorizer_cant_see_are_refused(self):
        # SQLite doesn't show the authorizer the columns USING and NATURAL JOIN compare: an equality test on the
        # token, or a join through an unlisted table, would go unseen.
        guess = f.CANARY_TOKEN
        for query in (f"SELECT slug FROM users JOIN (SELECT '{guess}' AS access_token) USING (access_token)",
                      f"SELECT slug FROM users NATURAL JOIN (SELECT '{guess}' AS access_token)",
                      "SELECT u.slug FROM users u JOIN users_copy c USING (discord_id)",
                      "select slug from users natural   join users_copy",
                      "SELECT slug FROM users/* a comment */NATURAL JOIN users_copy"):
            out = panel_sql.run_readonly_sql(self.path, query)
            self.assertIn("aren't available in the panel's sql", out, query)
            self.assertNotIn("hex", out, query)
        # The words inside quotes or comments are fine.
        for query in ("SELECT 'using natural' AS words", 'SELECT 1 AS "using"', "SELECT 1 -- using\n",
                      "SELECT 'it''s using' AS w", "SELECT [natural] FROM (SELECT 1 AS [natural])"):
            self.assertIn("(1 row)", panel_sql.run_readonly_sql(self.path, query), query)

    def test_a_column_added_later_reads_as_null_until_listed(self):
        out = self.sql("SELECT slug, later FROM users")
        self.assertNotIn("LATER-VALUE", out)
        self.assertIn("users.later isn't on the panel's list", out)
        self.assertIn("hex", out)

    def test_ctes_json_each_and_schema_lookups_still_work(self):
        self.assertIn("(1 row)", self.sql("WITH c(x) AS (SELECT 1) SELECT count(*) FROM c"))
        self.assertIn("(2 rows)", self.sql("SELECT value FROM json_each('[1,2]')"))
        self.assertIn("access_token", self.sql("SELECT name FROM pragma_table_info('users')"))
        self.assertIn("users", self.sql("SELECT name FROM sqlite_master WHERE type = 'table'"))

    def test_every_column_in_the_bots_schema_is_listed_or_hidden(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "schema.db")
            env = {**os.environ, "DB_PATH": path}
            subprocess.run([sys.executable, "-c", "import bot_config, db"], cwd=ROOT, env=env, check=True,
                           capture_output=True, timeout=60)
            conn = sqlite3.connect(path)
            schema = {name: {row[1] for row in conn.execute(f"PRAGMA table_xinfo('{name}')")}
                      for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' "
                                                  "AND name NOT LIKE 'sqlite_%'")}
            conn.close()
        listed = {t: cols for t, cols in panel_sql.READABLE.items() if not t.startswith("sqlite_")}
        self.assertEqual(set(schema), set(listed))
        for table, columns in schema.items():
            classified = listed[table] | {c for t, c in panel_sql.HIDDEN if t == table}
            self.assertEqual(columns, classified, table)  # a new column must be listed (readable) or hidden


# --- INS4-03, INS4-04: one writer for a queue's records and the Hall of Fame reset ----------------------------

class ResultWriteSerializationTests(unittest.IsolatedAsyncioTestCase):
    RESULT = {"is_final": True, "duration_ms": 60_000}

    def patches(self, gate, events):
        def hof(*args, **kwargs):
            events.append("hof start")
            gate.wait(5)
            events.append("hof end")
            return []

        return [patch.object(qss, "check_queue_hall_of_fame_records", side_effect=hof),
                patch.object(qss, "cache_player_queue_stats", side_effect=lambda *a: events.append("rows")),
                patch.object(qss, "clear_hall_of_fame_records", side_effect=lambda: events.append("reset") or 2)]

    async def test_a_reset_waits_for_a_queues_writes_to_finish(self):
        gate, events = threading.Event(), []
        for p in self.patches(gate, events):
            self.enterContext(p)
        writer = threading.Thread(target=qss._store_queue_result, args=([], "1", 7, 6, self.RESULT, None))
        writer.start()
        while "hof start" not in events:
            await asyncio.sleep(0.01)
        reset = threading.Thread(target=qss.reset_hall_of_fame_records)
        reset.start()
        await asyncio.sleep(0.2)
        self.assertNotIn("reset", events)  # not between this queue's record updates
        gate.set()
        writer.join(5)
        reset.join(5)
        self.assertEqual(events, ["hof start", "hof end", "rows", "reset"])

    async def test_a_cancelled_caller_keeps_others_out_until_its_writes_finish(self):
        gate, events = threading.Event(), []
        for p in self.patches(gate, events):
            self.enterContext(p)
        task = asyncio.ensure_future(asyncio.to_thread(qss._store_queue_result, [], "1", 7, 6, self.RESULT, None))
        while "hof start" not in events:
            await asyncio.sleep(0.01)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        reset = threading.Thread(target=qss.reset_hall_of_fame_records)
        reset.start()
        await asyncio.sleep(0.2)
        self.assertNotIn("reset", events)  # the worker still holds the lock
        gate.set()
        reset.join(5)
        self.assertEqual(events, ["hof start", "hof end", "rows", "reset"])

    def test_record_updates_are_atomic(self):
        # db.py leaves its connections to the garbage collector, which Windows won't let a folder be removed under.
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            path = os.path.join(tmp, "hof.db")
            conn = sqlite3.connect(path)
            conn.execute(f.SCHEMA[3])
            conn.commit()
            conn.close()
            with patch.object(db, "DB_PATH", path):
                self.assertTrue(db.try_set_hall_of_fame_record("most_kills", 10, 1, "A", "1", 7))
                self.assertFalse(db.try_set_hall_of_fame_record("most_kills", 10, 2, "B", "2", 7))  # a tie keeps A
                self.assertFalse(db.try_set_hall_of_fame_record("most_kills", 9, 2, "B", "2", 7))
                self.assertTrue(db.try_set_hall_of_fame_record("most_kills", 11, 2, "B", "2", 7))
                values = list(range(12, 60))
                threads = [threading.Thread(target=db.try_set_hall_of_fame_record,
                                            args=("most_kills", v, v, f"P{v}", str(v), 7)) for v in reversed(values)]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join(10)
                record = db.get_hall_of_fame_record("most_kills")
        self.assertEqual((record["value"], record["display_name"]), (59.0, "P59"))

    async def test_the_reset_answers_before_it_waits(self):
        import leaderboard_bot as app
        order = []
        interaction = MagicMock()
        interaction.user.id = 1
        interaction.response.edit_message = AsyncMock(side_effect=lambda **kw: order.append(kw["content"]))
        interaction.edit_original_response = AsyncMock(side_effect=lambda **kw: order.append(kw["content"]))

        def slow_reset():
            time.sleep(0.1)
            order.append("reset")
            return 3

        with patch.object(app, "_is_bot_owner", AsyncMock(return_value=True)), \
                patch.object(app, "reset_hall_of_fame_records", side_effect=slow_reset), \
                patch.object(app, "_hof_reset_answered", set()):
            await app.answer_hall_of_fame_reset(interaction, f"hof_reset:confirm:1:{int(time.time()) + 60}")
        self.assertEqual(order[0], "Deleting the Hall of Fame records…")
        self.assertEqual(order[1:], ["reset", "✅ Deleted 3 Hall of Fame records."])
        self.assertIsNone(interaction.response.edit_message.await_args.kwargs["view"])  # the buttons go first


# --- INS4-02: status reads its counts off the event loop -------------------------------------------------------

class StatusTests(unittest.IsolatedAsyncioTestCase):
    async def test_status_counts_never_stall_the_loop(self):
        ticks = []

        async def heartbeat():
            while True:
                ticks.append(time.monotonic())
                await asyncio.sleep(0.02)

        def slow_counts():
            time.sleep(0.3)  # a held database lock
            return 5, 7

        bot = SimpleNamespace(latency=0.05, user=SimpleNamespace(id=1), guilds=[])
        beat = asyncio.ensure_future(heartbeat())
        try:
            with patch.object(cs, "_status_counts", side_effect=slow_counts):
                out = await cs.run_console_command_captured(bot, "status")
        finally:
            beat.cancel()
        self.assertIn("Linked Users: 5", out)
        gaps = [b - a for a, b in zip(ticks, ticks[1:])]
        self.assertTrue(gaps and max(gaps) < 0.15, max(gaps or [0]))


# --- INS4-07: an unavailable guild isn't ready --------------------------------------------------------------

class GuildOutageTests(SiteTestCase):
    async def asyncSetUp(self):
        self.bot = fake_bot({111111111111111111: SimpleNamespace(display_name="MainNick")})
        await super().asyncSetUp()

    async def test_an_unavailable_guild_drops_cached_pages_and_names(self):
        guild = self.bot.get_guild(f.HOME)
        guild.unavailable = False
        resp, body = await self.get("/queues/4821")
        self.assertEqual(resp.headers["Cache-Control"], "public, max-age=60")
        self.assertIn("MainNick", body)
        guild.unavailable = True  # an outage: the old cache still says chunked
        self.assertFalse(NameResolver(self.bot, f.HOME).ready)
        resp, body = await self.get("/queues/4821")
        self.assertEqual(resp.headers["Cache-Control"], "public, max-age=10")
        self.assertNotIn("MainNick", body)
        self.assertEqual(len(self.app["site"].cache.items), 0)


# --- INS4-08, INS4-09: panel log reads ------------------------------------------------------------------------

class LogReadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "log"
        self.dir.mkdir()

    def tearDown(self):
        q._job.deadline = None
        q._job.stopping = None
        self.tmp.cleanup()

    def test_without_no_follow_a_link_is_still_refused(self):
        outside = Path(self.tmp.name) / "outside.json"
        outside.write_text(json.dumps({"status": "SECRET-OUTSIDE"}))
        try:
            os.symlink(outside, self.dir / "queue_stats_link.json")
        except (OSError, NotImplementedError):
            self.skipTest("this system can't create a symlink here")
        with patch.object(web_admin.os, "O_NOFOLLOW", 0, create=True):
            listed = {e["name"]: e for e in web_admin.list_logs(str(self.dir))}
            self.assertFalse(listed["queue_stats_link.json"]["readable"])
            self.assertEqual(web_admin.read_log(str(self.dir), "queue_stats_link.json"), ("unreadable", None))

    def test_deep_nesting_is_unreadable_not_a_crash(self):
        (self.dir / "queue_stats_deep.json").write_text("[" * 100_000 + "]" * 100_000)
        (self.dir / "queue_stats_ok.json").write_text(json.dumps({"status": "ok"}))
        listed = {e["name"]: e["readable"] for e in web_admin.list_logs(str(self.dir))}
        self.assertEqual(listed, {"queue_stats_deep.json": False, "queue_stats_ok.json": True})
        self.assertEqual(web_admin.read_log(str(self.dir), "queue_stats_deep.json"), ("unreadable", None))


# --- INS4-13: a published log never replaces another ------------------------------------------------------

class LogPublishTests(unittest.TestCase):
    def test_a_name_collision_retries_instead_of_overwriting(self):
        frozen = qss.datetime(2026, 11, 27, 12, 0, 0, tzinfo=qss.timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(qss, "QUEUE_STATS_LOG_DIR", tmp), \
                    patch.object(qss, "datetime", SimpleNamespace(now=lambda tz=None: frozen)), \
                    patch.object(qss.secrets, "token_hex", side_effect=["deadbeef", "deadbeef", "cafef00d"]):
                qss.write_queue_stats_log("1", None, None, trigger="auto_post", guild_id=1, channel_id=2)
                qss.write_queue_stats_log("1", None, "second", trigger="auto_post", guild_id=1, channel_id=2)
            names = sorted(os.listdir(tmp))
            self.assertEqual(len(names), 2, names)
            by_tag ={("deadbeef" in n): json.loads(Path(tmp, n).read_text()) for n in names}
            self.assertIsNone(by_tag[True]["error"])  # the first log is untouched
            self.assertEqual(by_tag[False]["error"], "second")


# --- INS4-14: a BLOB queue number is never counted -------------------------------------------------------------

class BlobQueueIdTests(SiteTestCase):
    async def test_digit_blobs_are_left_out_of_the_count_and_the_list(self):
        conn = sqlite3.connect(self.path)
        before = q.count_queues(conn, f.HOME)
        f.add_row(conn, f.HOME, 4991, name="BlobRow")
        conn.execute("UPDATE player_queue_stats SET match_id = CAST(match_id AS BLOB) WHERE match_id = '4991'")
        conn.execute("INSERT INTO queue_results VALUES (?, CAST('4992' AS BLOB), '{}', 'now')", (f.HOME,))
        conn.commit()
        self.assertEqual(q.count_queues(conn, f.HOME), before)
        conn.close()
        _, body = await self.get("/queues")
        self.assertNotIn("/queues/4991", body)
        self.assertNotIn("/queues/4992", body)
        self.assertIn(f"{before} saved finished queues", body)


# --- INS4-10: the chart's queues on older history pages ------------------------------------------------------

class PlayerChartTableTests(SiteTestCase):
    async def test_older_pages_list_the_charts_queues(self):
        conn = sqlite3.connect(self.path)
        for n in range(30):
            f.add_row(conn, f.HOME, 5000 + n, discord_id=111111111111111111, name="KestrelNick", slug="kestrel",
                      end_ms=f.ms(2026, 11, 1) + n * 60_000)
        conn.commit()
        conn.close()
        _, first = await self.get("/players/kestrel")
        self.assertNotIn("as a table", first)  # page 1's own table lists them
        _, second = await self.get("/players/kestrel?page=2")
        table = second.split('<details class="trend-data">', 1)[1].split("</details>", 1)[0]
        self.assertIn("The chart's 16 queues as a table", table)
        shown = re.findall(r'href="/queues/(\d+)"', table)
        # The 16 newest, newest first like the history table: the fixture's 4821 and 4700, then 5029 down.
        self.assertEqual(shown, ["4821", "4700"] + [str(5029 - k) for k in range(14)])


# --- INS4-11, INS4-12, SEC-15: script and ignore rules ------------------------------------------------------

class StaticRuleTests(unittest.TestCase):
    def test_the_script_moves_focus_and_locks_clear(self):
        js = (ROOT / "web" / "static" / "site.js").read_text(encoding="utf-8")
        self.assertIn("if (event.detail === 0) {", js)
        self.assertIn("focus({ preventScroll: true })", js)
        self.assertIn('querySelectorAll("[data-console-run], [data-console-clear]")', js)
        self.assertIn("new URLSearchParams({ command, run: newRunId() })", js)

    def test_runtime_files_never_reach_git_or_the_image(self):
        paths = ["leaderboard.db-wal", "leaderboard.db-shm", "leaderboard.db-journal", "preview.db.part",
                 ".env.production", "log/queue_stats_x.json", "queue_stats_x.json", "backups/leaderboard-1.db"]
        result = subprocess.run(["git", "check-ignore", "--no-index", *paths], cwd=ROOT, capture_output=True,
                                text=True)
        self.assertEqual(sorted(result.stdout.split()), sorted(paths))
        # The Docker build context, in folders too (final review FIN-11): checked with Docker's own matching rules.
        nested = [f"sub/dir/{p}" for p in paths] + ["data/leaderboard.db", "sub/custom.db", "sub/.env"]
        for path in paths + nested:
            self.assertTrue(docker_ignored(path), path)
        for path in ("bot_config.py", "panel_sql.py", "queue_stats_service.py", "web/static/site.js",
                     "web/templates/player.html", "assets/logo.png", "requirements.txt"):
            self.assertFalse(docker_ignored(path), path)


def docker_ignored(path: str) -> bool:
    """Whether .dockerignore leaves `path` out of the build context, by Docker's rules (moby patternmatcher): `*`
    and `?` stay inside one folder, `**/` matches any number of folders, a bare pattern is anchored at the top, and a
    folder's pattern covers everything in it."""
    patterns = [line.strip() for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.startswith("#")]
    parts = path.split("/")
    for pattern in patterns:
        text, regex, i = pattern.rstrip("/"), "", 0
        while i < len(text):
            if text.startswith("**/", i):
                regex, i = regex + "(?:.*/)?", i + 3
            elif text.startswith("**", i):
                regex, i = regex + ".*", i + 2
            elif text[i] in "*?":
                regex, i = regex + ("[^/]*" if text[i] == "*" else "[^/]"), i + 1
            else:
                regex, i = regex + re.escape(text[i]), i + 1
        if any(re.fullmatch(regex, "/".join(parts[:n])) for n in range(1, len(parts) + 1)):
            return True
    return False


if __name__ == "__main__":
    unittest.main()
