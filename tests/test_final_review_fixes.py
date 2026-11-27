"""Regression tests for code inspection 5, the final review before merging (FIN-01..13). FIN-01's /verify change is
tested in tests/test_verification.py and FIN-11's ignore rules in tests/test_web_inspection4_fixes.py."""
import asyncio
import inspect
import io
import json
import os
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
import leaderboard_bot
import panel_sql
import survev_client
import web_admin
import web_queries as q
import web_server
try:
    import web_fixture as f
    from test_verification import DEVICE_CODE, DEVICE_URL, LINK_URL, TOKEN_URL, FakeResponse, FakeSurvev
    from test_web_admin import config
    from test_web_console import FORM, FakeConsole, token_db
    from test_web_inspection4_fixes import StopConsole
except ImportError:  # run as tests.test_final_review_fixes
    from tests import web_fixture as f
    from tests.test_verification import DEVICE_CODE, DEVICE_URL, LINK_URL, TOKEN_URL, FakeResponse, FakeSurvev
    from tests.test_web_admin import config
    from tests.test_web_console import FORM, FakeConsole, token_db
    from tests.test_web_inspection4_fixes import StopConsole

ROOT = Path(__file__).resolve().parent.parent
RUN = "r" * 20
A, B = 333, 111


def link_db(tmp: str) -> str:
    path = str(Path(tmp) / "links.db")
    conn = sqlite3.connect(path)
    for statement in f.SCHEMA:
        conn.execute(statement)
    conn.commit()
    conn.close()
    return path


# --- FIN-02, FIN-03: the link migration --------------------------------------------------------------------

class LinkMigrationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)  # db.py leaves connections to the GC
        self.addCleanup(tmp.cleanup)
        self.path = link_db(tmp.name)
        conn = sqlite3.connect(self.path)
        conn.executemany("INSERT INTO users VALUES (?, ?, 'hex', 'Hex')", [(A, "tok-a"), (B, "tok-b")])
        conn.commit()
        conn.close()

    def users(self):
        conn = sqlite3.connect(self.path)
        try:
            return sorted(row[0] for row in conn.execute("SELECT discord_id FROM users"))
        finally:
            conn.close()

    def test_a_queue_is_counted_per_server(self):
        conn = sqlite3.connect(self.path)
        f.add_row(conn, f.HOME, 42, discord_id=A, slug="hex")
        f.add_row(conn, f.OTHER, 42, discord_id=A, slug="hex")  # another server's queue 42: a second queue
        f.add_row(conn, f.HOME, 50, discord_id=B, slug="hex")
        conn.commit()
        conn.close()
        with patch.object(db, "DB_PATH", self.path), self.assertLogs("db", level="WARNING"):
            self.assertEqual(db.migrate_unique_account_links(), [B])  # not a tie broken by the lower ID
        self.assertEqual(self.users(), [A])

    def test_importing_db_or_running_the_backup_never_unlinks_anyone(self):
        env = {**os.environ, "DB_PATH": self.path}
        result = subprocess.run([sys.executable, "-c", "import backup_db, db"], cwd=ROOT, env=env,
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.users(), [B, A])
        conn = sqlite3.connect(self.path)
        try:
            self.assertIsNone(conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name = 'users_slug_unique'").fetchone())
        finally:
            conn.close()


class StartupMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_bot_runs_it_in_a_thread_before_the_website_starts(self):
        threads = []
        with patch.object(leaderboard_bot, "migrate_unique_account_links",
                          side_effect=lambda: threads.append(threading.get_ident()) or []):
            await leaderboard_bot.enforce_unique_account_links()
        self.assertEqual(len(threads), 1)
        self.assertNotEqual(threads[0], threading.get_ident())
        source = inspect.getsource(leaderboard_bot.main)
        self.assertLess(source.index("await enforce_unique_account_links()"), source.index("await start_website()"))

    async def test_a_locked_database_only_postpones_the_index(self):
        with patch.object(leaderboard_bot, "migrate_unique_account_links",
                          side_effect=sqlite3.OperationalError("database is locked")), \
                self.assertLogs("leaderboard_bot", level="WARNING") as logs:
            await leaderboard_bot.enforce_unique_account_links()
        self.assertIn("next start", logs.output[0])


# --- FIN-04: link writes off the event loop ----------------------------------------------------------------

class LinkWritesOffTheLoopTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.sender = AsyncMock()
        self.state = {"senders": [self.sender], "uri": None, "code": None, "deadline": None}
        self.enterContext(patch("survev_client.asyncio.sleep", new=AsyncMock()))

    async def verify(self, save):
        survev = FakeSurvev({
            DEVICE_URL: [FakeResponse(200, DEVICE_CODE)],
            TOKEN_URL: [FakeResponse(200, {"accessToken": "tok-new"})],
            LINK_URL: [FakeResponse(200, {"slug": "hex", "username": "Hex"})],
        })
        with patch.object(survev_client, "http_session", survev), \
                patch.object(survev_client, "save_token", side_effect=save), \
                patch.object(survev_client, "get_user_token", return_value=None):
            await survev_client._run_verification_flow(7, self.state)
        return [c.kwargs.get("content") for c in self.sender.await_args_list if c.kwargs.get("content")]

    async def test_verify_saves_in_a_worker_thread(self):
        threads = []
        messages = await self.verify(lambda *args: threads.append(threading.get_ident()) or True)
        self.assertNotEqual(threads, [threading.get_ident()])
        self.assertEqual(len(threads), 1)
        self.assertEqual(messages, ["✅ **Account Linked!**"])

    async def test_a_busy_database_is_a_link_to_retry(self):
        def busy(*args):
            raise sqlite3.OperationalError("database is locked")
        with self.assertLogs("survev_client", level="WARNING"):
            messages = await self.verify(busy)
        self.assertEqual(len(messages), 1)
        self.assertIn("database was busy, so nothing was linked", messages[0])

    async def test_the_backfill_postpones_a_link_when_the_database_is_busy(self):
        threads = []

        def busy(*args):
            threads.append(threading.get_ident())
            raise sqlite3.OperationalError("database is locked")
        with patch.object(survev_client, "get_users_missing_slug", return_value=[(B, "tok-b")]), \
                patch.object(survev_client, "fetch_discord_link", new=AsyncMock(return_value=("hex", "Hex"))), \
                patch.object(survev_client, "update_user_slug", side_effect=busy), \
                self.assertLogs("survev_client", level="INFO") as logs:
            await survev_client.backfill_missing_slugs()
        self.assertNotEqual(threads, [threading.get_ident()])
        self.assertIn("postponed", logs.output[0])

    def test_link_writes_wait_no_longer_than_sqlites_usual_five_seconds(self):
        self.assertEqual(db.LINK_WRITE_TIMEOUT, 5.0)
        for function in (db.save_token, db.update_user_slug, db.migrate_unique_account_links):
            self.assertIn("timeout=LINK_WRITE_TIMEOUT", inspect.getsource(function))


# --- FIN-05: an account's URL follows its current holder ---------------------------------------------------

class TransferredAccountUrlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.conn = f.standard_db(str(Path(self.tmp.name) / "site.db"))
        self.addCleanup(self.conn.close)
        self.conn.execute("DELETE FROM users WHERE slug = 'zephyrine'")
        f.add_row(self.conn, f.HOME, 6001, discord_id=900, slug="zephyrine")  # 900 played as zephyrine, then lost the link
        self.conn.commit()

    def test_a_new_holder_without_a_page_gets_no_redirect_to_the_old_one(self):
        f.add_user(self.conn, 901, "zephyrine", "Zephyrine")  # the operator freed zephyrine and 901 linked it
        self.conn.commit()
        self.assertIsNone(q.resolve_player_slug(self.conn, f.HOME, "zephyrine"))
        f.add_row(self.conn, f.HOME, 6002, discord_id=901, slug="zephyrine")
        self.conn.commit()
        self.assertEqual(q.resolve_player_slug(self.conn, f.HOME, "zephyrine"), ("page", 901))

    def test_an_account_nobody_holds_still_redirects_to_its_last_player(self):
        self.assertEqual(q.resolve_player_slug(self.conn, f.HOME, "zephyrine"), ("redirect", 900))


# --- FIN-06, FIN-10, FIN-12: console runs, the stop reply and sessions --------------------------------------

class SlowConsole(FakeConsole):
    def __init__(self):
        super().__init__()
        self.release = asyncio.Event()

    async def run(self, line):
        self.seen.append(line)
        if line == "slow":
            await self.release.wait()
        if line == "boom":
            raise RuntimeError("the command failed halfway")
        return [{"text": f"ran {line}", "kind": ""}]


class ConsoleHttpCase(unittest.IsolatedAsyncioTestCase):
    console_class = SlowConsole

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
        self.cookie = self.session()

    async def asyncTearDown(self):
        await self.client.close()
        self.site.db.executor.shutdown(wait=True)
        self.site.checker.executor.shutdown(wait=True)
        self.tmp.cleanup()

    def session(self, now=None):
        return {"Cookie": f"{web_admin.COOKIE_NAME}={web_admin.make_session(self.site.admin, now)}"}

    async def post(self, body, cookie=None, json_reply=False):
        headers = {**FORM, **(cookie or self.cookie)}
        if json_reply:
            headers["Accept"] = "application/json"
        return await self.client.post("/admin/console", data=body, headers=headers, allow_redirects=False)


class ConsoleRunRecordTests(ConsoleHttpCase):
    async def test_a_running_command_keeps_its_record_past_the_cap(self):
        slow = asyncio.ensure_future(self.post(f"command=slow&run={RUN}"))
        while "slow" not in self.console.seen:
            await asyncio.sleep(0.01)
        for n in range(web_server.MAX_CONSOLE_RUNS + 8):
            await self.post(f"command=status+{n}&run=s{n:019d}")
        self.assertIn(RUN, self.site.console_runs)
        self.console.release.set()
        self.assertEqual((await slow).status, 303)  # not a 500 after the command ran
        data = await (await self.post(f"command=slow&run={RUN}", json_reply=True)).json()
        self.assertEqual(data["lines"][0]["text"], "ran slow")
        self.assertEqual(self.console.seen.count("slow"), 1)
        self.assertLessEqual(len(self.site.console_runs), web_server.MAX_CONSOLE_RUNS)

    async def test_a_command_that_failed_is_never_run_again_by_a_resubmit(self):
        with self.assertLogs("web_server", level="ERROR"):
            first = await self.post(f"command=boom&run={RUN}")
        self.assertEqual(first.status, 500)
        data = await (await self.post(f"command=boom&run={RUN}", json_reply=True)).json()
        self.assertIn("stopped before it finished", data["lines"][0]["text"])
        self.assertEqual(self.console.seen, ["boom"])

    async def test_two_sign_ins_in_the_same_second_keep_their_runs_apart(self):
        now = time.time()
        first, second = self.session(now), self.session(now)
        self.assertNotEqual(first, second)
        for cookie in (first, second):
            value = cookie["Cookie"].split("=", 1)[1]
            self.assertTrue(web_admin.check_session(self.site.admin, value, now))
        await self.post(f"command=dm+1+hello&run={RUN}", cookie=first)
        resp = await self.client.get(f"/admin/?run={RUN}", headers=second, allow_redirects=False)
        self.assertNotIn("ran dm 1 hello", await resp.text())


class StopReplyHeaderTests(ConsoleHttpCase):
    console_class = StopConsole

    async def test_both_stop_replies_carry_the_sites_headers(self):
        for json_reply, run in ((False, RUN), (True, "t" * 20)):
            self.console.stop_requested = False
            resp = await self.post(f"command=stop+confirm&run={run}", json_reply=json_reply)
            await resp.read()
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.headers["Content-Security-Policy"], web_server.CSP)
            self.assertEqual(resp.headers["X-Content-Type-Options"], "nosniff")
            self.assertEqual(resp.headers["X-Robots-Tag"], "noindex")
            self.assertEqual(resp.headers["X-Frame-Options"], "DENY")
            self.assertEqual(resp.headers["Referrer-Policy"], "same-origin")
            self.assertEqual(resp.headers["Cache-Control"], "no-store")
        self.assertEqual(self.console.replies_sent, 2)

    async def test_only_the_stop_confirm_reply_reports_that_it_went_out(self):
        await (await self.post(f"command=stop+confirm&run={RUN}")).read()
        self.assertEqual(self.console.replies_sent, 1)
        other = await self.post("command=status&run=" + "u" * 20)  # stop still pending: an ordinary reply
        self.assertEqual(other.status, 303)
        again = await self.post(f"command=stop+confirm&run={RUN}")  # the stop's own form, resubmitted
        await again.read()
        self.assertEqual(self.console.replies_sent, 1)
        self.assertEqual(self.console.seen, ["stop confirm", "status"])


# --- FIN-07: output past the cap ----------------------------------------------------------------------------

class WorkerOutputTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = token_db(self.tmp.name)
        self.console = cs.PanelConsole(MagicMock(), MagicMock(), db_path=self.path)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    def worker(self, source: str) -> Path:
        path = Path(self.tmp.name) / "worker.py"
        path.write_text(source, encoding="utf-8")
        return path

    async def run_sql(self):
        return "\n".join(line["text"] for line in await self.console.run("sql SELECT 1"))

    async def test_a_worker_writing_past_the_cap_is_stopped_and_the_console_frees_up(self):
        for tail in ("time.sleep(60)", "pass"):  # still running, or finished after writing it all
            script = self.worker("import sys, time\nsys.stdin.read()\n"
                                 "sys.stdout.buffer.write(b'x' * 3_000_000)\nsys.stdout.flush()\n" + tail + "\n")
            start = time.monotonic()
            with patch.object(cs, "_SQL_WORKER", script), patch.object(cs, "PANEL_SQL_MAX_BYTES", 1 << 20):
                out = await self.run_sql()
            self.assertIn("too large for the panel", out)
            self.assertLess(time.monotonic() - start, 5)
            self.assertFalse(self.console.busy)
            self.assertIsNone(self.console._sql_proc)
        with patch.object(cs, "_SQL_WORKER", self.worker("import sys\nsys.stdin.read()\nprint('next')\n")):
            self.assertIn("next", await self.run_sql())  # the next command is admitted

    def test_the_worker_cuts_a_wide_table_itself(self):
        name = "a" * 60_000  # one very long column name pads every row
        query = (f'WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 150) '
                 f'SELECT i AS "{name}" FROM n')
        out = panel_sql.run_readonly_sql(self.path, query)
        self.assertLessEqual(len(out), panel_sql.MAX_OUTPUT + 200)
        self.assertIn(f"cut at {panel_sql.MAX_OUTPUT:,} characters", out)
        self.assertLess(len(out.encode("utf-8")), cs.PANEL_SQL_MAX_BYTES)


# --- FIN-08: names the database takes over -----------------------------------------------------------------

class ShadowedNameTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = token_db(self.tmp.name)

    def sql(self, query):
        out = panel_sql.run_readonly_sql(self.path, query)
        self.assertNotIn(f.CANARY_TOKEN, out, query)
        return out

    def change(self, *statements):
        conn = sqlite3.connect(self.path)
        for statement in statements:
            conn.execute(statement)
        conn.commit()
        conn.close()

    def test_a_table_named_like_a_built_in_is_refused_like_any_other(self):
        self.change("CREATE TABLE json_each AS SELECT access_token AS value FROM users",
                    "CREATE VIEW pragma_table_info AS SELECT access_token AS name FROM users")
        self.assertIn("can't use table json_each", self.sql("SELECT * FROM json_each"))
        self.assertIn("can't use table pragma_table_info", self.sql("SELECT name FROM pragma_table_info"))

    def test_a_declared_column_with_a_row_id_name_needs_listing(self):
        self.change("ALTER TABLE users ADD COLUMN oid TEXT", "UPDATE users SET oid = access_token")
        out = self.sql("SELECT oid, slug FROM users")
        self.assertIn("users.oid isn't on the panel's list of readable columns", out)
        self.assertIn("hex", out)
        self.assertNotIn("Console Warning", self.sql("SELECT rowid, guild_id FROM queue_results LIMIT 2"))


# --- FIN-09: the worker's limits -----------------------------------------------------------------------------

class WorkerLimitTests(unittest.TestCase):
    def fake(self, current, fail=False):
        calls = {}

        def setrlimit(which, value):
            if fail:
                raise OSError("not permitted")
            calls[which] = value
        return calls, SimpleNamespace(RLIM_INFINITY=-1, RLIMIT_CPU=0, RLIMIT_AS=1, RLIMIT_FSIZE=2, RLIMIT_NOFILE=3,
                                      getrlimit=lambda which: current.get(which, (-1, -1)), setrlimit=setrlimit)

    def test_a_stricter_limit_already_in_place_is_kept(self):
        calls, fake = self.fake({0: (1, -1), 1: (256 << 20, 512 << 20), 3: (32, 4096)})
        with patch.dict(sys.modules, {"resource": fake}):
            panel_sql.limit_resources(2.0)
        self.assertEqual(calls, {0: (1, 1), 1: (256 << 20, 512 << 20), 2: (panel_sql.LIMIT_FILE,) * 2,
                                 3: (32, 4096)})

    def test_the_query_never_runs_without_its_limits(self):
        _, fake = self.fake({}, fail=True)
        stdin = SimpleNamespace(buffer=io.BytesIO(json.dumps({"db": "x.db", "query": "SELECT 1"}).encode()))
        stdout = SimpleNamespace(buffer=io.BytesIO())
        with patch.dict(sys.modules, {"resource": fake}), patch.object(sys, "stdin", stdin), \
                patch.object(sys, "stdout", stdout), patch.object(panel_sql, "run_readonly_sql") as run:
            self.assertEqual(panel_sql.main(), 0)
        run.assert_not_called()
        self.assertIn("didn't run: the panel couldn't set its RLIMIT_CPU limit", stdout.buffer.getvalue().decode())


# --- FIN-13: the chart's table marks the chart's metric ---------------------------------------------------

class ChartTableMetricTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        path = str(Path(self.tmp.name) / "site.db")
        conn = f.standard_db(path)
        for n in range(30):
            f.add_row(conn, f.HOME, 5000 + n, discord_id=111111111111111111, name="KestrelNick", slug="kestrel",
                      end_ms=f.ms(2026, 11, 1) + n * 60_000)
        conn.commit()
        conn.close()
        self.app = web_server.create_app(f.HOME, path)
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.app["site"].db.executor.shutdown(wait=True)
        self.tmp.cleanup()

    async def test_both_tables_mark_the_selected_metric(self):
        for metric in ("kills", "damage", "impact"):
            resp = await self.client.get(f"/players/kestrel?page=2&metric={metric}")
            body = await resp.text()
            chart_table = body.split('<details class="trend-data">', 1)[1].split("</thead>", 1)[0]
            history = body.split('aria-labelledby="hist-h"', 1)[1].split("</thead>", 1)[0]
            for head in (chart_table, history):
                self.assertEqual(head.count("data-col="), 3)
                self.assertIn(f'class="accent" data-col="{metric}"', head)
                self.assertEqual(head.count('class="accent"'), 1)


if __name__ == "__main__":
    unittest.main()
