# Talent Solutions AI: features

Talent Solutions AI is the chief of staff for **HelperConnects** and **HelperXpress**: it learns
every project, follows the code, gets access to the businesses' systems, does the
work, and reports back. You talk to it in text or live voice, alongside two AI
partners, Claude and ChatGPT. The Cloud Run service is still called `chat-assistant`.
Setup and deployment: [README.md](README.md).

**The chat** isn't a tab: it's a window at the bottom right, over every tab, so you can talk while you look at
the board or the Access list. **–** (or Esc) minimizes it to a **💬 Talent Solutions AI** button. The button gets a red dot
when a reply arrives while it's minimized, and reads **On a call** while a voice call is running (a call keeps
going while it's minimized). Open or minimized is remembered per browser; the first time, it starts open on a
computer and minimized on a phone, where the open chat fills the screen.

Everyone signs in with one shared password and shares one conversation per partner, so whoever is typing may
not be the owner. The models ask who it is when it matters (an approval, a fact about someone).

## Tabs

| Where | What it's for |
|---|---|
| **Chat** (bottom right) | Chat with **Talent Solutions AI** (Gemini), **Claude** or **ChatGPT**. Each one has the same company background, knowledge base and tools, and its own conversation history. **🎙 Talk** starts a live voice call with whichever of the three is selected. **🔊** on a reply reads it aloud (press again to stop); **🔊 Read aloud** in the chat's header reads each new typed reply automatically. |
| **Board** | Tasks: yours, and the ones you give to Talent Solutions AI. |
| **Projects** | Every project it knows, per business: goal, owner, status, deadline, next steps and open tasks. |
| **Questions** | The interview: what it needs to know, highest priority first. Answer in the box, in the chat (**Discuss in chat**), or by voice; each answer is processed into projects, facts, tasks and follow-up questions. |
| **Access** | Systems it can use now, those with credentials that aren't connected yet, and those it still needs. **How do I connect this?** asks it to walk you through one. |
| **Activity** | Changes it made in systems and databases, and the run history. |

## How it learns

- **Company background**: `src/cloud_run/knowledge/company.md`, given to every model. Keep it current there.
- **Conversations and answers**: the models record what they learn with tools (`save_project`,
  `record_fact`, `ask_owner`, `request_access`, ...).
- **Keeping the board tidy**: the AI adds, changes and removes anything on it on its own: tasks
  (`create_task`, `update_task`, `delete_task`), projects (`save_project`, `delete_project`; their tasks stay,
  unlinked), facts (`record_fact`, `delete_fact`), questions (`ask_owner`, `delete_question`, which removes the
  recorded answer too) and Access rows (`request_access`, `update_system`, `delete_system`). Every removal is
  listed under Activity. A consulted partner can't remove anything.
- It starts with the round-1 interview questions and the known systems list, seeded once.

## What it may do on its own (tiered autonomy)

Settings (⚙) sets each category to **Auto** (act and log it) or **Ask** (propose and wait). Defaults:

| Category | Default |
|---|---|
| Calendar events and invites | Auto |
| Messages to customers or anyone outside the company | Ask |
| Refunds, prices, discounts, ad budgets, payments | Ask |
| Staff, schedules, hiring, HR | Ask |
| Deleting or overwriting data | Ask |

**Act automatically** in the header pauses everything. On top of the tiers, any change in a company system or
database waits for the owner's OK on the exact change (see below).

## AI partners

| Partner | How it's called | Setup |
|---|---|---|
| Assistant | Gemini (`TALK_MODEL`, default `gemini-2.5-pro`) on Vertex AI | none |
| Claude | Claude Opus 5.5 (`claude-opus-5-5`) via the Anthropic SDK, with automatic refusal fallback to Claude Opus 4.8. Tries the Claude API with the `ANTHROPIC_API_KEY` secret first, then Vertex AI | Either: credit on the Anthropic account behind `ANTHROPIC_API_KEY` plus Secret Accessor on it for the app's service account, or Claude quota on Vertex AI for `helperexpress-hk` (IAM & Admin > Quotas, `global_online_prediction_requests_per_base_model`, base model `anthropic-claude-opus`) |
| ChatGPT | OpenAI SDK, model from Settings (default `gpt-5`) | Re-run `setup.sh` so the app can read the `CHATGPT_API_KEY` secret |

The Assistant can consult either partner (`consult_partner`), for example for a
second opinion on a plan. A consulted partner can use the knowledge tools but can't
make changes or change settings. Talking to Claude or ChatGPT sends company context
to Anthropic or OpenAI; **Let Claude and ChatGPT work with company information** in
Settings switches that off. **Test partners** in Settings checks all three.

With `CLAUDE_BACKEND=auto` (the default) a route that can't serve at all (no quota,
no credit, no readable key) is skipped, and when neither works the error says what
each needs. `CLAUDE_BACKEND=anthropic` or `=vertex` pins one route.

## Live voice

**🎙 Talk** opens a WebSocket to `/ws/voice?partner=…`. The browser sends
16 kHz 16-bit PCM from the microphone (with echo cancellation), the service relays
it to **Gemini 3.8 Live** (`gemini-3.8-live` on Vertex AI), and streams the spoken
answer back at 24 kHz. You can interrupt it; the transcript appears live and is
saved into Talent Solutions AI's chat conversation. During a call it uses the same tools as
text. Use Chrome or Edge. Only a signed-in session, from the app's own page, can
open the voice socket.

Each bot has one voice, on calls and when its replies are read aloud: the Assistant
**Kore** (Gemini), Claude **cedar** and ChatGPT **marin** (both OpenAI). Hovering over a
partner's button names the models behind it, and a call's bar names the model on the
line ("Listening (gpt-realtime-2.1)").

**ChatGPT** calls run on OpenAI's own live model (`gpt-realtime-2.1` through the
Realtime API, with the `CHATGPT_API_KEY` secret): ChatGPT hears your audio directly,
answers in its own voice, and uses the same tools, company background and
conversation history as typed ChatGPT. The on-screen transcript is primed with the
businesses', projects' and systems' names. OpenAI bills live audio per minute,
noticeably more than typed chat. `CHATGPT_VOICE=relay` switches ChatGPT to the relay
below instead. A turn on OpenAI's line ends after 1.2 s of silence
(`OPENAI_TURN_DETECTION`).

**Claude** has no audio input in Anthropic's API, so a call with Claude uses a live
voice line only as ears and voice: by default ChatGPT's (`CLAUDE_VOICE=realtime`) or
Gemini's (`CLAUDE_VOICE=gemini`). The line gets a single tool (`ask_claude`) that
passes your words to Claude, which answers with its own model, tools and
conversation history; the line reads Claude's answer out word for word. The line is
given the businesses' names and terms so they reach Claude spelled right. On
Gemini's line a turn ends after a 1.5 s pause (`RELAY_END_SILENCE_MS`).

## Working on its own

Every partner is told to use its access before answering: look things up in the
connected systems (`list_integrations`, `call_api`), query the HelperXpress database
(`query_database`), and chain up to 24 tool calls per answer.

**Board → a task → 🤖 Give to Talent Solutions AI** makes the assistant the task's owner and has
it work the task right away (`worker.py`); each hourly run works up to `WORK_PER_RUN`
(3) of its open tasks again, any it hasn't touched for six hours. Working alone it
can't change data (it prepares the exact change and asks for your OK). When a task
is finished it marks it done. Its report appears on the task (and a 🤖 report badge
on the card) and in Talent Solutions AI's chat conversation.

## Changing code and deploying, on its own

The owner chose full autonomy for code. `commit_code` makes one commit straight to `main` of a repository in
`CODE_REPOS` (`github_code.py`), with no approval, typed, on a call or working a task alone. The push starts the
repository's GitHub Actions workflow (`.github/workflows/deploy.yml`), which runs the checks (the tests here;
`npm run check` in helperxpress) and, if they pass, deploys to Cloud Run. The assistant then reads the workflow
run (`call_api GET /repos/{owner}/{repo}/actions/runs?head_sha=…`) and reports. Every commit is listed under
Activity with its link.

| Repository | Deploys to |
|---|---|
| `talenttsang/chief-of-staff` | Cloud Run `chat-assistant` (us-central1): Talent Solutions AI itself |
| `Talent-Solutions-Limited/helperxpress` | Cloud Run `helperexpress` (asia-east1): the HelperXpress app |

Limits that hold whatever a model asks: only those repositories; nothing under `.github/` (the checks and the
deploy stay as people set them up); no path outside the repository; at most 50 files and 500 KB each per commit;
a commit only ever adds to `main`, never rewrites it; consulted partners and the interview can't commit. GitHub
signs in to Google as `github-deploy@helperexpress-hk` through Workload Identity Federation, only from `main` of
those two repositories, so no Google key is stored anywhere. To undo a change, revert its commit (that deploys
too) or move Cloud Run traffic back to the previous revision.

## Company systems

Every partner (typed or on a call) has two tools for company systems:
`list_integrations` (which systems, whether each is ready, useful paths) and
`call_api` (one request). The connectors are in `integrations.py`; a deployment
offers only those named in `INTEGRATIONS`. The live app offers:

| System | How it connects |
|---|---|
| GitHub | The `GITHUB_TOKEN` secret: repositories, issues, pull requests, CI; and code changes with `commit_code` |
| HelperXpress database | `query_database` on the Postgres URL in `SUPABASE_DB_URL` (`DATABASES=helperxpress=SUPABASE_DB_URL`) |
| Gmail, Calendar, Drive, Sheets, Google Analytics, Search Console | Connect Google, as helperxpresss@gmail.com |
| Drive folders and calendars shared with Talent Solutions AI | Shared with chat-assistant@helperexpress-hk.iam.gserviceaccount.com; nothing to connect |
| Notion, Figma, Airtable, web research (Firecrawl) | Their key in `helperexpress-hk`'s Secret Manager (`NOTION_API_KEY`, `FIGMA_TOKEN`, `AIRTABLE_COMPANY_TOKEN`, `FIRECRAWL_API`); until it's there, the Access tab says the app can't read it |

SEO research (DataForSEO) is in `integrations.py` but not offered; add `dataforseo` to `INTEGRATIONS` to offer
it. The Superhairpieces connectors (BigCommerce, Amazon, Walmart, SkuVault, Trustpilot, Stamped, Omnisend,
TeamDesk, Merchant Center) were removed on 2026-10-13.

The models read pages, emails and a conversation many people share, so the design
assumes someone may try to talk them into misusing a key:

- Key values never reach a model. The server adds them to each request (logins are
  exchanged for tokens on the server) and scrubs them from anything a system returns.
- Each system has one pinned address; a path can't send a request (or its key)
  anywhere else.
- Reads run at once. Anything that changes data needs `confirmed=true`, which a model
  may set only after you agreed to that exact change in the conversation; a
  consulted partner can't confirm at all. Database reads run in read-only
  transactions. Confirmed changes are listed under Activity.

**Access → Connect everything** (or Connect on one row) asks Google once for the
project owner's OK. With that one-hour token, used right away and never stored
(`cloud_setup.py`), the app gives its own service account read access to each secret
it needs, one secret at a time and never project-wide, creates an empty secret for
any key you'll paste in the app, and switches on the Google APIs the tools need. A
**paste** row opens a form with the steps for finding the key; what you paste goes
straight into Secret Manager and is tested at once.

## Web browser

Talent Solutions AI can open web pages in a real browser and **see** them: every `browser` call does one action (open,
click, type, press a key, pick an option, scroll, back, screenshot) and shows the model a screenshot of the
result, plus what's clickable and the visible text. Use it for how a page looks or works, including the
HelperXpress app itself and the mobile view (`device=mobile`). All three models see the screenshots; on a live
call Gemini Flash describes the screenshot instead.

The browser is its own Cloud Run service, `web-browser` (`src/browser/`): Playwright's Chromium, one instance at
most (open tabs live in its memory, closed after 10 idle minutes; at most 4), scaled to zero when unused. It
runs as `web-browser@helperexpress-hk.iam.gserviceaccount.com`, which holds **no** roles, and only Talent Solutions AI's
service account may call it. Talent Solutions AI finds it through `BROWSER_URL`; without it the tool isn't offered.

**Test accounts.** `browser` with `action=sign_in` and an account (`BROWSER_ACCOUNTS`: `helperxpress-helper`,
`helperxpress-employer`, `helperxpress-admin`) signs in to HelperXpress, so it can check signed-in pages after a
change, even working alone. The helper and employer go through `/test-login`; the admin through the normal admin
login with its own password (`AI_ADMIN_PASSWORD`): full admin, but the audit trail names it
`talent-solutions-ai`. The app's server fills the login from the `BROWSER_ACCOUNTS` secret;
the model never sees it. The accounts are "Test Helper" and "Test Employer (Talent Solutions AI)"; the test
helper is left out of every employer's search. HelperXpress's key is `TEST_LOGIN_KEY`; rotate it by adding new
versions of both secrets (the same for `AI_ADMIN_PASSWORD`).

Guards in the service itself, whatever the model asks:
- only http(s) to public addresses: localhost, private and link-local ranges (the metadata server), and
  `*.internal` / `*.local` are blocked for the page and every request it makes, names resolving to them too;
- no typing into password or payment-card fields; no downloads, file pickers or device permissions; dialogs
  are dismissed (and reported).

Sites that challenge data-centre visitors stay blocked; it doesn't disguise itself or solve challenges. Working
alone on a task it may look and click but not type, press keys or pick options. The models are told never to
log in, enter personal or payment details, place orders or send a form without your OK; that rule is an
instruction, not a technical block.

Deploy:

    gcloud run deploy web-browser --source src/browser --region us-central1 --project helperexpress-hk --service-account web-browser@helperexpress-hk.iam.gserviceaccount.com --no-allow-unauthenticated --memory 2Gi --cpu 2 --concurrency 8 --max-instances 1 --timeout 120
    gcloud run services add-iam-policy-binding web-browser --region us-central1 --project helperexpress-hk --member serviceAccount:chat-assistant@helperexpress-hk.iam.gserviceaccount.com --role roles/run.invoker

then set `BROWSER_URL=<the web-browser URL>` on the `chat-assistant` service.

## Read aloud

**🔊** on any reply, or the **Read replies aloud** switch (remembered in this browser),
speaks a typed reply out loud in that bot's call voice: the Assistant with Gemini
text-to-speech on Vertex AI (`gemini-2.5-flash-tts`), Claude and ChatGPT with OpenAI's
(`gpt-4o-mini-tts`). If one provider fails the other reads instead, except for
ChatGPT, which uses OpenAI only. Speech is streamed, so it starts in about two
seconds whatever the length. A finished reading is cached, so replaying it is free.

## Configuration

| Env var | Default |
|---|---|
| `TALK_MODEL` | `gemini-2.5-pro` (falls back to `GEMINI_MODEL`) |
| `LIVE_MODEL` / `LIVE_LOCATION` | `gemini-3.8-live` / `us-central1` |
| `RELAY_END_SILENCE_MS` | `1500` (pause that ends your turn in a relayed call) |
| `CHATGPT_VOICE` / `CLAUDE_VOICE` | `realtime` / `realtime` (OpenAI's line; `relay` / `gemini` for Gemini's) |
| `OPENAI_TURN_DETECTION` | `server:1200` (or `semantic:low`..`high`) |
| `OPENAI_LIVE_MODEL` / `OPENAI_LIVE_REASONING` | `gpt-realtime-2.1` / `low` |
| `OPENAI_TRANSCRIBE_MODEL` | `gpt-4o-transcribe` (on-screen transcript of ChatGPT calls) |
| `GEMINI_TTS_MODEL` / `OPENAI_TTS_MODEL` | `gemini-2.5-flash-tts` / `gpt-4o-mini-tts` (read aloud) |
| `VOICE_ASSISTANT` / `VOICE_CLAUDE` / `VOICE_CHATGPT` | `Kore` / `cedar` / `marin` (calls and read aloud) |
| `CLAUDE_MODEL` / `CLAUDE_FALLBACK_MODEL` | `claude-opus-5-5` / `claude-opus-4-8` |
| `CLAUDE_BACKEND` / `CLAUDE_REGION` / `CLAUDE_EFFORT` | `auto` / `global` / `medium` |
| `OPENAI_MODEL` / `OPENAI_KEY_SECRET` | `gpt-5` / `CHATGPT_API_KEY` |

Deployment settings (project, database, owner, integrations): README.md → Configuration.

## Tests

`python -m unittest discover -s tests` runs everything offline with a fake Google,
fake models and a scripted Gemini Live session: the knowledge base, autonomy tiers,
every tool, the partners, the interview, the integrations, the database tool, and the
voice bridge's audio, transcript and tool-call path.
