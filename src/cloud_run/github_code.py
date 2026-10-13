"""Talent Solutions AI changing code: one commit straight to a repository's main branch.

The owner chose full autonomy: a commit needs no approval, and each repository's GitHub Actions workflow
deploys whatever lands on main. What still holds, whatever a model asks:
  - only the repositories in CODE_REPOS ("owner/repo" separated by ";", "," or spaces);
  - nothing under .github/ (the workflows that test and deploy stay as people set them up);
  - no path that climbs out of the repository, at most MAX_FILES files and MAX_FILE_BYTES each;
  - a commit only ever adds to main (a fast-forward); it never rewrites history.
The GitHub token stays on the server: every request goes through integrations.call.

Reading: search(repo, pattern) finds lines across a repository and read(repo, path, start, end) shows a file's
lines, so big files (HelperXpress's server.js) can be read in parts; a commit can change a file with small
find-and-replace edits instead of its whole content.
"""

import base64
import os
import re
from urllib.parse import quote

import integrations

CODE_REPOS = [r for r in re.split(r"[;,\s]+", os.environ.get("CODE_REPOS", "")) if r]
BRANCH = "main"
MAX_FILES = 50
MAX_FILE_BYTES = 2_000_000
READ_LINES = 400        # lines one read shows, at most
SEARCH_FILES = 120      # files one search looks through, at most
SEARCH_HITS = 60
TEXT = re.compile(r"\.(js|mjs|cjs|ts|tsx|jsx|json|py|html|css|scss|md|txt|sql|yml|yaml|toml|sh|ejs|hbs|vue|svelte|env\.example)$|"
                  r"(^|/)(Dockerfile|Procfile|Makefile)$", re.I)
PROTECTED = (".github/",)


def _bad_path(path):
    if not path or path.startswith("/") or "\\" in path or any(p in ("", ".", "..") for p in path.split("/")):
        return f"'{path}' isn't a plain path inside the repository"
    if any(path.lower().startswith(p) for p in PROTECTED):
        return f"'{path}' is part of the test and deploy setup, which only people change"
    return ""


def _gh(method, path, secrets, body=None, http=None):
    result = integrations.call("github", method, path, secrets, body=body, confirmed=True, session=http,
                               max_chars=4 * MAX_FILE_BYTES)  # whole files (base64); the model sees only parts
    if not result.get("ok") or result.get("truncated"):
        detail = result.get("error") or f"HTTP {result.get('status')}: {str(result.get('data'))[:300]}"
        raise RuntimeError(f"GitHub {method} {path.split('?')[0]} failed: {detail}")
    return result["data"]


def _file(repo, path, secrets, ref=BRANCH, http=None):
    """A file's text at `ref`. The contents API stops at 1 MB; bigger files come through their blob."""
    data = _gh("GET", f"/repos/{repo}/contents/{quote(path)}?ref={ref}", secrets, http=http)
    if isinstance(data, list):
        raise RuntimeError(f"'{path}' is a folder")
    raw = data.get("content") or ""
    if not raw and data.get("sha"):
        raw = _gh("GET", f"/repos/{repo}/git/blobs/{data['sha']}", secrets, http=http).get("content") or ""
    return base64.b64decode(raw).decode("utf-8", errors="replace")


def read(repo, path, secrets, start=1, end=None, http=None):
    """Lines start..end of a file (numbered, at most READ_LINES), and how many lines it has."""
    try:
        lines = _file(repo, str(path).strip("/"), secrets, http=http).split("\n")
    except RuntimeError as exc:
        return {"error": str(exc)[:400]}
    start = max(1, int(start or 1))
    end = min(len(lines), int(end or start + READ_LINES - 1), start + READ_LINES - 1)
    return {"repo": repo, "path": path, "total_lines": len(lines), "from": start, "to": end,
            "text": "\n".join(f"{n}: {lines[n - 1]}" for n in range(start, end + 1)),
            **({"more": f"read on with start_line={end + 1}"} if end < len(lines) else {})}


def search(repo, pattern, secrets, path="", http=None):
    """Lines matching `pattern` (a regular expression, case-insensitive; or plain text) in the repository's text
    files, optionally under `path`."""
    try:
        rx = re.compile(pattern, re.I)
    except re.error:
        rx = re.compile(re.escape(pattern), re.I)
    try:
        tree = _gh("GET", f"/repos/{repo}/git/trees/{BRANCH}?recursive=1", secrets, http=http).get("tree") or []
    except RuntimeError as exc:
        return {"error": str(exc)[:400]}
    prefix = str(path or "").strip("/")
    files = [t["path"] for t in tree if t.get("type") == "blob" and TEXT.search(t["path"])
             and "node_modules/" not in t["path"] and (not prefix or t["path"].startswith(prefix))
             and t.get("size", 0) <= MAX_FILE_BYTES]
    hits = []
    for name in files[:SEARCH_FILES]:
        try:
            text = _file(repo, name, secrets, http=http)
        except RuntimeError:
            continue
        for n, line in enumerate(text.split("\n"), 1):
            if rx.search(line):
                hits.append({"path": name, "line": n, "text": line.strip()[:200]})
                if len(hits) >= SEARCH_HITS:
                    return {"matches": hits, "note": f"stopped at {SEARCH_HITS} matches: narrow the pattern or path"}
    return {"matches": hits, "files_searched": min(len(files), SEARCH_FILES),
            **({"note": f"only the first {SEARCH_FILES} of {len(files)} files: give a path"} if len(files) > SEARCH_FILES else {})}


def _apply_edits(text, edits, path):
    """Each edit's `old` must appear exactly once, so a change never lands in the wrong place."""
    for i, edit in enumerate(edits, 1):
        old, new = (edit or {}).get("old"), (edit or {}).get("new")
        if not isinstance(old, str) or not old or not isinstance(new, str):
            raise ValueError(f"'{path}' edit {i}: give old (text in the file now) and new")
        count = text.count(old)
        if count != 1:
            raise ValueError(f"'{path}' edit {i}: the old text appears {count} times; it must appear exactly once "
                             "(copy it exactly from read_code, with enough lines around it to be unique)")
        text = text.replace(old, new)
    return text


def commit(repo, message, files, secrets, http=None):
    """Commit `files` to main in one commit: each {path, content} (the whole new file), {path, edits: [{old, new}]}
    (find-and-replace in the current file) or {path, delete: true}. Returns {commit, url}."""
    match = next((r for r in CODE_REPOS if r.lower() == str(repo).lower()), None)
    if not match:
        return {"error": f"'{repo}' isn't a repository you may change. Allowed: {', '.join(CODE_REPOS) or 'none'}"}
    message = str(message or "").strip()
    if not message:
        return {"error": "write a commit message: what changed and why"}
    if not isinstance(files, list) or not files:
        return {"error": "files must be a list of {path, content}, {path, edits: [{old, new}]} or {path, delete: true}"}
    if len(files) > MAX_FILES:
        return {"error": f"at most {MAX_FILES} files per commit"}
    tree = []
    for f in files:
        path = str((f or {}).get("path", "")).strip()
        problem = _bad_path(path)
        if problem:
            return {"error": problem}
        if f.get("delete"):
            tree.append({"path": path, "mode": "100644", "type": "blob", "sha": None})
            continue
        if f.get("edits"):
            if not isinstance(f["edits"], list):
                return {"error": f"'{path}': edits must be a list of {{old, new}}"}
            tree.append({"path": path, "edits": f["edits"]})  # applied to main's current file below
            continue
        content = f.get("content")
        if not isinstance(content, str):
            return {"error": f"'{path}': give the file's full new content as text, edits, or delete: true"}
        if len(content.encode("utf-8")) > MAX_FILE_BYTES:
            return {"error": f"'{path}' is over {MAX_FILE_BYTES // 1000} KB"}
        tree.append({"path": path, "mode": "100644", "type": "blob", "content": content})
    try:
        for attempt in (1, 2):  # main may move between reading it and updating it; try once more
            head = _gh("GET", f"/repos/{match}/git/ref/heads/{BRANCH}", secrets, http=http)["object"]["sha"]
            base_tree = _gh("GET", f"/repos/{match}/git/commits/{head}", secrets, http=http)["tree"]["sha"]
            entries = []
            for entry in tree:
                if "edits" in entry:
                    text = _apply_edits(_file(match, entry["path"], secrets, ref=head, http=http), entry["edits"],
                                        entry["path"])
                    entry = {"path": entry["path"], "mode": "100644", "type": "blob", "content": text}
                entries.append(entry)
            new_tree = _gh("POST", f"/repos/{match}/git/trees", secrets,
                           body={"base_tree": base_tree, "tree": entries}, http=http)["sha"]
            new = _gh("POST", f"/repos/{match}/git/commits", secrets,
                      body={"message": message, "tree": new_tree, "parents": [head]}, http=http)["sha"]
            try:
                _gh("PATCH", f"/repos/{match}/git/refs/heads/{BRANCH}", secrets,
                    body={"sha": new, "force": False}, http=http)
                break
            except RuntimeError:
                if attempt == 2:
                    raise
    except (RuntimeError, ValueError) as exc:
        return {"error": str(exc)[:600]}
    return {"committed": True, "repo": match, "branch": BRANCH, "commit": new,
            "url": f"https://github.com/{match}/commit/{new}",
            "deploy": (f"The push starts the repository's deploy workflow. Check it with call_api GET "
                       f"/repos/{match}/actions/runs?head_sha={new}, and report whether it succeeded.")}
