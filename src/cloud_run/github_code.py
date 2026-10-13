"""Talent Solutions AI changing code: one commit straight to a repository's main branch.

The owner chose full autonomy: a commit needs no approval, and each repository's GitHub Actions workflow
deploys whatever lands on main. What still holds, whatever a model asks:
  - only the repositories in CODE_REPOS ("owner/repo" separated by ";", "," or spaces);
  - nothing under .github/ (the workflows that test and deploy stay as people set them up);
  - no path that climbs out of the repository, at most MAX_FILES files and MAX_FILE_BYTES each;
  - a commit only ever adds to main (a fast-forward); it never rewrites history.
The GitHub token stays on the server: every request goes through integrations.call.
"""

import os
import re

import integrations

CODE_REPOS = [r for r in re.split(r"[;,\s]+", os.environ.get("CODE_REPOS", "")) if r]
BRANCH = "main"
MAX_FILES = 50
MAX_FILE_BYTES = 500_000
PROTECTED = (".github/",)


def _bad_path(path):
    if not path or path.startswith("/") or "\\" in path or any(p in ("", ".", "..") for p in path.split("/")):
        return f"'{path}' isn't a plain path inside the repository"
    if any(path.lower().startswith(p) for p in PROTECTED):
        return f"'{path}' is part of the test and deploy setup, which only people change"
    return ""


def _gh(method, path, secrets, body=None, http=None):
    result = integrations.call("github", method, path, secrets, body=body, confirmed=True, session=http)
    if not result.get("ok"):
        detail = result.get("error") or f"HTTP {result.get('status')}: {str(result.get('data'))[:300]}"
        raise RuntimeError(f"GitHub {method} {path.split('?')[0]} failed: {detail}")
    return result["data"]


def commit(repo, message, files, secrets, http=None):
    """Commit `files` ([{path, content} or {path, delete: true}]) to main in one commit. Returns {commit, url}."""
    match = next((r for r in CODE_REPOS if r.lower() == str(repo).lower()), None)
    if not match:
        return {"error": f"'{repo}' isn't a repository you may change. Allowed: {', '.join(CODE_REPOS) or 'none'}"}
    message = str(message or "").strip()
    if not message:
        return {"error": "write a commit message: what changed and why"}
    if not isinstance(files, list) or not files:
        return {"error": "files must be a list of {path, content} or {path, delete: true}"}
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
        content = f.get("content")
        if not isinstance(content, str):
            return {"error": f"'{path}': give the file's full new content as text (or delete: true)"}
        if len(content.encode("utf-8")) > MAX_FILE_BYTES:
            return {"error": f"'{path}' is over {MAX_FILE_BYTES // 1000} KB"}
        tree.append({"path": path, "mode": "100644", "type": "blob", "content": content})
    try:
        for attempt in (1, 2):  # main may move between reading it and updating it; try once more
            head = _gh("GET", f"/repos/{match}/git/ref/heads/{BRANCH}", secrets, http=http)["object"]["sha"]
            base_tree = _gh("GET", f"/repos/{match}/git/commits/{head}", secrets, http=http)["tree"]["sha"]
            new_tree = _gh("POST", f"/repos/{match}/git/trees", secrets,
                           body={"base_tree": base_tree, "tree": tree}, http=http)["sha"]
            new = _gh("POST", f"/repos/{match}/git/commits", secrets,
                      body={"message": message, "tree": new_tree, "parents": [head]}, http=http)["sha"]
            try:
                _gh("PATCH", f"/repos/{match}/git/refs/heads/{BRANCH}", secrets,
                    body={"sha": new, "force": False}, http=http)
                break
            except RuntimeError:
                if attempt == 2:
                    raise
    except RuntimeError as exc:
        return {"error": str(exc)[:600]}
    return {"committed": True, "repo": match, "branch": BRANCH, "commit": new,
            "url": f"https://github.com/{match}/commit/{new}",
            "deploy": (f"The push starts the repository's deploy workflow. Check it with call_api GET "
                       f"/repos/{match}/actions/runs?head_sha={new}, and report whether it succeeded.")}
