"""Offline tests for the web app: fake Google, in-memory store. No network.

    python -m unittest discover -s tests
"""

import os
import sys
import unittest
from unittest import mock

os.environ["STORE_BACKEND"] = "memory"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "cloud_run"))

from store import MemoryStore  # noqa: E402


class FakeGoogle:
    """Talent Solutions AI's Google account: every call answers empty, and is recorded."""

    def __init__(self):
        self.calls = []

    def auth_header(self):
        return {"Authorization": "Bearer fake"}

    def request(self, method, url, params=None, body=None):
        self.calls.append((method, url, params, body))
        return {}


def scenario():
    """A fresh store and a connected Google account."""
    return MemoryStore(), FakeGoogle(), None


class WebTests(unittest.TestCase):
    def setUp(self):
        import main

        self.main = main
        main.store = MemoryStore()
        main.secret_store = main.make_secrets()
        main.secret_store.put(main.RUN_TOKEN_SECRET_ID, "run-secret")
        self.store, self.google, self.llm = scenario()
        main.store = self.store
        main.app.config["MAKE_GOOGLE"] = lambda: self.google
        main.app.config["TESTING"] = True
        self.client = main.app.test_client()

    def sign_in(self):
        self.main.secret_store.put(self.main.PASSWORD_SECRET_ID, "test-password")
        with self.client.session_transaction() as s:
            s["signed_in"] = self.main.password_mark("test-password")

    def call(self, method, path, body=None, csrf=True):
        headers = {"X-Chat-Assistant": "1"} if csrf else {}
        return self.client.open(path, method=method, json=body, headers=headers)

    def test_watchdog_needs_the_run_token(self):
        self.assertEqual(self.client.post("/watchdog", headers={"X-Run-Token": "wrong"}).status_code, 403)
        self.assertEqual(self.client.post("/watchdog", headers={"X-Run-Token": "run-secret"}).get_json(), {"resumed": []})

    def test_background_work_needs_the_run_token(self):
        self.store.save_task("t1", {"title": "x", "status": "todo", "owner": "Assistant"})
        self.assertEqual(self.client.post("/work/t1", headers={"X-Run-Token": "wrong"}).status_code, 403)
        self.assertEqual(self.client.post("/work/none", headers={"X-Run-Token": "run-secret"}).status_code, 404)

    def test_steps_show_the_log_and_what_is_being_worked(self):
        from store import utcnow_iso

        self.sign_in()
        self.store.add_step({"caller": "assistant (task)", "tool": "browser", "detail": "open"})
        self.store.save_task("t1", {"title": "E2E", "status": "in_progress", "working_since": utcnow_iso()})
        body = self.call("GET", "/api/steps").get_json()
        self.assertEqual(body["steps"][0]["tool"], "browser")
        self.assertEqual([w["id"] for w in body["working"]], ["t1"])

    def test_signed_out(self):
        self.assertEqual(self.call("GET", "/api/state").status_code, 401)
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("尚未設定密碼".encode(), page.data)

    def test_board_page_for_owner(self):
        self.sign_in()
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'id="view-board"', page.data)
        self.assertIn(b'id="chat"', page.data)          # the chat window, over every tab
        self.assertIn(b'id="chatLauncher"', page.data)  # what it minimizes to
        self.assertNotIn(b'data-tab="talk"', page.data)
        self.assertIn(b"<title>Talent Solutions AI</title>", page.data)
        self.assertEqual(page.headers["X-Frame-Options"], "DENY")

    def test_other_account_session_is_rejected(self):
        with self.client.session_transaction() as s:
            s["email"] = "someone@example.com"
        self.assertEqual(self.call("GET", "/api/state").status_code, 401)

    def test_csrf_header_required(self):
        self.sign_in()
        self.assertEqual(self.call("POST", "/api/tasks", {"title": "x"}, csrf=False).status_code, 403)
        self.assertEqual(self.call("POST", "/api/tasks", {"title": "x"}).status_code, 200)

    def test_task_crud(self):
        self.sign_in()
        task = self.call("POST", "/api/tasks", {"title": "Order wig tape", "priority": "bogus"}).get_json()["task"]
        self.assertEqual(task["priority"], "medium")
        updated = self.call("PATCH", f"/api/tasks/{task['id']}", {"status": "done"}).get_json()["task"]
        self.assertEqual(updated["status"], "done")
        self.assertTrue(updated["user_edited"])
        self.assertEqual(self.call("DELETE", f"/api/tasks/{task['id']}").status_code, 200)
        self.assertIsNone(self.store.get_task(task["id"]))

    def test_scheduled_run_needs_token(self):
        self.assertEqual(self.client.post("/run").status_code, 403)
        self.assertEqual(self.client.post("/run", headers={"X-Run-Token": "wrong"}).status_code, 403)
        resp = self.client.post("/run", headers={"X-Run-Token": "run-secret"})
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.get_json()["trigger"], "schedule")
        self.assertEqual(self.store.get_status()["last_run"]["tasks_worked"], [])
        self.assertEqual(self.call("GET", "/api/progress").status_code, 401)  # signed out
        self.sign_in()
        progress = self.call("GET", "/api/progress").get_json()
        self.assertEqual((progress["running"], progress["phase"], progress["trigger"]), (False, "完成", "schedule"))

    def test_run_without_google_still_checks_systems(self):
        self.sign_in()
        self.main.app.config["MAKE_GOOGLE"] = lambda: None  # Connect Google not pressed yet
        with mock.patch.object(self.main.integrations, "check_all", return_value={"github": {"ok": True}}) as check:
            resp = self.call("POST", "/api/run")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertIsNone(check.call_args.kwargs["google"])
        progress = self.call("GET", "/api/progress").get_json()
        self.assertEqual((progress["running"], progress["phase"]), (False, "完成"))

    def test_browser_screens_need_sign_in(self):
        self.store.save_browser_view("assistant", {"at": "2026-10-13T04:00:00+00:00", "action": "open", "image": "AA=="})
        self.assertEqual(self.call("GET", "/api/browser").status_code, 401)
        self.sign_in()
        views = self.call("GET", "/api/browser").get_json()["views"]
        self.assertEqual((views[0]["id"], views[0]["image"]), ("assistant", "AA=="))

    def test_settings_only_known_keys(self):
        self.sign_in()
        s = self.call("PATCH", "/api/settings", {"auto_act": False, "evil": True}).get_json()["settings"]
        self.assertFalse(s["auto_act"])
        self.assertNotIn("evil", s)

    def test_callback_rejects_bad_state(self):
        self.assertEqual(self.client.get("/oauth/callback?state=x&code=y").status_code, 302)  # signed out
        self.sign_in()
        self.assertEqual(self.client.get("/oauth/callback?state=x&code=y").status_code, 400)

    def test_password_login(self):
        self.main.secret_store.put(self.main.PASSWORD_SECRET_ID, "right-password")
        self.assertEqual(self.client.post("/login", data={"password": "wrong"}).status_code, 401)
        self.assertEqual(self.call("GET", "/api/state").status_code, 401)
        resp = self.client.post("/login", data={"password": "right-password"})
        self.assertEqual((resp.status_code, resp.headers["Location"]), (302, "/"))
        self.assertEqual(self.call("GET", "/api/state").status_code, 200)
        self.main.secret_store.put(self.main.PASSWORD_SECRET_ID, "changed")  # a new password signs everyone out
        self.assertEqual(self.call("GET", "/api/state").status_code, 401)


if __name__ == "__main__":
    unittest.main()
