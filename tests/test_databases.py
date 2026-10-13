import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "cloud_run"))
os.environ.setdefault("STORE_BACKEND", "memory")

import databases  # noqa: E402
from store import MemorySecrets  # noqa: E402

URL = "postgresql://postgres:s3cret-pw@db.fictional.supabase.co/postgres"


class FakeConn:
    def __init__(self, fail=None):
        self.executed, self.committed, self.rolled_back, self.fail = [], False, False, fail

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        conn = self

        class Cur:
            description = [("n",)]
            rowcount = 1

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql):
                conn.executed.append(sql)
                if conn.fail and sql not in ("SET TRANSACTION READ ONLY",) and not sql.startswith("SET LOCAL"):
                    raise RuntimeError(conn.fail)

            def fetchmany(self, n):
                return [(3,)]

        return Cur()

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(databases.DATABASES, {"helperxpress": "SUPABASE_DB_URL"}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.secrets = MemorySecrets({"SUPABASE_DB_URL": URL})

    def test_reads_run_read_only_and_roll_back(self):
        conn = FakeConn()
        result = databases.run("helperxpress", "SELECT count(*) AS n FROM bookings;", self.secrets, connect=lambda u: conn)
        self.assertEqual(result, {"columns": ["n"], "rows": [[3]], "row_count": 1, "truncated": False})
        self.assertEqual(conn.executed[0], "SET TRANSACTION READ ONLY")
        self.assertTrue(conn.rolled_back)
        self.assertFalse(conn.committed)

    def test_confirmed_change_commits(self):
        conn = FakeConn()
        databases.run("helperxpress", "UPDATE bookings SET status='x' WHERE id=1", self.secrets, confirmed=True,
                      connect=lambda u: conn)
        self.assertNotIn("SET TRANSACTION READ ONLY", conn.executed)
        self.assertTrue(conn.committed)

    def test_one_statement_unknown_database_and_scrubbed_errors(self):
        self.assertIn("error", databases.run("helperxpress", "SELECT 1; DROP TABLE x", self.secrets))
        self.assertIn("no database called", databases.run("other", "SELECT 1", self.secrets)["error"])
        conn = FakeConn(fail=f"cannot connect to {URL}")
        error = databases.run("helperxpress", "SELECT 1", self.secrets, connect=lambda u: conn)["error"]
        self.assertNotIn("s3cret-pw", error)
        self.assertIn("<connection url>", error)


if __name__ == "__main__":
    unittest.main()
