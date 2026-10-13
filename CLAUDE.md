# Talent Solutions AI (HelperConnects / HelperXpress) – Agent Instructions

## Agent Behavior

- Do not announce that you have read the background information. Only reference it when relevant.
- If you believe something is correct, say "correct."
- If you believe something is wrong, say "wrong." Do not side with the user just to agree.

## Git

- Whenever you complete a piece of work (new feature, fix, config change), commit it — don't leave finished work uncommitted. Then push to GitHub.
- Then deploy it (see Deploying below) and check it: `/health` returns 200, the sign-in page loads, and the new revision logs no errors. Don't wait to be asked.
- If the push fails because this machine's credentials (`talenttsang`) lack write access to the repo, still commit locally and tell the user the push is pending.
- Never commit secrets: tokens, webhook URLs, service-account keys, `.env` files. Config with secrets goes in env vars / repo secrets, not the repo.

---

## Local Environment

Manne runs commands locally on **Windows** (`cmd.exe`), not a Unix shell. When
handing over a command to run:

- Keep it on **one line**. A trailing `\` is not a line continuation in CMD and
  gets passed through as a literal argument. (CMD uses `^`, PowerShell uses a
  backtick.)
- Use `python`, not `python3`.
- Prefer `gcloud`/`git` invocations over shell builtins, pipes, `export`, or
  heredocs, none of which behave the same way.

---

## Data & Credentials

**GCP project:** `helperexpress-hk` (talenttsang's). It runs the HelperXpress app
(Cloud Run `helperexpress`, asia-east1) and Talent Solutions AI (Cloud Run `chat-assistant` and
`web-browser`, us-central1). Talent Solutions AI keeps its data in its own Firestore database,
`chat-assistant`.

All credentials live in that project's **Secret Manager**; nothing sensitive goes
in the repo, in a GitHub Actions *variable*, or in a Claude cloud environment's
*environment variables* box. Never read secrets from `shp-ai-bot-2026` (mannetsang's
project): the app refuses to start if `SECRETS_PROJECT` points there.

Secrets Talent Solutions AI uses:

| Secret | What it is |
|---|---|
| `chat-assistant-password` | The shared sign-in password |
| `chat-assistant-oauth-client` | OAuth client for Connect Google |
| `chat-assistant-user-token` | Token for Talent Solutions AI's Google account (helperxpresss@gmail.com) |
| `chat-assistant-run-token` | Shared token Cloud Scheduler sends to `/run` |
| `SUPABASE_DB_URL` | HelperXpress Postgres (Supabase), used by `query_database` |
| `GITHUB_TOKEN` | GitHub, pasted in the Access tab |
| `CHATGPT_API_KEY`, `ANTHROPIC_API_KEY` | AI partners (optional) |

The app's service account, `chat-assistant@helperexpress-hk`, is given read access
one secret at a time, never project-wide, and Firestore access only to the
`chat-assistant` database.

---

## Deploying

From the repo root (Cloud Shell or any machine with gcloud signed in to
`helperexpress-hk`):

```
gcloud run deploy chat-assistant --source src/cloud_run --region us-central1 --project helperexpress-hk
```

The service keeps its settings between deploys (`OWNER_EMAIL`, `GCP_PROJECT`,
`FIRESTORE_DATABASE=chat-assistant`, `DATABASES=helperxpress=SUPABASE_DB_URL`,
`GOOGLE_ACCOUNT`, `INTEGRATIONS`, `BROWSER_URL`, `PUBLIC_URL`).
Roll back with `gcloud run services update-traffic chat-assistant --to-revisions <revision>=100 ...`.

---

## Businesses

The company background the app gives every model is
`src/cloud_run/knowledge/company.md`; keep it current there. In short:

- **HelperConnects** and **HelperXpress** are the two businesses Talent Solutions AI serves as
  chief of staff, focused on project management and software engineering.
- What each one does, for whom and who works on it isn't recorded yet; the app's
  interview questions collect it.
- Code is on GitHub (`helperconnects` under `mannetsang`; others under
  `talenttsang`, e.g. `talent-solutions-limited/helperxpress`).
- Talent Solutions AI's Google account is helperxpresss@gmail.com (Gmail, Calendar, Drive).
  It doesn't read team chats.
