"""One survev.de account, one Discord user (the user's rule, 2026-11-27; security review SEC-04): the first link
keeps the account, a second Discord user's /verify is refused, the slug backfill follows the same rule (and never
attaches a slug to a link that changed meanwhile), and the database itself refuses a second link."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import db
import survev_client
try:
    import web_fixture as f
    from test_verification import DEVICE_CODE, DEVICE_URL, LINK_URL, TOKEN_URL, FakeResponse, FakeSurvev
except ImportError:  # run as tests.test_account_links
    from tests import web_fixture as f
    from tests.test_verification import DEVICE_CODE, DEVICE_URL, LINK_URL, TOKEN_URL, FakeResponse, FakeSurvev

A, B, C = 111, 222, 333


class LinkDatabaseTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)  # db.py leaves connections to the GC
        self.addCleanup(tmp.cleanup)
        self.path = str(Path(tmp.name) / "links.db")
        conn = sqlite3.connect(self.path)
        for statement in f.SCHEMA:
            conn.execute(statement)
        conn.commit()
        conn.close()
        self.enterContext(patch.object(db, "DB_PATH", self.path))

    def rows(self):
        conn = sqlite3.connect(self.path)
        try:
            return {d: (t, s) for d, t, s in conn.execute("SELECT discord_id, access_token, slug FROM users")}
        finally:
            conn.close()

    def test_the_first_link_keeps_the_account(self):
        db.migrate_unique_account_links()
        self.assertTrue(db.save_token(A, "tok-a", "hex", "Hex"))
        self.assertFalse(db.save_token(B, "tok-b", "hex", "Hex"))  # another Discord user: refused
        self.assertEqual(self.rows(), {A: ("tok-a", "hex")})
        self.assertTrue(db.save_token(A, "tok-a2", "hex", "Hex"))  # the holder re-verifying is fine
        self.assertTrue(db.save_token(B, "tok-b", None, None))  # no profile yet: nothing to compare
        self.assertTrue(db.save_token(C, "tok-c", None, None))
        self.assertEqual(self.rows(), {A: ("tok-a2", "hex"), B: ("tok-b", None), C: ("tok-c", None)})
        self.assertTrue(db.save_token(A, "tok-a3", "juno", "Juno"))  # A moves to another account...
        self.assertTrue(db.save_token(B, "tok-b2", "hex", "Hex"))  # ...so hex is free again
        self.assertEqual(db.get_discord_id_by_slug("hex"), B)

    def test_the_database_refuses_a_second_link_too(self):
        db.migrate_unique_account_links()
        db.save_token(A, "tok-a", "hex", "Hex")
        conn = sqlite3.connect(self.path)
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO users VALUES (?, 'tok-b', 'hex', 'Hex')", (B,))
        conn.execute("INSERT INTO users VALUES (?, 'tok-b', NULL, NULL)", (B,))  # links without a profile can share
        conn.execute("INSERT INTO users VALUES (?, 'tok-c', '', NULL)", (C,))
        conn.close()

    def test_the_backfill_follows_the_same_rule(self):
        db.migrate_unique_account_links()
        db.save_token(A, "tok-a", "hex", "Hex")
        db.save_token(B, "tok-b", None, None)
        db.save_token(C, "tok-c", None, None)
        self.assertEqual(db.update_user_slug(B, "tok-b", "hex", "Hex"), "taken")  # A holds hex: B is unlinked
        self.assertNotIn(B, self.rows())
        self.assertEqual(db.update_user_slug(C, "tok-c", "juno", "Juno"), "filled")
        self.assertEqual(self.rows()[C], ("tok-c", "juno"))

    def test_a_backfill_never_writes_onto_a_link_that_changed(self):
        db.migrate_unique_account_links()
        db.save_token(B, "tok-old", None, None)
        db.save_token(B, "tok-new", "okra", "Okra")  # B re-verified while the backfill's lookup ran
        self.assertEqual(db.update_user_slug(B, "tok-old", "hex", "Hex"), "stale")
        self.assertEqual(self.rows()[B], ("tok-new", "okra"))
        db.save_token(C, "tok-c", None, None)
        db.save_token(C, "tok-c2", None, None)  # a new token, still no profile
        self.assertEqual(db.update_user_slug(C, "tok-c", "hex", "Hex"), "stale")
        self.assertEqual(db.update_user_slug(A, "tok-a", "hex", "Hex"), "stale")  # no link at all

    def test_old_duplicates_are_resolved_before_the_rule_is_enforced(self):
        conn = sqlite3.connect(self.path)
        conn.executemany("INSERT INTO users VALUES (?, ?, 'hex', 'Hex')", [(A, "tok-a"), (B, "tok-b")])
        conn.execute("INSERT INTO users VALUES (?, 'tok-c', 'juno', 'Juno')", (C,))
        conn.commit()
        f.add_row(conn, f.HOME, 4001, discord_id=A, slug="hex")
        for n in range(3):
            f.add_row(conn, f.HOME, 4100 + n, discord_id=B, slug="hex")  # B played most on the account
        conn.commit()
        conn.close()
        with self.assertLogs("db", level="WARNING") as logs:
            self.assertEqual(db.migrate_unique_account_links(), [A])
        self.assertIn(f"Unlinked Discord user {A}", logs.output[0])
        self.assertNotIn("tok-a", "".join(logs.output))
        self.assertEqual(set(self.rows()), {B, C})
        self.assertEqual(db.migrate_unique_account_links(), [])  # idempotent
        self.assertFalse(db.save_token(A, "tok-a", "hex", "Hex"))


class LinkFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.sender = AsyncMock()
        self.state = {"senders": [self.sender], "uri": None, "code": None, "deadline": None}
        self.enterContext(patch("survev_client.asyncio.sleep", new=AsyncMock()))

    def messages(self):
        return [call.kwargs.get("content") for call in self.sender.await_args_list if call.kwargs.get("content")]

    async def test_verify_says_so_when_another_discord_user_holds_the_account(self):
        survev = FakeSurvev({
            DEVICE_URL: [FakeResponse(200, DEVICE_CODE)],
            TOKEN_URL: [FakeResponse(200, {"accessToken": "tok-new"})],
            LINK_URL: [FakeResponse(200, {"slug": "hex", "username": "Hex"})],
        })
        with patch.object(survev_client, "http_session", survev), \
                patch.object(survev_client, "save_token", return_value=False) as save, \
                patch.object(survev_client, "get_user_token", return_value=None), \
                self.assertLogs("survev_client", level="WARNING"):
            await survev_client._run_verification_flow(7, self.state)
        save.assert_called_once_with(7, "tok-new", "hex", "Hex")
        self.assertEqual(len(self.messages()), 1)
        self.assertIn("already linked to another Discord user", self.messages()[0])
        self.assertNotIn("Account Linked", self.messages()[0])
        self.assertNotIn("hex", self.messages()[0])  # nothing about who holds it

    async def test_the_backfill_unlinks_a_second_claim_and_logs_it(self):
        with patch.object(survev_client, "get_users_missing_slug", return_value=[(B, "tok-b"), (C, "tok-c")]), \
                patch.object(survev_client, "fetch_discord_link",
                             new=AsyncMock(side_effect=[("hex", "Hex"), ("juno", "Juno")])), \
                patch.object(survev_client, "update_user_slug", side_effect=["taken", "filled"]) as update, \
                self.assertLogs("survev_client", level="WARNING") as logs:
            await survev_client.backfill_missing_slugs()
        self.assertEqual([c.args for c in update.call_args_list],
                         [(B, "tok-b", "hex", "Hex"), (C, "tok-c", "juno", "Juno")])
        self.assertEqual(len(logs.output), 1)
        self.assertIn(f"Unlinked Discord user {B}", logs.output[0])


if __name__ == "__main__":
    unittest.main()
