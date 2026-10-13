"""Calls to Google as Man AI's Google account (GOOGLE_ACCOUNT), with its own OAuth token.

The token can only see and do what that account could itself: its Gmail, Calendar and Drive, and the Google
tools on the Access tab (integrations.py calls them through GoogleClient.auth_header).
"""

import json

import requests

IDENTITY_SCOPES = ["openid", "https://www.googleapis.com/auth/userinfo.email"]
ASSISTANT_SCOPES = IDENTITY_SCOPES + [
    "https://www.googleapis.com/auth/calendar.events",
]
# Google tools the partners use through integrations.py (Access tab: Gmail, Drive/Sheets, Analytics, Search Console).
GOOGLE_TOOL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",        # drafts, sent only after the owner's OK
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/analytics.readonly",
    "https://www.googleapis.com/auth/webmasters.readonly",
    "https://www.googleapis.com/auth/content",               # Merchant Center (changes need the owner's OK)
]
ASSISTANT_SCOPES = ASSISTANT_SCOPES + GOOGLE_TOOL_SCOPES


class GoogleApiError(RuntimeError):
    def __init__(self, status, body, url=""):
        self.status = status
        self.body = body
        try:
            message = json.loads(body)["error"]["message"]
        except (ValueError, KeyError, TypeError):
            message = (body or "")[:300]
        super().__init__(f"HTTP {status} from {url.split('?')[0]}: {message}")


def credentials_from_json(token_json):
    from google.oauth2.credentials import Credentials

    info = json.loads(token_json)
    if info.get("expiry"):
        # Sign-in (google-auth-oauthlib) gives a time-zone-aware expiry, which to_json() writes as "...+00:00Z";
        # google-auth reads only the naive "...Z" form and compares it with a naive clock.
        info["expiry"] = info["expiry"].replace("+00:00", "")
    return Credentials.from_authorized_user_info(info, scopes=info.get("scopes"))


def naive_utc_expiry(creds):
    """google-auth compares expiry with a naive UTC clock; sign-in sets an aware one ("can't compare
    offset-naive and offset-aware datetimes"). Call before using or saving fresh sign-in credentials."""
    from datetime import timezone

    expiry = getattr(creds, "expiry", None)
    if expiry is not None and expiry.tzinfo is not None:
        creds.expiry = expiry.astimezone(timezone.utc).replace(tzinfo=None)
    return creds


class GoogleClient:
    """requests wrapper that keeps the owner's access token fresh."""

    def __init__(self, credentials):
        import threading

        self.creds = credentials
        self._refresh_lock = threading.Lock()  # requests can come from several threads

    def _auth(self):
        from google.auth.transport.requests import Request

        with self._refresh_lock:
            if not self.creds.valid:
                self.creds.refresh(Request())
            return {"Authorization": f"Bearer {self.creds.token}"}

    def auth_header(self):
        """For integrations.py: Google APIs called with Man AI's Google sign-in."""
        return self._auth()

    def request(self, method, url, params=None, body=None):
        resp = requests.request(method, url, headers=self._auth(), params=params, json=body, timeout=60)
        if resp.status_code >= 400:
            raise GoogleApiError(resp.status_code, resp.text, url)
        return resp.json() if resp.content else {}

    def paginate(self, url, key, params=None, limit=None):
        params = dict(params or {})
        seen = 0
        while True:
            page = self.request("GET", url, params=params)
            for item in page.get(key, []):
                yield item
                seen += 1
                if limit and seen >= limit:
                    return
            token = page.get("nextPageToken")
            if not token:
                return
            params["pageToken"] = token
