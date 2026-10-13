"""Talent Solutions AI: a chief of staff that keeps a project board, learns the businesses and works its own tasks.

Routes
  GET  /                 the board (the password page when signed out)
  POST /login            sign in with the password
  GET  /connect/google   connect Talent Solutions AI's Google account (GOOGLE_ACCOUNT): Gmail, Calendar, Drive, ...
  GET  /oauth/callback   OAuth redirect target
  POST /logout
  POST /api/password     change the password (everyone else is signed out)
  GET  /api/state        tasks, activity, runs, settings, connection status
  GET  /api/progress     live progress of the running (or last) pass
  POST/PATCH/DELETE /api/tasks[/<id>]
  PATCH /api/settings
  POST /api/run          run now (from the UI)
  POST /run              hourly run (Cloud Scheduler, X-Run-Token header)

Sign-in is one shared password, kept in Secret Manager (chat-assistant-password); there is no Google
sign-in. A browser stays signed in for SESSION_DAYS after its last visit, until the password changes.
Wrong passwords are limited per address and overall (LOGIN_LIMITS).

Talent Solutions AI's Google account (GOOGLE_ACCOUNT) is connected once with Connect Google; its token lives in
Secret Manager (chat-assistant-user-token), readable only by this service's own service account. A
token from any other account is ignored.
"""

import hmac
import json
import re
import os
import secrets as pysecrets
import time
from datetime import timedelta
from urllib.parse import urlsplit

from flask import Flask, jsonify, redirect, render_template, request, session
from werkzeug.middleware.proxy_fix import ProxyFix

import assistant
import integrations
import knowledge
import worker
from google_apis import (ASSISTANT_SCOPES, IDENTITY_SCOPES, GoogleClient, credentials_from_json,
                         naive_utc_expiry)
from store import AUTONOMY_CATEGORIES, make_secrets, make_store, utcnow_iso
from talk import PARTNERS, Talk
from tools import NOT_AN_ANSWER

# Google may grant scopes in a different spelling/order than requested.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

OWNER_EMAIL = os.environ.get("OWNER_EMAIL", "manne@superhairpieces.com").lower()
# The Google account Talent Solutions AI reads Gmail, Calendar and Drive as. Connect Google accepts only this one.
GOOGLE_ACCOUNT = os.environ.get("GOOGLE_ACCOUNT", OWNER_EMAIL).lower()
PASSWORD_SECRET_ID = os.environ.get("PASSWORD_SECRET_ID", "chat-assistant-password")
SESSION_DAYS = int(os.environ.get("SESSION_DAYS", "365"))
# Wrong passwords: (how many, within seconds) from one address, and from everywhere together.
LOGIN_LIMITS = {"address": (5, 15 * 60), "everyone": (30, 60 * 60)}
OWNER_DOMAIN = OWNER_EMAIL.split("@")[-1]
# Google's sign-in can be limited to a Workspace domain; a Gmail address has none to limit to.
HOSTED_DOMAIN = {} if OWNER_DOMAIN in ("gmail.com", "googlemail.com") else {"hd": OWNER_DOMAIN}
OAUTH_CLIENT_SECRET_ID = os.environ.get("OAUTH_CLIENT_SECRET_ID", "chat-assistant-oauth-client")
USER_TOKEN_SECRET_ID = os.environ.get("USER_TOKEN_SECRET_ID", "chat-assistant-user-token")
CHATGPT_VOICE = os.environ.get("CHATGPT_VOICE", "realtime")  # realtime (ChatGPT hears you) | relay (via Gemini)
CLAUDE_VOICE = os.environ.get("CLAUDE_VOICE", "realtime")  # realtime (ChatGPT's voice line) | gemini (Gemini's)
RUN_TOKEN_SECRET_ID = os.environ.get("RUN_TOKEN_SECRET_ID", "chat-assistant-run-token")
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")  # the one address pages are served from (see one_address)
CSRF_HEADER = "X-Chat-Assistant"
LOCAL = os.environ.get("STORE_BACKEND") == "memory"
# Cloud Run names every deploy (K_REVISION); open pages reload themselves when it changes.
APP_VERSION = os.environ.get("K_REVISION") or "local"

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
store = make_store()
secret_store = make_secrets()
app.secret_key = store.session_key()
knowledge.ensure_seeded(store)
integrations.seed_systems(store)
app.config.update(
    SESSION_COOKIE_SECURE=not LOCAL,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(days=SESSION_DAYS),  # renewed on every visit
)


def google_token():
    """The stored Google token, if it's GOOGLE_ACCOUNT's (an earlier account's token is left unused)."""
    token = secret_store.get(USER_TOKEN_SECRET_ID)
    if not token:
        return None
    connected_as = ((store.get_owner() or {}).get("email") or "").lower()
    return token if not GOOGLE_ACCOUNT or connected_as == GOOGLE_ACCOUNT else None


def make_google():
    """Talent Solutions AI's Google client, or None before GOOGLE_ACCOUNT is connected."""
    token = google_token()
    return GoogleClient(credentials_from_json(token)) if token else None


# Tests swap these for fakes.
app.config["MAKE_GOOGLE"] = make_google


# -- access control ----------------------------------------------------------------

def password():
    return (secret_store.get(PASSWORD_SECRET_ID) or "").strip()


def password_mark(value):
    """What a signed-in browser's cookie holds: tied to this password (a new one signs everyone out), and keyed
    with the app's secret so the cookie doesn't give the password away."""
    return hmac.new(app.secret_key.encode(), f"password:{value}".encode(), "sha256").hexdigest()[:32]


def signed_in():
    current = password()
    return bool(current) and hmac.compare_digest(session.get("signed_in", ""), password_mark(current))


def client_address():
    # Google's front end appends the caller's address last; anything before it the caller could have sent.
    forwarded = request.headers.get("X-Forwarded-For", "").split(",")[-1].strip()
    return forwarded or request.remote_addr or "?"


def login_wait(address, now):
    """Seconds until another try is allowed, or 0."""
    failures = store.login_failures()
    waits = []
    for scope, (limit, window) in LOGIN_LIMITS.items():
        recent = [f["at"] for f in failures if f.get("at", 0) > now - window
                  and (scope == "everyone" or f.get("ip") == address)]
        if len(recent) >= limit:
            waits.append(int(sorted(recent)[-limit] + window - now) + 1)
    return max(waits, default=0)


@app.before_request
def one_address():
    """Cloud Run answers on two addresses, and a sign-in cookie belongs to one: send pages opened on the other
    to PUBLIC_URL."""
    if PUBLIC_URL and request.method == "GET" and request.path != "/health":
        if request.host.lower() != urlsplit(PUBLIC_URL).netloc.lower():
            return redirect(PUBLIC_URL + request.full_path.rstrip("?"), 302)


@app.before_request
def guard():
    if request.path.startswith("/api/"):
        if not signed_in():
            return jsonify(error="請先登入。"), 401
        # CSRF: browsers can't add a custom header cross-site without a CORS preflight we never allow.
        if request.method != "GET" and request.headers.get(CSRF_HEADER) != "1":
            return jsonify(error="缺少請求標頭。"), 403


@app.after_request
def security_headers(resp):
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    return resp


# -- sign-in -----------------------------------------------------------------------

def oauth_client_config():
    raw = secret_store.get(OAUTH_CLIENT_SECRET_ID)
    return json.loads(raw) if raw else None


def redirect_uri():
    return f"{PUBLIC_URL or request.host_url.rstrip('/')}/oauth/callback"


def make_flow(scopes, state=None):
    from google_auth_oauthlib.flow import Flow

    return Flow.from_client_config(oauth_client_config(), scopes=scopes, redirect_uri=redirect_uri(),
                                   state=state, autogenerate_code_verifier=True)


def login_page(error="", status=200):
    return render_template("login.html", error=error, configured=bool(password()),
                           secret=PASSWORD_SECRET_ID), status


def oauth_error(message, status=400):
    """A Google connection that didn't work, on the board's own address."""
    return render_template("login.html", error=message, configured=True, secret=PASSWORD_SECRET_ID,
                           back=True), status


@app.get("/")
def index():
    if not signed_in():
        return login_page()
    return render_template("index.html", version=APP_VERSION)


@app.post("/login")
def login():
    current = password()
    if not current:
        return login_page(status=503)
    now, address = time.time(), client_address()
    wait = login_wait(address, now)
    if wait:
        return login_page(f"密碼錯誤次數太多，請於 {max(1, round(wait / 60))} 分鐘後再試。", 429)
    if not hmac.compare_digest(request.form.get("password", "").strip().encode(), current.encode()):
        store.add_login_failure(address, now)
        return login_page("密碼不正確。", 401)
    session.clear()
    session.permanent = True
    session["signed_in"] = password_mark(current)
    return redirect("/")


@app.get("/connect/google")
def connect_google():
    """Connect Talent Solutions AI's Google account (GOOGLE_ACCOUNT): offline, every scope it uses."""
    if not signed_in():
        return redirect("/")
    if not oauth_client_config():
        return oauth_error(f"Google 用戶端尚未設定：請把它的 JSON 儲存到 Secret Manager，名稱為 "
                           f"{OAUTH_CLIENT_SECRET_ID}（重新導向 URI：{redirect_uri()}）。", 503)
    flow = make_flow(ASSISTANT_SCOPES)
    url, state = flow.authorization_url(prompt="consent", access_type="offline",
                                        **({"login_hint": GOOGLE_ACCOUNT} if GOOGLE_ACCOUNT else {}))
    session["oauth"] = {"state": state, "verifier": flow.code_verifier, "connect": True,
                        "back": "access" if request.args.get("back") == "access" else ""}
    return redirect(url)


@app.get("/connect/setup")
def connect_setup():
    """Connect everything: one Google approval as the project owner (see cloud_setup.py)."""
    import cloud_setup

    if not signed_in():
        return redirect("/")
    if not oauth_client_config():
        return oauth_error(f"Google 用戶端尚未設定（{OAUTH_CLIENT_SECRET_ID}）。", 503)
    flow = make_flow(IDENTITY_SCOPES + [cloud_setup.CLOUD_SCOPE])
    # online: only a one-hour token, no refresh token (the library asks for offline unless told otherwise)
    url, state = flow.authorization_url(login_hint=OWNER_EMAIL, **HOSTED_DOMAIN, access_type="online")
    then = request.args.get("then", "")
    session["oauth"] = {"state": state, "verifier": flow.code_verifier, "connect": False, "mode": "setup",
                        "then": then if re.fullmatch(r"[a-z0-9_]{1,40}", then) else ""}
    return redirect(url)


@app.get("/oauth/callback")
def oauth_callback():
    if not signed_in():
        return redirect("/")
    pending = session.pop("oauth", None)
    if request.args.get("error"):
        return oauth_error(f"Google 連接已取消（{request.args['error']}）。")
    if not pending or not hmac.compare_digest(request.args.get("state", ""), pending["state"]):
        return oauth_error("Google 連結已過期，請再試一次。")

    from google.auth.transport.requests import Request
    from google.oauth2 import id_token

    import cloud_setup

    setup = pending.get("mode") == "setup"
    scopes = IDENTITY_SCOPES + [cloud_setup.CLOUD_SCOPE] if setup else ASSISTANT_SCOPES
    flow = make_flow(scopes, state=pending["state"])
    flow.code_verifier = pending["verifier"]
    try:
        flow.fetch_token(code=request.args.get("code", ""))
        creds = naive_utc_expiry(flow.credentials)
        claims = id_token.verify_oauth2_token(creds.id_token, Request(), flow.client_config["client_id"])
    except Exception as exc:  # noqa: BLE001 - shown to the user, who can retry
        return oauth_error(f"Google 拒絕了：{exc}")

    email = (claims.get("email") or "").lower()
    if not claims.get("email_verified"):
        return oauth_error(f"Google 尚未驗證 {email or '該帳戶'} 的電郵地址。", 403)
    # Connect everything acts as whoever owns the Cloud project (Google checks what that account may do);
    # Talent Solutions AI's own Google access must be GOOGLE_ACCOUNT's.
    if pending["connect"] and GOOGLE_ACCOUNT and email != GOOGLE_ACCOUNT:
        return oauth_error(f"剛才登入的是 {email}。請以 {GOOGLE_ACCOUNT} 連接 Google：再按「連接」並選擇該帳戶"
                           "（或「使用其他帳戶」）。", 403)

    if setup:
        # The owner's token is used here and dropped: nothing about it is stored.
        report = cloud_setup.run(creds.token)
        secret_store.invalidate()
        store.set_flag("connect_setup", {"at": utcnow_iso(), **report})
        return redirect("/?tab=access&setup=done" + (f"&open={pending['then']}" if pending.get("then") else ""))

    if pending["connect"]:
        if not creds.refresh_token:
            return oauth_error("Google 沒有給予長期存取權。請在 myaccount.google.com/permissions "
                               "移除此應用程式，然後再連接。")
        granted = flow.oauth2session.token.get("scope") or []
        granted = set(granted.split() if isinstance(granted, str) else granted)
        missing = sorted(s.rsplit("/", 1)[-1] for s in ASSISTANT_SCOPES if s not in granted and s != "openid")
        secret_store.put(USER_TOKEN_SECRET_ID, creds.to_json())
        store.set_owner({"user": f"users/{claims['sub']}", "email": email})
        store.set_status({"connected_at": utcnow_iso(), "connection_error": "", "missing_scopes": missing})
    return redirect("/?tab=access&setup=done" if pending.get("back") == "access" else "/")


@app.post("/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


@app.post("/api/password")
def api_password():
    """Change the password. This browser stays signed in; every other one is signed out."""
    new = str((request.get_json(force=True) or {}).get("password", "")).strip()
    if len(new) < 4 or len(new) > 200:
        return jsonify(error="請用 4 至 200 個字元。越長越安全：幾個詞語組成的密碼很難猜。"), 400
    try:
        secret_store.put(PASSWORD_SECRET_ID, new)
    except Exception as exc:  # noqa: BLE001 - the app may not have been allowed to change it
        print(f"password change failed: {type(exc).__name__}")
        return jsonify(error=f"Couldn't save it ({type(exc).__name__})."), 502
    session["signed_in"] = password_mark(new)
    return jsonify(ok=True)


# -- board API ---------------------------------------------------------------------

TASK_FIELDS = {"title", "detail", "owner", "owner_is_me", "due", "priority", "status"}


def _clean_task(body):
    fields = {k: body[k] for k in TASK_FIELDS if k in body}
    if "title" in fields:
        fields["title"] = str(fields["title"]).strip()[:200]
    if fields.get("status") not in (None, "todo", "in_progress", "done"):
        fields.pop("status")
    if fields.get("priority") not in (None, "high", "medium", "low"):
        fields.pop("priority")
    for k in ("detail", "owner", "due"):
        if k in fields:
            fields[k] = str(fields[k] or "")[:2000 if k == "detail" else 100]
    if "owner_is_me" in fields:
        fields["owner_is_me"] = bool(fields["owner_is_me"])
    return fields


@app.get("/api/state")
def api_state():
    status = store.get_status()
    token = google_token()
    if token:  # a permission added since the last sign-in (e.g. starting direct messages) needs one Reconnect
        status["missing_scopes"] = sorted(set(status.get("missing_scopes") or []) | set(scopes_missing_from(token)))
    return jsonify(
        version=APP_VERSION,
        owner=OWNER_EMAIL,
        google_account=GOOGLE_ACCOUNT,
        connected=bool(token),
        status=status,
        settings=store.get_settings(),
        tasks=store.list_tasks(),
        actions=store.list_actions(150),
        runs=store.list_runs(12),
    )


def scopes_missing_from(token_json):
    """The assistant's permissions the stored sign-in wasn't asked for (short names, as in the banner)."""
    try:
        asked = set(json.loads(token_json).get("scopes") or [])
    except (ValueError, AttributeError):
        return []
    if not asked:
        return []  # an old token that doesn't record its scopes: nothing to compare
    return [s.rsplit("/", 1)[-1] for s in ASSISTANT_SCOPES if s not in asked and s != "openid"]


@app.post("/api/tasks")
def api_create_task():
    fields = _clean_task(request.get_json(force=True) or {})
    if not fields.get("title"):
        return jsonify(error="任務需要標題。"), 400
    now = utcnow_iso()
    task = store.save_task(pysecrets.token_hex(10), {
        "detail": "", "owner": "", "owner_is_me": True, "due": "", "priority": "medium", "status": "todo",
        **fields, "origin": "manual", "user_edited": True, "created_at": now, "updated_at": now,
    })
    return jsonify(task=task)


@app.post("/api/tasks/<task_id>/work")
def api_work_task(task_id):
    """Give a task to the assistant and let it work it now (it reports on the task and in the chat)."""
    if not store.get_task(task_id):
        return jsonify(error="找不到此任務。"), 404
    try:
        outcome = worker.work_on(talk_service(), store, task_id)
    except worker.Busy as exc:
        return jsonify(error=str(exc)), 409
    except Exception as exc:  # noqa: BLE001 - shown as a toast; the task keeps the error
        print(f"work[{task_id}] failed: {exc!r}")
        return jsonify(error=f"The assistant couldn't finish working on it: {str(exc)[:300]}"), 502
    return jsonify(task=store.get_task(task_id), report=outcome["report"], done=outcome["done"])


@app.patch("/api/tasks/<task_id>")
def api_update_task(task_id):
    if not store.get_task(task_id):
        return jsonify(error="找不到此任務。"), 404
    fields = _clean_task(request.get_json(force=True) or {})
    if "title" in fields and not fields["title"]:
        return jsonify(error="任務需要標題。"), 400
    task = store.save_task(task_id, {**fields, "user_edited": True, "updated_at": utcnow_iso()})
    return jsonify(task=task)


@app.delete("/api/tasks/<task_id>")
def api_delete_task(task_id):
    store.delete_task(task_id)
    return jsonify(ok=True)


@app.patch("/api/settings")
def api_settings():
    body = request.get_json(force=True) or {}
    changes = {k: bool(body[k]) for k in ("auto_act", "partners_enabled") if k in body}
    if isinstance(body.get("autonomy"), dict):
        current = store.get_settings()["autonomy"]
        changes["autonomy"] = {**current, **{k: v for k, v in body["autonomy"].items()
                                             if k in AUTONOMY_CATEGORIES and v in ("auto", "ask")}}
    if "openai_model" in body:
        changes["openai_model"] = str(body["openai_model"] or "").strip()[:60]
    return jsonify(settings=store.update_settings(changes))


# -- knowledge base, interview, access ------------------------------------------------

def talk_service():
    return Talk(store, secret_store, app.config["MAKE_GOOGLE"], OWNER_EMAIL)


@app.get("/api/knowledge")
def api_knowledge():
    service = talk_service()
    return jsonify(
        projects=store.list_items("projects"),
        questions=store.list_items("questions"),
        systems=[{**s, "connect": integrations.connect_info(s["id"])} for s in store.list_items("systems")],
        setup=store.get_flag("connect_setup"),
        facts=store.list_items("facts")[:200],
        autonomy_categories=[[k, v] for k, v in AUTONOMY_CATEGORIES.items()],  # lists keep their order in JSON
        partners=[{"name": name, "label": module.LABEL, "available": ok, "detail": why, "models": partner_models(name)}
                  for name, module in PARTNERS.items() for ok, why in [service.partner_status(name)]],
    )


def partner_models(name):
    """Which models are behind a partner, shown on its button, so it's plain who's answering."""
    import partner_claude
    import partner_gemini
    import partner_openai
    import voice
    import voice_openai

    if name == "assistant":
        return f"Gemini: {partner_gemini.TALK_MODEL} when typing, {voice.LIVE_MODEL} on calls"
    if name == "chatgpt":
        typed = store.get_settings().get("openai_model") or partner_openai.OPENAI_MODEL
        call = voice_openai.OPENAI_LIVE_MODEL if CHATGPT_VOICE == "realtime" else f"{typed} through Gemini's voice line"
        return f"OpenAI: {typed} when typing, {call} on calls"
    line = voice_openai.OPENAI_LIVE_MODEL if CLAUDE_VOICE == "realtime" else voice.LIVE_MODEL
    return f"Anthropic: {partner_claude.CLAUDE_MODEL}, typing and on calls ({line} carries the voice)"


@app.post("/api/questions/<question_id>/answer")
def api_answer_question(question_id):
    q = store.get_item("questions", question_id)
    if not q:
        return jsonify(error="找不到此問題。"), 404
    answer = str((request.get_json(force=True) or {}).get("answer", "")).strip()
    if not answer:
        return jsonify(error="請先輸入答案。"), 400
    if NOT_AN_ANSWER.match(answer):  # "not relevant": remove the question rather than keep it as answered
        store.delete_item("questions", question_id)
        return jsonify(question=None, note="已移除：與此無關。", tools=[])
    now = utcnow_iso()
    store.save_item("questions", question_id, {"status": "answered", "answer": answer[:8000], "answered_at": now,
                                               "updated_at": now})
    knowledge.add_fact(store, f"Q: {q['question']} A: {answer}", "general", q.get("project_id", ""), "owner's answer")
    try:
        digest = talk_service().digest_answer(q, answer)
        note = digest["text"]
        tools = digest.get("tools", [])
    except Exception as exc:  # noqa: BLE001 - the answer is saved either way
        note, tools = f"Saved your answer, but couldn't process it further: {exc}", []
    store.save_item("questions", question_id, {"digest": note[:1000]})
    return jsonify(question=store.get_item("questions", question_id), note=note, tools=tools)


@app.post("/api/questions/<question_id>/dismiss")
def api_dismiss_question(question_id):
    """Skip: the question is removed entirely, not kept as "dismissed"."""
    if not store.get_item("questions", question_id):
        return jsonify(error="找不到此問題。"), 404
    store.delete_item("questions", question_id)
    return jsonify(deleted=True)


@app.post("/api/questions")
def api_add_question():
    body = request.get_json(force=True) or {}
    q = knowledge.add_question(store, body.get("question", ""), body.get("why", ""), body.get("project_id", ""),
                               body.get("priority", 2), source="you")
    return (jsonify(question=q), 200) if q else (jsonify(error="請輸入問題。"), 400)


PROJECT_FIELDS = ("name", "company", "goal", "owner", "status", "deadline", "summary", "next_steps")


@app.post("/api/projects")
def api_create_project():
    body = request.get_json(force=True) or {}
    project = knowledge.save_project(store, {k: body.get(k) for k in PROJECT_FIELDS}, source="you")
    return (jsonify(project=project), 200) if project else (jsonify(error="項目需要名稱。"), 400)


@app.patch("/api/projects/<project_id>")
def api_update_project(project_id):
    if not store.get_item("projects", project_id):
        return jsonify(error="找不到此項目。"), 404
    body = request.get_json(force=True) or {}
    changes = {k: str(body[k])[:2000] for k in PROJECT_FIELDS if k in body}
    return jsonify(project=store.save_item("projects", project_id, {**changes, "updated_at": utcnow_iso()}))


@app.delete("/api/projects/<project_id>")
def api_delete_project(project_id):
    store.delete_item("projects", project_id)
    return jsonify(ok=True)


@app.patch("/api/systems/<system_id>")
def api_update_system(system_id):
    if not store.get_item("systems", system_id):
        return jsonify(error="找不到此系統。"), 404
    body = request.get_json(force=True) or {}
    changes = {}
    if body.get("status") in ("connected", "available", "needed", "requested", "not_used"):
        changes["status"] = body["status"]
    if "notes" in body:
        changes["notes"] = str(body["notes"])[:1000]
    return jsonify(system=store.save_item("systems", system_id, {**changes, "updated_at": utcnow_iso()}))


# -- talking to the assistant and its partners -----------------------------------------

@app.get("/api/talk/<partner>")
def api_talk_history(partner):
    if partner not in PARTNERS:
        return jsonify(error="找不到此 AI 夥伴。"), 404
    return jsonify(turns=store.get_talk(partner))


@app.post("/api/talk/<partner>")
def api_talk(partner):
    if partner not in PARTNERS:
        return jsonify(error="找不到此 AI 夥伴。"), 404
    message = str((request.get_json(force=True) or {}).get("message", "")).strip()
    if not message:
        return jsonify(error="請先說點甚麼。"), 400
    try:
        reply = talk_service().ask(partner, message)
    except Exception as exc:  # noqa: BLE001 - shown in the conversation
        import traceback

        print(f"talk[{partner}] failed: {exc!r}\n{traceback.format_exc()}")
        return jsonify(error=str(exc)[:500]), 502
    return jsonify(reply=reply)


@app.delete("/api/talk/<partner>")
def api_talk_clear(partner):
    if partner not in PARTNERS:
        return jsonify(error="找不到此 AI 夥伴。"), 404
    store.clear_talk(partner)
    return jsonify(ok=True)


@app.post("/api/partners/test")
def api_test_partners():
    return jsonify(results=talk_service().test_partners())


@app.post("/api/integrations/check")
def api_check_integrations():
    """Try each company system with its stored key (a cheap read), and each AI partner, and update the Access tab."""
    results = integrations.check_all(secret_store, store, google=_google_or_none())
    results.update(talk_service().check_partners())
    return jsonify(results=results)


def _google_or_none():
    try:
        return app.config["MAKE_GOOGLE"]()
    except Exception:  # noqa: BLE001 - not connected: Google tools report that
        return None


@app.post("/api/connect/<integration_id>")
def api_connect(integration_id):
    """Save a pasted key straight into Secret Manager, then try the system with it. Values are never echoed."""
    integration = integrations.BY_ID.get(integration_id)
    if not integration or integration.kind != "paste":
        return jsonify(error="此系統不接受貼上的金鑰。"), 404
    values = (request.get_json(force=True) or {}).get("values") or {}
    provided = {name: str(values.get(name, "")).strip() for name in integration.paste_names()}
    if not all(provided.values()):
        return jsonify(error="請填寫所有欄位。"), 400
    try:
        for name, value in provided.items():
            secret_store.put(name, value)
    except Exception as exc:  # noqa: BLE001 - usually: the one-time setup hasn't made this secret yet
        if type(exc).__name__ in ("PermissionDenied", "NotFound", "Forbidden"):
            return jsonify(needs_setup=True, url=f"/connect/setup?then={integration.system_id}"), 409
        print(f"connect[{integration_id}] failed: {type(exc).__name__}")
        return jsonify(error=f"Couldn't save it: {type(exc).__name__}"), 502
    result = integrations.check_all(secret_store, store, google=_google_or_none(), only={integration.id})
    return jsonify(result=result.get(integration.id))


_speaker = None


@app.post("/api/speak")
def api_speak():
    """A reply as speech, streamed as it's made: 16-bit mono PCM at X-Sample-Rate (24 kHz)."""
    global _speaker
    body = request.get_json(force=True) or {}
    partner = body.get("partner", "assistant")
    if partner not in PARTNERS:
        return jsonify(error="找不到此 AI 夥伴。"), 404
    if _speaker is None:
        import speech

        _speaker = app.config.get("SPEAKER") or speech.Speaker(secret_store)
    try:
        chunks = _speaker.stream(partner, str(body.get("text", "")))
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    except Exception as exc:  # noqa: BLE001 - shown as a toast
        print(f"speak[{partner}] failed: {exc!r}")
        return jsonify(error=str(exc)[:400]), 502
    return app.response_class(chunks, mimetype="audio/pcm",
                              headers={"X-Sample-Rate": "24000", "Cache-Control": "no-store",
                                       "X-Accel-Buffering": "no"})


# -- live voice ----------------------------------------------------------------------

def _voice_origin_ok():
    """Only the app's own page may open the voice socket (blocks cross-site WebSocket use of the cookie).

    Compares host names: behind Cloud Run's proxy a WebSocket upgrade can arrive without the
    forwarded https scheme, which made the full-URL comparison fail for the app's own page.
    """
    from urllib.parse import urlsplit

    origin_host = urlsplit(request.headers.get("Origin", "")).netloc.lower()
    expected = urlsplit(PUBLIC_URL).netloc.lower() if PUBLIC_URL else request.host.lower()
    return bool(origin_host) and origin_host == expected


def voice_ws(ws):
    import json as _json

    import voice
    from talk import system_prompt

    if not signed_in():
        ws.send(_json.dumps({"type": "error", "message": "Sign in to the app first."}))
        return
    if not _voice_origin_ok():
        print(f"voice: origin {request.headers.get('Origin')!r} != {PUBLIC_URL or request.host_url!r}")
        ws.send(_json.dumps({"type": "error", "message": "Open the app from its own address to use voice."}))
        return
    partner = request.args.get("partner", "assistant")
    if partner not in PARTNERS:
        ws.send(_json.dumps({"type": "error", "message": "No such partner."}))
        return
    try:
        label = PARTNERS[partner].LABEL if partner != "assistant" else "your assistant"
        ws.send(_json.dumps({"type": "status", "text": f"Preparing {label}…"}))
        service = talk_service()
        ok, why = service.partner_status(partner)
        if not ok:
            ws.send(_json.dumps({"type": "error", "message": f"{label} isn't available: {why}"}))
            return

        def live_instruction():
            recent = [t for t in store.get_talk(partner) if t.get("text")][-8:]
            history = "\n".join(f"{'Manne' if t['role'] == 'user' else 'You'}: {t['text'][:500]}" for t in recent)
            text = system_prompt(store, partner, voice=True, owner_email=OWNER_EMAIL)
            return text + ("\n\nRECENT CONVERSATION (continue from here)\n" + history if history else "")

        import speech
        import voice_openai

        own_voice = speech.VOICES[partner][1]  # the voice its replies are read aloud in, too

        def openai_connect():
            return app.config.get("OPENAI_VOICE_CONNECT") or voice_openai.default_connect(secret_store)

        if partner == "assistant":  # Gemini Live hears and answers
            bridge = voice.VoiceBridge(ws, store, service.toolset("assistant", voice=True), live_instruction(),
                                       connect=app.config.get("VOICE_CONNECT"), voice=own_voice)
        elif partner == "chatgpt" and CHATGPT_VOICE == "realtime":  # ChatGPT hears and answers, same as Gemini
            bridge = voice_openai.RealtimeVoiceBridge(ws, store, service.toolset("chatgpt", voice=True),
                                                      live_instruction(), openai_connect(), save_as="chatgpt",
                                                      hints=knowledge.name_hints(store), voice=own_voice)
        else:
            # Claude has no audio input: a live voice line listens and speaks, Claude answers (voice.RelayTools).
            toolset = voice.RelayTools(partner, label, lambda m: service.ask(partner, m, voice=True)["text"])
            instruction = voice.relay_instruction(label, toolset.name, knowledge.vocabulary(store))
            if CLAUDE_VOICE == "realtime":  # ChatGPT's line: hears the audio itself, waits for the end of a thought
                bridge = voice_openai.RealtimeVoiceBridge(ws, store, toolset, instruction, openai_connect(),
                                                          save_as=None, hints=knowledge.name_hints(store),
                                                          voice=own_voice, reminder=False, label=label)
                bridge.shown_model = f"{label}, voice by {voice_openai.OPENAI_LIVE_MODEL}"
            else:  # Gemini's line
                bridge = voice.VoiceBridge(ws, store, toolset, instruction, connect=app.config.get("VOICE_CONNECT"),
                                           save_as=None, end_silence_ms=voice.RELAY_END_SILENCE_MS)
                bridge.shown_model = f"{label}, voice by {voice.LIVE_MODEL}"
    except Exception as exc:  # noqa: BLE001 - without this the socket would stay open and silent
        import traceback

        print(f"voice setup failed: {exc!r}\n{traceback.format_exc()}")
        ws.send(_json.dumps({"type": "error", "message": f"Voice couldn't start: {str(exc)[:300]}"}))
        return
    bridge.run()


try:
    from flask_sock import Sock

    Sock(app).route("/ws/voice")(voice_ws)
except ImportError:  # the voice route needs flask-sock; everything else works without it
    pass


# -- running the assistant ---------------------------------------------------------

def do_run(trigger):
    if not store.acquire_run_lock():
        return {"error": "已有一次執行正在進行。"}, 409
    started = utcnow_iso()
    progress = assistant.Progress(store, trigger)
    try:
        google = app.config["MAKE_GOOGLE"]()  # None until Connect Google: the Google tools wait, nothing else does
        summary = assistant.empty_summary()
        summary["trigger"] = trigger
        progress.phase("Working on the assistant's own tasks")
        try:
            summary["tasks_worked"] = worker.work_due(talk_service(), store)
        except Exception as exc:  # noqa: BLE001 - the connection check below still runs
            summary["errors"].append({"space": "(assistant's tasks)", "error": str(exc)[:300]})
        try:  # keep the Access tab true: a status stuck on an old failure fixes itself within the hour
            checked = integrations.check_all(secret_store, store, google=google)
            checked.update(talk_service().check_partners())
            summary["systems_connected"] = sum(1 for r in checked.values() if r["ok"])
        except Exception as exc:  # noqa: BLE001
            summary["errors"].append({"space": "(connections)", "error": str(exc)[:300]})
        summary["finished_at"] = utcnow_iso()
        store.add_run(summary)
        store.set_status({"last_run": summary, "connection_error": ""})
        progress.finish()
        return summary, 200
    except Exception as exc:  # noqa: BLE001 - recorded, then surfaced to the caller
        import traceback

        trace = traceback.format_exc()
        print(f"run failed: {exc!r}\n{trace}")
        failed = {"started_at": started, "finished_at": utcnow_iso(), "trigger": trigger,
                  "error": assistant.error_text(exc, 500),  # says where, even in the header's one line
                  "trace": trace[-2000:]}  # shown under Activity -> Runs, so a failure explains itself
        store.add_run(failed)
        store.set_status({"last_run": failed})
        progress.finish(failed["error"])
        return failed, 500
    finally:
        store.release_run_lock()


@app.get("/api/browser")
def api_browser():
    """The AI's browser screen, as each partner last left it; the Browser tab polls it while open."""
    return jsonify(views=store.list_browser_views())


@app.get("/api/progress")
def api_progress():
    """Small and cheap, so the page can poll it every couple of seconds during a run."""
    return jsonify(store.get_progress())


@app.post("/api/run")
def api_run():
    body, status = do_run("manual")
    return jsonify(body), status


@app.post("/run")
def scheduled_run():
    expected = secret_store.get(RUN_TOKEN_SECRET_ID) or ""
    given = request.headers.get("X-Run-Token", "")
    if not expected or not hmac.compare_digest(given, expected):
        return jsonify(error="forbidden"), 403
    body, status = do_run("schedule")
    return jsonify(body), status


# Not /healthz: Cloud Run reserves paths ending in "z" and never forwards them.
@app.get("/health")
def health():
    return "ok"


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "8080")), debug=LOCAL)
