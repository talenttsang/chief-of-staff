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
    ("google_calendar", "Google Calendar", "Communication", "connected", "Read and create calendar events."),
    # The AI rows' status is kept true by talk.Talk.test_partners (Check connections and every hourly run).
    ("gemini", "Gemini (Vertex AI)", "AI", "connected", "Talent Solutions AI's own brain and voice."),
    ("chatgpt", "ChatGPT (OpenAI)", "AI partner", "needed",
     "Second opinions, drafts and live voice calls from ChatGPT. Needs CHATGPT_API_KEY in Secret Manager."),
    ("claude", "Claude (Anthropic)", "AI partner", "needed",
     "Second opinions, drafts and code review from Claude. Needs ANTHROPIC_API_KEY in Secret Manager."),
    ("project_tracker", "Project tracker (GitHub Projects, Jira, Linear?)", "Engineering", "needed",
     "Where tasks, bugs and sprints live, if not GitHub issues."),
    ("hosting", "Hosting (Google Cloud, Vercel, AWS?)", "Engineering", "needed",
     "Deploys, logs and costs of the HelperConnects and HelperXpress apps."),
    ("error_tracking", "Error tracking (Sentry?)", "Engineering", "needed", "Production errors to triage."),
    ("team_chat", "Team chat (Slack, WhatsApp, Discord?)", "Communication", "needed",
     "Where the team talks."),
]

SEED_QUESTIONS = [
    ("What does HelperConnects do, for whom, and how does it make money? The same for HelperXpress, and how do "
     "the two relate?", "I can't run a business I can't describe.", 1),
    ("Who works on each one (developers, designers, operations, contractors), and who decides what?",
     "So I route work to the right people.", 1),
    ("Which GitHub repositories belong to each product, and what's the stack (frontend, backend, database, "
     "hosting)?", "Engineering work starts with knowing the code.", 1),
    ("List the active projects and features in flight, one line each: name, goal, owner, status, deadline.",
     "Projects are how I organise the work.", 1),
    ("Where do tasks and bugs live today (GitHub issues, Jira, Linear, Notion, a sheet)? How do releases happen?",
     "Tells me which board to keep current.", 2),
    ("What are the top 3 goals for each product for the next 90 days, with numbers if you have them?",
     "Goals decide what I push first.", 2),
    ("Which systems will you give me access to: GitHub, hosting, error tracking, analytics, payments, team chat?",
     "Each connection lets me do work instead of asking about it.", 2),
    ("What are my hard limits: what must I never touch, deploy or say without you?",
     "Sets the guardrails for autopilot.", 1),
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
