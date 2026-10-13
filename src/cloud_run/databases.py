"""SQL access to the businesses' own Postgres databases (e.g. HelperXpress on Supabase).

DATABASES names them: "name=SECRET" pairs separated by ";" (or spaces), where the Secret Manager secret
holds a postgres:// connection URL, e.g. DATABASES="helperxpress=SUPABASE_DB_URL". The URL never reaches a
model, and is scrubbed from error messages.

The same rule as every other system: reading runs at once, changing waits for the owner's OK.
  - Without confirmed=true the statement runs in a READ ONLY transaction, so an INSERT, UPDATE, DELETE or
    schema change fails there whatever the model wrote; the error says it needs the owner's OK.
  - With confirmed=true (which the model may set only after the owner approved this exact statement) it runs
    in a normal transaction and is committed.
Every statement has a time limit, one statement per call, and at most MAX_ROWS rows come back.
"""

import base64
import datetime
import decimal
import os
import re
import uuid

DATABASES = dict(pair.split("=", 1) for pair in re.split(r"[;\s]+", os.environ.get("DATABASES", "")) if "=" in pair)
MAX_ROWS = 200
TIMEOUT_MS = 20000


def configured():
    return sorted(DATABASES)


def _jsonable(value):
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return float(value) if value.is_finite() and abs(value) < 10 ** 15 else str(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        data = bytes(value)
        return f"<{len(data)} bytes: {base64.b64encode(data[:24]).decode()}…>" if len(data) > 24 else \
            f"<bytes: {base64.b64encode(data).decode()}>"
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return value


def _one_statement(sql):
    """The statement without a trailing ";", or None if there's more than one (outside quotes)."""
    text = (sql or "").strip().rstrip(";").strip()
    unquoted = re.sub(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|--[^\n]*|/\*.*?\*/", "", text, flags=re.S)
    return None if ";" in unquoted else text


def _scrub(message, url):
    message = str(message)
    if url:
        message = message.replace(url, "<connection url>")
        password = re.match(r"^[a-z]+://[^:/]*:([^@]*)@", url)
        if password and password.group(1):
            message = message.replace(password.group(1), "***")
    return message[:600]


def run(name, sql, secrets, confirmed=False, connect=None):
    """Run one statement; returns {"columns", "rows", "row_count", "truncated"} or {"rows_affected"} or {"error"}."""
    if name not in DATABASES:
        return {"error": f"no database called '{name}'. Known: {', '.join(configured()) or 'none'}"}
    statement = _one_statement(sql)
    if not statement:
        return {"error": "send exactly one SQL statement per call"}
    try:
        url = (secrets.get(DATABASES[name]) or "").strip() if secrets else ""
    except Exception:  # noqa: BLE001 - no permission counts as missing
        url = ""
    if not url:
        return {"error": f"the app can't read the {DATABASES[name]} secret for the {name} database"}
    if connect is None:
        import psycopg

        connect = lambda u: psycopg.connect(u, autocommit=False, connect_timeout=10, prepare_threshold=None)  # noqa: E731
    try:
        with connect(url) as conn:
            with conn.cursor() as cur:
                if not confirmed:
                    cur.execute("SET TRANSACTION READ ONLY")
                cur.execute(f"SET LOCAL statement_timeout = {TIMEOUT_MS}")
                cur.execute(statement)
                if cur.description:
                    columns = [d.name if hasattr(d, "name") else d[0] for d in cur.description]
                    rows = cur.fetchmany(MAX_ROWS + 1)
                    result = {"columns": columns, "rows": [[_jsonable(v) for v in r] for r in rows[:MAX_ROWS]],
                              "row_count": min(len(rows), MAX_ROWS), "truncated": len(rows) > MAX_ROWS}
                else:
                    result = {"rows_affected": cur.rowcount}
            if confirmed:
                conn.commit()
            else:
                conn.rollback()
        return result
    except Exception as exc:  # noqa: BLE001 - the model reads this and adjusts
        message = _scrub(exc, url)
        if "read-only transaction" in message:
            message += (" (This statement changes data. Show Manne the exact statement and ask; run it with "
                        "confirmed=true only after he says yes.)")
        return {"error": message}
