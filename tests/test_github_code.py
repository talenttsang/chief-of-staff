import json
import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("STORE_BACKEND", "memory")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "cloud_run"))

import github_code  # noqa: E402
import integrations  # noqa: E402
from store import MemorySecrets, MemoryStore  # noqa: E402
from tools import Toolset  # noqa: E402

API = "https://api.github.com/repos/talenttsang/chief-of-staff"


class FakeGitHub:
    """Just enough of the Git Data API for one commit; `moves` makes main move under the first update."""

    FILES = {"server.js": "\n".join(f"// line {n}" for n in range(1, 1001)).replace(
                 "// line 500", "app.post('/stripe/webhook', handleWebhook);"),
             "public/app.js": "const total = price * qty;\n", "logo.png": "x"}

    def __init__(self, moves=0):
        self.calls, self.moves, self.head = [], moves, "c0"

    def request(self, method, url, headers=None, auth=None, json=None, timeout=None):
        self.calls.append((method, url, json, headers))
        path = url.replace(API, "")
        if method == "GET" and path == "/git/ref/heads/main":
            body = {"object": {"sha": self.head}}
        elif method == "GET" and path.startswith("/git/commits/"):
            body = {"tree": {"sha": "t-" + path.rsplit("/", 1)[1]}}
        elif method == "GET" and path.startswith("/contents/"):
            name = path[len("/contents/"):].split("?")[0]
            if name not in self.FILES:
                return SimpleNamespace(status_code=404, ok=False, text='{"message": "Not Found"}')
            body = {"content": __import__("base64").b64encode(self.FILES[name].encode()).decode(), "sha": "b-" + name}
        elif method == "GET" and path.startswith("/git/trees/"):
            body = {"tree": [{"path": n, "type": "blob", "size": len(c)} for n, c in self.FILES.items()]}
        elif method == "POST" and path == "/git/trees":
            body = {"sha": "t-new"}
        elif method == "POST" and path == "/git/commits":
            body = {"sha": f"c{len(self.calls)}"}
        elif method == "PATCH" and path == "/git/refs/heads/main":
            if self.moves:
                self.moves -= 1
                self.head = "c-other"
                return SimpleNamespace(status_code=422, ok=False, text='{"message": "Update is not a fast forward"}')
            self.head = json["sha"]
            body = {"object": {"sha": json["sha"]}}
        else:
            return SimpleNamespace(status_code=404, ok=False, text='{"message": "Not Found"}')
        return SimpleNamespace(status_code=200, ok=True, text=__import__("json").dumps(body))


class CommitTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(github_code, "CODE_REPOS", ["talenttsang/chief-of-staff"])
        patcher.start()
        self.addCleanup(patcher.stop)
        self.secrets = MemorySecrets({"GITHUB_TOKEN": "github_pat_secret"})
        self.gh = FakeGitHub()

    def commit(self, files, repo="talenttsang/chief-of-staff", message="Fix the footer"):
        return github_code.commit(repo, message, files, self.secrets, http=self.gh)

    def test_one_commit_fast_forwards_main(self):
        result = self.commit([{"path": "src/a.py", "content": "x = 1\n"}, {"path": "old.txt", "delete": True}])
        self.assertTrue(result["committed"], result)
        trees = [c for c in self.gh.calls if c[1].endswith("/git/trees")][0][2]
        self.assertEqual(trees["base_tree"], "t-c0")
        self.assertEqual(trees["tree"][0]["content"], "x = 1\n")
        self.assertIsNone(trees["tree"][1]["sha"])                     # a deletion
        update = [c for c in self.gh.calls if c[0] == "PATCH"][0][2]
        self.assertEqual((update["sha"], update["force"]), (result["commit"], False))
        self.assertEqual(self.gh.calls[0][3]["Authorization"], "Bearer github_pat_secret")
        self.assertNotIn("github_pat_secret", json.dumps(result))

    def test_main_moving_is_retried_once_on_the_new_head(self):
        self.gh.moves = 1
        result = self.commit([{"path": "a.py", "content": "y = 2\n"}])
        self.assertTrue(result["committed"], result)
        parents = [c[2]["parents"] for c in self.gh.calls if c[1].endswith("/git/commits") and c[0] == "POST"]
        self.assertEqual(parents, [["c0"], ["c-other"]])

    def test_guards(self):
        self.assertIn("isn't a repository", self.commit([{"path": "a", "content": ""}], repo="evil/repo")["error"])
        self.assertIn("deploy setup", self.commit([{"path": ".github/workflows/deploy.yml", "content": ""}])["error"])
        self.assertIn("plain path", self.commit([{"path": "../x", "content": ""}])["error"])
        self.assertIn("plain path", self.commit([{"path": "/etc/x", "content": ""}])["error"])
        self.assertIn("full new content", self.commit([{"path": "a.py"}])["error"])
        self.assertIn("message", self.commit([{"path": "a.py", "content": ""}], message=" ")["error"])
        self.assertEqual(self.gh.calls, [])  # nothing reached GitHub

    def test_search_then_read_a_big_file_in_parts(self):
        found = github_code.search("talenttsang/chief-of-staff", "stripe/webhook", self.secrets, http=self.gh)
        self.assertEqual(found["matches"], [{"path": "server.js", "line": 500,
                                             "text": "app.post('/stripe/webhook', handleWebhook);"}])
        self.assertEqual(found["files_searched"], 2)  # text files only: not logo.png
        part = github_code.read("talenttsang/chief-of-staff", "server.js", self.secrets, start=495, end=505, http=self.gh)
        self.assertEqual((part["total_lines"], part["from"], part["to"]), (1000, 495, 505))
        self.assertIn("500: app.post('/stripe/webhook'", part["text"])
        big = github_code.read("talenttsang/chief-of-staff", "server.js", self.secrets, http=self.gh)
        self.assertEqual((big["to"], big["more"]), (400, "read on with start_line=401"))

    def test_a_commit_can_edit_part_of_a_file(self):
        result = self.commit([{"path": "server.js", "edits": [{"old": "handleWebhook);", "new": "handleStripe);"}]}])
        self.assertTrue(result["committed"], result)
        sent = [c for c in self.gh.calls if c[1].endswith("/git/trees") and c[0] == "POST"][0][2]["tree"][0]
        self.assertIn("app.post('/stripe/webhook', handleStripe);", sent["content"])
        self.assertEqual(sent["content"].count("\n"), 999)  # the rest of the file is untouched
        twice = self.commit([{"path": "server.js", "edits": [{"old": "// line 1", "new": "x"}]}])
        self.assertIn("appears", twice["error"])  # "// line 1" is in lines 1, 10-19, 100-199...: ambiguous

    def test_the_tool_records_the_commit(self):
        store = MemoryStore()
        tools = Toolset(store, None, caller="assistant", secrets=self.secrets)
        with mock.patch.object(integrations.requests, "request", self.gh.request):
            result = tools.call("commit_code", {"repo": "talenttsang/chief-of-staff", "message": "Fix the footer",
                                                "files": [{"path": "a.py", "content": "z = 3\n"}]})
        self.assertTrue(result["committed"], result)
        action = store.list_actions()[0]
        self.assertEqual(action["space_label"], "GitHub: talenttsang/chief-of-staff")
        self.assertIn(result["url"], action["reply_text"])


if __name__ == "__main__":
    unittest.main()
