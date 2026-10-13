#!/bin/bash
# One-time setup for Talent Solutions AI (Cloud Run service chat-assistant). Run by a project Owner in
# Cloud Shell; safe to re-run.
#
# It does only what the deploying identity (stephen@, used by Claude sessions) isn't allowed
# to: create the app's own service account and its IAM grants, give it the secrets, let the
# deployer deploy as it, and create the hourly job.
# Nothing here prints a secret.
set -euo pipefail
PROJECT=helperexpress-hk
# Where the app's secrets live; never mannetsang's shp-ai-bot-2026.
SECRETS_PROJECT=${SECRETS_PROJECT:-$PROJECT}
[ "$SECRETS_PROJECT" != shp-ai-bot-2026 ] || { echo "SECRETS_PROJECT must not be shp-ai-bot-2026" >&2; exit 1; }
REGION=us-central1
SA=chat-assistant@$PROJECT.iam.gserviceaccount.com
DEPLOYER=stephen@$PROJECT.iam.gserviceaccount.com
URL=https://chat-assistant-f2n7veewpa-uc.a.run.app
DATABASE=chat-assistant

gcloud services enable calendar-json.googleapis.com \
  aiplatform.googleapis.com firestore.googleapis.com cloudscheduler.googleapis.com --project $PROJECT

gcloud iam service-accounts describe $SA --project $PROJECT >/dev/null 2>&1 \
  || gcloud iam service-accounts create chat-assistant --project $PROJECT --display-name "Chat assistant (Cloud Run)"
gcloud projects add-iam-policy-binding $PROJECT --member serviceAccount:$SA --role roles/aiplatform.user --condition=None --quiet >/dev/null
# Firestore: only the app's own database, so it can't touch anything else in the project.
gcloud projects add-iam-policy-binding $PROJECT --member serviceAccount:$SA --role roles/datastore.user --quiet \
  --condition="expression=resource.name == \"projects/$PROJECT/databases/$DATABASE\",title=chat-assistant-database-only" >/dev/null
# Lets the deployer deploy the service to run as this account.
gcloud iam service-accounts add-iam-policy-binding $SA --project $PROJECT \
  --member serviceAccount:$DEPLOYER --role roles/iam.serviceAccountUser --quiet >/dev/null

for s in chat-assistant-password chat-assistant-oauth-client chat-assistant-user-token chat-assistant-run-token; do
  gcloud secrets describe $s --project $SECRETS_PROJECT >/dev/null 2>&1 \
    || gcloud secrets create $s --replication-policy automatic --project $SECRETS_PROJECT >/dev/null
  gcloud secrets add-iam-policy-binding $s --project $SECRETS_PROJECT \
    --member serviceAccount:$SA --role roles/secretmanager.secretAccessor --quiet >/dev/null
done
# The app writes these itself (Connect Google, Change password) and disables older versions.
for s in chat-assistant-user-token chat-assistant-password; do
  gcloud secrets add-iam-policy-binding $s --project $SECRETS_PROJECT \
    --member serviceAccount:$SA --role roles/secretmanager.secretVersionManager --quiet >/dev/null
done

# The HelperXpress database (query_database), when its connection URL is stored.
if gcloud secrets describe SUPABASE_DB_URL --project $SECRETS_PROJECT >/dev/null 2>&1; then
  gcloud secrets add-iam-policy-binding SUPABASE_DB_URL --project $SECRETS_PROJECT \
    --member serviceAccount:$SA --role roles/secretmanager.secretAccessor --quiet >/dev/null
fi

# AI partners: ChatGPT's key (and an Anthropic key, only if you use the Claude API instead of Vertex).
for s in CHATGPT_API_KEY ANTHROPIC_API_KEY; do
  if gcloud secrets describe $s --project $SECRETS_PROJECT >/dev/null 2>&1; then
    gcloud secrets add-iam-policy-binding $s --project $SECRETS_PROJECT \
      --member serviceAccount:$SA --role roles/secretmanager.secretAccessor --quiet >/dev/null
  fi
done

TOKEN=$(gcloud secrets versions access latest --secret chat-assistant-run-token --project $SECRETS_PROJECT 2>/dev/null || true)
if [ -z "$TOKEN" ]; then
  TOKEN=$(openssl rand -hex 32)
  printf %s "$TOKEN" | gcloud secrets versions add chat-assistant-run-token --data-file=- --project $SECRETS_PROJECT >/dev/null
fi

args=(--location $REGION --project $PROJECT --schedule "0 * * * *" --time-zone America/Toronto
      --uri $URL/run --http-method POST --attempt-deadline 1800s --quiet)
gcloud scheduler jobs update http chat-assistant-hourly "${args[@]}" --update-headers "X-Run-Token=$TOKEN" >/dev/null 2>&1 \
  || gcloud scheduler jobs create http chat-assistant-hourly "${args[@]}" --headers "X-Run-Token=$TOKEN" >/dev/null

# Every 5 minutes: restart tasks whose work was cut off (a deploy replaces the server mid-task).
args=(--location $REGION --project $PROJECT --schedule "*/5 * * * *" --time-zone America/Toronto
      --uri $URL/watchdog --http-method POST --attempt-deadline 60s --quiet)
gcloud scheduler jobs update http chat-assistant-watchdog "${args[@]}" --update-headers "X-Run-Token=$TOKEN" >/dev/null 2>&1 \
  || gcloud scheduler jobs create http chat-assistant-watchdog "${args[@]}" --headers "X-Run-Token=$TOKEN" >/dev/null

echo "Talent Solutions AI setup done: service account, secrets, the hourly job and the watchdog are in place."
