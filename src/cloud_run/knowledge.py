"""What the assistant knows about the companies, and how that knowledge is shown to a model.

Three layers:
  1. knowledge/company.md: the stable background (the businesses, people, how to work),
     copied from the repo's CLAUDE.md and shipped with the service.
  2. The knowledge base in the store: projects, facts, open questions for the
     owner, and the systems the assistant can or can't reach yet. It grows from
     the hourly Chat read, from the owner's answers and from conversations.
  3. brief(): a compact text view of (2) that goes into every prompt.

SEED_SYSTEMS and SEED_QUESTIONS are written once, on first start; after that
the knowledge base is the owner's and the assistant's to edit.
"""

import hashlib
import os
import re

from store import AUTONOMY_CATEGORIES, utcnow_iso

_COMPANY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "knowledge", "company.md")
_company_cache = None

# status: connected (the assistant can use it now), available (credentials exist in the
# project but the assistant can't use them yet), needed, requested, not_used.
SEED_SYSTEMS = [
    ("google_calendar", "Google 日曆", "溝通", "connected", "讀取和建立日曆活動。"),
    # The AI rows' status is kept true by talk.Talk.test_partners (Check connections and every hourly run).
    ("gemini", "Gemini (Vertex AI)", "AI", "connected", "Talent Solutions AI 自己的大腦和聲音。"),
    ("chatgpt", "ChatGPT (OpenAI)", "AI 夥伴", "needed",
     "ChatGPT 提供第二意見、草稿和即時語音通話。需要 Secret Manager 中的 CHATGPT_API_KEY。"),
    ("claude", "Claude (Anthropic)", "AI 夥伴", "needed",
     "Claude 提供第二意見、草稿和程式碼審查。需要 Secret Manager 中的 ANTHROPIC_API_KEY。"),
    ("project_tracker", "項目追蹤（GitHub Projects、Jira、Linear？）", "工程", "needed",
     "如果不是 GitHub issues，任務、錯誤和衝刺放在哪裡。"),
    ("hosting", "託管（Google Cloud、Vercel、AWS？）", "工程", "needed",
     "HelperConnects 和 HelperXpress 應用程式的部署、日誌和費用。"),
    ("error_tracking", "錯誤追蹤（Sentry？）", "工程", "needed", "需要處理的正式環境錯誤。"),
    ("team_chat", "團隊通訊（Slack、WhatsApp、Discord？）", "溝通", "needed",
     "團隊在哪裡溝通。"),
]

SEED_QUESTIONS = [
    ("HelperConnects 做甚麼、服務誰、怎樣賺錢？HelperXpress 也一樣，兩者有甚麼關係？",
     "不了解的業務，我無法管理。", 1),
    ("每間公司有誰參與（開發、設計、營運、外判），誰負責決定甚麼？",
     "讓我把工作交給合適的人。", 1),
    ("哪些 GitHub 儲存庫屬於哪個產品？技術架構是甚麼（前端、後端、資料庫、託管）？",
     "工程工作要先了解程式碼。", 1),
    ("列出進行中的項目和功能，每行一個：名稱、目標、負責人、狀態、截止日期。",
     "我用項目來組織工作。", 1),
    ("任務和錯誤現在放在哪裡（GitHub issues、Jira、Linear、Notion、試算表）？怎樣發佈新版本？",
     "讓我知道要維護哪個工作板。", 2),
    ("每個產品未來 90 日最重要的 3 個目標是甚麼？如有數字請提供。",
     "目標決定我先推進甚麼。", 2),
    ("你會給我哪些系統的權限：GitHub、託管、錯誤追蹤、分析、付款、團隊通訊？",
     "每個連接都讓我可以直接做事，而不只是發問。", 2),
    ("我的底線是甚麼：哪些事沒有你同意絕對不能碰、部署或說？",
     "為自動執行設定界限。", 1),
]


def company_background():
    global _company_cache
    if _company_cache is None:
        try:
            with open(_COMPANY_FILE, encoding="utf-8") as handle:
                _company_cache = handle.read()
        except OSError:
            _company_cache = ""
    return _company_cache


def vocabulary(store):
    """Words a voice line must hear right: the company background, plus every project and system name."""
    names = lambda kind: ", ".join(sorted({i.get("name", "") for i in store.list_items(kind)} - {""}))  # noqa: E731
    return "\n\n".join(part for part in (company_background(), f"Projects: {names('projects')}",
                                           f"Systems: {names('systems')}") if not part.endswith(": "))


def name_hints(store, limit=900):
    """A short list of names for speech-to-text: company terms, people and places, projects, systems."""
    names = ["HelperConnects", "HelperXpress", "Talent Solutions AI", "GitHub"]
    for line in company_background().splitlines():
        if line.startswith("|") and not line.startswith("|--") and "`" not in line:
            for cell in line.strip("|").split("|"):
                names += [n.strip() for n in cell.split(",") if re.fullmatch(r"[A-Z][\w' .-]{1,30}", n.strip())]
    for kind in ("projects", "systems"):
        names += [i.get("name", "") for i in store.list_items(kind)]
    seen, out = set(), []
    for n in names:
        if n and n.lower() not in seen:
            seen.add(n.lower())
            out.append(n)
    return ", ".join(out)[:limit]


def slug(text, prefix=""):
    base = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:48] or "item"
    digest = hashlib.sha1((text or "").lower().encode("utf-8")).hexdigest()[:6]
    return f"{prefix}{base}-{digest}"


def ensure_seeded(store):
    """Write the starting systems and interview questions once."""
    if store.get_flag("seeded_v1"):
        return
    now = utcnow_iso()
    for sid, name, category, status, unlocks in SEED_SYSTEMS:
        if not store.get_item("systems", sid):
            store.save_item("systems", sid, {"name": name, "category": category, "status": status,
                                             "unlocks": unlocks, "source": "seed", "created_at": now, "updated_at": now})
    for i, (question, why, priority) in enumerate(SEED_QUESTIONS):
        qid = slug(question, "q-")
        if not store.get_item("questions", qid):
            store.save_item("questions", qid, {"question": question, "why": why, "priority": priority,
                                               "status": "open", "source": "round 1", "project_id": "",
                                               "created_at": f"{now}#{i:02d}", "updated_at": now})
    store.set_flag("seeded_v1", now)


def add_fact(store, text, topic="general", project_id="", source=""):
    text = (text or "").strip()
    if not text:
        return None
    fid = slug(text, "f-")
    if store.get_item("facts", fid):
        return store.get_item("facts", fid)
    now = utcnow_iso()
    return store.save_item("facts", fid, {"text": text[:1000], "topic": topic or "general", "project_id": project_id or "",
                                          "source": source[:120], "created_at": now, "updated_at": now})


def add_question(store, question, why="", project_id="", priority=2, source=""):
    question = (question or "").strip()
    if not question:
        return None
    qid = slug(question, "q-")
    existing = store.get_item("questions", qid)
    if existing:
        return existing
    now = utcnow_iso()
    return store.save_item("questions", qid, {"question": question[:600], "why": (why or "")[:300],
                                              "project_id": project_id or "", "priority": int(priority or 2),
                                              "status": "open", "source": source[:120], "created_at": now,
                                              "updated_at": now})


def find_project(store, name_or_id):
    if not name_or_id:
        return None
    item = store.get_item("projects", name_or_id)
    if item:
        return item
    wanted = name_or_id.strip().lower()
    for p in store.list_items("projects"):
        if p.get("name", "").strip().lower() == wanted:
            return p
    return None


def save_project(store, fields, source=""):
    """Create or update a project by id or exact name. Returns the saved project."""
    existing = find_project(store, fields.get("project_id") or fields.get("name"))
    now = utcnow_iso()
    clean = {k: (str(v)[:2000] if isinstance(v, str) else v) for k, v in fields.items()
             if k in ("name", "company", "goal", "owner", "status", "deadline", "summary", "next_steps") and v not in (None, "")}
    if existing:
        return store.save_item("projects", existing["id"], {**clean, "updated_at": now})
    if not clean.get("name"):
        return None
    pid = slug(clean["name"], "p-")
    return store.save_item("projects", pid, {"company": "", "goal": "", "owner": "", "status": "active", "deadline": "",
                                             "summary": "", "next_steps": "", **clean,
                                             "source": source[:120], "created_at": now, "updated_at": now})


def brief(store, settings=None, facts_limit=60, compact=False):
    """A compact picture of the knowledge base for a prompt."""
    projects = store.list_items("projects")
    lines = [f"PROJECTS ({len(projects)})"]
    for p in projects[:80]:
        meta = " · ".join(x for x in (p.get("company"), p.get("status"), p.get("owner") and f"owner {p['owner']}",
                                      p.get("deadline") and f"due {p['deadline']}") if x)
        if compact:
            lines.append(f"- {p.get('name')} [{p['id']}]")
        else:
            lines.append(f"- {p.get('name')} [{p['id']}] ({meta}) {p.get('goal') or p.get('summary') or ''}".rstrip())
    if not projects:
        lines.append("- none recorded yet")
    if compact:
        return "\n".join(lines)

    facts = store.list_items("facts")[:facts_limit]
    lines.append(f"\nWHAT YOU'VE LEARNED ({len(facts)} most recent)")
    lines += [f"- {f.get('text')}" + (f" (project {f['project_id']})" if f.get("project_id") else "") for f in facts]
    if not facts:
        lines.append("- nothing yet")

    questions = sorted((q for q in store.list_items("questions") if q.get("status") == "open"),
                       key=lambda q: (q.get("priority", 2), q.get("created_at", "")))
    lines.append(f"\nOPEN QUESTIONS FOR THE OWNER ({len(questions)})")
    lines += [f"- [{q['id']}] {q.get('question')}" for q in questions[:12]]

    systems = store.list_items("systems")
    by_status = {}
    for s in systems:
        by_status.setdefault(s.get("status", "needed"), []).append(s.get("name", s["id"]))
    lines.append("\nSYSTEMS")
    for status in ("connected", "available", "requested", "needed"):
        if by_status.get(status):
            lines.append(f"- {status}: {', '.join(sorted(by_status[status]))}")

    if settings:
        autonomy = settings.get("autonomy", {})
        lines.append("\nAUTONOMY (auto = do it; ask = propose and wait for the owner)")
        lines.append("- automatic actions are " + ("ON" if settings.get("auto_act", True) else "PAUSED (everything asks)"))
        lines += [f"- {AUTONOMY_CATEGORIES[k]}: {autonomy.get(k, 'ask')}" for k in AUTONOMY_CATEGORIES]
    return "\n".join(lines)
