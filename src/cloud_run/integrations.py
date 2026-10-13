"""Company systems the assistant uses for real: keys from Secret Manager, or Manne's Google sign-in.

Three ways a system gets connected (the Access tab's Connect buttons):
- "key": its key is already in Secret Manager. One Google approval by Manne lets the
  app give its own account read access to exactly those secrets (cloud_setup.py).
- "paste": no key yet. The same approval creates an empty secret the app may fill;
  Manne pastes the key in the app, and it goes straight into Secret Manager. (No
  connector uses this right now; HubSpot and Re:amaze did until they were retired.)
- "google": a Google API, used with Manne's own sign-in (the scopes are part of it).
- "self": a Google API used as the app's own service account (SERVICE_ACCOUNT): it
  reads what people share with that address (a Drive folder, a calendar). No sign-in,
  no key, nothing that expires: Cloud Run hands the app its tokens.

Everyone who signs in shares one conversation, and pages and emails it reads are written by
others, so any of them could try to talk it into misusing a key. The rules that make that fail:

- Key values never reach a model. The server adds them to each request (a login that
  must be exchanged for a token is exchanged on the server), and scrubs
  them from anything a system sends back.
- Every system has one pinned address. A path can't point a request (or its key)
  at any other host.
- Reading runs straight away. Anything that changes data needs confirmed=true,
  which a model may only set after Manne agreed to that exact change.
"""

import json
import os
import re
from dataclasses import dataclass, field
from urllib.parse import urlencode, urlsplit

import requests

MAX_RESPONSE_CHARS = 10000
# The app's own Google identity: share a Drive folder or a calendar with this address and Talent Solutions AI can read it.
SERVICE_ACCOUNT = os.environ.get("SERVICE_ACCOUNT_EMAIL") or \
    f"chat-assistant@{os.environ.get('GCP_PROJECT', 'helperexpress-hk')}.iam.gserviceaccount.com"
READ_METHODS = {"GET", "HEAD"}
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class NotReady(RuntimeError):
    """The app can't read a secret this system needs."""


@dataclass
class Integration:
    id: str
    label: str
    secrets: list                 # Secret Manager names this system needs
    base: object                  # "https://host/prefix", or callable(values) -> that
    headers: object = None        # callable(values) -> dict
    auth: object = None           # callable(values) -> (user, password) for basic auth
    hint: str = ""                # useful paths and caveats, shown to the models
    probe: object = ""            # a cheap read that proves access: "/path" or ("POST", "/path", body)
    read_posts: tuple = ()        # POST paths (regex) that only read, e.g. a search
    system_id: str = ""           # its row in the Access tab
    category: str = "Other"
    kind: str = "key"             # key | paste | google | self (see the module docstring)
    scopes: list = field(default_factory=list)  # self: the Google scopes its tokens carry
    fields: list = field(default_factory=list)  # paste: [(secret, label, placeholder, hidden)]
    help: str = ""                # paste: where to find the key, step by step
    exchange: object = None       # callable(values, http) -> {"headers"|"body": {...}, "ttl": s}: a login -> token
    services: list = field(default_factory=list)  # google: APIs to switch on in the project
    optional: list = field(default_factory=list)  # secrets used when readable (e.g. unlocking private endpoints)
    sign: object = None           # callable(values, method, url) -> headers, for APIs that sign every request
    labeler: object = None        # callable(probe data) -> a better row name (the storefront, the marketplaces)

    def values(self, secrets):
        out = {}
        for name in self.secrets:
            try:
                value = secrets.get(name) if secrets else None
            except Exception:  # noqa: BLE001 - no permission counts as missing
                value = None
            value = value.strip() if isinstance(value, str) else value  # a pasted line break breaks a header
            if not value:
                raise NotReady(f"應用程式無法讀取 {name} 金鑰")
            out[name] = value
        for name in self.optional:
            try:
                value = (secrets.get(name) or "").strip() if secrets else ""
            except Exception:  # noqa: BLE001 - optional: simply not used
                value = ""
            if value:
                out[name] = value
        return out

    def base_url(self, values):
        base = self.base(values) if callable(self.base) else self.base
        return base.rstrip("/")

    def paste_names(self):
        return [f[0] for f in self.fields]


REGISTRY = [
    Integration(
        id="airtable", label="Airtable", secrets=["AIRTABLE_COMPANY_TOKEN"], base="https://api.airtable.com",
        headers=lambda v: {"Authorization": f"Bearer {v['AIRTABLE_COMPANY_TOKEN']}"},
        hint="/v0/meta/bases (list bases), /v0/meta/bases/{baseId}/tables, /v0/{baseId}/{tableIdOrName}?maxRecords=50",
        probe="/v0/meta/bases", system_id="airtable", category="營運"),
    Integration(
        id="notion", label="Notion", secrets=["NOTION_API_KEY"], base="https://api.notion.com",
        headers=lambda v: {"Authorization": f"Bearer {v['NOTION_API_KEY']}", "Notion-Version": "2022-06-28"},
        hint=('POST /v1/search with {"query": "..."} finds pages and databases (a read); '
              "/v1/pages/{id}, /v1/blocks/{id}/children, POST /v1/databases/{id}/query (a read)"),
        probe="/v1/users/me", read_posts=(r"^/v1/search$", r"^/v1/databases/[^/]+/query$"),
        system_id="notion", category="文件"),
    Integration(
        id="figma", label="Figma", secrets=["FIGMA_TOKEN"], base="https://api.figma.com",
        headers=lambda v: {"X-Figma-Token": v["FIGMA_TOKEN"]},
        hint="/v1/files/{fileKey}?depth=1, /v1/files/{fileKey}/comments, /v1/images/{fileKey}?ids=...",
        probe="/v1/me", system_id="figma", category="設計"),
]


REGISTRY += [  # research
    Integration(
        id="firecrawl", label="網上研究（Firecrawl）", secrets=["FIRECRAWL_API"], base="https://api.firecrawl.dev",
        headers=lambda v: {"Authorization": f"Bearer {v['FIRECRAWL_API']}", "Content-Type": "application/json"},
        hint=('Used by web_search and read_webpage. Directly: POST /v1/search {"query": "...", "limit": 5}, '
              'POST /v1/scrape {"url": "...", "formats": ["markdown"], "onlyMainContent": true}, '
              'POST /v1/map {"url": "https://site"} (a site\'s pages). All are reads.'),
        probe=("POST", "/v1/search", {"query": "github", "limit": 1}),
        read_posts=(r"^/v1/(search|scrape|map)$",), system_id="web_research", category="研究"),
    Integration(
        id="dataforseo", label="SEO 研究（DataForSEO）", secrets=["DATAFORSEO_LOGIN", "DATAFORSEO_API_TOKEN"],
        base="https://api.dataforseo.com", auth=lambda v: (v["DATAFORSEO_LOGIN"], v["DATAFORSEO_API_TOKEN"]),
        hint=("Each call costs a few cents: batch keywords into one request. Bodies are JSON arrays of tasks. "
              "POST /v3/serp/google/organic/live/advanced [{\"keyword\": \"...\", \"location_code\": 2124, "
              "\"language_code\": \"en\"}] (2344 Hong Kong), POST /v3/keywords_data/google_ads/search_volume/live "
              "[{\"keywords\": [...], \"location_code\": 2344}], POST /v3/dataforseo_labs/google/ranked_keywords/live "
              "[{\"target\": \"helperxpress.com\", \"location_code\": 2344}]. GET /v3/appendix/user_data (balance)."),
        probe="/v3/appendix/user_data", read_posts=(r"^/v3/.+/live(/\w+)?$",),
        system_id="seo_research", category="研究"),
]

GOOGLE = [  # used with Manne's own Google sign-in; their scopes are in google_apis.GOOGLE_TOOL_SCOPES
    Integration(id="gmail", label="Gmail", secrets=[], base="https://gmail.googleapis.com", kind="google",
                hint=("/gmail/v1/users/me/messages?q=from:x newer_than:7d&maxResults=20, /gmail/v1/users/me/messages/{id}"
                      "?format=full. Drafts: POST /gmail/v1/users/me/drafts (a change: needs Manne's OK)."),
                probe="/gmail/v1/users/me/profile", system_id="gmail", category="溝通"),
    Integration(id="google_drive", label="Google Drive", secrets=[], base="https://www.googleapis.com", kind="google",
                hint="/drive/v3/files?q=name contains 'x'&fields=files(id,name,mimeType,modifiedTime), "
                     "/drive/v3/files/{id}/export?mimeType=text/plain (Docs as text)",
                probe="/drive/v3/about?fields=user", system_id="google_drive", category="文件"),
    Integration(id="google_sheets", label="Google Sheets", secrets=[], base="https://sheets.googleapis.com",
                kind="google", hint="/v4/spreadsheets/{id}?fields=sheets.properties, /v4/spreadsheets/{id}/values/{range}",
                system_id="google_drive", category="文件"),
    Integration(id="ga4_admin", label="Google Analytics（資源）", secrets=[], base="https://analyticsadmin.googleapis.com",
                kind="google", hint="/v1beta/accountSummaries (lists GA4 properties and their ids)",
                probe="/v1beta/accountSummaries", system_id="analytics", category="市場推廣",
                services=["analyticsadmin.googleapis.com"]),
    Integration(id="ga4_data", label="Google Analytics（報告）", secrets=[], base="https://analyticsdata.googleapis.com",
                kind="google", read_posts=(r"^/v1beta/properties/\d+:runReport$",),
                hint=('POST /v1beta/properties/{id}:runReport (a read) with {"dateRanges": [{"startDate": "28daysAgo", '
                      '"endDate": "today"}], "dimensions": [{"name": "sessionDefaultChannelGroup"}], '
                      '"metrics": [{"name": "sessions"}, {"name": "purchaseRevenue"}]}'),
                system_id="analytics", category="市場推廣", services=["analyticsdata.googleapis.com"]),
    Integration(id="search_console", label="Google Search Console", secrets=[], base="https://searchconsole.googleapis.com",
                kind="google", read_posts=(r"^/webmasters/v3/sites/[^/]+/searchAnalytics/query$",),
                hint=("/webmasters/v3/sites (the sites), POST /webmasters/v3/sites/{url-encoded site}/searchAnalytics/query "
                      '(a read) with {"startDate": "...", "endDate": "...", "dimensions": ["query"], "rowLimit": 50}'),
                probe="/webmasters/v3/sites", system_id="analytics", category="市場推廣",
                services=["searchconsole.googleapis.com"]),
]
REGISTRY += GOOGLE

OWN = [  # read as the app's own service account: what people share with SERVICE_ACCOUNT, no one's sign-in
    Integration(
        id="shared_drive", label="與 Talent Solutions AI 共用的 Drive", secrets=[], base="https://www.googleapis.com",
        kind="self", scopes=["https://www.googleapis.com/auth/drive.readonly"],
        hint=(f"Files and folders people shared with Talent Solutions AI's own address, {SERVICE_ACCOUNT}. Read-only. "
              "GET /drive/v3/files with query {\"q\": \"sharedWithMe\", \"fields\": \"files(id,name,mimeType,"
              "modifiedTime,owners(emailAddress),webViewLink)\"} lists what's shared; a folder's contents: query "
              "{\"q\": \"'FOLDER_ID' in parents\", \"fields\": ...}. A Google Doc as text: "
              "/drive/v3/files/{id}/export?mimeType=text/plain (a Sheet's first tab: mimeType=text/csv); "
              "other files: /drive/v3/files/{id}?alt=media. A 404 means it isn't shared with Talent Solutions AI."),
        probe="/drive/v3/about?fields=user", system_id="shared_drive", category="文件",
        services=["drive.googleapis.com"]),
    Integration(
        id="shared_calendar", label="與 Talent Solutions AI 共用的日曆", secrets=[], base="https://www.googleapis.com",
        kind="self", scopes=["https://www.googleapis.com/auth/calendar.readonly"],
        hint=(f"Calendars people shared with Talent Solutions AI's own address, {SERVICE_ACCOUNT}. Read-only. A shared "
              "calendar isn't listed anywhere: read it by its id, which for someone's main calendar is their email "
              "with @ written as %40: GET /calendar/v3/calendars/name%40gmail.com/events with query {\"timeMin\": "
              "\"2026-10-13T00:00:00Z\", \"timeMax\": \"...\", \"singleEvents\": \"true\", \"orderBy\": "
              "\"startTime\"}. Other calendars' ids are in their Google Calendar settings (Integrate calendar). "
              "A 404 means it isn't shared with Talent Solutions AI."),
        probe="/calendar/v3/users/me/calendarList?maxResults=1", system_id="shared_calendar", category="溝通",
        services=["calendar-json.googleapis.com"]),
]
REGISTRY += OWN

REGISTRY += [  # engineering
    Integration(
        id="github", label="GitHub", secrets=["GITHUB_TOKEN"], base="https://api.github.com",
        headers=lambda v: {"Authorization": f"Bearer {v['GITHUB_TOKEN']}", "Accept": "application/vnd.github+json",
                           "X-GitHub-Api-Version": "2022-11-28"},
        hint=("/user (whose token), /user/repos?sort=pushed&per_page=30, /repos/{owner}/{repo}, "
              "/repos/{owner}/{repo}/issues?state=open&per_page=50, /repos/{owner}/{repo}/pulls?state=open, "
              "/repos/{owner}/{repo}/pulls/{n}/files, /repos/{owner}/{repo}/commits?per_page=20, "
              "/repos/{owner}/{repo}/actions/runs?per_page=10 (CI), /repos/{owner}/{repo}/contents/{path} (a file, "
              "base64), /search/issues?q=repo:{owner}/{repo}+is:open+label:bug. Changes need Manne's OK: POST "
              "/repos/{owner}/{repo}/issues {\"title\", \"body\"}, POST /repos/{owner}/{repo}/issues/{n}/comments "
              "{\"body\"}, PATCH /repos/{owner}/{repo}/issues/{n} {\"state\": \"closed\"}."),
        probe="/user", system_id="github", category="工程", kind="paste",
        fields=[("GITHUB_TOKEN", "個人存取權杖（fine-grained）", "github_pat_…", True)],
        help=("在 GitHub 以擁有程式碼的帳戶登入：Settings > Developer settings > Personal access tokens > "
              "Fine-grained tokens > Generate new token。Resource owner：該帳戶。Repository access：HelperConnects 和 "
              "HelperXpress 的儲存庫。權限（Read and write）：Contents、Pull requests、Workflows、Issues；"
              "（Read）：Actions、Metadata。產生後複製權杖並貼在這裡。")),
]

# Which systems this deployment offers: ids in INTEGRATIONS, separated by ";" (gcloud --set-env-vars splits on
# commas), "," or spaces; empty: all of them. A copy run for another business lists its own, so the Access tab,
# the checks and the models see only those.
ENABLED = {s for s in re.split(r"[;,\s]+", os.environ.get("INTEGRATIONS", "")) if s}
if ENABLED:
    REGISTRY = [i for i in REGISTRY if i.id in ENABLED]
BY_ID = {i.id: i for i in REGISTRY}
_exchanged = {}  # integration id -> (exchange result, expires at); logins aren't redone on every call
_own_credentials = {}  # scopes -> the app's own credentials, refreshed when they run out


def _own_token(scopes):
    """An access token for the app's own service account with these scopes (Cloud Run's metadata server issues
    them, so there's nothing to store or renew)."""
    import google.auth
    from google.auth.transport.requests import Request

    key = tuple(scopes)
    creds = _own_credentials.get(key)
    if creds is None:
        creds, _ = google.auth.default(scopes=list(scopes))
        _own_credentials[key] = creds
    if not creds.valid:
        creds.refresh(Request())
    return creds.token


def secret_names():
    """Every secret the integrations read: the list the app's account needs access to."""
    return sorted({name for i in REGISTRY for name in i.secrets + i.optional})


def _clean_path(path):
    path = "/" + (path or "").strip().lstrip("/")
    if "://" in path or "@" in path or "\\" in path or ".." in path.split("?")[0] or re.search(r"\s", path):
        raise ValueError("give a path on this system, like /v2/orders?limit=10, not a full address")
    return path


def _scrub(text, values):
    for value in values.values():
        if len(value) >= 6:
            text = text.replace(value, "[secret]")
    return text


def is_read(integration, method, path):
    method = method.upper()
    if method in READ_METHODS:
        return True
    bare = path.split("?")[0]
    return method == "POST" and any(re.match(p, bare) for p in integration.read_posts)


def _exchange(integration, values, http):
    import time

    hit = _exchanged.get(integration.id)
    if hit and hit[1] > time.time():
        return hit[0]
    result = integration.exchange(values, http)
    _exchanged[integration.id] = (result, time.time() + result.get("ttl", 3600))
    return result


def call(integration_id, method, path, secrets, query=None, body=None, confirmed=False, session=None, google=None):
    """One request to a company system; returns a JSON-safe dict for the model (never a key)."""
    integration = BY_ID.get(integration_id)
    if not integration:
        return {"error": f"unknown system {integration_id}; call list_integrations for the ids"}
    method = (method or "GET").upper()
    if method not in READ_METHODS | WRITE_METHODS:
        return {"error": f"method must be one of {sorted(READ_METHODS | WRITE_METHODS)}"}
    try:
        path = _clean_path(path)
    except ValueError as exc:
        return {"error": str(exc)}
    if not is_read(integration, method, path) and not confirmed:
        return {"not_sent": True,
                "reason": "This changes data. Describe the exact change to Manne and call again with confirmed=true "
                          "only after he agrees to it."}
    http = session or requests
    try:
        values = integration.values(secrets)
        extra = _exchange(integration, values, http) if integration.exchange else {}
        base = (extra.get("base") or integration.base_url(values)).rstrip("/")  # an exchange may pick the region
        headers = dict(integration.headers(values)) if integration.headers else {}
        if integration.kind == "google":
            if google is None:
                raise NotReady("Google 尚未連接（按頁頂橫額的「連接」）")
            headers.update(google.auth_header())
        elif integration.kind == "self":
            headers["Authorization"] = f"Bearer {_own_token(integration.scopes)}"
    except NotReady as exc:
        return {"error": f"{integration.label} 暫時無法使用：{exc}"}
    except Exception as exc:  # noqa: BLE001 - a failed login or token refresh, reported to the model
        return {"error": f"{integration.label} 暫時無法使用：{str(exc)[:300]}"}
    scrub = dict(values, **{f"x{i}": str(v) for i, v in enumerate({**extra.get("headers", {}),
                                                                   **extra.get("body", {})}.values())})
    headers.update(extra.get("headers", {}))
    if extra.get("body"):
        body = {**(body or {}), **extra["body"]}
    url = base + path
    if query:
        url += ("&" if "?" in url else "?") + urlencode(query, doseq=True)
    if urlsplit(url).netloc != urlsplit(base).netloc:  # belt and braces: the key only ever goes to its own host
        return {"error": "that path leaves the system's own address"}
    if integration.sign:
        try:
            signed = integration.sign(values, method, url)
        except Exception as exc:  # noqa: BLE001 - e.g. a private key in the wrong format
            return {"error": f"{integration.label} 暫時無法使用：無法簽署請求（{str(exc)[:200]}）"}
        headers.update(signed)
        scrub.update({f"s{i}": str(v) for i, v in enumerate(signed.values()) if len(str(v)) >= 20})
    auth = integration.auth(values) if integration.auth else None
    try:
        resp = http.request(method, url, headers=headers, auth=auth, json=body if body is not None else None, timeout=45)
    except requests.RequestException as exc:
        return {"error": _scrub(f"{integration.label} didn't answer: {exc}", scrub)}
    if resp.status_code == 401 and integration.exchange:
        _exchanged.pop(integration.id, None)  # a stale token: log in again next time
    text = _scrub(resp.text or "", scrub)
    try:
        data = json.loads(text) if text else None
    except ValueError:
        data = text
    out = {"system": integration.label, "status": resp.status_code, "ok": resp.ok}
    rendered = json.dumps(data, ensure_ascii=False, default=str)
    if len(rendered) > MAX_RESPONSE_CHARS:
        out.update(truncated=True, data=rendered[:MAX_RESPONSE_CHARS],
                   note="Response cut short; narrow it with filters, limit or paging.")
    else:
        out["data"] = data
    return out


def probe(integration, secrets, session=None, google=None):
    """The system's cheap read(s). With several, the first that answers wins; else the most telling failure."""
    probes = integration.probe if isinstance(integration.probe, list) else [integration.probe]
    first = None
    for one in probes:
        method, path, body = ("GET", one, None) if isinstance(one, str) else one
        result = call(integration.id, method, path, secrets, body=body, session=session, google=google)
        if result.get("ok") or result.get("error"):  # answered, or not reachable at all: no point trying more
            return result
        first = first or result
        if result.get("status") == 401:  # the key itself was refused: other paths won't help
            return result
    return first


def status(integration, secrets, google=None):
    if integration.kind == "google":
        return ("ready", "") if google is not None else ("no_access", "Google 尚未連接")
    if integration.kind == "self":
        return "ready", ""  # whatever is shared with it; a 404 says what isn't
    try:
        integration.values(secrets)
        return "ready", ""
    except NotReady as exc:
        return "no_access", str(exc)


def describe(secrets, google=None):
    """What the models see in list_integrations: ids, whether usable, how to use them. No values."""
    out = []
    for i in REGISTRY:
        state, why = status(i, secrets, google)
        out.append({"id": i.id, "system": i.label, "status": state, **({"why": why} if why else {}),
                    "how": i.hint, "changes_need_confirmation": True})
    return out


def connect_info(system_id):
    """How the Access tab connects this system (None: no connector yet, ask the assistant)."""
    found = [i for i in REGISTRY if i.system_id == system_id]
    if not found:
        return None
    paste = next((i for i in found if i.kind == "paste"), None)
    if paste:
        return {"kind": "paste", "integration": paste.id, "help": paste.help,
                "fields": [{"secret": n, "label": label, "placeholder": ph, "hidden": hidden}
                           for n, label, ph, hidden in paste.fields]}
    return {"kind": found[0].kind, "integration": found[0].id}


# Connectors removed from the app; their Access rows are dropped. The Superhairpieces ones went 2026-10-13.
RETIRED = {"hubspot", "reamaze", "bigcommerce_qet21urb3p", "bigcommerce_eu", "bigcommerce_ca", "bigcommerce_com",
           "bigcommerce_es", "bigcommerce_fr", "bigcommerce_nl", "bigcommerce_de", "bigcommerce_genc", "trustpilot",
           "stamped", "omnisend", "skuvault", "amazon", "amazon_2", "amazon_3", "walmart", "teamdesk",
           "merchant_center", "google_ads", "meta", "accounting", "google_chat", "workspace_directory"}
RENAMED = {}


def seed_systems(store):
    """Give every connector a row in the Access tab (existing rows keep their status); drop retired ones."""
    for system_id in RETIRED:
        if store.get_item("systems", system_id):
            store.delete_item("systems", system_id)
    for system_id, name in RENAMED.items():
        if store.get_item("systems", system_id):
            store.save_item("systems", system_id, {"name": name})
    for i in REGISTRY:
        if i.system_id and not store.get_item("systems", i.system_id):
            store.save_item("systems", i.system_id, {
                "name": i.label, "category": i.category, "status": "available" if i.kind == "key" else "needed",
                "unlocks": i.hint[:200]})


def check_all(secrets, store=None, session=None, google=None, only=None):
    """Run each system's cheap read (all, or the ids in `only`). Updates the Access tab when a store is given."""
    results, by_system = {}, {}
    for i in REGISTRY:
        if not i.probe or (only and i.id not in only):
            continue
        result = probe(i, secrets, session=session, google=google)
        ok = bool(result.get("ok"))
        detail = result.get("error") or ("" if ok else f"HTTP {result.get('status')}: {str(result.get('data'))[:200]}")
        label = i.label
        data = result.get("data") if ok else None
        if ok and i.labeler:
            try:
                label = i.labeler(data) or label  # names what's really behind it (storefront, marketplaces)
            except Exception:  # noqa: BLE001 - a nicer name is optional
                pass
        results[i.id] = {"ok": ok, "system": label, "detail": detail}
        if i.system_id:
            by_system.setdefault(i.system_id, []).append((i, ok, label, detail))
    for system_id, rows in (by_system.items() if store is not None else ()):
        ok = all(r[1] for r in rows)  # a system with several parts (Analytics) is connected when all work
        first_bad = next((r for r in rows if not r[1]), rows[0])
        detail = "" if ok else first_bad[3]
        state = "connected" if ok else ("no_access" if "無法讀取" in detail or "尚未連接" in detail
                                        else "error")
        existing = store.get_item("systems", system_id) or {}
        label = rows[0][2] if len(rows) == 1 else existing.get("name", rows[0][0].label)
        store.save_item("systems", system_id, {
            "name": label if ok else existing.get("name", label), "category": existing.get("category", rows[0][0].category),
            "status": state, "why": "" if ok else detail[:300],
            "unlocks": existing.get("unlocks") or rows[0][0].hint[:200], "via": rows[0][0].kind})
    return results
