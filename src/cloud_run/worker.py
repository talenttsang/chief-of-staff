"""The assistant working board tasks on its own.

A task whose owner is "Assistant" (the board's "Give to assistant" button, or the
assistant assigning itself) gets worked: right away when it's handed over, and
again in each hourly run until it's done. The assistant uses every tool it has:
company systems (call_api), the web (web_search, read_webpage), chats, calendar
and the knowledge base.

No one is in the conversation while it works, so it can't change data in a system
or message anyone (the toolset's may_change is off): it prepares the exact change
or text and asks for Manne's OK in its report. The report goes on the task and into
the Assistant's chat conversation.
"""

import os
from datetime import datetime, timedelta, timezone

from store import utcnow_iso
from zh import to_hk

OWNER = "Assistant"
WORK_PER_RUN = int(os.environ.get("WORK_PER_RUN", "3"))   # tasks the hourly run works, at most
REWORK_HOURS = 6        # a task in progress is picked up again after this long
STALL_MINUTES = 6       # a task being worked with no step for this long was cut off (a deploy, a crash)
ROUNDS = int(os.environ.get("WORK_ROUNDS", "8"))  # a handed-over task goes on round after round, at most this many
MAX_PARALLEL = 3        # tasks worked at the same time; more wait for the hourly run

PROMPT = """\
You're working on a board task on your own. No one else is in this conversation.

Task: {title}
{detail}Priority: {priority}. Due: {due}. Status: {status}.{project}{previous}

Do as much of it as you can with your tools, for real: look things up in company systems (list_integrations,
then call_api), research the web (web_search, read_webpage, and browser to see and click through a page), read
the relevant chats, check the calendar and
the knowledge base. Chain as many calls as it takes.

This is your whole turn: do the work now and deliver what the task asks for (the summary, the numbers, the
draft) in your report. Don't say you'll do something later or "keep him updated". If a tool fails, try another
way once; if a source isn't connected, say which one (Manne can press Connect on the Access tab) and finish
everything else.

You can't change data in a system (call_api, query_database) or message anyone while working alone. If the task
needs that, prepare it exactly (the request with its body, or the message text) and say it needs Manne's OK. In the
browser you may type and fill in forms with test accounts and made-up data, e.g. to test a flow end to end; never
pay or enter card details.

When the task is complete, call update_task with status done. If only Manne can unblock it, ask_owner with
one precise question. Record anything worth keeping (record_fact, save_project).

Problems you find go on the board and get fixed, not just reported. For each real problem in the app (a bug, an
error, a broken or confusing page, a step a user can't get past): list_tasks first so you don't add it twice, then
create_task (title: what's wrong; detail: the page, the steps to reproduce, what happened and what should happen;
the same project). Leave the owner empty: a task you add while working is yours and starts at once (set an owner
only for something a person must do). Working that task means fixing it: find the code
(call_api on GitHub), fix it with commit_code, check the deploy, and test it again. Then carry on with this task.
Trouble with your own browser (an element you couldn't find or click) is not a problem in the app: try another
way first (x,y from the screenshot, the label from elements).

You get several rounds: if you run out of steps, report where you are and the next round continues from there.

Finish with a short report for Manne: what you found or did (numbers, names, links), the tasks you added for
problems, what's waiting for his OK, and what's left."""


def is_mine(task):
    return (task.get("owner") or "").strip().lower() in ("assistant", "ai", "ai assistant") and task.get("status") != "done"


def _busy(task, now):
    """Being worked right now: started, and a step (the heartbeat) not long ago. One task isn't worked twice."""
    if not task.get("working_since"):
        return False
    try:
        last = max(datetime.fromisoformat(task["working_since"]), datetime.fromisoformat(task.get("heartbeat") or task["working_since"]))
    except ValueError:
        return False
    return now - last < timedelta(minutes=STALL_MINUTES)


def interrupted(task, now):
    """Started but gone quiet: the work was cut off, e.g. a deploy replaced the server mid-round."""
    return bool(task.get("working_since")) and not _busy(task, now)


def due_for_work(task, now):
    if not is_mine(task) or _busy(task, now):
        return False
    if task.get("interrupted") or not task.get("worked_at"):
        return True
    try:
        return now - datetime.fromisoformat(task["worked_at"]) >= timedelta(hours=REWORK_HOURS)
    except ValueError:
        return True


class Busy(RuntimeError):
    pass


def work_on(service, store, task_id, partner="assistant"):
    """Work one task now. Returns the report (text) and the tools used."""
    import talk
    from tools import Toolset

    now = datetime.now(timezone.utc)
    task = store.get_task(task_id)
    if not task:
        raise KeyError(task_id)
    if _busy(task, now):
        raise Busy("The assistant is already working on this task.")
    store.save_task(task_id, {"working_since": utcnow_iso(), "heartbeat": "", "interrupted": False, "owner": OWNER,
                              "owner_is_me": False,
                              **({"status": "in_progress"} if task.get("status") == "todo" else {})})
    project = store.get_item("projects", task["project_id"]) if task.get("project_id") else None
    prompt = PROMPT.format(
        title=task.get("title", ""), detail=f"Details: {task['detail']}\n" if task.get("detail") else "",
        priority=task.get("priority", "medium"), due=task.get("due") or "none", status=task.get("status", "todo"),
        project=f" Project: {project['name']}." if project else "",
        previous=f"\n\nYour report last time ({task.get('worked_at', '')[:16]}):\n{task['result'][:3000]}"
        if task.get("result") else "")
    toolset = Toolset(store, service._google(), caller=f"{partner} (task)", secrets=service.secrets,
                      consult=service.consult_fn(partner), may_change=False, starter=service.starter)
    toolset.task_id = task_id
    title = task.get("title", "")
    store.add_step({"caller": toolset.caller, "tool": "task_started", "detail": title[:240], "ok": True, "task_id": task_id})
    try:
        result = talk.PARTNERS[partner].respond(talk.system_prompt(store, partner, owner_email=service.owner_email),
                                                [{"role": "user", "text": prompt}], toolset, service.secrets)
    except Exception as exc:
        store.save_task(task_id, {"working_since": "", "work_error": str(exc)[:300], "worked_at": utcnow_iso()})
        store.add_step({"caller": toolset.caller, "tool": "task_failed", "detail": title[:240], "ok": False,
                        "error": str(exc)[:200], "task_id": task_id})
        raise
    report = to_hk((result.get("text") or "").strip()) or "（沒有報告）"
    tools = result.get("tools", [])
    finished = store.get_task(task_id) or {}
    store.save_task(task_id, {"working_since": "", "work_error": "", "worked_at": utcnow_iso(), "result": report[:6000],
                              "work_tools": sorted({t.get("tool") for t in tools if t.get("tool")})})
    done = finished.get("status") == "done"
    if done:
        store.add_step({"caller": toolset.caller, "tool": "task_done", "detail": f"{title[:120]}：{report[:300]}",
                        "ok": True, "task_id": task_id})
    else:
        store.add_step({"caller": toolset.caller, "tool": "task_report", "detail": f"{title[:120]}：{report[:300]}",
                        "ok": True, "task_id": task_id})
    store.append_talk(partner, [{"role": "assistant", "at": utcnow_iso(), "task_id": task_id, "tools": tools[-15:],
                                 "text": f"Worked on the task “{title}” ({'done' if done else 'still open'}).\n\n{report}"}])
    return {"report": report, "tools": tools, "done": done}


def _stuck(tools):
    """A round that got nowhere: no tool calls, or most of them failed."""
    return not tools or sum(1 for t in tools if not t.get("ok", True)) * 2 > len(tools)


def work_until_stuck(service, store, task_id, rounds=ROUNDS):
    """Work a task round after round until it's done, needs Manne, stops getting anywhere, or the rounds run out.
    The last step in the work log says which, so the page can tell people."""
    asked = False
    for _ in range(max(1, rounds)):
        outcome = work_on(service, store, task_id)
        if outcome["done"]:
            return outcome
        asked = any(t.get("tool") == "ask_owner" and t.get("ok", True) for t in outcome["tools"])
        if asked or _stuck(outcome["tools"]):
            break
    task = store.get_task(task_id) or {}
    stuck = _stuck(outcome["tools"])
    if asked:
        why, tool = "需要你回答問題（見「問題」分頁）", "task_needs_you"
    elif stuck:
        why, tool = "卡住了：這一輪沒有進展", "task_paused"
    else:
        why, tool = f"已處理 {rounds} 輪，{REWORK_HOURS} 小時內會自動繼續", "task_paused"
    store.add_step({"caller": "assistant (task)", "tool": tool, "detail": f"{task.get('title', '')[:120]}：{why}",
                    "ok": not stuck, "task_id": task_id})
    return dict(outcome, stopped=why)


def resume_interrupted(store, starter):
    """Find tasks whose work was cut off, say so in the work log (the page tells people), and start them again.
    Called every few minutes (POST /watchdog) and by the hourly run."""
    now = datetime.now(timezone.utc)
    resumed = []
    for task in store.list_tasks():
        if not (is_mine(task) and interrupted(task, now)):
            continue
        store.save_task(task["id"], {"working_since": "", "interrupted": True})
        again = busy_count(store) < MAX_PARALLEL
        store.add_step({"caller": "assistant (task)", "tool": "task_interrupted", "ok": True, "task_id": task["id"],
                        "detail": f"{task.get('title', '')[:120]}：工作被中斷（例如系統更新），"
                                  + ("已自動重新開始" if again else "下次每小時執行時會繼續")})
        if again:
            starter(task["id"])
        resumed.append(task["id"])
    return resumed


def busy_count(store):
    now = datetime.now(timezone.utc)
    return sum(1 for t in store.list_tasks() if _busy(t, now))


def work_due(service, store, limit=WORK_PER_RUN):
    """The hourly run's part: work the assistant's own tasks that are due, oldest first."""
    now = datetime.now(timezone.utc)
    due = [t for t in store.list_tasks() if due_for_work(t, now)]
    due.sort(key=lambda t: (t.get("worked_at") or "", {"high": 0, "medium": 1, "low": 2}.get(t.get("priority"), 1)))
    worked = []
    for task in due[:limit]:
        try:
            outcome = work_until_stuck(service, store, task["id"], rounds=1)
            worked.append({"task": task["id"], "title": task.get("title", ""), "done": outcome["done"]})
        except Exception as exc:  # noqa: BLE001 - one task's failure mustn't stop the others
            worked.append({"task": task["id"], "title": task.get("title", ""), "error": str(exc)[:200]})
    return worked
