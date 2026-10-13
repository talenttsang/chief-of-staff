"""Talent Solutions AI's browser tool: calls to the browser service, and screenshots reaching each model."""

import copy
import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "cloud_run"))
import tools  # noqa: E402
from store import MemorySecrets, MemoryStore  # noqa: E402
from tools import Toolset  # noqa: E402

PAGE = {"session": "s1", "device": "desktop", "url": "https://www.helperxpress.com/", "title": "HelperXpress",
        "viewport": {"width": 1280, "height": 800}, "scroll": {"y": 0, "height": 4000}, "image": "SU1BR0U=",
        "elements": [{"kind": "a", "label": "Toupees", "x": 120, "y": 40}], "text": "Hair systems for men",
        "notes": []}


class BrowserToolTests(unittest.TestCase):
    def setUp(self):
        self.calls = []

        def fake_call(body):
            self.calls.append(body)
            return copy.deepcopy(PAGE)

        for patch in (mock.patch.object(tools, "BROWSER_URL", "https://web-browser.example"),
                      mock.patch.object(tools, "_browser_call", side_effect=fake_call),
                      mock.patch.dict(tools._browser_sessions, clear=True)):
            patch.start()
            self.addCleanup(patch.stop)

    def toolset(self, **kw):
        return Toolset(MemoryStore(), None, caller="assistant", **kw)

    def test_each_step_updates_the_screen_people_watch(self):
        store = MemoryStore()
        ts = Toolset(store, None, caller="claude")
        ts.call("browser", {"action": "open", "url": "https://www.helperxpress.com/"})
        view = store.list_browser_views()[0]
        self.assertEqual((view["id"], view["action"], view["image"], view["closed"]), ("claude", "open", "SU1BR0U=", False))
        with mock.patch.object(tools, "_browser_call", return_value={"session": "s1", "error": "No field matches 'x'."}):
            ts.call("browser", {"action": "click", "target": "x"})
        view = store.list_browser_views()[0]
        self.assertEqual((view["action"], view["image"]), ("click", "SU1BR0U="))  # the last screen stays
        self.assertIn("No field", view["error"])

    def test_sign_in_uses_the_secret_login_and_never_shows_it(self):
        login = {"url": "https://app.example/test-login?role=helper",
                 "fields": [{"target": "#key", "value": "s3cret-key"}], "submit": "#go"}
        secrets = MemorySecrets({"BROWSER_ACCOUNTS": __import__("json").dumps({"app-helper": login})})
        with mock.patch.object(tools, "BROWSER_ACCOUNTS", ["app-helper"]):
            ts = self.toolset(secrets=secrets, may_change=False)  # working alone may sign in too
            out = ts.call("browser", {"action": "sign_in", "account": "app-helper"})
            self.assertEqual(self.calls[-1]["fields"], login["fields"])
            self.assertEqual((self.calls[-1]["url"], self.calls[-1]["submit"]), (login["url"], "#go"))
            self.assertNotIn("s3cret-key", __import__("json").dumps(out, default=str))
            self.assertIn("error", ts.call("browser", {"action": "sign_in", "account": "other"}))
            self.assertIn("isn't set up", self.toolset(secrets=MemorySecrets()).call(
                "browser", {"action": "sign_in", "account": "app-helper"})["error"])
            self.assertIn("not type", ts.call("browser", {"action": "type", "target": "#key", "text": "x"})["error"])

    def test_open_attaches_the_screenshot_and_reuses_the_tab(self):
        ts = self.toolset()
        self.assertIn("browser", [s["name"] for s in ts.specs()])
        result = ts.call("browser", {"action": "open", "url": "helperxpress.com"})
        self.assertNotIn("_images", result)  # the image goes to the model, not into the JSON
        self.assertEqual(result["screenshot"], "attached: look at it")
        self.assertEqual(result["elements"], ["a 'Toupees' at 120,40"])
        self.assertEqual(ts.take_images(), [{"mime": "image/jpeg", "data": "SU1BR0U="}])
        self.assertEqual(ts.take_images(), [])
        ts.call("browser", {"action": "click", "target": "Toupees"})
        self.assertNotIn("session", self.calls[0])
        self.assertEqual(self.calls[1]["session"], "s1")  # same tab

    def test_working_alone_it_looks_and_clicks_but_does_not_type(self):
        ts = self.toolset(may_change=False)
        self.assertNotIn("error", ts.call("browser", {"action": "open", "url": "https://example.com"}))
        self.assertNotIn("error", ts.call("browser", {"action": "click", "x": 10, "y": 20}))
        for action in ("type", "press", "select"):
            self.assertIn("error", ts.call("browser", {"action": action, "target": "Search", "text": "wig"}))
        self.assertEqual(len(self.calls), 2)

    def test_a_live_call_gets_a_description_instead(self):
        import partner_gemini

        with mock.patch.object(partner_gemini, "describe_image", return_value="A hero banner with a man smiling."):
            result = self.toolset(images=False).call("browser", {"action": "open", "url": "https://example.com"})
        self.assertEqual(result["screenshot"], "A hero banner with a man smiling.")

    def test_not_offered_without_the_service(self):
        with mock.patch.object(tools, "BROWSER_URL", ""):
            self.assertNotIn("browser", [s["name"] for s in self.toolset().specs()])


class ImagesReachEachModelTests(unittest.TestCase):
    """One browser call, then an answer: the screenshot must be in what the model is sent next."""

    def setUp(self):
        patch = mock.patch.object(tools, "BROWSER_URL", "https://web-browser.example")
        patch.start()
        self.addCleanup(patch.stop)
        patch = mock.patch.object(tools, "_browser_call", return_value=copy.deepcopy(PAGE))
        patch.start()
        self.addCleanup(patch.stop)
        self.toolset = Toolset(MemoryStore(), None, caller="assistant")

    def test_claude_gets_it_inside_the_tool_result(self):
        import partner_claude

        sent = []
        replies = [SimpleNamespace(stop_reason="tool_use", content=[SimpleNamespace(
                       type="tool_use", id="t1", name="browser", input={"action": "open", "url": "https://x.com"})]),
                   SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="Looks fine.")])]

        def create(secrets, **kwargs):
            sent.append(copy.deepcopy(kwargs["messages"]))
            return replies[len(sent) - 1]

        with mock.patch.object(partner_claude, "_create", side_effect=create):
            out = partner_claude.respond("sys", [{"role": "user", "text": "look"}], self.toolset, MemorySecrets())
        self.assertEqual(out["text"], "Looks fine.")
        result = sent[1][-1]["content"][0]
        self.assertEqual(result["type"], "tool_result")
        self.assertEqual(result["content"][1]["type"], "image")
        self.assertEqual(result["content"][1]["source"]["data"], "SU1BR0U=")

    def test_chatgpt_gets_it_right_after_the_tool_messages(self):
        import partner_openai

        sent = []
        call = SimpleNamespace(id="c1", function=SimpleNamespace(name="browser", arguments='{"action":"open","url":"x.com"}'))
        first = SimpleNamespace(tool_calls=[call], content=None,
                                model_dump=lambda **k: {"role": "assistant", "tool_calls": [{"id": "c1"}]})
        second = SimpleNamespace(tool_calls=None, content="Looks fine.")

        def create(**kwargs):
            sent.append(copy.deepcopy(kwargs["messages"]))
            return SimpleNamespace(choices=[SimpleNamespace(message=[first, second][len(sent) - 1])])

        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        with mock.patch("openai.OpenAI", return_value=client):
            out = partner_openai.respond("sys", [{"role": "user", "text": "look"}], self.toolset,
                                         MemorySecrets({"CHATGPT_API_KEY": "k"}))
        self.assertEqual(out["text"], "Looks fine.")
        roles = [m["role"] for m in sent[1]]
        self.assertEqual(roles[-2:], ["tool", "user"])
        self.assertTrue(sent[1][-1]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,SU1BR0U="))

    def test_gemini_gets_it_in_the_function_response_turn(self):
        import partner_gemini

        sent = []
        replies = [{"candidates": [{"content": {"role": "model", "parts": [
                       {"functionCall": {"name": "browser", "args": {"action": "open", "url": "x.com"}}}]}}]},
                   {"candidates": [{"content": {"role": "model", "parts": [{"text": "Looks fine."}]}}]}]

        def post(url, headers=None, json=None, timeout=None):
            sent.append(copy.deepcopy(json))
            return SimpleNamespace(status_code=200, json=lambda: replies[len(sent) - 1])

        with mock.patch.object(partner_gemini, "_token", return_value="tok"), \
                mock.patch.object(partner_gemini.requests, "post", side_effect=post):
            out = partner_gemini.respond("sys", [{"role": "user", "text": "look"}], self.toolset)
        self.assertEqual(out["text"], "Looks fine.")
        parts = sent[1]["contents"][-1]["parts"]
        self.assertIn("functionResponse", parts[0])
        self.assertEqual(parts[1]["inlineData"], {"mimeType": "image/jpeg", "data": "SU1BR0U="})


if __name__ == "__main__":
    unittest.main()
