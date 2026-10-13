# Talent Solutions AI

Chief of staff for **HelperConnects** and **HelperXpress**. It keeps a project
board, learns every project through an interview and conversation, follows the
code on GitHub, queries the HelperXpress database, reads the Gmail, Calendar
and Drive of helperxpresss@gmail.com, and works its own tasks every hour. You
talk to it in text or live voice, alongside two AI partners, Claude and ChatGPT.

The Cloud Run service is still called `chat-assistant`. Features in detail:
[COMPANY_ASSISTANT.md](COMPANY_ASSISTANT.md).

```
Cloud Scheduler (hourly) ──POST /run──►  Cloud Run: chat-assistant (Flask), project helperexpress-hk
                                          ├─ Vertex AI Gemini, Claude, ChatGPT: the models
                                          ├─ Firestore (database chat-assistant): board, knowledge, activity, runs
                                          ├─ Secret Manager: password, Google token, keys
                                          ├─ Google (helperxpresss@gmail.com): Gmail, Calendar, Drive
                                          ├─ GitHub: repositories, issues, pull requests, CI
                                          ├─ HelperXpress Postgres (Supabase): query_database
                                          └─ Cloud Run: web-browser (sees web pages)
Browser ──shared password──►  chat · board · projects · questions · access · activity
```

## What each hourly run does

It works up to `WORK_PER_RUN` (3) of Talent Solutions AI's own open tasks that it hasn't
worked on in the last six hours, then re-checks every connected system so the
Access tab stays true. **Run now** starts a run at once. Talent Solutions AI doesn't read
team chats.

## Security

- **Sign-in is one shared password**, kept in Secret Manager as
  `chat-assistant-password`. Changing it (in the app) signs every other
  browser out. Wrong passwords are rate-limited.
- Everyone who signs in shares one conversation, so the models ask who is
  typing when it matters.
- **Connect Google** links one Google account (`GOOGLE_ACCOUNT`) for Gmail,
  Calendar and Drive; a token from any other account is ignored. There is no
  domain-wide delegation.
- That token lives in Secret Manager as `chat-assistant-user-token`, readable
  only by the dedicated `chat-assistant@` service account. A new connection
  disables the old version.
- **That isolation holds only if nothing else has project-wide Secret Manager
  read access.** A project-level *Secret Manager Secret Accessor* (or Admin)
  grant lets its holder read every key. Keep grants per secret. Check: IAM &
  Admin → IAM on `helperexpress-hk`, look for Secret Manager roles granted on
  the project.
- Secrets come only from `helperexpress-hk`; the app refuses to start if
  `SECRETS_PROJECT` is `shp-ai-bot-2026`.
- The HelperXpress database is read in read-only transactions; a change runs
  only after the owner approves the exact statement.
- Scheduler calls `/run` with a shared token (`chat-assistant-run-token`);
  UI writes need the signed-in session plus a custom header (CSRF).

## Setup

Everything is on project `helperexpress-hk`. The service, its service account,
the `chat-assistant` database, the hourly job and the 5-minute watchdog already exist; `setup.sh`
(run once by a project Owner, safe to re-run) recreates the account's grants,
the app's secrets and both scheduler jobs.

1. **Password**: Secret Manager → `chat-assistant-password` → new version.
2. **OAuth client** (for Connect Google): Credentials → OAuth client ID → Web
   application, with these redirect URIs, then store the JSON as the secret
   `chat-assistant-oauth-client`:
   - `https://chat-assistant-f2n7veewpa-uc.a.run.app/oauth/callback`
   - `https://chat-assistant-943501248136.us-central1.run.app/oauth/callback`
3. **Deploy**: every push to `main` runs the tests and deploys
   (`.github/workflows/deploy.yml`). By hand, from the repo root:
   ```
   gcloud run deploy chat-assistant --source src/cloud_run --region us-central1 --project helperexpress-hk
   ```
   The service keeps its settings (below) between deploys.
4. Open <https://chat-assistant-f2n7veewpa-uc.a.run.app>, sign in, then on the
   **Access** tab press **Connect Google** (as helperxpresss@gmail.com) and paste
   a GitHub token.

## Configuration

Live values are set on the Cloud Run service.

| Env var | Live value | Default in code |
|---|---|---|
| `OWNER_EMAIL` | `talenttsang@gmail.com` | `talenttsang@gmail.com` |
| `GOOGLE_ACCOUNT` | `helperxpresss@gmail.com` | `OWNER_EMAIL` |
| `GCP_PROJECT` | `helperexpress-hk` | `helperexpress-hk` |
| `SECRETS_PROJECT` | (unset) | `GCP_PROJECT` |
| `FIRESTORE_DATABASE` | `chat-assistant` | `(default)` |
| `DATABASES` | `helperxpress=SUPABASE_DB_URL` | none (`name=SECRET` pairs; the secret holds a postgres:// URL) |
| `INTEGRATIONS` | `github;notion;figma;airtable;firecrawl;gmail;google_drive;google_sheets;ga4_admin;ga4_data;search_console;shared_drive;shared_calendar` | every connector in `integrations.py` |
| `CODE_REPOS` | `talenttsang/chief-of-staff,Talent-Solutions-Limited/helperxpress` | none (no `commit_code` tool) |
| `BROWSER_URL` | the `web-browser` service | none (no browser tool) |
| `PUBLIC_URL` | the service's URL | the request's host |
| `TIME_ZONE` | | `America/Toronto` |
| `WORK_PER_RUN` | | `3` |

Models, voices and the rest: COMPANY_ASSISTANT.md → Configuration.

## Local development and tests

Tests use a fake Google, fake models and an in-memory store, so they need no
network or credentials:

```
python -m unittest discover -s tests
```
